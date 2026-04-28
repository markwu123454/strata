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

- Multi-profile model: there is one SyncEngine per configured profile,
  kept in self.engines. All tray actions (Start, End, Quick Pull, Open
  Folder) act on the *active* profile. Sessions held in non-active
  profiles continue to live; switching the active profile shows their
  state. _active_sessions tracks which profiles currently hold a lock
  so we can warn on quit.

- Profile menu items are pre-allocated up to MAX_PROFILES_IN_MENU and
  toggled via enabled/text rather than dynamically inserted, because
  Toga doesn't reliably support reordering menu items after the icon
  is visible. If a user has more than MAX_PROFILES_IN_MENU profiles
  they need to use Settings to switch — the rest are not shown.
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


# ── Status presentation ────────────────────────────────────────────────────────
# Centralized so tray tooltip and status window agree on labels.

STATUS_TEXT = {
    SyncStatus.IDLE: "Idle",
    SyncStatus.STARTING: "Starting session",
    SyncStatus.SYNCING: "Downloading",
    SyncStatus.ENDING: "Uploading",
    SyncStatus.ERROR: "Error",
}

# Hard cap on profile entries shown in the tray submenu. Anything beyond
# this is reachable through Settings only. Keeps the menu sane and lets us
# pre-allocate command slots (Toga doesn't like dynamic menu rebuilds).
MAX_PROFILES_IN_MENU = 8


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

        # One engine per profile. Keyed by profile name. Built lazily by
        # _rebuild_engines whenever config changes — that way edits to one
        # profile's credentials don't tear down sessions held by others.
        self.engines: dict[str, SyncEngine] = {}

        # Per-profile session state. A profile is in this set iff this
        # device currently holds the R2 lock for it.
        self._active_sessions: set[str] = set()

        # Status/progress reporting is per-engine but we only display the
        # *active* profile's status in the tray. Off-active engines still
        # update these fields when they fire callbacks — it's fine, the
        # active engine's next callback will overwrite, and the user opening
        # the status window for that profile will see live state.
        self._current_status = SyncStatus.IDLE
        self._status_message = "Not configured"
        # Which profile most recently fired a status callback. Lets us label
        # the tray tooltip with profile context when it's not the active one.
        self._status_source: str | None = None

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

        self._rebuild_engines()
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

    # ── Engine wiring ──────────────────────────────────────────────────────

    def _rebuild_engines(self):
        """Reconcile self.engines with the current config.profiles list.

        Called on startup and after settings save. Existing engines for
        unchanged profiles are kept (preserving their lock_manager._token
        and any in-flight operations). Profiles that disappeared have their
        engines dropped — note this does NOT release any lock the dropped
        engine held; deleting a profile while its session is active is a
        user mistake we don't try to recover from automatically.
        """
        wanted = {p.get("name", ""): p for p in self.config.get("profiles", [])}
        wanted.pop("", None)  # nameless profiles ignored

        # Drop engines for profiles that no longer exist.
        for stale in list(self.engines):
            if stale not in wanted:
                self.engines.pop(stale, None)
                self._active_sessions.discard(stale)

        for name, profile in wanted.items():
            if not is_profile_configured(profile):
                # Skip half-configured profiles entirely — building an R2Client
                # with empty credentials would just fail noisily on first use.
                # Drop any existing engine in case the user blanked it out.
                self.engines.pop(name, None)
                continue

            existing = self.engines.get(name)
            if existing is not None:
                # If the user edited credentials/sync_dir for an *existing*
                # engine, rebuild it — easier than mutating the engine
                # in place. Compare on the fields the engine actually uses.
                if (
                    str(existing.sync_dir) == profile["sync_dir"]
                    and existing.r2.bucket == profile["r2_bucket"]
                    and existing.r2.access_key == profile["r2_access_key"]
                    and existing.r2.secret_key == profile["r2_secret_key"]
                    and existing.r2.account_id == profile["r2_account_id"]
                ):
                    continue  # unchanged

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
                # Closures capture the profile name so callbacks can route
                # back to the right profile. Without this, off-active engines
                # would silently overwrite the active engine's status display.
                on_status_change=(
                    lambda s, m, _name=name: self._on_status_change(_name, s, m)
                ),
                on_progress=(
                    lambda c, t, f, _name=name: self._on_progress(_name, c, t, f)
                ),
            )
            self.engines[name] = engine
            threading.Thread(
                target=self._recover_session, args=(name,), daemon=True
            ).start()

        # If the active profile was deleted or renamed, reset to the first
        # available one — keeps the tray pointing at something real.
        active = self.config.get("active_profile", "")
        if active not in wanted and wanted:
            self.config["active_profile"] = next(iter(wanted))
            save_config(self.config)
        elif not wanted:
            self.config["active_profile"] = ""
            save_config(self.config)

        # Update the displayed status to match whatever the (possibly new)
        # active engine looks like.
        self._refresh_active_status_display()

    def _active_engine(self) -> SyncEngine | None:
        return self.engines.get(self.config.get("active_profile", ""))

    def _active_profile_name(self) -> str:
        return self.config.get("active_profile", "")

    def _refresh_active_status_display(self):
        """Snap the tray's display fields to the active engine's state.

        Called after switching profiles or rebuilding engines so the tooltip
        and status window don't show stale info from the previously-active
        profile.
        """
        engine = self._active_engine()
        active = self._active_profile_name()
        if engine is None:
            self._current_status = SyncStatus.IDLE
            if not self.config.get("profiles"):
                self._status_message = "No profiles configured"
            elif not active:
                self._status_message = "No active profile"
            else:
                self._status_message = f"{active} — not configured"
        else:
            self._current_status = engine.status
            if active in self._active_sessions:
                self._status_message = f"{active} — session active"
            else:
                self._status_message = f"{active} — ready"
        self._status_source = active or None

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

        # ── Top-level commands ──
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
        # New: Quick Pull. Always between Session and Open Folder.
        self._cmd_quick_pull = toga.Command(
            self._on_quick_pull,
            text="Quick Pull",
            group=self._status_icon,
            id="strata.quick_pull",
            order=15,
        )
        self._cmd_open_folder = toga.Command(
            self._on_open_folder,
            text="Open Sync Folder",
            group=self._status_icon,
            id="strata.folder",
            order=20,
        )

        # ── Profile picker submenu ──
        # A toga.Group is the way to render a submenu under the status icon.
        # We pre-allocate MAX_PROFILES_IN_MENU command slots; each slot's
        # text and enabled flag flips based on which profiles exist. This
        # avoids the Toga issue where dynamically adding/removing menu
        # items after first display sometimes leaves orphan items behind.
        self._profile_group = toga.Group(
            "Profile",
            parent=self._status_icon,
            order=25,
        )
        self._profile_cmds: list[toga.Command] = []
        for i in range(MAX_PROFILES_IN_MENU):
            cmd = toga.Command(
                # Default no-op handler; replaced per-slot in _refresh_menu
                # via the closure trick below.
                self._make_profile_handler(i),
                text=f"Profile {i}",
                group=self._profile_group,
                id=f"strata.profile.{i}",
                order=i,
                enabled=False,
            )
            self._profile_cmds.append(cmd)

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
            self._cmd_quick_pull,
            self._cmd_open_folder,
            *self._profile_cmds,
            self._cmd_settings,
            self._cmd_update,
            self._cmd_quit,
        )

    def _make_profile_handler(self, slot_index: int):
        """Build a handler that switches to the profile in slot N.

        We can't bind by profile name at build time because the slot→name
        mapping changes when the user adds/removes profiles. Look up the
        name fresh on click.
        """
        def handler(command, **kwargs):
            # The command's *text* at click time is the profile name we
            # baked in during the last _refresh_menu. Empty/disabled slots
            # do nothing.
            cmd = self._profile_cmds[slot_index]
            if not cmd.enabled:
                return
            name = cmd.text.lstrip("• ").strip()  # strip the active-marker prefix
            self._switch_profile(name)
        return handler

    def _switch_profile(self, name: str):
        if name == self._active_profile_name():
            return
        if name not in self.engines and not get_profile(self.config, name):
            return  # stale click; menu out of sync
        self.config["active_profile"] = name
        save_config(self.config)
        self._refresh_active_status_display()
        self._refresh_menu()

    def _refresh_menu(self):
        """Update enabled/text of menu items based on app state. Safe to call
        from the main thread only."""
        active_engine = self._active_engine()
        active_name = self._active_profile_name()
        active_configured = is_profile_configured(get_active_profile(self.config))
        active_session = active_name in self._active_sessions

        # ── Top-level action commands ──
        # Session command — disabled if active profile isn't configured or
        # an op is in progress. Label flips between Start/End.
        self._cmd_session.enabled = (
            active_configured
            and not self._operation_in_progress
            and active_engine is not None
        )
        self._cmd_session.text = "End Session" if active_session else "Start Session"

        # Quick Pull is disabled while a session is active on the same
        # profile (no point pulling — you're already at remote state) and
        # while any op is in progress.
        self._cmd_quick_pull.enabled = (
            active_configured
            and not self._operation_in_progress
            and not active_session
            and active_engine is not None
        )

        self._cmd_open.enabled = True
        self._cmd_open_folder.enabled = active_configured
        self._cmd_settings.enabled = True

        # ── Profile picker submenu ──
        profiles = self.config.get("profiles", [])
        for i, cmd in enumerate(self._profile_cmds):
            if i >= len(profiles) or i >= MAX_PROFILES_IN_MENU:
                cmd.enabled = False
                cmd.text = ""  # blank slot
                continue
            p = profiles[i]
            name = p.get("name", "")
            # Mark the active profile with a bullet, and append a tiny
            # status hint for any profile holding a session.
            prefix = "• " if name == active_name else "  "
            suffix = ""
            if name in self._active_sessions:
                suffix = " (session)"
            elif not is_profile_configured(p):
                suffix = " (not configured)"
            cmd.text = f"{prefix}{name}{suffix}"
            # Disable picking an already-active profile (it's a no-op anyway,
            # and visually confirms the bullet means "currently selected").
            cmd.enabled = (name != active_name)

        # ── Update slot ──
        if self._pending_update is not None:
            self._cmd_update.text = f"Update to {self._pending_update.version}…"
            self._cmd_update.enabled = True
        else:
            self._cmd_update.text = "Up to date"
            self._cmd_update.enabled = False

        # Tooltip
        try:
            label = STATUS_TEXT.get(self._current_status, "")
            if active_name:
                self._status_icon.text = f"Strata ({active_name}) — {label}"
            else:
                self._status_icon.text = f"Strata — {label}"
        except Exception:
            # Some Toga backends don't support text updates after init;
            # silently ignore rather than crash.
            pass

    # ── Engine + state callbacks ───────────────────────────────────────────

    def _recover_session(self, profile_name: str):
        """If the lock manager for `profile_name` shows *us* holding the
        lock from a prior run, restore it so the user doesn't have to
        re-take it after a crash or reboot."""
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        try:
            lock = engine.lock_manager.get_current_lock()
            if lock and lock.device_id == self.config["device_id"]:
                engine.lock_manager._token = lock.token
                self._active_sessions.add(profile_name)
                if profile_name == self._active_profile_name():
                    self._status_message = (
                        f"{profile_name} — session resumed "
                        f"(started {lock.acquired_at_str()})"
                    )
                self._marshal(self._refresh_menu)
        except Exception:
            pass

    def _on_status_change(self, profile_name: str, status: SyncStatus, message: str):
        """Engine status callback. Routes through profile name so we can
        decide whether to update the visible tray state."""
        self._status_source = profile_name
        # Always update the tray for the *active* profile. For non-active
        # profiles, we still keep the engine's internal status (engine.status
        # is already updated by _set_status before this fires) but don't
        # overwrite the displayed values.
        if profile_name == self._active_profile_name():
            self._current_status = status
            self._status_message = message
            self._marshal(self._refresh_menu)

    def _on_progress(self, profile_name: str, current: int, total: int, filename: str):
        if profile_name != self._active_profile_name():
            return  # not displayed; ignore
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
        active = self._active_profile_name()
        if not is_profile_configured(get_active_profile(self.config)):
            self._on_open_settings(command)
            return
        if active in self._active_sessions:
            threading.Thread(
                target=self._end_session_worker, args=(active,), daemon=True
            ).start()
        else:
            threading.Thread(
                target=self._start_session_worker, args=(active,), daemon=True
            ).start()

    def _on_quick_pull(self, command, **kwargs):
        if self._operation_in_progress:
            return
        active = self._active_profile_name()
        engine = self.engines.get(active)
        if engine is None:
            return
        # Always check for out-of-session changes first. If any exist, route
        # through the confirmation window (which is the same one used by
        # Start Session, repurposed). Otherwise pull immediately.
        threading.Thread(
            target=self._quick_pull_dispatch, args=(active,), daemon=True
        ).start()

    def _on_open_folder(self, command, **kwargs):
        import subprocess
        import sys
        engine = self._active_engine()
        active_profile = get_active_profile(self.config)
        if engine is not None:
            path = str(engine.sync_dir)
        elif active_profile and active_profile.get("sync_dir"):
            path = active_profile["sync_dir"]
        else:
            path = str(Path.home())
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
        if self._active_sessions:
            self._confirm_quit()
        else:
            self._do_quit()

    # ── Session worker threads ─────────────────────────────────────────────

    def _start_session_worker(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = engine.start_session()
            if result.success:
                self._active_sessions.add(profile_name)
            elif result.lock_info:
                self._marshal(
                    self._show_lock_conflict, profile_name, result.lock_info
                )
            elif result.out_of_session_changes:
                self._marshal(
                    self._show_out_of_session,
                    profile_name,
                    result.out_of_session_changes,
                )
            elif result.error:
                self._status_message = f"Error: {result.error}"
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def _end_session_worker(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = engine.end_session()
            if result.success:
                self._active_sessions.discard(profile_name)
            elif result.error:
                self._status_message = f"Error: {result.error}"
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def _quick_pull_dispatch(self, profile_name: str):
        """Background-thread entry for Quick Pull. Decides whether to
        prompt the user (out-of-session changes exist) or pull straight
        away."""
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        try:
            changes = engine.check_out_of_session_changes()
            existing_lock = engine.peek_lock()
        except Exception as e:
            self._status_message = f"Error: {e}"
            self._marshal(self._refresh_menu)
            return

        if changes:
            # Prompt the user. Same window class as Start Session reuses,
            # but we tell it the destination is Quick Pull so the buttons
            # do the right thing.
            self._marshal(
                self._show_quick_pull_prompt, profile_name, changes, existing_lock
            )
            return

        # No local edits to clobber. If the lock is held by someone else,
        # mention it briefly via a status message but proceed.
        if existing_lock and existing_lock.device_id != self.config["device_id"]:
            self._status_message = (
                f"Quick pull (note: {existing_lock.device_name} has a "
                f"session open — pulling their last-uploaded state)"
            )
            self._marshal(self._refresh_menu)
        self._quick_pull_run(profile_name)

    def _quick_pull_run(self, profile_name: str):
        """Actually perform the Quick Pull. Called either directly (no
        out-of-session changes) or via quick_pull_after_choice (user
        confirmed)."""
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = engine.quick_pull()
            if not result.success and result.error:
                self._status_message = f"Error: {result.error}"
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def _show_lock_conflict(self, profile_name: str, lock_info):
        from strata.ui.lock_window import LockConflictWindow
        LockConflictWindow.open(self, profile_name, lock_info)

    def _show_out_of_session(self, profile_name: str, changes):
        from strata.ui.out_of_session_window import OutOfSessionWindow
        OutOfSessionWindow.open(self, profile_name, changes, mode="start_session")

    def _show_quick_pull_prompt(self, profile_name: str, changes, lock_info):
        from strata.ui.out_of_session_window import OutOfSessionWindow
        OutOfSessionWindow.open(
            self, profile_name, changes, mode="quick_pull", lock_info=lock_info
        )

    def force_take_session(self, profile_name: str):
        """Called by LockConflictWindow when user clicks Force Take."""
        threading.Thread(
            target=self._force_take_worker, args=(profile_name,), daemon=True
        ).start()

    def _force_take_worker(self, profile_name: str):
        engine = self.engines.get(profile_name)
        if engine is None:
            return
        self._operation_in_progress = True
        self._marshal(self._refresh_menu)
        try:
            result = engine.force_take_session()
            if result.success:
                self._active_sessions.add(profile_name)
        finally:
            self._operation_in_progress = False
            self._marshal(self._refresh_menu)

    def start_session_after_choice(
        self, profile_name: str, *, discard: bool, changes=None
    ):
        """Called by OutOfSessionWindow (mode=start_session) with the
        user's choice."""
        engine = self.engines.get(profile_name)
        if engine is None:
            return

        def worker():
            self._operation_in_progress = True
            self._marshal(self._refresh_menu)
            try:
                if not discard and changes is not None:
                    # Keep local: persist current hashes as the manifest
                    # before starting, so the engine treats them as the new
                    # baseline.
                    from strata.core.manifest import hash_directory
                    current_hashes = hash_directory(engine.sync_dir)
                    engine.manifest.save(current_hashes, self.config["device_id"])
                result = engine.start_session(discard_local_changes=discard)
                if result.success:
                    self._active_sessions.add(profile_name)
            finally:
                self._operation_in_progress = False
                self._marshal(self._refresh_menu)
        threading.Thread(target=worker, daemon=True).start()

    def quick_pull_after_choice(self, profile_name: str, *, proceed: bool):
        """Called by OutOfSessionWindow (mode=quick_pull). If proceed is
        False, the user cancelled — do nothing. If True, run the pull."""
        if not proceed:
            return
        threading.Thread(
            target=self._quick_pull_run, args=(profile_name,), daemon=True
        ).start()

    # ── Settings save callback ─────────────────────────────────────────────

    def on_settings_saved(self, new_config: dict):
        """Called by SettingsWindow when the user saves."""
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
        """Called from the updater's background thread."""
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
