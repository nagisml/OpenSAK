# tests/unit-tests/test_desc_images_1361.py — #1361 (part 2): description
# images downloaded for offline use. Waypoint → Download description images…
# stores the images of the chosen caches under <data dir>/description_images,
# filed by a hash of the URL, and the description pane shows the stored copy
# (its interceptor redirects the request to opensak://img/<key>), also with
# Offline mode on.

from __future__ import annotations

import email.utils
import io
import socket
import time
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from opensak import desc_images, net

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.fixture
def root(tmp_path):
    return tmp_path / "images"


@pytest.fixture(autouse=True)
def _no_host_pause(monkeypatch):
    """No pause between requests to one host, except in the tests of it."""
    monkeypatch.setattr(desc_images, "HOST_INTERVAL_S", 0.0)


def _store(url: str, data: bytes = PNG, root=None):
    path = desc_images.key_path(desc_images.url_key(url), root)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class FakeResp(io.BytesIO):
    pass


def _urlopen(responses: dict[str, object], calls: list[str]):
    def urlopen(request, timeout=None, context=None):
        url = request.full_url
        calls.append(url)
        answer = responses[url]
        if isinstance(answer, BaseException):
            raise answer
        return FakeResp(answer)
    return urlopen


# ── URLs and keys ─────────────────────────────────────────────────────────


class TestUrls:
    def test_http_and_https_share_a_file(self):
        assert (desc_images.url_key("http://img.example.com/a.png")
                == desc_images.url_key("https://img.example.com/a.png"))

    def test_host_case_does_not_matter_but_path_does(self):
        assert (desc_images.url_key("https://IMG.Example.com/a.png")
                == desc_images.url_key("https://img.example.com/a.png"))
        assert (desc_images.url_key("https://img.example.com/A.png")
                != desc_images.url_key("https://img.example.com/a.png"))

    @pytest.mark.parametrize("raw, url", [
        ("https://a.example/x.png", "https://a.example/x.png"),
        ("  http://a.example/x.png ", "http://a.example/x.png"),
        ("//a.example/x.png", "https://a.example/x.png"),
        ("data:image/png;base64,AAAA", None),
        ("images/x.png", None),
        ("file:///C:/x.png", None),
        ("https://", None),
    ])
    def test_normalize(self, raw, url):
        assert desc_images.normalize_url(raw) == url

    def test_image_urls(self):
        html = """
        <p><img src="https://a.example/1.jpg"> <IMG SRC='//b.example/2.png'></p>
        <img srcset="https://a.example/3.png 1x, https://a.example/4.png 2x">
        <table background="http://c.example/bg.gif"><tr><td>x</td></tr></table>
        <div style="background-image: url('https://d.example/5.png')"></div>
        <img src="https://a.example/1.jpg"> <img src="data:image/png;base64,AA">
        <img src="local.png"> <a href="https://e.example/page">link</a>
        """
        assert desc_images.image_urls(html) == [
            "https://a.example/1.jpg", "https://b.example/2.png",
            "https://a.example/3.png", "https://a.example/4.png",
            "http://c.example/bg.gif", "https://d.example/5.png",
        ]

    def test_broken_markup_keeps_what_was_found(self):
        assert desc_images.image_urls('<img src="https://a.example/x.png"><div <<') == [
            "https://a.example/x.png"]

    @pytest.mark.parametrize("data, mime", [
        (PNG, "image/png"), (JPEG, "image/jpeg"), (b"GIF89a...", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"<html>not found</html>", None), (b"<svg xmlns=...>", None), (b"", None),
    ])
    def test_image_mime(self, data, mime):
        assert desc_images.image_mime(data) == mime


class TestStore:
    def test_local_path_and_read(self, root):
        url = "https://a.example/x.png"
        assert desc_images.local_path(url, root) is None
        _store(url, root=root)
        assert desc_images.local_path(url, root) is not None
        assert desc_images.local_path("http://a.example/x.png", root) is not None
        assert desc_images.read_image(desc_images.url_key(url), root) == (PNG, "image/png")

    @pytest.mark.parametrize("key", ["", "../x", "ABC", "0" * 63, "g" * 64])
    def test_invalid_keys(self, root, key):
        assert desc_images.key_path(key, root) is None
        assert desc_images.read_image(key, root) is None

    def test_missing_images(self, root):
        _store("https://a.example/1.png", root=root)
        html = '<img src="https://a.example/1.png"><img src="https://a.example/2.png">'
        assert desc_images.missing_images(html, root) == ["https://a.example/2.png"]

    def test_size_and_clear(self, root):
        _store("https://a.example/1.png", root=root)
        _store("https://a.example/2.png", JPEG, root=root)
        assert desc_images.size_bytes(root) == len(PNG) + len(JPEG)
        assert desc_images.clear(root) == len(PNG) + len(JPEG)
        assert desc_images.size_bytes(root) == 0

    def test_default_folder_is_in_the_data_dir(self):
        from opensak.config import get_app_data_dir
        assert desc_images.images_dir() == get_app_data_dir() / "description_images"


# ── Download ──────────────────────────────────────────────────────────────


class TestDownload:
    def test_stores_images(self, root, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr("urllib.request.urlopen", _urlopen({
            "https://a.example/1.png": PNG, "https://a.example/2.jpg": JPEG,
        }, calls))
        seen: list[tuple[int, int]] = []
        result = desc_images.download_images(
            ["https://a.example/1.png", "https://a.example/2.jpg", "https://a.example/1.png"],
            root, progress=lambda d, t: seen.append((d, t)))
        assert (result.downloaded, result.already, result.failed) == (2, 0, {})
        assert sorted(calls) == ["https://a.example/1.png", "https://a.example/2.jpg"]
        assert seen[0] == (0, 2) and seen[-1] == (2, 2)
        assert desc_images.local_path("https://a.example/2.jpg", root) is not None

    def test_stored_images_are_skipped(self, root, monkeypatch):
        _store("https://a.example/1.png", root=root)
        calls: list[str] = []
        monkeypatch.setattr("urllib.request.urlopen", _urlopen({}, calls))
        result = desc_images.download_images(["http://a.example/1.png"], root)
        assert (result.downloaded, result.already) == (0, 1)
        assert calls == []

    @pytest.mark.parametrize("answer, reason", [
        (b"<html>404</html>", "not an image"),
        (PNG + b"\x00" * (desc_images.MAX_IMAGE_BYTES), "larger than"),
        (URLError(socket.gaierror(11001, "getaddrinfo failed")), "getaddrinfo"),
    ], ids=["not_an_image", "too_large", "no_network"])
    def test_failures_are_reported_not_stored(self, root, monkeypatch, answer, reason):
        monkeypatch.setattr("urllib.request.urlopen",
                            _urlopen({"https://a.example/x": answer}, []))
        result = desc_images.download_images(["https://a.example/x"], root)
        assert result.downloaded == 0
        assert reason in result.failed["https://a.example/x"]
        assert desc_images.local_path("https://a.example/x", root) is None

    def test_offline_mode_downloads_nothing(self, root, monkeypatch):
        net.set_offline_mode(True)
        monkeypatch.setattr("urllib.request.urlopen", _urlopen({}, []))
        with pytest.raises(net.OfflineModeError):
            desc_images.download_images(["https://a.example/x.png"], root)

    def test_cancel_stops_further_downloads(self, root, monkeypatch):
        urls = [f"https://a.example/{i}.png" for i in range(20)]
        calls: list[str] = []
        monkeypatch.setattr("urllib.request.urlopen", _urlopen(dict.fromkeys(urls, PNG), calls))
        result = desc_images.download_images(urls, root, is_cancelled=lambda: True, workers=1)
        assert result.cancelled
        assert result.downloaded < len(urls)
        assert len(calls) < len(urls)


# ── Throttling: be polite to the image hosts ──────────────────────────────


def _http_error(url: str, code: int, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(url, code, "busy", headers, None)


def _timed_urlopen(answers, log: list[tuple[str, float, float]], delay: float = 0.02):
    """urlopen that takes *delay* and logs (url, start, end); answers[url]
    is a list consumed one per call (bytes or an exception)."""
    def urlopen(request, timeout=None, context=None):
        url = request.full_url
        start = time.monotonic()
        time.sleep(delay)
        log.append((url, start, time.monotonic()))
        answer = answers[url].pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return FakeResp(answer)
    return urlopen


class TestThrottle:
    INTERVAL = 0.1

    @pytest.fixture(autouse=True)
    def _pause(self, monkeypatch):
        monkeypatch.setattr(desc_images, "HOST_INTERVAL_S", self.INTERVAL)

    def test_one_request_at_a_time_per_host_with_a_pause(self, root, monkeypatch):
        urls = [f"https://a.example/{i}.png" for i in range(4)]
        log: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen",
                            _timed_urlopen({u: [PNG] for u in urls}, log))
        result = desc_images.download_images(urls, root, workers=4)
        assert result.downloaded == 4
        log.sort(key=lambda entry: entry[1])
        for (_, _, prev_end), (_, start, _) in zip(log, log[1:]):
            assert start - prev_end >= self.INTERVAL * 0.9

    def test_different_hosts_run_in_parallel(self, root, monkeypatch):
        urls = ["https://a.example/1.png", "https://b.example/1.png"]
        log: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen",
                            _timed_urlopen({u: [PNG] for u in urls}, log, delay=0.2))
        result = desc_images.download_images(urls, root, workers=2)
        assert result.downloaded == 2
        (_, start_a, end_a), (_, start_b, end_b) = log
        assert start_a < end_b and start_b < end_a  # overlapping

    def test_host_case_is_one_host(self, root, monkeypatch):
        urls = ["https://A.example/1.png", "https://a.example/2.png"]
        log: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen",
                            _timed_urlopen({u: [PNG] for u in urls}, log, delay=0.1))
        desc_images.download_images(urls, root, workers=2)
        (_, _, first_end), (_, second_start, _) = sorted(log, key=lambda e: e[1])
        assert second_start >= first_end

    @pytest.mark.parametrize("code", [429, 503])
    def test_retry_after_is_honoured_then_retried(self, root, monkeypatch, code):
        url = "https://a.example/1.png"
        monkeypatch.setattr(desc_images, "BACKOFF_MAX_S", 0.3)
        log: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen", _timed_urlopen(
            {url: [_http_error(url, code, "120"), PNG]}, log))
        result = desc_images.download_images([url], root)
        assert (result.downloaded, result.failed) == (1, {})
        (_, _, first_end), (_, second_start, _) = log
        assert second_start - first_end >= 0.3 * 0.9  # capped at BACKOFF_MAX_S

    def test_gives_up_after_the_retries(self, root, monkeypatch):
        url = "https://a.example/1.png"
        monkeypatch.setattr(desc_images, "BACKOFF_MAX_S", 0.1)
        calls: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen", _timed_urlopen(
            {url: [_http_error(url, 429, "1")] * 10}, calls))
        result = desc_images.download_images([url], root)
        assert len(calls) == desc_images.RETRIES + 1
        assert "HTTP 429" in result.failed[url]

    def test_other_http_errors_are_not_retried(self, root, monkeypatch):
        url = "https://a.example/1.png"
        calls: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen", _timed_urlopen(
            {url: [_http_error(url, 404)] * 3}, calls))
        result = desc_images.download_images([url], root)
        assert len(calls) == 1
        assert "HTTP 404" in result.failed[url]

    def test_cancel_during_a_backoff_returns_at_once(self, root, monkeypatch):
        urls = ["https://a.example/1.png", "https://a.example/2.png"]
        calls: list[tuple[str, float, float]] = []
        monkeypatch.setattr("urllib.request.urlopen", _timed_urlopen(
            {urls[0]: [_http_error(urls[0], 429, "300")], urls[1]: [PNG]}, calls))
        started = time.monotonic()
        result = desc_images.download_images(urls, root, is_cancelled=lambda: bool(calls))
        assert time.monotonic() - started < 5
        assert result.cancelled and result.downloaded == 0
        assert len(calls) == 1

    @pytest.mark.parametrize("value, seconds", [
        ("7", 7.0),
        (None, 30.0),
        ("soon", 30.0),
        ("0", 0.1),          # never below the pause between requests
        ("99999", 300.0),    # never above BACKOFF_MAX_S
    ])
    def test_parse_retry_after(self, value, seconds):
        assert desc_images.parse_retry_after(value) == seconds

    def test_parse_retry_after_http_date(self):
        now = 1_700_000_000.0
        value = email.utils.formatdate(now + 60, usegmt=True)
        assert desc_images.parse_retry_after(value, now=now) == pytest.approx(60, abs=1)


# ── Description pane ──────────────────────────────────────────────────────


pytest.importorskip("pytestqt")


class TestDescInterceptor:
    @staticmethod
    def _run(url: str, allow_remote: bool = False):
        from PySide6.QtCore import QUrl
        from opensak.gui.cache_detail import _DescInterceptor
        events: list = []
        info = SimpleNamespace(
            requestUrl=lambda: QUrl(url),
            block=lambda on: events.append(("block", on)),
            redirect=lambda target: events.append(("redirect", target.toString())),
        )
        interceptor = _DescInterceptor()
        interceptor.allow_remote = allow_remote
        interceptor.interceptRequest(info)
        return events

    def test_stored_image_is_shown_from_disk(self, qapp):
        url = "https://img.example/x.png"
        _store(url)
        key = desc_images.url_key(url)
        assert self._run(url) == [("redirect", f"opensak://img/{key}")]
        net.set_offline_mode(True)
        assert self._run(url) == [("redirect", f"opensak://img/{key}")]

    def test_other_images_blocked_only_in_offline_mode(self, qapp):
        assert self._run("https://img.example/y.png") == []
        net.set_offline_mode(True)
        assert self._run("https://img.example/y.png") == [("block", True)]
        assert self._run("https://img.example/y.png", allow_remote=True) == []


def _fake_job(url: str):
    from PySide6.QtCore import QByteArray, QObject, QUrl

    class FakeJob(QObject):
        """Stands in for QWebEngineUrlRequestJob; records reply()/fail()."""

        def __init__(self) -> None:
            super().__init__()
            self.replied = None
            self.error = None

        def requestUrl(self):
            return QUrl(url)

        def requestMethod(self):
            return QByteArray(b"GET")

        def reply(self, mime, device):
            self.replied = (bytes(mime), bytes(device.readAll().data()))

        def fail(self, error):
            self.error = error

    return FakeJob()


class TestImageHandler:
    def _request(self, url: str):
        from opensak.gui.cache_detail import _DescImageHandler
        job = _fake_job(url)
        _DescImageHandler().requestStarted(job)  # type: ignore[arg-type]
        return job

    def test_serves_a_stored_image(self, qapp):
        url = "https://img.example/x.jpg"
        _store(url, JPEG)
        job = self._request(f"opensak://img/{desc_images.url_key(url)}")
        assert job.replied == (b"image/jpeg", JPEG)

    @pytest.mark.parametrize("url", [
        "opensak://img/" + "0" * 64,        # not stored
        "opensak://img/../secret",          # not a key
        "opensak://assets/" + "0" * 64,     # another host
    ])
    def test_refuses(self, qapp, url):
        from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob
        job = self._request(url)
        assert job.replied is None
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound


class TestLoadImagesBar:
    IMG = '<img src="https://img.example/a.png"><img src="https://img.example/b.png">'

    @pytest.fixture
    def panel(self, qapp):
        from opensak.gui.cache_detail import CacheDetailPanel
        p = CacheDetailPanel()
        p._current_gc_code = "GC1"
        net.set_offline_mode(True)
        return p

    def test_shown_while_an_image_is_missing(self, panel):
        _store("https://img.example/a.png")
        panel._set_description(self.IMG)
        assert not panel._images_bar.isHidden()

    def test_hidden_when_all_images_are_stored(self, panel):
        _store("https://img.example/a.png")
        _store("https://img.example/b.png")
        panel._set_description(self.IMG)
        assert panel._images_bar.isHidden()

    def test_shown_for_other_remote_content(self, panel):
        panel._set_description('<link rel="stylesheet" href="https://x.example/s.css">')
        assert not panel._images_bar.isHidden()


# ── The dialog ────────────────────────────────────────────────────────────


def _db_with_caches(tmp_path):
    from opensak.db.database import get_session, init_db
    from opensak.db.models import Cache
    init_db(db_path=tmp_path / "images.db")
    with get_session() as s:
        s.add(Cache(gc_code="GC1", name="One", cache_type="Traditional Cache",
                    latitude=55.0, longitude=12.0, long_desc_html=True,
                    long_description='<img src="https://img.example/1.png">',
                    short_desc_html=True,
                    short_description='<img src="https://img.example/s.png">'))
        s.add(Cache(gc_code="GC2", name="Two", cache_type="Traditional Cache",
                    latitude=55.0, longitude=12.0, long_desc_html=True,
                    long_description='<img src="https://img.example/1.png">'
                                     '<img src="https://img.example/2.png">'))
        s.add(Cache(gc_code="GC3", name="Plain", cache_type="Traditional Cache",
                    latitude=55.0, longitude=12.0, long_desc_html=False,
                    long_description='Not HTML: <img src="https://img.example/3.png">'))


class TestDialog:
    def test_urls_of_the_chosen_caches(self, qapp, tmp_path):
        from opensak.gui.dialogs.description_images_dialog import description_image_urls
        _db_with_caches(tmp_path)
        assert description_image_urls(["GC2"]) == [
            "https://img.example/1.png", "https://img.example/2.png"]
        assert sorted(description_image_urls(None)) == [
            "https://img.example/1.png", "https://img.example/2.png",
            "https://img.example/s.png"]   # plain-text descriptions show no images

    def test_scopes(self, qtbot):
        from opensak.gui.dialogs.description_images_dialog import DescriptionImagesDialog
        menu = DescriptionImagesDialog(list_codes=["GC1", "GC2"])
        qtbot.addWidget(menu)
        assert menu._rb_list.isChecked() and menu._rb_this.isHidden()
        assert menu.selected_codes() == ["GC1", "GC2"]
        menu._rb_all.setChecked(True)
        assert menu.selected_codes() is None
        ctx = DescriptionImagesDialog(gc_codes=["GC7"])
        qtbot.addWidget(ctx)
        assert ctx._rb_this.isChecked() and ctx._rb_list.isHidden()
        assert ctx.selected_codes() == ["GC7"]

    def test_offline_mode_refuses_to_start(self, qtbot):
        from opensak.gui.dialogs import description_images_dialog as did
        from opensak.lang import tr
        net.set_offline_mode(True)
        dlg = did.DescriptionImagesDialog(list_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._start()
        assert dlg._worker is None
        assert dlg._status.text() == "✗ " + tr("offline_mode_blocked")

    def test_worker_downloads_and_reports(self, qtbot, tmp_path, monkeypatch):
        from opensak.gui.dialogs.description_images_dialog import DescriptionImagesWorker
        _db_with_caches(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _urlopen({
            "https://img.example/1.png": PNG, "https://img.example/2.png": b"oops",
        }, []))
        worker = DescriptionImagesWorker(["GC2"])
        results: list = []
        worker.all_done.connect(results.append)
        worker.run()
        (result,) = results
        assert (result.downloaded, len(result.failed)) == (1, 1)
        assert desc_images.local_path("https://img.example/1.png") is not None

    def test_settings_show_and_delete_the_images(self, qtbot, monkeypatch):
        from opensak.gui.dialogs import settings_dialog as sd
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        monkeypatch.setattr(sd, "get_settings", lambda: s)
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: (_ for _ in ()).throw(RuntimeError("no manager")))
        monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
        monkeypatch.setattr(sd.QMessageBox, "information", lambda *a, **k: None)
        _store("https://img.example/a.png")
        dlg = sd.SettingsDialog()
        qtbot.addWidget(dlg)
        assert dlg._desc_images_clear_btn.isEnabled()
        dlg._clear_desc_images()
        assert desc_images.size_bytes() == 0
        assert not dlg._desc_images_clear_btn.isEnabled()
