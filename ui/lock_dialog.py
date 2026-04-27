"""
UI: Lock conflict dialog
Shown when Start Session fails because another device holds the lock.
"""

import customtkinter as ctk
from core.lock import LockInfo


class LockConflictDialog(ctk.CTkToplevel):
    """
    Shown when another device holds the session lock.
    User can wait, or force-take the session.
    """

    def __init__(self, parent, lock_info: LockInfo):
        super().__init__(parent)
        self.lock_info = lock_info
        self.result = None  # "force" | None (cancelled)

        self.title("Session In Use")
        self.geometry("440x280")
        self.resizable(False, False)
        self._center()
        self._build_ui()
        self.grab_set()
        self.focus()

    def _center(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 440) // 2
        y = (sh - 280) // 2
        self.geometry(f"440x280+{x}+{y}")

    def _build_ui(self):
        ctk.set_appearance_mode("dark")

        # Icon + title
        ctk.CTkLabel(
            self,
            text="🔒",
            font=ctk.CTkFont(size=40),
        ).pack(pady=(28, 4))

        ctk.CTkLabel(
            self,
            text="Session Active on Another Device",
            font=ctk.CTkFont(family="Courier New", size=14, weight="bold"),
            text_color="#f0c040",
        ).pack()

        ctk.CTkLabel(
            self,
            text=f"{self.lock_info.device_name}  ·  started {self.lock_info.acquired_at_str()}\n({self.lock_info.age_minutes():.0f} minutes ago)",
            font=ctk.CTkFont(size=12),
            text_color="#aaaacc",
            justify="center",
        ).pack(pady=(8, 0))

        ctk.CTkLabel(
            self,
            text="End the session on that device, or force-take\nif it crashed and the lock is stale.",
            font=ctk.CTkFont(size=11),
            text_color="#777799",
            justify="center",
        ).pack(pady=(8, 0))

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=24)

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=100,
            fg_color="#2a2a40",
            hover_color="#3a3a55",
            command=self._cancel,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_frame,
            text="Force Take Session",
            width=160,
            fg_color="#5a1a1a",
            hover_color="#7a2a2a",
            command=self._force,
        ).pack(side="left", padx=8)

    def _force(self):
        self.result = "force"
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
