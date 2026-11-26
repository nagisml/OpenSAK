"""
src/opensak/desc_images.py — description images stored for offline use (#1361).

Cache descriptions load their images from the cache owner's image hosts.
"Download description images…" (Waypoint menu) fetches the images of the
chosen caches once and keeps them under <data dir>/description_images,
shared by all databases. The description pane (gui/cache_detail.py) then
shows the stored copy instead of asking the host: its request interceptor
redirects the image request to opensak://img/<key>, which serves the file.
That works with Offline mode on, and saves the request when it's off.

Each image is filed by a hash of its URL, without the scheme, so the http
and https address of one image share a file:

    description_images/<key[:2]>/<key>

Only real image files are kept (PNG, JPEG, GIF, WebP, BMP, ICO, checked by
their first bytes, not by what the server claims), up to MAX_IMAGE_BYTES
each. Nothing is downloaded while Offline mode is on. The download is
throttled per host (one request at a time, HOST_INTERVAL_S apart, and
Retry-After honoured on HTTP 429/503), so a large database doesn't hammer
the image hosts.

Qt-free, so the download runs on a worker thread and can be tested without
a GUI.
"""

from __future__ import annotations

import email.utils
import hashlib
import queue
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

from opensak import net
from opensak.logger import get_logger

log = get_logger("desc_images")

DIR_NAME = "description_images"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
TIMEOUT_S = 20
# Hosts downloaded from at a time; each host gets one request at a time.
WORKERS = 4
# Pause between two requests to the same host.
HOST_INTERVAL_S = 0.5
# Wait after HTTP 429/503 without a Retry-After, and the longest wait we
# accept from one; then the image is tried again, up to RETRIES times.
BACKOFF_DEFAULT_S = 30.0
BACKOFF_MAX_S = 300.0
RETRIES = 2

_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
# style="background: url(...)" and the like
_CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")\s]+)""", re.IGNORECASE)

# First bytes → MIME type of the image formats a description may show.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)


def images_dir() -> Path:
    from opensak.config import get_app_data_dir
    return get_app_data_dir() / DIR_NAME


def image_mime(data: bytes) -> Optional[str]:
    """The MIME type of *data* if it is an image format we keep, else None."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    return None


def normalize_url(url: str) -> Optional[str]:
    """An absolute http(s) URL for an image reference, or None.
    Protocol-relative ("//host/x.png") becomes https."""
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return url


def url_key(url: str) -> str:
    """The file name of *url*'s image: a hash of the URL without its scheme,
    with the host in lower case."""
    parts = urlsplit(url.strip())
    rest = url.strip()[len(parts.scheme) + 1:] if parts.scheme else url.strip()
    rest = rest.replace(parts.netloc, parts.netloc.lower(), 1)
    return hashlib.sha256(rest.encode("utf-8")).hexdigest()


def key_path(key: str, root: Optional[Path] = None) -> Optional[Path]:
    """Where the image with *key* is stored; None for an invalid key."""
    if not _KEY_RE.match(key):
        return None
    return (root or images_dir()) / key[:2] / key


def local_path(url: str, root: Optional[Path] = None) -> Optional[Path]:
    """The stored copy of the image at *url*, or None."""
    normalized = normalize_url(url)
    if normalized is None:
        return None
    path = key_path(url_key(normalized), root)
    return path if path is not None and path.is_file() else None


class _ImageUrlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        for name, value in attrs:
            if not value:
                continue
            if name == "src" and tag in ("img", "input"):
                self.urls.append(value)
            elif name == "srcset":
                self.urls.extend(part.split()[0] for part in value.split(",") if part.strip())
            elif name == "background":
                self.urls.append(value)
            elif name == "style":
                self.urls.extend(_CSS_URL_RE.findall(value))


def image_urls(html: str) -> list[str]:
    """The absolute http(s) image URLs in a description, in order, once each:
    <img src>, srcset, background="" and url() in style="" attributes."""
    parser = _ImageUrlParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 — broken markup: keep what was found
        pass
    seen: dict[str, None] = {}
    for raw in parser.urls:
        url = normalize_url(raw)
        if url is not None:
            seen.setdefault(url, None)
    return list(seen)


def missing_images(html: str, root: Optional[Path] = None) -> list[str]:
    """The image URLs of *html* that have no stored copy."""
    return [u for u in image_urls(html) if local_path(u, root) is None]


def read_image(key: str, root: Optional[Path] = None) -> Optional[tuple[bytes, str]]:
    """The stored image with *key* and its MIME type, or None."""
    path = key_path(key, root)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    mime = image_mime(data)
    return None if mime is None else (data, mime)


# ── Download ──────────────────────────────────────────────────────────────────
#
# Politeness towards the image hosts: each host gets one request at a time,
# started at least HOST_INTERVAL_S after the previous one ended; up to
# *workers* different hosts are served in parallel. A host that answers
# 429 Too Many Requests or 503 Service Unavailable is left alone for as long
# as its Retry-After asks (BACKOFF_DEFAULT_S without one, at most
# BACKOFF_MAX_S), then the image is tried again, up to RETRIES times.


class DownloadError(Exception):
    """One image could not be stored."""


class SlowDown(DownloadError):
    """The host asked us to wait (HTTP 429/503) before the next request."""

    def __init__(self, status: int, retry_after: float) -> None:
        super().__init__(f"server busy (HTTP {status})")
        self.status = status
        self.retry_after = retry_after


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> float:
    """Seconds to wait for a Retry-After header (seconds or an HTTP date),
    between HOST_INTERVAL_S and BACKOFF_MAX_S; BACKOFF_DEFAULT_S when the
    header is missing or unreadable."""
    seconds: Optional[float] = None
    value = (value or "").strip()
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(value)
                seconds = when.timestamp() - (time.time() if now is None else now)
            except (TypeError, ValueError, IndexError, OverflowError):
                seconds = None
    if seconds is None or seconds != seconds:  # missing, unreadable or NaN
        seconds = BACKOFF_DEFAULT_S
    return min(max(seconds, HOST_INTERVAL_S), BACKOFF_MAX_S)


def _user_agent() -> str:
    from opensak import __version__
    return f"OpenSAK/{__version__} (+https://github.com/OpenSAK-Org/OpenSAK)"


def download_image(url: str, root: Optional[Path] = None,
                   timeout: float = TIMEOUT_S) -> Path:
    """Download the image at *url* and store it. Raises DownloadError, or
    SlowDown when the host asks us to wait."""
    path = key_path(url_key(url), root)
    assert path is not None
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=net.SSL_CONTEXT) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
        net.note_success()
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 503):
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise SlowDown(exc.code, parse_retry_after(retry_after)) from None
        raise DownloadError(f"HTTP {exc.code} {exc.reason}".strip()) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        net.note_failure(exc)
        raise DownloadError(str(getattr(exc, "reason", exc))) from None
    if len(data) > MAX_IMAGE_BYTES:
        raise DownloadError(f"larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    if image_mime(data) is None:
        raise DownloadError("not an image")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as exc:
        raise DownloadError(str(exc)) from None
    return path


@dataclass
class DownloadResult:
    """What download_images() did: images stored now, already stored
    before, and failed (URL → reason)."""

    downloaded: int = 0
    already: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False

    @property
    def total(self) -> int:
        return self.downloaded + self.already + len(self.failed)


# A worker's answer for an image it did not try because of a cancel.
_CANCELLED = "\x00cancelled"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def download_images(
    urls: Iterable[str],
    root: Optional[Path] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    workers: int = WORKERS,
) -> DownloadResult:
    """Store every image of *urls* that isn't stored yet, throttled per
    host as described above: *workers* hosts in parallel, each host one
    request at a time.

    progress(done, total) is called from the calling thread after each URL.
    Raises net.OfflineModeError while Offline mode is on. A cancel stops
    after the downloads already running, also while waiting for a host."""
    net.ensure_online()
    result = DownloadResult()
    by_host: dict[str, list[str]] = {}
    for url in dict.fromkeys(urls):
        if local_path(url, root) is not None:
            result.already += 1
        else:
            by_host.setdefault(_host(url), []).append(url)
    pending = sum(len(host_urls) for host_urls in by_host.values())
    total = result.already + pending
    done = result.already
    if progress:
        progress(done, total)
    if not pending:
        return result
    stop = threading.Event()
    # (url, None when stored | reason | _CANCELLED), from the workers
    answers: queue.Queue[tuple[str, Optional[str]]] = queue.Queue()
    interval, retries = HOST_INTERVAL_S, RETRIES

    def fetch_host(host_urls: list[str]) -> None:
        next_at = 0.0  # monotonic time the host may get its next request
        for url in host_urls:
            answer: Optional[str] = _CANCELLED
            attempts = 0
            while not stop.wait(max(0.0, next_at - time.monotonic())):
                try:
                    download_image(url, root)
                    answer = None
                except SlowDown as exc:
                    attempts += 1
                    log.debug("%s asks to wait %.0f s (HTTP %d)",
                              _host(url), exc.retry_after, exc.status)
                    next_at = time.monotonic() + exc.retry_after
                    if attempts <= retries:
                        continue
                    answer = str(exc)
                except DownloadError as exc:
                    answer = str(exc)
                next_at = max(next_at, time.monotonic() + interval)
                break
            answers.put((url, answer))

    def check_cancel() -> None:
        if not stop.is_set() and is_cancelled():
            stop.set()
            result.cancelled = True

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for host_urls in by_host.values():
            pool.submit(fetch_host, host_urls)
        while pending:
            check_cancel()
            try:
                url, answer = answers.get(timeout=0.2)
            except queue.Empty:
                continue
            pending -= 1
            if answer == _CANCELLED:
                result.cancelled = True
                continue
            if answer is None:
                result.downloaded += 1
            else:
                result.failed[url] = answer
                log.debug("description image not stored: %s (%s)", url, answer)
            done += 1
            if progress:
                progress(done, total)
            check_cancel()
    return result


# ── Disk use ──────────────────────────────────────────────────────────────────


def size_bytes(root: Optional[Path] = None) -> int:
    """Disk space the stored images use."""
    total = 0
    base = root or images_dir()
    if not base.is_dir():
        return 0
    for path in base.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def clear(root: Optional[Path] = None) -> int:
    """Delete every stored image; returns the bytes they used."""
    size = size_bytes(root)
    shutil.rmtree(root or images_dir(), ignore_errors=True)
    log.info("description images deleted (%d bytes)", size)
    return size
