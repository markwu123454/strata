"""
Strata main app — Toga BackgroundApp with a system tray status icon.

Architectural notes:

- We use `main_window = toga.App.BACKGROUND` so the app runs without any
  visible window by default, just the tray icon. Windows (Settings, Status,
  dialogs) are created on demand.

- The sync engine runs in background threads (`engine.start_session` blocks
  on network IO). Toga's event loop is asyncio-based, so we use
  `app.loop.call_soon_threadsafe` to marshal UI updates back to the main
  thread.

- The tray menu is intentionally minimal: Open Status, Settings, update
  slot, Quit. All profile-specific actions live in the Status window.
  Left-clicking the tray icon directly also opens the Status window via
  on_press, since on Windows the icon only shows its menu on right-click.

- Multi-profile model: one SyncEngine per configured profile in self.engines.
  _active_sessions tracks which profiles currently hold a lock.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import toga

from strata.config import (
    APP_VERSION,
    is_configured,
    is_profile_configured,
    get_active_profile,
    get_profile,
    load_config,
    profile_state_dir,
    save_config,
    STATE_DIR,
)
from strata.core import autostart
from strata.core.engine import SyncEngine, SyncStatus
from strata.core.r2 import R2Client
from strata.core.updater import UpdateChecker, UpdateInfo


class StrataApp(toga.App):
    """Background app with a tray status icon."""

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def startup(self):
        self.main_window = toga.App.BACKGROUND

        self.config = load_config()
        self.engines: dict[str, SyncEngine] = {}
        self._active_sessions: set[str] = set()

        # Per-profile status/message. Keyed by profile name.
        self._profile_status: dict[str, SyncStatus] = {}
        self._profile_message: dict[str, str] = {}

        # Profile names with an operation currently running.
        self._operation_in_progress: set[str] = set()
        self._pending_update: UpdateInfo | None = None

        autostart._cleanup_legacy()
        self._sync_autostart_state()
        self._build_status_icon()
        self._rebuild_engines()
        self._refresh_menu()

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
            autostart.enable()

    # ── Engine wiring ──────────────────────────────────────────────────────

    def _rebuild_engines(self):
        """Reconcile self.engines with the current config.profiles list."""
        wanted = {p.get("name", ""): p for p in self.config.get("profiles", [])}
        wanted.pop("", None)

        for stale in list(self.engines):
            if stale not in wanted:
                self.engines.pop(stale, None)
                self._active_sessions.discard(stale)
                self._profile_status.pop(stale, None)
                self._profile_message.pop(stale, None)
                self._operation_in_progress.discard(stale)

        for name, profile in wanted.items():
            if not is_profile_configured(profile):
                self.engines.pop(name, None)
                continue

            existing = self.engines.get(name)
            if existing is not None:
                if (
                    str(existing.sync_dir) == profile["sync_dir"]
                    and existing.r2.bucket == profile["r2_bucket"]
                    and existing.r2.access_key == profile["r2_access_key"]
                    and existing.r2.secret_key == profile["r2_secret_key"]
                    and existing.r2.account_id == profile["r2_account_id"]
                ):
                    continue

            r2 = R2Client(
                account_id=profile["r2_account_id"],
                access_key=profile["r2_access_key"],
                secret_key=profile["r2_secret_key"],
                bucket=profile["r2_bucket"],
            )
            sync_dir = Path(profile["sync_dir"])
            sync_dir.mkdir(parents=True, exist_ok=True)
            engine = SyncEngine(
                sync_dir=sync_dir,
                state_dir=profile_state_dir(name),
                r2=r2,
                device_id=self.config["device_id"],
                device_name=self.config["device_name"],
                on_status_change=(
                    lambda s, m, _name=name: self._on_status_change(_name, s, m)
                ),
                on_progress=(
                    lambda c, t, f, _name=name: self._on_progress(_name, c, t, f)
                ),
            )
            self.engines[name] = engine
            self._profile_status[name] = SyncStatus.IDLE
            self._profile_message[name] = "Ready"
            threading.Thread(
                target=self._recover_session, args=(name,), daemon=True
            ).start()

    # ── Status icon + menu ─────────────────────────────────────────────────

    def _build_status_icon(self):
        self._status_icon = toga.MenuStatusIcon(
            icon=toga.Icon("resources/strata"),
            text="Strata",
        )
        self.status_icons.add(self._status_icon)
        self.status_icons.commands.clear()

        self._cmd_open = toga.Command(
            self._on_open_status,
            text="Open Status",
            group=self._status_icon,
            id="strata.open",
            order=0,
        )
        self._cmd_settings = toga.Command(
            self._on_open_settings,
            text="Settings…",
            group=self._status_icon,
            id="strata.settings",
            order=10,
        )
        self._cmd_update = toga.Command(
            self._on_update_clicked,
            text="Up to date",
            group=self._status_icon,
            id="strata.update",
            order=20,
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
            self._cmd_settings,
            self._cmd_update,
            self._cmd_quit,
        )

        self._wire_native_click()

    def _wire_native_click(self):
        import sys
        if sys.platform != "win32":
            return
        try:
            native = self._status_icon._impl.native  # System.Windows.Forms.NotifyIcon
            native.Click += self._on_native_click
        except Exception:
            pass  # Non-fatal — menu still works

    def _on_native_click(self, sender, event_args):
        # This fires on the .NET thread — marshal back to Toga's asyncio loop.
        self._marshal(self._on_open_status, None)

    def _refresh_menu(self):
        """Update tray tooltip. Safe to call from the main thread only."""
        active_count = len(self._active_sessions)
        busy_count = len(self._operation_in_progress)
        if busy_count:
            tip = f"Strata — syncing ({busy_count} profile{'s' if busy_count > 1 else ''})"
        elif active_count:
            tip = f"Strata — {active_count} session{'s' if active_count > 1 else ''} active"
        else:
            tip = "Strata — idle"
        try:
            self._status_icon.text = tip
        except Exception:
            pass

        if self._pending_update is not None:
            self._cmd_update.text = f"Update to {self._pending_update.version}…"
            self._cmd_update.enabled = True
        else:
            self._cmd_update.text = "Up to date"
            self._cmd_update.enabled = False

    # ── Engine + state callbacks ───────────────────────────────────────────

    def _recover_session(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        try:
            lock = engine.lock_manager.get_current_lock()
            if lock and lock.device_id == self.config["device_id"]:
                engine.lock_manager._token = lock.token
                self._active_sessions.add(profile_name)
                self._profile_message[profile_name] = (
                    f"Session resumed (started {lock.acquired_at_str()})"
                )
                self._marshal(self._refresh_menu)
        except Exception:
            pass

    def _on_status_change(self, profile_name: str, status: SyncStatus, message: str):
        self._profile_status[profile_name] = status
        self._profile_message[profile_name] = message
        self._marshal(self._refresh_menu)

    def _on_progress(self, profile_name: str, current: int, total: int, filename: str):
        short = Path(filename).name
        self._profile_message[profile_name] = f"{current}/{total} — {short}"
        self._marshal(self._refresh_menu)

    # ── Marshalling ────────────────────────────────────────────────────────

    def _marshal(self, fn, *args):
        try:
            self.loop.call_soon_threadsafe(lambda: fn(*args))
        except RuntimeError:
            pass

    # ── Tray command handlers ──────────────────────────────────────────────

    def _on_open_status(self, command, **kwargs):
        from strata.ui.status_window import StatusWindow
        StatusWindow.open_or_focus(self)

    def _on_open_settings(self, command, **kwargs):
        from strata.ui.settings_window import SettingsWindow
        SettingsWindow.open_or_focus(self)

    def _on_update_clicked(self, command, **kwargs):
        if self._pending_update is None:
            return
        from strata.ui.update_window import UpdateWindow
        UpdateWindow.open(self, self._pending_update)

    def _on_quit(self, command, **kwargs):
        if self._active_sessions:
            self._confirm_quit()
        else:
            self._do_quit()

    # ── Session/pull actions (called by StatusWindow) ──────────────────────

    def toggle_session(self, profile_name: str):
        if profile_name in self._operation_in_progress:
            return
        if profile_name in self._active_sessions:
            threading.Thread(
                target=self._end_session_worker, args=(profile_name,), daemon=True
            ).start()
        else:
            threading.Thread(
                target=self._start_session_worker, args=(profile_name,), daemon=True
            ).start()

    def quick_pull(self, profile_name: str):
        if profile_name in self._operation_in_progress:
            return
        threading.Thread(
            target=self._quick_pull_dispatch, args=(profile_name,), daemon=True
        ).start()

    def open_folder(self, profile_name: str):
        import subprocess, sys
        engine = self.engines.get(profile_name)
        profile = get_profile(self.config, profile_name)
        if engine is not None:
            path = str(engine.sync_dir)
        elif profile and profile.get("sync_dir"):
            path = profile["sync_dir"]
        else:
            path = str(Path.home())
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def check_for_updates(self):
        """Manually trigger an update check. Called from Settings window."""
        def _check():
            if self._update_checker is not None:
                self._update_checker.check_now()
        threading.Thread(target=_check, daemon=True).start()

    # ── Session worker threads ─────────────────────────────────────────────

    def _start_session_worker(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress.add(profile_name)
        self._marshal(self._refresh_menu)
        try:
            result = engine.start_session()
            if result.success:
                self._active_sessions.add(profile_name)
            elif result.lock_info:
                self._marshal(self._show_lock_conflict, profile_name, result.lock_info)
            elif result.out_of_session_changes:
                self._marshal(self._show_out_of_session, profile_name, result.out_of_session_changes)
            elif result.error:
                self._profile_message[profile_name] = f"Error: {result.error}"
        finally:
            self._operation_in_progress.discard(profile_name)
            self._marshal(self._refresh_menu)

    def _end_session_worker(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress.add(profile_name)
        self._marshal(self._refresh_menu)
        try:
            result = engine.end_session()
            if result.success:
                self._active_sessions.discard(profile_name)
            elif result.error:
                self._profile_message[profile_name] = f"Error: {result.error}"
        finally:
            self._operation_in_progress.discard(profile_name)
            self._marshal(self._refresh_menu)

    def _quick_pull_dispatch(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        try:
            changes = engine.check_out_of_session_changes()
            existing_lock = engine.peek_lock()
        except Exception as e:
            self._profile_message[profile_name] = f"Error: {e}"
            self._marshal(self._refresh_menu)
            return

        if changes:
            self._marshal(self._show_quick_pull_prompt, profile_name, changes, existing_lock)
            return

        if existing_lock and existing_lock.device_id != self.config["device_id"]:
            self._profile_message[profile_name] = (
                f"Quick pull (note: {existing_lock.device_name} has a session open)"
            )
            self._marshal(self._refresh_menu)
        self._quick_pull_run(profile_name)

    def _quick_pull_run(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress.add(profile_name)
        self._marshal(self._refresh_menu)
        try:
            result = engine.quick_pull()
            if not result.success and result.error:
                self._profile_message[profile_name] = f"Error: {result.error}"
        finally:
            self._operation_in_progress.discard(profile_name)
            self._marshal(self._refresh_menu)

    def _show_lock_conflict(self, profile_name: str, lock_info):
        from strata.ui.lock_window import LockConflictWindow
        LockConflictWindow.open(self, profile_name, lock_info)

    def _show_out_of_session(self, profile_name: str, changes):
        from strata.ui.out_of_session_window import OutOfSessionWindow
        OutOfSessionWindow.open(self, profile_name, changes, mode="start_session")

    def _show_quick_pull_prompt(self, profile_name: str, changes, lock_info):
        from strata.ui.out_of_session_window import OutOfSessionWindow
        OutOfSessionWindow.open(self, profile_name, changes, mode="quick_pull", lock_info=lock_info)

    def force_take_session(self, profile_name: str):
        threading.Thread(
            target=self._force_take_worker, args=(profile_name,), daemon=True
        ).start()

    def _force_take_worker(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress.add(profile_name)
        self._marshal(self._refresh_menu)
        try:
            result = engine.force_take_session()
            if result.success:
                self._active_sessions.add(profile_name)
        finally:
            self._operation_in_progress.discard(profile_name)
            self._marshal(self._refresh_menu)

    def start_session_after_choice(self, profile_name: str, *, discard: bool, changes=None):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        def worker():
            self._operation_in_progress.add(profile_name)
            self._marshal(self._refresh_menu)
            try:
                if not discard and changes is not None:
                    from strata.core.manifest import hash_directory
                    current_hashes = hash_directory(engine.sync_dir)
                    engine.manifest.save(current_hashes, self.config["device_id"])
                result = engine.start_session(discard_local_changes=discard)
                if result.success:
                    self._active_sessions.add(profile_name)
            finally:
                self._operation_in_progress.discard(profile_name)
                self._marshal(self._refresh_menu)
        threading.Thread(target=worker, daemon=True).start()

    def quick_pull_after_choice(self, profile_name: str, *, proceed: bool):
        if not proceed:
            return
        threading.Thread(
            target=self._quick_pull_run, args=(profile_name,), daemon=True
        ).start()

    # ── Settings save callback ─────────────────────────────────────────────

    def on_settings_saved(self, new_config: dict):
        old_check = self.config.get("check_for_updates", True)
        self.config = new_config
        self._rebuild_engines()
        self._sync_autostart_state()
        new_check = new_config.get("check_for_updates", True)
        if new_check and not old_check:
            self._start_update_checker()
        elif not new_check and old_check:
            self._stop_update_checker()
        self._refresh_menu()

    # ── Update flow ────────────────────────────────────────────────────────

    def _on_update_available(self, info: UpdateInfo):
        def apply():
            self._pending_update = info
            self._refresh_menu()
        self._marshal(apply)

    # ── Quit flow ──────────────────────────────────────────────────────────

    def _confirm_quit(self):
        from strata.ui.confirm_quit_window import ConfirmQuitWindow
        ConfirmQuitWindow.open(self, sorted(self._active_sessions))

    def _do_quit(self):
        self._stop_update_checker()
        self.exit()


def main():
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