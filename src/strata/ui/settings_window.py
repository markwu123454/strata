"""
Settings window — R2 credentials, sync directory, preferences.

Native Toga widgets means we get the system's native form controls instead
of customtkinter's painted approximations. Padding/spacing follows Toga's
Pack model, which is loosely flexbox-like.
"""
from __future__ import annotations

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from strata.config import save_config
from strata.core import autostart


class SettingsWindow:
    """
    Holds a single instance of the Settings window. open_or_focus brings it
    forward if already open. We use composition rather than subclassing
    toga.Window because Toga's Window doesn't really want to be subclassed
    (its lifecycle is managed by the App).
    """

    _instance: "SettingsWindow | None" = None

    def __init__(self, app):
        self.app = app
        self.config = app.config.copy()
        self.window = toga.Window(
            title="Strata — Settings",
            size=(560, 640),
            resizable=False,
            on_close=self._on_close,
        )
        self.window.content = self._build_content()
        self.window.show()

    @classmethod
    def open_or_focus(cls, app):
        """Show the window. If already open, just focus it."""
        if cls._instance is not None:
            try:
                cls._instance.window.show()
                return
            except Exception:
                cls._instance = None
        cls._instance = cls(app)

    # ── Layout ─────────────────────────────────────────────────────────────

    def _build_content(self) -> toga.Box:
        # Container split into a scrollable form and a sticky button bar.
        # ScrollContainer means long forms (or smaller windows) stay usable.
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1))

        form = toga.Box(style=Pack(direction=COLUMN, padding=20))

        # ── R2 section ────────────────────────────────────────────────────
        form.add(self._section_heading("Cloudflare R2"))
        self._field_account = self._add_field(form, "Account ID", "r2_account_id")
        self._field_bucket = self._add_field(form, "Bucket Name", "r2_bucket")
        self._field_access = self._add_field(form, "Access Key ID", "r2_access_key")
        self._field_secret = self._add_field(
            form, "Secret Access Key", "r2_secret_key", password=True
        )

        # ── Device ────────────────────────────────────────────────────────
        form.add(self._section_heading("Device"))
        self._field_device_name = self._add_field(form, "Device Name", "device_name")

        # ── Sync directory ────────────────────────────────────────────────
        form.add(self._section_heading("Sync Directory"))
        form.add(toga.Label("Folder", style=Pack(padding=(8, 0, 4, 0))))

        # Folder picker row: text input + Browse button. Toga doesn't have a
        # built-in folder picker widget, so we compose it from TextInput +
        # a Button that opens a dialog.
        dir_row = toga.Box(style=Pack(direction=ROW, padding=(0, 0, 8, 0)))
        self._field_dir = toga.TextInput(
            value=self.config.get("sync_dir", ""),
            style=Pack(flex=1, padding=(0, 8, 0, 0)),
        )
        dir_row.add(self._field_dir)
        dir_row.add(
            toga.Button(
                "Browse…",
                on_press=self._on_browse,
                style=Pack(width=90),
            )
        )
        form.add(dir_row)

        # ── Preferences ───────────────────────────────────────────────────
        form.add(self._section_heading("Preferences"))

        # Autostart switch is hidden entirely on platforms that don't support
        # it, rather than shown disabled — a greyed-out toggle would just
        # make non-Windows users wonder.
        self._switch_autostart = None
        if autostart.is_supported():
            self._switch_autostart = toga.Switch(
                "Run Strata when I sign in to Windows",
                value=self.config.get("autostart_enabled", False),
                style=Pack(padding=(4, 0)),
            )
            form.add(self._switch_autostart)

        self._switch_updates = toga.Switch(
            "Automatically check for updates",
            value=self.config.get("check_for_updates", True),
            style=Pack(padding=(4, 0)),
        )
        form.add(self._switch_updates)

        # Wrap the form in a ScrollContainer so it scrolls if the window is
        # smaller than the form's natural height.
        scroll = toga.ScrollContainer(content=form, style=Pack(flex=1))
        outer.add(scroll)

        # ── Sticky footer with Save / Cancel ──────────────────────────────
        footer = toga.Box(
            style=Pack(direction=ROW, padding=12, alignment="center")
        )
        # Spacer pushes buttons to the right
        footer.add(toga.Box(style=Pack(flex=1)))
        footer.add(
            toga.Button(
                "Cancel",
                on_press=self._on_cancel,
                style=Pack(width=100, padding=(0, 8, 0, 0)),
            )
        )
        footer.add(
            toga.Button(
                "Save",
                on_press=self._on_save,
                style=Pack(width=100),
            )
        )
        outer.add(footer)

        return outer

    def _section_heading(self, text: str) -> toga.Label:
        return toga.Label(
            text.upper(),
            style=Pack(padding=(16, 0, 6, 0), font_weight="bold", font_size=11),
        )

    def _add_field(
        self, parent: toga.Box, label: str, key: str, *, password: bool = False
    ) -> toga.TextInput:
        parent.add(toga.Label(label, style=Pack(padding=(8, 0, 4, 0))))
        cls = toga.PasswordInput if password else toga.TextInput
        widget = cls(
            value=self.config.get(key, ""),
            style=Pack(padding=(0, 0, 4, 0)),
        )
        # Stash the key on the widget so _gather_form() can pull it back out.
        widget._strata_key = key
        parent.add(widget)
        return widget

    # ── Handlers ───────────────────────────────────────────────────────────

    async def _on_browse(self, widget):
        """Folder picker. Toga's dialogs are async — we await the result."""
        try:
            path = await self.window.dialog(
                toga.SelectFolderDialog(title="Select sync directory")
            )
            if path:
                self._field_dir.value = str(path)
        except Exception:
            # User cancelled or dialog failed — leave the value alone.
            pass

    def _on_cancel(self, widget):
        self.window.close()

    def _on_save(self, widget):
        # Pull values out of the form fields by their stashed key
        for field in (
            self._field_account,
            self._field_bucket,
            self._field_access,
            self._field_secret,
            self._field_device_name,
        ):
            self.config[field._strata_key] = field.value.strip()
        self.config["sync_dir"] = self._field_dir.value.strip()
        self.config["autostart_enabled"] = (
            self._switch_autostart.value if self._switch_autostart else False
        )
        self.config["check_for_updates"] = self._switch_updates.value

        save_config(self.config)

        # Apply autostart change immediately. Done after save_config so a
        # registry write failure doesn't block credential persistence.
        if autostart.is_supported():
            if self.config["autostart_enabled"]:
                autostart.enable()
            else:
                autostart.disable()

        self.app.on_settings_saved(self.config)
        self.window.close()

    def _on_close(self, window):
        SettingsWindow._instance = None
        return True  # allow close
