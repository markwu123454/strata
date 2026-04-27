"""
Main application entry point.
Tray icon + window orchestration.
"""

import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw

from config import load_config, is_configured, CONFIG_DIR, STATE_DIR
from core.r2 import R2Client
from core.engine import SyncEngine, SyncStatus
from ui.out_of_session_dialog import OutOfSessionDialog
from ui.lock_dialog import LockConflictDialog
from ui.settings_dialog import SettingsDialog
from ui.status_window import StatusWindow


# ── Tray icon image ────────────────────────────────────────────────────────────

def make_tray_icon(color: str = "#8888ff") -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    # Simple cloud-like shape
    d.ellipse([8, 20, 36, 44], fill=(r, g, b, 255))
    d.ellipse([20, 12, 52, 40], fill=(r, g, b, 255))
    d.ellipse([30, 20, 56, 44], fill=(r, g, b, 255))
    d.rectangle([12, 34, 52, 50], fill=(r, g, b, 255))
    return img


TRAY_COLORS = {
    SyncStatus.IDLE: "#8888ff",
    SyncStatus.STARTING: "#f0c040",
    SyncStatus.SYNCING: "#40aaff",
    SyncStatus.ENDING: "#40aaff",
    SyncStatus.ERROR: "#f04060",
}


# ── App ────────────────────────────────────────────────────────────────────────

class DirSyncApp:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        self.config = load_config()
        self.engine: SyncEngine | None = None
        self.tray: pystray.Icon | None = None
        self._status_message = "Not configured"
        self._current_status = SyncStatus.IDLE
        self._session_active = False
        self._status_window = None
        self._operation_in_progress = False  # guard against double-clicks
        self._tray_color = "#8888ff"  # track current icon color to avoid unnecessary redraws

        # Hidden root window (needed for dialogs)
        self.root = ctk.CTk()
        self.root.withdraw()
        self.root.title("DirSync")

        if is_configured(self.config):
            self._init_engine()

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

        # Recover session state on startup — check if this device already holds
        # the lock (e.g. app crashed or was restarted mid-session)
        threading.Thread(target=self._recover_session, daemon=True).start()

    def _recover_session(self):
        """Check R2 on startup to see if we already own the lock."""
        if self.engine is None:
            return
        try:
            lock = self.engine.lock_manager.get_current_lock()
            if lock and lock.device_id == self.config["device_id"]:
                # We own the lock — restore token so is_locked_by_me() works
                self.engine.lock_manager._token = lock.token
                self._session_active = True
                self._status_message = f"Session resumed — started {lock.acquired_at_str()}"
                self._rebuild_tray_menu()
                self._update_tray()
        except Exception:
            pass  # R2 unreachable on startup, stay in default state

    # ── Status callbacks ───────────────────────────────────────────────────────

    def _on_status_change(self, status: SyncStatus, message: str):
        self._current_status = status
        self._status_message = message
        self._update_tray()

    def _on_progress(self, current: int, total: int, filename: str):
        short = Path(filename).name
        self._status_message = f"{current}/{total} — {short}"
        self._update_tray()

    def _update_tray(self):
        if self.tray is None:
            return
        color = TRAY_COLORS.get(self._current_status, "#8888ff")
        # Only rebuild icon image when color actually changes — avoids flicker on Windows
        if color != self._tray_color:
            self._tray_color = color
            self.tray.icon = make_tray_icon(color)
        title = f"DirSync — {self._status_message}"
        self.tray.title = title[:127]  # Windows tray title max is 128 chars

    # ── Session actions (run in background thread) ─────────────────────────────

    def _start_session(self):
        if self._operation_in_progress:
            return
        self._operation_in_progress = True
        try:
            self._start_session_inner()
        finally:
            self._operation_in_progress = False

    def _start_session_inner(self):
        if self.engine is None:
            self._show_not_configured()
            return

        result = self.engine.start_session()

        if result.success:
            self._session_active = True
            self._rebuild_tray_menu()
            return

        if result.lock_info:
            self.root.after(0, lambda: self._show_lock_dialog(result.lock_info))
            return

        if result.out_of_session_changes:
            self.root.after(0, lambda: self._show_out_of_session_dialog(result.out_of_session_changes))
            return

        if result.error:
            self._status_message = f"Error: {result.error}"
            self._update_tray()

    def _end_session(self):
        if self._operation_in_progress:
            return
        self._operation_in_progress = True
        try:
            self._end_session_inner()
        finally:
            self._operation_in_progress = False

    def _end_session_inner(self):
        if self.engine is None:
            return
        result = self.engine.end_session()
        if result.success:
            self._session_active = False
            self._rebuild_tray_menu()
        else:
            self._status_message = f"Error: {result.error}"
            self._update_tray()

    # ── Dialog handlers (must run on main thread) ──────────────────────────────

    def _show_lock_dialog(self, lock_info):
        dlg = LockConflictDialog(self.root, lock_info)
        self.root.wait_window(dlg)
        if dlg.result == "force":
            threading.Thread(target=self._force_take_session, daemon=True).start()

    def _force_take_session(self):
        result = self.engine.force_take_session()
        if result.success:
            self._session_active = True
            self._rebuild_tray_menu()

    def _show_out_of_session_dialog(self, changes):
        dlg = OutOfSessionDialog(self.root, changes)
        self.root.wait_window(dlg)

        if dlg.result == "discard":
            threading.Thread(
                target=lambda: self._start_session_after_choice(discard=True),
                daemon=True
            ).start()
        elif dlg.result == "keep":
            # Upload local changes first, then start session
            threading.Thread(
                target=lambda: self._start_session_keep_local(changes),
                daemon=True
            ).start()
        # else: cancelled, do nothing

    def _start_session_after_choice(self, discard: bool):
        result = self.engine.start_session(discard_local_changes=discard)
        if result.success:
            self._session_active = True
            self._rebuild_tray_menu()

    def _start_session_keep_local(self, changes):
        # Save current local state as if we're ending a session, then start
        # This uploads local changes before pulling from R2
        from core.manifest import hash_directory
        current_hashes = hash_directory(Path(self.config["sync_dir"]))
        self.engine.manifest.save(current_hashes, self.config["device_id"])
        result = self.engine.start_session(discard_local_changes=False)
        if result.success:
            self._session_active = True
            self._rebuild_tray_menu()

    def _show_not_configured(self):
        self.root.after(0, self._open_settings)

    # ── Settings ───────────────────────────────────────────────────────────────

    def _open_status_window(self):
        """Open or focus the status window."""
        if self._status_window is not None:
            try:
                if self._status_window._alive and self._status_window.winfo_exists():
                    self._status_window.focus()
                    return
            except Exception:
                pass
            self._status_window = None

        win = StatusWindow(self.root, self)
        self._status_window = win
        # Clear our reference when window closes so next click opens a fresh one
        orig_close = win._on_close
        def _on_close_and_clear():
            self._status_window = None
            orig_close()
        win._on_close = _on_close_and_clear
        win.protocol("WM_DELETE_WINDOW", _on_close_and_clear)

    def _open_settings(self):
        def on_save(new_config):
            self.config = new_config
            self._init_engine()
            self._rebuild_tray_menu()

        dlg = SettingsDialog(self.root, self.config, on_save=on_save)
        self.root.wait_window(dlg)

    # ── Tray icon ──────────────────────────────────────────────────────────────

    def _rebuild_tray_menu(self):
        if self.tray is None:
            return
        self.tray.menu = self._build_menu()

    def _build_menu(self):
        if self._session_active:
            session_item = pystray.MenuItem(
                "End Session",
                lambda: threading.Thread(target=self._end_session, daemon=True).start(),
            )
        else:
            session_item = pystray.MenuItem(
                "Start Session",
                lambda: threading.Thread(target=self._start_session, daemon=True).start(),
            )

        # default=True makes this the left-click action on Windows.
        # visible=False hides it from the right-click menu.
        open_status = pystray.MenuItem(
            "Open",
            lambda: self.root.after(0, self._open_status_window),
            default=True,
            visible=False,
        )

        return pystray.Menu(
            open_status,
            pystray.MenuItem(
                f"DirSync — {self.config.get('device_name', 'Not configured')}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            session_item,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open Status",
                lambda: self.root.after(0, self._open_status_window),
            ),
            pystray.MenuItem(
                "Open Sync Folder",
                self._open_sync_folder,
            ),
            pystray.MenuItem(
                "Settings",
                lambda: self.root.after(0, self._open_settings),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _open_sync_folder(self):
        import subprocess, sys
        # Prefer engine's sync_dir (authoritative) over config string (can be stale)
        if self.engine is not None:
            path = str(self.engine.sync_dir)
        else:
            path = self.config.get("sync_dir") or str(Path.home())

        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _quit(self):
        if self._session_active:
            # Warn user — don't auto end session, they might have unsaved files
            self.root.after(0, self._confirm_quit)
        else:
            self._do_quit()

    def _confirm_quit(self):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Quit DirSync")
        dlg.geometry("360x160")
        dlg.grab_set()

        ctk.CTkLabel(
            dlg,
            text="⚠  Session still active",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f0c040",
        ).pack(pady=(20, 4))

        ctk.CTkLabel(
            dlg,
            text="End your session before quitting\nto avoid leaving the lock open.",
            font=ctk.CTkFont(size=12),
            text_color="#aaaacc",
        ).pack(pady=(0, 16))

        btn = ctk.CTkFrame(dlg, fg_color="transparent")
        btn.pack()

        ctk.CTkButton(btn, text="Cancel", width=90,
                      fg_color="#2a2a40", hover_color="#3a3a55",
                      command=dlg.destroy).pack(side="left", padx=8)

        ctk.CTkButton(btn, text="Quit Anyway", width=110,
                      fg_color="#5a1a1a", hover_color="#7a2a2a",
                      command=self._do_quit).pack(side="left", padx=8)

    def _do_quit(self):
        if self.tray:
            self.tray.stop()
        self.root.quit()

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(self):
        icon_img = make_tray_icon()
        self.tray = pystray.Icon(
            "dirsync",
            icon_img,
            f"DirSync — {self._status_message}",
            menu=self._build_menu(),
        )
        # On Windows, left-click is handled by marking a menu item as default
        # We rebuild the menu with a hidden default item that opens the status window
        # This is the correct cross-platform approach for pystray

        # Run tray in background thread, tkinter on main thread
        tray_thread = threading.Thread(target=self.tray.run, daemon=True)
        tray_thread.start()

        self.root.mainloop()


if __name__ == "__main__":
    app = DirSyncApp()
    app.run()
