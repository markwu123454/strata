"""
Status window — opened from the tray icon. Shows current sync state and
provides a primary action button.

We refresh the labels on a 1-second timer using asyncio. The engine pushes
state through callbacks, but the timer covers cases where the user opens
the window mid-syncing — they should see live progress immediately.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from strata.core.engine import SyncStatus


# Label text and color hint per status. Toga's Pack `color` accepts hex
# strings; we still want some visual differentiation for error vs success.
STATUS_DISPLAY = {
    SyncStatus.IDLE: ("Idle", "#444"),
    SyncStatus.STARTING: ("Starting Session…", "#b07a00"),
    SyncStatus.SYNCING: ("Downloading…", "#0a6dc2"),
    SyncStatus.ENDING: ("Uploading…", "#0a6dc2"),
    SyncStatus.ERROR: ("Error", "#c2270a"),
}


class StatusWindow:
    _instance: "StatusWindow | None" = None

    def __init__(self, app):
        self.app = app
        self._refresh_task: asyncio.Task | None = None

        self.window = toga.Window(
            title="Strata",
            size=(420, 320),
            resizable=False,
            on_close=self._on_close,
        )
        self.window.content = self._build_content()
        self.window.show()
        self._refresh()

        # Schedule periodic refresh on the asyncio loop. We cancel it in
        # _on_close so it doesn't keep ticking after the window is gone.
        self._refresh_task = asyncio.ensure_future(self._tick())

    @classmethod
    def open_or_focus(cls, app):
        if cls._instance is not None:
            try:
                cls._instance.window.show()
                return
            except Exception:
                cls._instance = None
        cls._instance = cls(app)

    # ── Layout ─────────────────────────────────────────────────────────────

    def _build_content(self) -> toga.Box:
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1, padding=20))

        # Header: app name + device
        header = toga.Box(style=Pack(direction=ROW, padding=(0, 0, 16, 0)))
        header.add(
            toga.Label(
                "Strata",
                style=Pack(font_size=16, font_weight="bold", flex=1),
            )
        )
        self._device_label = toga.Label(
            self.app.config.get("device_name", ""),
            style=Pack(font_size=11, color="#888"),
        )
        header.add(self._device_label)
        outer.add(header)

        # Status row: dot + label
        status_row = toga.Box(style=Pack(direction=ROW, padding=(0, 0, 4, 0)))
        self._status_dot = toga.Label(
            "●",
            style=Pack(font_size=18, padding=(0, 8, 0, 0), color="#0a6dc2"),
        )
        status_row.add(self._status_dot)
        self._status_label = toga.Label(
            "Idle",
            style=Pack(font_size=14, font_weight="bold", flex=1),
        )
        status_row.add(self._status_label)
        outer.add(status_row)

        # Detail / message
        self._detail_label = toga.Label(
            "",
            style=Pack(font_size=11, color="#555", padding=(2, 0, 12, 0)),
        )
        outer.add(self._detail_label)

        # Progress bar — shown only during active sync. Toga's ProgressBar
        # supports `max=None` for indeterminate mode.
        self._progress = toga.ProgressBar(
            max=None,
            style=Pack(padding=(0, 0, 12, 0), height=8),
        )
        outer.add(self._progress)

        # Path footer
        self._path_label = toga.Label(
            "",
            style=Pack(font_size=10, color="#888", padding=(0, 0, 12, 0)),
        )
        outer.add(self._path_label)

        # Spacer
        outer.add(toga.Box(style=Pack(flex=1)))

        # Action button row
        actions = toga.Box(style=Pack(direction=ROW))
        self._action_btn = toga.Button(
            "Start Session",
            on_press=self._on_action,
            style=Pack(flex=1, padding=(0, 8, 0, 0)),
        )
        actions.add(self._action_btn)
        actions.add(
            toga.Button(
                "Open Folder",
                on_press=self._on_open_folder,
                style=Pack(width=120),
            )
        )
        outer.add(actions)

        return outer

    # ── Refresh logic ──────────────────────────────────────────────────────

    def _refresh(self):
        """Pull state from the app and update widgets. Called from the UI
        thread (timer or on_close). Wrapped in try/except because the
        window can be closed mid-refresh."""
        try:
            app = self.app
            status = app._current_status
            session_active = app._session_active
            is_active_op = status in (SyncStatus.SYNCING, SyncStatus.ENDING, SyncStatus.STARTING)

            # Status dot + label
            if status == SyncStatus.IDLE:
                if session_active:
                    self._status_dot.style.color = "#0a8a4e"  # green
                    self._status_label.text = "Session Active"
                    self._detail_label.text = (
                        "Files are checked out to this device.\n"
                        "End session to upload changes."
                    )
                else:
                    self._status_dot.style.color = "#0a6dc2"
                    self._status_label.text = "No Active Session"
                    self._detail_label.text = "Start a session to check out files."
            else:
                label, color = STATUS_DISPLAY[status]
                self._status_dot.style.color = color
                self._status_label.text = label
                self._detail_label.text = app._status_message

            # Path
            if app.engine is not None:
                self._path_label.text = str(app.engine.sync_dir)

            # Progress bar
            if is_active_op:
                self._progress.style.visibility = "visible"
                if not self._progress.is_running:
                    self._progress.start()
            else:
                self._progress.stop()
                # Hiding via visibility keeps layout stable; `display=none`
                # would reflow.
                self._progress.style.visibility = "hidden"

            # Action button
            if is_active_op:
                self._action_btn.text = "Working…"
                self._action_btn.enabled = False
            elif session_active:
                self._action_btn.text = "End Session"
                self._action_btn.enabled = True
            else:
                self._action_btn.text = "Start Session"
                self._action_btn.enabled = True
        except Exception:
            # Window was closed mid-refresh; let _on_close clean up.
            pass

    async def _tick(self):
        """Refresh on a 1-second cadence while window is open."""
        try:
            while True:
                await asyncio.sleep(1.0)
                self._refresh()
        except asyncio.CancelledError:
            pass

    # ── Handlers ───────────────────────────────────────────────────────────

    def _on_action(self, widget):
        # Delegate to the app — same logic as the tray "Start/End Session" item.
        self.app._on_toggle_session(None)

    def _on_open_folder(self, widget):
        self.app._on_open_folder(None)

    def _on_close(self, window):
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None
        StatusWindow._instance = None
        return True
