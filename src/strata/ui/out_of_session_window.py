"""
Out-of-session changes window — shown when Start Session detects local files
changed outside a session.

Toga doesn't have a list-with-icons widget like CTk's row-of-frames, so we
use Table with status/path columns. This is more native-feeling on Windows
than the painted approximation we had before.
"""
from __future__ import annotations

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from strata.core.engine import OutOfSessionChange


STATUS_LABEL = {
    "modified": "Modified",
    "added": "Added",
    "deleted": "Deleted",
}


class OutOfSessionWindow:
    def __init__(self, app, changes: list[OutOfSessionChange]):
        self.app = app
        self.changes = changes

        self.window = toga.Window(
            title="Files Modified Outside Session",
            size=(640, 520),
            resizable=True,
        )
        self.window.content = self._build_content()
        self.window.show()

    @classmethod
    def open(cls, app, changes: list[OutOfSessionChange]):
        cls(app, changes)

    def _build_content(self) -> toga.Box:
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1, padding=20))

        # Header
        outer.add(
            toga.Label(
                "⚠ Files Modified Outside Session",
                style=Pack(font_size=14, font_weight="bold", color="#b07a00"),
            )
        )
        outer.add(
            toga.Label(
                "These files changed since your last session ended.\n"
                "Choose how to handle them before starting.",
                style=Pack(font_size=11, color="#666", padding=(4, 0, 12, 0)),
            )
        )

        # File list — Toga Table is the native-Windows way to show this. We
        # build a list of (status, path) rows from the changes.
        table_rows = [
            (STATUS_LABEL.get(c.status, c.status), c.path) for c in self.changes
        ]
        self._table = toga.Table(
            headings=["Change", "Path"],
            data=table_rows,
            style=Pack(flex=1),
        )
        outer.add(self._table)

        # Helper text
        outer.add(
            toga.Label(
                "Keep local → uploads your changes\n"
                "Discard local → overwrites with R2 version",
                style=Pack(font_size=11, color="#888", padding=(12, 0, 0, 0)),
            )
        )

        # Action buttons
        btns = toga.Box(style=Pack(direction=ROW, padding=(12, 0, 0, 0)))
        btns.add(toga.Box(style=Pack(flex=1)))
        btns.add(
            toga.Button(
                "Cancel",
                on_press=self._on_cancel,
                style=Pack(width=90, padding=(0, 8, 0, 0)),
            )
        )
        btns.add(
            toga.Button(
                "Discard Local",
                on_press=self._on_discard,
                style=Pack(width=120, padding=(0, 8, 0, 0)),
            )
        )
        btns.add(
            toga.Button(
                "Keep Local",
                on_press=self._on_keep,
                style=Pack(width=110),
            )
        )
        outer.add(btns)

        return outer

    def _on_cancel(self, widget):
        self.window.close()

    def _on_discard(self, widget):
        self.app.start_session_after_choice(discard=True, changes=self.changes)
        self.window.close()

    def _on_keep(self, widget):
        self.app.start_session_after_choice(discard=False, changes=self.changes)
        self.window.close()
