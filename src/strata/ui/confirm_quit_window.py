"""
Confirm-quit window — small modal warning when user tries to quit with an
active session, since quitting leaves the lock held.
"""
from __future__ import annotations

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW


class ConfirmQuitWindow:
    def __init__(self, app):
        self.app = app

        self.window = toga.Window(
            title="Quit Strata",
            size=(400, 200),
            resizable=False,
        )
        self.window.content = self._build_content()
        self.window.show()

    @classmethod
    def open(cls, app):
        cls(app)

    def _build_content(self) -> toga.Box:
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1, padding=24, alignment="center"))

        outer.add(
            toga.Label(
                "⚠ Session still active",
                style=Pack(font_size=14, font_weight="bold", color="#b07a00"),
            )
        )
        outer.add(
            toga.Label(
                "End your session before quitting\nto avoid leaving the lock open.",
                style=Pack(font_size=12, color="#555", padding=(8, 0, 0, 0)),
            )
        )

        outer.add(toga.Box(style=Pack(flex=1)))

        btns = toga.Box(style=Pack(direction=ROW))
        btns.add(toga.Box(style=Pack(flex=1)))
        btns.add(
            toga.Button(
                "Cancel",
                on_press=self._on_cancel,
                style=Pack(width=100, padding=(0, 8, 0, 0)),
            )
        )
        btns.add(
            toga.Button(
                "Quit Anyway",
                on_press=self._on_quit,
                style=Pack(width=120),
            )
        )
        outer.add(btns)
        return outer

    def _on_cancel(self, widget):
        self.window.close()

    def _on_quit(self, widget):
        self.window.close()
        self.app._do_quit()
