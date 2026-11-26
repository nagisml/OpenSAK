"""
src/opensak/gui/dialogs/description_images_dialog.py — download the images
of cache descriptions for offline use (#1361).

Waypoint → Download description images… (and the cache list's right-click
menu). The images are stored once under <data dir>/description_images
(desc_images.py) and the description pane shows them from there, also with
Offline mode on.

Same layout as Update waypoint locations: the entry point sets the default
scope.
  - Menu: the caches in the current list.
  - Right-click (gc_codes): only the selected cache.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QRadioButton, QVBoxLayout,
)

from opensak import desc_images
from opensak.gui.dialogs.widgets import WorkerGuardMixin, clamp_dialog_height_to_screen
from opensak.gui.theme import hint_style
from opensak.lang import tr

# Caches read from the database per query (SQLite's parameter limit, #1041).
_CHUNK = 500


def description_image_urls(gc_codes: Optional[list[str]],
                           is_cancelled=lambda: False) -> list[str]:
    """The image URLs in the HTML descriptions of *gc_codes* (None: every
    cache in the active database), each once."""
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    columns = (Cache.short_description, Cache.short_desc_html,
               Cache.long_description, Cache.long_desc_html)
    urls: dict[str, None] = {}

    def collect(rows) -> None:
        for short, short_html, long, long_html in rows:
            for text, is_html in ((short, short_html), (long, long_html)):
                if text and is_html:
                    for url in desc_images.image_urls(text):
                        urls.setdefault(url, None)

    with get_session() as session:
        if gc_codes is None:
            collect(session.query(*columns).yield_per(_CHUNK))
        else:
            for i in range(0, len(gc_codes), _CHUNK):
                if is_cancelled():
                    break
                collect(session.query(*columns)
                        .filter(Cache.gc_code.in_(gc_codes[i:i + _CHUNK])).all())
    return list(urls)


class DescriptionImagesWorker(QThread):
    """Collects the image URLs, then downloads the missing images."""

    progress = Signal(int, int)    # (done, total) images
    all_done = Signal(object)      # desc_images.DownloadResult
    failed = Signal(str)           # message (e.g. Offline mode is on)

    def __init__(self, gc_codes: Optional[list[str]], parent=None) -> None:
        super().__init__(parent)
        self._gc_codes = gc_codes
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from opensak.net import OfflineModeError
        try:
            urls = description_image_urls(self._gc_codes, lambda: self._cancel)
            result = desc_images.download_images(
                urls, progress=self.progress.emit, is_cancelled=lambda: self._cancel)
            if self._cancel:
                result.cancelled = True
            self.all_done.emit(result)
        except OfflineModeError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — shown in the dialog
            self.failed.emit(str(exc))


class DescriptionImagesDialog(WorkerGuardMixin, QDialog):
    """Download description images for offline use.

    *list_codes*: the caches in the current list (menu). *gc_codes*: the
    selected cache (right-click), which becomes the default scope."""

    images_downloaded = Signal()

    def __init__(self, parent=None, *, list_codes: Optional[list[str]] = None,
                 gc_codes: Optional[list[str]] = None) -> None:
        super().__init__(parent)
        self._list_codes = list_codes
        self._gc_codes = gc_codes
        self._worker: Optional[DescriptionImagesWorker] = None
        self.setWindowTitle(tr("desc_img_title"))
        self.setMinimumWidth(460)
        clamp_dialog_height_to_screen(self, parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(tr("desc_img_info"))
        info.setWordWrap(True)
        layout.addWidget(info)

        self._scope_box = QGroupBox(tr("update_loc_scope_group"))
        scope_layout = QVBoxLayout(self._scope_box)
        self._rb_this = QRadioButton(tr("update_loc_scope_this"))
        self._rb_list = QRadioButton(tr("desc_img_scope_list",
                                        count=len(self._list_codes or [])))
        self._rb_all = QRadioButton(tr("desc_img_scope_all"))
        for rb in (self._rb_this, self._rb_list, self._rb_all):
            scope_layout.addWidget(rb)
        layout.addWidget(self._scope_box)
        self._rb_this.setVisible(self._gc_codes is not None)
        self._rb_list.setVisible(self._list_codes is not None)
        if self._gc_codes is not None:
            self._rb_this.setChecked(True)
        elif self._list_codes is not None:
            self._rb_list.setChecked(True)
        else:
            self._rb_all.setChecked(True)

        hint = QLabel(tr("desc_img_hint"))
        hint.setStyleSheet(hint_style())
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton(tr("desc_img_start_btn"))
        self._start_btn.clicked.connect(self._start)
        btn_row.addWidget(self._start_btn)
        self._cancel_btn = QPushButton(tr("cancel"))
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._request_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def selected_codes(self) -> Optional[list[str]]:
        """The caches the chosen scope covers; None: the whole database."""
        if self._rb_this.isChecked() and self._gc_codes is not None:
            return list(self._gc_codes)
        if self._rb_list.isChecked() and self._list_codes is not None:
            return list(self._list_codes)
        return None

    def _start(self) -> None:
        from opensak import net
        if net.offline_mode():
            self._status.setText("✗ " + tr("offline_mode_blocked"))
            return
        self._set_running(True)
        self._status.setText(tr("desc_img_collecting"))
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._worker = DescriptionImagesWorker(self.selected_codes(), parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.all_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        # Forget the worker only once run() has returned (see
        # UpdateLocationDialog._start for why).
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _request_cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self._progress.setRange(0, total)
        self._progress.setValue(done)
        self._status.setText(tr("desc_img_running", done=done, total=total))

    def _on_done(self, result: desc_images.DownloadResult) -> None:
        self._set_running(False)
        self._progress.setVisible(False)
        if result.total == 0:
            self._status.setText(tr("desc_img_none"))
        else:
            key = "desc_img_cancelled" if result.cancelled else "desc_img_done"
            self._status.setText(tr(key, downloaded=result.downloaded,
                                    already=result.already, failed=len(result.failed)))
        if result.downloaded:
            self.images_downloaded.emit()

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        self._progress.setVisible(False)
        self._status.setText("✗ " + message)

    def _set_running(self, running: bool) -> None:
        self._start_btn.setEnabled(not running)
        self._scope_box.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        self._close_btn.setEnabled(not running)

    def _on_worker_finished(self) -> None:
        self._worker = None

    def _close_while_running(self) -> None:
        # #1265: Escape and the window's close button cancel the download.
        self._request_cancel()
