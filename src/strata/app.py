"""
Strata main app — Toga BackgroundApp with a system tray status icon.

Architectural notes:

- We use `main_window = toga.App.BACKGROUND` so the app runs without any
  visible window by default, just the tray icon. Windows (Settings, Status,
  dialogs) are created on demand.

- The sync engine runs in background threads (`engine.start_session` blocks
  on network IO). Toga's event loop is asyncio-based, so we use
  `app.loop.call_soon_threadsafe` to marshal UI updates back to the main
  thread. Calling `widget.text = ...` from a worker thread will sometimes
  appear to work but races with paint; always marshal.

- The status icon's menu is built once with all commands always present;
  individual commands toggle their `enabled` state and `text` to reflect
  app state. This is the pattern Toga supports — dynamically rebuilding
  the menu doesn't work cleanly.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from strata.config import (
    APP_VERSION,
    is_configured,
    load_config,
    save_config,
    STATE_DIR,
)
from strata.core import autostart
from strata.core.engine import SyncEngine, SyncStatus
from strata.core.r2 import R2Client
from strata.core.updater import UpdateChecker, UpdateInfo


# ── Status presentation ────────────────────────────────────────────────────────
# Centralized so tray tooltip and status window agree on labels.

STATUS_TEXT = {
    SyncStatus.IDLE: "Idle",
    SyncStatus.STARTING: "Starting session",
    SyncStatus.SYNCING: "Downloading",
    SyncStatus.ENDING: "Uploading",
    SyncStatus.ERROR: "Error",
}


class StrataApp(toga.App):
    """Background app with a tray status icon."""

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def startup(self):
        # No main window — we live in the tray. Without this assignment the
        # app would refuse to start (Toga requires *something* for main_window).
        self.main_window = toga.App.BACKGROUND

        # Mutable UI/state. Worker threads write some of these and the tray
        # menu reads them; always go through _refresh_menu / _set_status to
        # marshal back to the main thread.
        self.config = load_config()
        self.engine: SyncEngine | None = None
        self._current_status = SyncStatus.IDLE
        self._status_message = "Not configured"
        self._session_active = False
        self._operation_in_progress = False
        self._pending_update: UpdateInfo | None = None

        # One-time housekeeping: clean up the old "DirSync" autostart entry
        # if the user upgraded from the previous app name. Cheap, idempotent.
        autostart._cleanup_legacy()

        # Reconcile autostart registry with config preference. If the user
        # toggled autostart, then the install path moved (e.g. after an MSI
        # upgrade), the registry value would point at a stale path.
        self._sync_autostart_state()

        self._build_status_icon()

        if is_configured(self.config):
            self._init_engine()
            self._refresh_menu()

        # Start the update checker if enabled. Runs on a background thread,
        # fires _on_update_available off-thread — we marshal to the asyncio
        # loop in there.
        self._update_checker: UpdateChecker | None = None
        if self.config.get("check_for_updates", True):
            self._start_update_checker()

    def _start_update_checker(self):
        if self._update_checker is not None:
            return
        self._update_checker = UpdateChecker(
            current_version=APP_VERSION,
            on_update_available=self._on_update_available,
        )
        self._update_checker.start()

    def _stop_update_checker(self):
        if self._update_checker is not None:
            self._update_checker.stop()
            self._update_checker = None

    def _sync_autostart_state(self):
        if not autostart.is_supported():
            return
        want_enabled = bool(self.config.get("autostart_enabled", False))
        currently_enabled = autostart.is_enabled()
        if want_enabled and not currently_enabled:
            autostart.enable()
        elif not want_enabled and currently_enabled:
            autostart.disable()
        elif want_enabled and currently_enabled:
            # Re-write to refresh the path — no-op if path already matches.
            autostart.enable()

    # ── Status icon + menu ─────────────────────────────────────────────────

    def _build_status_icon(self):
        """Create the tray icon and its commands. Commands are built once;
        their `enabled` and `text` properties update with state."""
        # The icon path is relative to the app's resource directory in the
        # frozen build, but Toga's Icon resolves it from src/strata/resources/
        # at dev time too.
        self._status_icon = toga.MenuStatusIcon(
            icon=toga.Icon("resources/strata"),
            text="Strata",
        )
        self.status_icons.add(self._status_icon)

        # Toga adds a default "Quit" item to status icons automatically. We
        # clear those so we have full control over ordering.
        self.status_icons.commands.clear()

        # Commands. We give each an `id` so we can look them up later when
        # toggling state (see _refresh_menu). Toga's CommandSet supports
        # lookup by ID.
        self._cmd_open = toga.Command(
            self._on_open_status,
            text="Open Status",
            group=self._status_icon,
            id="strata.open",
            order=0,
        )
        self._cmd_session = toga.Command(
            self._on_toggle_session,
            text="Start Session",
            group=self._status_icon,
            id="strata.session",
            order=10,
        )
        self._cmd_open_folder = toga.Command(
            self._on_open_folder,
            text="Open Sync Folder",
            group=self._status_icon,
            id="strata.folder",
            order=20,
        )
        self._cmd_settings = toga.Command(
            self._on_open_settings,
            text="Settings…",
            group=self._status_icon,
            id="strata.settings",
            order=30,
        )
        # Update slot — text and enabled are toggled when an update appears.
        # Always present so users have a discoverable place to see update
        # status, even when there's nothing pending.
        self._cmd_update = toga.Command(
            self._on_update_clicked,
            text="Up to date",
            group=self._status_icon,
            id="strata.update",
            order=40,
            enabled=False,
        )
        self._cmd_quit = toga.Command(
            self._on_quit,
            text="Quit Strata",
            group=self._status_icon,
            id="strata.quit",
            order=100,
        )

        self.status_icons.commands.add(
            self._cmd_open,
            self._cmd_session,
            self._cmd_open_folder,
            self._cmd_settings,
            self._cmd_update,
            self._cmd_quit,
        )

    def _refresh_menu(self):
        """Update enabled/text of menu items based on app state. Safe to call
        from the main thread only."""
        configured = is_configured(self.config)

        # Session command — disabled if nothing's configured, label flips
        # between Start/End based on session state.
        self._cmd_session.enabled = configured and not self._operation_in_progress
        self._cmd_session.text = (
            "End Session" if self._session_active else "Start Session"
        )

        self._cmd_open.enabled = True
        self._cmd_open_folder.enabled = configured
        self._cmd_settings.enabled = True

        # Update slot
        if self._pending_update is not None:
            self._cmd_update.text = f"Update to {self._pending_update.version}…"
            self._cmd_update.enabled = True
        else:
            self._cmd_update.text = "Up to date"
            self._cmd_update.enabled = False

        # Tooltip
        try:
            self._status_icon.text = f"Strata — {STATUS_TEXT.get(self._current_status, '')}"
        except Exception:
            # Some Toga backends don't support text updates after init;
            # silently ignore rather than crash.
            pass

    # ── Engine + state callbacks ───────────────────────────────────────────

    def _init_engine(self):
        r2 = R2Client(
            account_id=self.config["r2_account_id"],
            access_key=self.config["r2_access_key"],
            secret_key=self.config["r2_secret_key"],
            bucket=self.config["r2_bucket"],
        )
        sync_dir = Path(self.config["sync_dir"])
        sync_dir.mkdir(parents=True, exist_ok=True)
        self.engine = SyncEngine(
            sync_dir=sync_dir,
            state_dir=STATE_DIR,
            r2=r2,
            device_id=self.config["device_id"],
            device_name=self.config["device_name"],
            on_status_change=self._on_status_change,
            on_progress=self._on_progress,
        )
        self._status_message = "Ready — no active session"

        threading.Thread(target=self._recover_session, daemon=True).start()

    def _recover_session(self):
        """If the lock manager has a record of *us* holding the lock from a
        prior run, restore it so the user doesn't have to re-take it after
        a crash or reboot."""
        if self.engine is None:
            return
        try:
            lock = self.engine.lock_manager.get_current_lock()
            if lock and lock.device_id == self.config["device_id"]:
                self.engine.lock_manager._token = lock.token
                self._session_active = True
                self._status_message = f"Session resumed — started {lock.acquired_at_str()}"
                self._marshal(self._refresh_menu)
        except Exception:
            pass

    def _on_status_change(self, status: SyncStatus, message: str):
        self._current_status = status
        self._status_message = message
        self._marshal(self._refresh_menu)

    def _on_progress(self, current: int, total: int, filename: str):
        short = Path(filename).name
        self._status_message = f"{current}/{total} — {short}"
        self._marshal(self._refresh_menu)

    # ── Marshalling ────────────────────────────────────────────────────────

    def _marshal(self, fn, *args):
        """Schedule fn(*args) on the asyncio event loop. Safe to call from
        any thread. We use call_soon_threadsafe rather than asyncio.run_coroutine
        because our handlers are sync."""
        try:
            self.loop.call_soon_threadsafe(lambda: fn(*args))
        except RuntimeError:
            # Loop closed — shutting down. Drop silently.
            pass

    # ── Tray command handlers ──────────────────────────────────────────────

    def _on_open_status(self, command, **kwargs):
        from strata.ui.status_window import StatusWindow
        StatusWindow.open_or_focus(self)

    def _on_toggle_session(self, command, **kwargs):
        if self._operation_in_progress:
            return
        if not is_configured(self.config):
            self._on_open_settings(command)
            return
        if self._session_active:
            threading.Thread(target=self._end_session_worker, daemon=True).start()
        else:
            threading.Thread(target=self._start_session_worker, daemon=True).start()

    def _on_open_folder(self, command, **kwargs):
        import subprocess
        import sys
        if self.engine is not None:
            path = str(self.engine.sync_dir)
        else:
            path = self.config.get("sync_dir") or str(Path.home())
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _on_open_settings(self, command, **kwargs):
        from strata.ui.settings_window import SettingsWindow
        SettingsWindow.open_or_focus(self)

    def _on_update_clicked(self, command, **kwargs):
        if self._pending_update is None:
            return
        from strata.ui.update_window import UpdateWindow
        UpdateWindow.open(self, self._pending_update)

    def _on_quit(self, command, **kwargs):
        if self._session_active:
            self._confirm_quit()
        else:
            self._do_quit()

    # ── Session worker threads ─────────────────────────────────────────────

    def _start_session_worker(self):
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = self.engine.start_session()
            if result.success:
                self._session_active = True
            elif result.lock_info:
                self._marshal(self._show_lock_conflict, result.lock_info)
            elif result.out_of_session_changes:
                self._marshal(self._show_out_of_session, result.out_of_session_changes)
            elif result.error:
                self._status_message = f"Error: {result.error}"
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def _end_session_worker(self):
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = self.engine.end_session()
            if result.success:
                self._session_active = False
            elif result.error:
                self._status_message = f"Error: {result.error}"
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def _show_lock_conflict(self, lock_info):
        from strata.ui.lock_window import LockConflictWindow
        LockConflictWindow.open(self, lock_info)

    def _show_out_of_session(self, changes):
        from strata.ui.out_of_session_window import OutOfSessionWindow
        OutOfSessionWindow.open(self, changes)

    def force_take_session(self):
        """Called by LockConflictWindow when user clicks Force Take."""
        threading.Thread(target=self._force_take_worker, daemon=True).start()

    def _force_take_worker(self):
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = self.engine.force_take_session()
            if result.success:
                self._session_active = True
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def start_session_after_choice(self, *, discard: bool, changes=None):
        """Called by OutOfSessionWindow with the user's choice."""
        def worker():
            self._operation_in_progress = True
            self._marshal(self._refresh_menu)
            try:
                if not discard and changes is not None:
                    # Keep local: persist current hashes as the manifest before
                    # starting, so the engine treats them as the new baseline.
                    from strata.core.manifest import hash_directory
                    current_hashes = hash_directory(Path(self.config["sync_dir"]))
                    self.engine.manifest.save(current_hashes, self.config["device_id"])
                result = self.engine.start_session(discard_local_changes=discard)
                if result.success:
                    self._session_active = True
            finally:
                self._operation_in_progress = False
                self._marshal(self._refresh_menu)
        threading.Thread(target=worker, daemon=True).start()

    # ── Settings save callback ─────────────────────────────────────────────

    def on_settings_saved(self, new_config: dict):
        """Called by SettingsWindow when the user saves."""
        old_check = self.config.get("check_for_updates", True)
        self.config = new_config
        self._init_engine()
        self._sync_autostart_state()

        new_check = new_config.get("check_for_updates", True)
        if new_check and not old_check:
            self._start_update_checker()
        elif not new_check and old_check:
            self._stop_update_checker()

        self._refresh_menu()

    # ── Update flow ────────────────────────────────────────────────────────

    def _on_update_available(self, info: UpdateInfo):
        """Called from the updater's background thread."""
        def apply():
            self._pending_update = info
            self._refresh_menu()
        self._marshal(apply)

    # ── Quit flow ──────────────────────────────────────────────────────────

    def _confirm_quit(self):
        from strata.ui.confirm_quit_window import ConfirmQuitWindow
        ConfirmQuitWindow.open(self)

    def _do_quit(self):
        self._stop_update_checker()
        # Toga's exit method tears down the event loop and the status icon.
        self.exit()


def main():
    """Entry point for `python -m strata` and Briefcase."""
    return StrataApp(
        formal_name="Strata",
        app_id="org.markwu.strata",
        app_name="strata",
        author="Mark Wu",
        version=APP_VERSION,
        home_page="https://github.com/markwu123454/strata",
        description="Sync a directory between laptops via Cloudflare R2",
    )


if __name__ == "__main__":
    main().main_loop()
