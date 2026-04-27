"""
UI: Status window
Shown when the user clicks the tray icon.
Displays current session state, last sync time, and live progress.
"""

import time
import customtkinter as ctk
from core.engine import SyncStatus


class StatusWindow(ctk.CTkToplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._alive = True
        self._refresh_job = None

        self.title("DirSync")
        self.geometry("380x260")
        self.resizable(False, False)
        self._position_near_tray()
        self._build_ui()
        self._refresh()

        # Auto-refresh every second while open
        self._refresh_job = self.after(1000, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.focus()
        self.lift()

    def _position_near_tray(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # Bottom-right corner near system tray
        x = sw - 400
        y = sh - 320
        self.geometry(f"380x260+{x}+{y}")

    def _build_ui(self):
        ctk.set_appearance_mode("dark")
        self.configure(fg_color="#0f0f1e")

        # Header bar
        header = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0, height=44)
        header.pack(fill="x")
        header.pack_propagate(False)

        self.title_label = ctk.CTkLabel(
            header,
            text="DirSync",
            font=ctk.CTkFont(family="Courier New", size=13, weight="bold"),
            text_color="#8888ff",
        )
        self.title_label.pack(side="left", padx=16, pady=12)

        self.device_label = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#555577",
        )
        self.device_label.pack(side="right", padx=16, pady=12)

        # Status area
        status_frame = ctk.CTkFrame(self, fg_color="transparent")
        status_frame.pack(fill="both", expand=True, padx=20, pady=16)

        # Big status indicator
        self.status_dot = ctk.CTkLabel(
            status_frame,
            text="●",
            font=ctk.CTkFont(size=22),
            text_color="#8888ff",
        )
        self.status_dot.pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            status_frame,
            text="",
            font=ctk.CTkFont(family="Courier New", size=13, weight="bold"),
            text_color="#ddddee",
            anchor="w",
        )
        self.status_label.pack(fill="x", pady=(2, 0))

        self.sub_label = ctk.CTkLabel(
            status_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#777799",
            anchor="w",
            wraplength=340,
            justify="left",
        )
        self.sub_label.pack(fill="x", pady=(2, 0))

        # Progress bar (hidden when not syncing)
        self.progress_bar = ctk.CTkProgressBar(
            status_frame,
            fg_color="#1c1c30",
            progress_color="#4444aa",
        )
        self.progress_bar.set(0)

        # Sync dir path
        self.path_label = ctk.CTkLabel(
            status_frame,
            text="",
            font=ctk.CTkFont(family="Courier New", size=10),
            text_color="#444466",
            anchor="w",
        )
        self.path_label.pack(fill="x", side="bottom", pady=(0, 2))

        # Action button
        btn_frame = ctk.CTkFrame(self, fg_color="#0a0a18", corner_radius=0, height=52)
        btn_frame.pack(fill="x", side="bottom")
        btn_frame.pack_propagate(False)

        self.action_btn = ctk.CTkButton(
            btn_frame,
            text="",
            width=160,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2a3a6a",
            hover_color="#3a4a8a",
            command=self._action,
        )
        self.action_btn.pack(side="left", padx=16, pady=10)

        self.open_btn = ctk.CTkButton(
            btn_frame,
            text="Open Folder",
            width=100,
            height=32,
            font=ctk.CTkFont(size=11),
            fg_color="#1c1c30",
            hover_color="#2c2c45",
            command=self.app._open_sync_folder,
        )
        self.open_btn.pack(side="left", padx=(0, 8), pady=10)

    def _refresh(self):
        """Update all labels from current app state."""
        if not self._alive:
            return
        try:
            self.winfo_exists()  # raises if window is gone
        except Exception:
            return
        status = self.app._current_status
        message = self.app._status_message
        session_active = self.app._session_active

        # Device name
        self.device_label.configure(text=self.app.config.get("device_name", ""))

        # Sync dir
        if self.app.engine:
            self.path_label.configure(text=str(self.app.engine.sync_dir))

        # Status dot color
        dot_colors = {
            SyncStatus.IDLE: "#4444aa" if not session_active else "#44aa44",
            SyncStatus.STARTING: "#f0c040",
            SyncStatus.SYNCING: "#40aaff",
            SyncStatus.ENDING: "#40aaff",
            SyncStatus.ERROR: "#f04060",
        }
        self.status_dot.configure(text_color=dot_colors.get(status, "#8888ff"))

        # Status text
        if status == SyncStatus.IDLE:
            if session_active:
                self.status_label.configure(text="Session Active", text_color="#44cc44")
                self.sub_label.configure(text="Files are checked out to this device.\nEnd session to upload changes.")
            else:
                self.status_label.configure(text="No Active Session", text_color="#ddddee")
                self.sub_label.configure(text="Start a session to check out files.")
        elif status == SyncStatus.STARTING:
            self.status_label.configure(text="Starting Session...", text_color="#f0c040")
            self.sub_label.configure(text=message)
        elif status == SyncStatus.SYNCING:
            self.status_label.configure(text="Downloading...", text_color="#40aaff")
            self.sub_label.configure(text=message)
        elif status == SyncStatus.ENDING:
            self.status_label.configure(text="Uploading...", text_color="#40aaff")
            self.sub_label.configure(text=message)
        elif status == SyncStatus.ERROR:
            self.status_label.configure(text="Error", text_color="#f04060")
            self.sub_label.configure(text=message)

        # Progress bar
        is_syncing = status in (SyncStatus.SYNCING, SyncStatus.ENDING, SyncStatus.STARTING)
        if is_syncing:
            self.progress_bar.pack(fill="x", pady=(8, 0))
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
            self.progress_bar.pack_forget()

        # Action button
        if is_syncing:
            self.action_btn.configure(text="Working...", state="disabled", fg_color="#1c1c30")
        elif session_active:
            self.action_btn.configure(
                text="End Session  ↑",
                state="normal",
                fg_color="#1a3a1a",
                hover_color="#2a5a2a",
            )
        else:
            self.action_btn.configure(
                text="Start Session  ↓",
                state="normal",
                fg_color="#2a3a6a",
                hover_color="#3a4a8a",
            )

    def _tick(self):
        """Periodic refresh while window is open."""
        if not self._alive:
            return
        self._refresh()
        if self._alive:
            self._refresh_job = self.after(1000, self._tick)

    def _action(self):
        import threading
        if self.app._session_active:
            threading.Thread(target=self.app._end_session, daemon=True).start()
        else:
            threading.Thread(target=self.app._start_session, daemon=True).start()

    def _on_close(self):
        if not self._alive:
            return
        self._alive = False
        if self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = None
        try:
            self.withdraw()  # hide immediately before destroy to avoid flicker
            self.after(50, self._safe_destroy)  # let customtkinter finish its own callbacks
        except Exception:
            pass

    def _safe_destroy(self):
        try:
            self.destroy()
        except Exception:
            pass
