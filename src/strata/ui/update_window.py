"""
Update window — shown when the user clicks the "Update to X.Y.Z" tray item.

Two stages:
  1. Show release notes + Download button
  2. After Download is clicked: show progress bar, on completion launch
     installer and quit the app
"""
from __future__ import annotations

import asyncio
import threading

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from strata.config import APP_VERSION
from strata.core.updater import (
    UpdateInfo,
    download_installer,
    launch_installer_and_quit,
)


class UpdateWindow:
    def __init__(self, app, info: UpdateInfo):
        self.app = app
        self.info = info

        self.window = toga.Window(
            title="Strata Update",
            size=(520, 460),
            resizable=False,
        )
        self.window.content = self._build_content()
        self.window.show()

    @classmethod
    def open(cls, app, info: UpdateInfo):
        cls(app, info)

    def _build_content(self) -> toga.Box:
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1, padding=20))

        outer.add(
            toga.Label(
                "A new version is available",
                style=Pack(font_size=15, font_weight="bold"),
            )
        )
        outer.add(
            toga.Label(
                f"{APP_VERSION}    →    {self.info.version}",
                style=Pack(font_size=12, color="#666", padding=(4, 0, 16, 0)),
            )
        )

        # Release notes — multiline, read-only. Toga MultilineTextInput is
        # editable by default; we set readonly=True.
        self._notes = toga.MultilineTextInput(
            value=self.info.notes.strip() or "(No release notes)",
            readonly=True,
            style=Pack(flex=1, padding=(0, 0, 12, 0)),
        )
        outer.add(self._notes)

        # Progress (hidden until download starts). We toggle visibility in
        # _start_download.
        self._progress = toga.ProgressBar(
            max=100, value=0,
            style=Pack(padding=(0, 0, 4, 0), height=8, visibility="hidden"),
        )
        outer.add(self._progress)
        self._progress_label = toga.Label(
            "",
            style=Pack(font_size=11, color="#666", padding=(0, 0, 12, 0), visibility="hidden"),
        )
        outer.add(self._progress_label)

        # Buttons
        btns = toga.Box(style=Pack(direction=ROW))
        btns.add(toga.Box(style=Pack(flex=1)))
        self._later_btn = toga.Button(
            "Later",
            on_press=self._on_later,
            style=Pack(width=100, padding=(0, 8, 0, 0)),
        )
        btns.add(self._later_btn)
        self._download_btn = toga.Button(
            "Download & Install",
            on_press=self._on_download,
            style=Pack(width=180),
        )
        btns.add(self._download_btn)
        outer.add(btns)

        return outer

    def _on_later(self, widget):
        self.window.close()

    def _on_download(self, widget):
        self._download_btn.text = "Downloading…"
        self._download_btn.enabled = False
        self._later_btn.enabled = False
        self._progress.style.visibility = "visible"
        self._progress_label.style.visibility = "visible"
        self._progress_label.text = "Starting download…"

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        def on_progress(downloaded: int, total: int):
            self._marshal(self._update_progress, downloaded, total)

        path = download_installer(self.info, on_progress=on_progress)
        self._marshal(self._download_done, path)

    def _marshal(self, fn, *args):
        try:
            self.app.loop.call_soon_threadsafe(lambda: fn(*args))
        except RuntimeError:
            pass

    def _update_progress(self, downloaded: int, total: int):
        if total > 0:
            self._progress.value = (downloaded / total) * 100
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._progress_label.text = f"{mb_done:.1f} / {mb_total:.1f} MB"
        else:
            mb_done = downloaded / (1024 * 1024)
            self._progress_label.text = f"{mb_done:.1f} MB"

    def _download_done(self, path):
        if path is None:
            self._progress_label.text = "Download failed. Check connection and retry."
            self._progress_label.style.color = "#c2270a"
            self._download_btn.text = "Retry"
            self._download_btn.enabled = True
            self._later_btn.enabled = True
            return

        self._progress_label.text = "Launching installer…"
        ok = launch_installer_and_quit(path, self.app._do_quit)
        if not ok:
            self._progress_label.text = "Could not launch installer."
            self._progress_label.style.color = "#c2270a"
            self._download_btn.text = "Retry"
            self._download_btn.enabled = True
            self._later_btn.enabled = True
