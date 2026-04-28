"""
Lock conflict window — shown when Start Session fails because another device
already holds the lock. User can wait or force-take.
"""
from __future__ import annotations

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from strata.core.lock import LockInfo


class LockConflictWindow:
    def __init__(self, app, lock_info: LockInfo):
        self.app = app
        self.lock_info = lock_info

        self.window = toga.Window(
            title="Session In Use",
            size=(440, 280),
            resizable=False,
        )
        self.window.content = self._build_content()
        self.window.show()

    @classmethod
    def open(cls, app, lock_info: LockInfo):
        cls(app, lock_info)

    def _build_content(self) -> toga.Box:
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1, padding=24, alignment="center"))

        outer.add(
            toga.Label(
                "🔒",
                style=Pack(font_size=36, padding=(0, 0, 8, 0)),
            )
        )
        outer.add(
            toga.Label(
                "Session Active on Another Device",
                style=Pack(font_size=14, font_weight="bold", color="#b07a00"),
            )
        )
        outer.add(
            toga.Label(
                self.lock_info.device_name,
                style=Pack(font_size=12, padding=(12, 0, 0, 0)),
            )
        )
        outer.add(
            toga.Label(
                f"started {self.lock_info.acquired_at_str()} · "
                f"{self.lock_info.age_minutes():.0f} min ago",
                style=Pack(font_size=11, color="#666", padding=(2, 0, 0, 0)),
            )
        )
        outer.add(
            toga.Label(
                "End the session on that device, or force-take if it crashed.",
                style=Pack(font_size=11, color="#888", padding=(12, 0, 0, 0)),
            )
        )

        # Spacer pushes buttons to bottom
        outer.add(toga.Box(style=Pack(flex=1)))

        btns = toga.Box(style=Pack(direction=ROW, padding=(16, 0, 0, 0)))
        btns.add(toga.Box(style=Pack(flex=1)))
        btns.add(
            toga.Button(
                "Cancel",
                on_press=self._on_cancel,
                style=Pack(width=110, padding=(0, 8, 0, 0)),
            )
        )
        btns.add(
            toga.Button(
                "Force Take",
                on_press=self._on_force,
                style=Pack(width=140),
            )
        )
        outer.add(btns)
        return outer

    def _on_cancel(self, widget):
        self.window.close()

    def _on_force(self, widget):
        self.app.force_take_session()
        self.window.close()
