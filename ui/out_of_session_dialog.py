"""
UI: Out-of-session changes dialog
Shows when local files were modified outside a session.
User can choose to keep or discard changes per file.
"""

import customtkinter as ctk
from core.engine import OutOfSessionChange


class OutOfSessionDialog(ctk.CTkToplevel):
    """
    Shown when Start Session detects local files changed outside a session.
    User decides: keep local changes (upload them) or discard (pull from R2).
    """

    def __init__(self, parent, changes: list[OutOfSessionChange]):
        super().__init__(parent)
        self.changes = changes
        self.result = None  # "keep" | "discard" | None (cancelled)

        self.title("Files Modified Outside Session")
        self.geometry("560x480")
        self.resizable(False, False)
        self._center()
        self._build_ui()
        self.grab_set()
        self.focus()

    def _center(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 560) // 2
        y = (sh - 480) // 2
        self.geometry(f"560x480+{x}+{y}")

    def _build_ui(self):
        ctk.set_appearance_mode("dark")

        # Header
        header = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0)
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text="⚠  Files Modified Outside Session",
            font=ctk.CTkFont(family="Courier New", size=14, weight="bold"),
            text_color="#f0c040",
        ).pack(padx=20, pady=(16, 4), anchor="w")

        ctk.CTkLabel(
            header,
            text="These files changed since your last session ended.\nChoose how to handle them before starting.",
            font=ctk.CTkFont(size=12),
            text_color="#aaaacc",
            justify="left",
        ).pack(padx=20, pady=(0, 16), anchor="w")

        # File list
        list_frame = ctk.CTkScrollableFrame(self, fg_color="#12121f", corner_radius=0)
        list_frame.pack(fill="both", expand=True, padx=0, pady=0)

        status_colors = {
            "modified": "#f0c040",
            "added": "#40f080",
            "deleted": "#f04060",
        }
        status_icons = {
            "modified": "~",
            "added": "+",
            "deleted": "−",
        }

        for change in self.changes:
            row = ctk.CTkFrame(list_frame, fg_color="#1c1c30", corner_radius=6)
            row.pack(fill="x", padx=12, pady=4)

            color = status_colors.get(change.status, "#ffffff")
            icon = status_icons.get(change.status, "?")

            ctk.CTkLabel(
                row,
                text=f" {icon} ",
                font=ctk.CTkFont(family="Courier New", size=13, weight="bold"),
                text_color=color,
                width=30,
            ).pack(side="left", padx=(8, 0), pady=10)

            ctk.CTkLabel(
                row,
                text=change.path,
                font=ctk.CTkFont(family="Courier New", size=12),
                text_color="#ddddee",
                anchor="w",
            ).pack(side="left", padx=8, pady=10, fill="x", expand=True)

            ctk.CTkLabel(
                row,
                text=change.status,
                font=ctk.CTkFont(size=11),
                text_color=color,
            ).pack(side="right", padx=12, pady=10)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="#0f0f1e", corner_radius=0)
        btn_frame.pack(fill="x")

        ctk.CTkLabel(
            btn_frame,
            text="Keep local → uploads your changes first\nDiscard local → overwrites with R2 version",
            font=ctk.CTkFont(size=11),
            text_color="#777799",
            justify="left",
        ).pack(side="left", padx=16, pady=12)

        btn_right = ctk.CTkFrame(btn_frame, fg_color="transparent")
        btn_right.pack(side="right", padx=16, pady=12)

        ctk.CTkButton(
            btn_right,
            text="Cancel",
            width=90,
            fg_color="#2a2a40",
            hover_color="#3a3a55",
            command=self._cancel,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_right,
            text="Discard Local",
            width=110,
            fg_color="#5a1a1a",
            hover_color="#7a2a2a",
            command=self._discard,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_right,
            text="Keep Local",
            width=100,
            fg_color="#1a4a2a",
            hover_color="#2a6a3a",
            command=self._keep,
        ).pack(side="left")

    def _keep(self):
        self.result = "keep"
        self.destroy()

    def _discard(self):
        self.result = "discard"
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
