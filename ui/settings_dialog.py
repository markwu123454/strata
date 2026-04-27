"""
UI: Settings dialog
Configure R2 credentials, sync directory, device name.
"""

import customtkinter as ctk
from tkinter import filedialog
from config import save_config


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, config: dict, on_save=None):
        super().__init__(parent)
        self.config = config.copy()
        self.on_save = on_save
        self.saved = False

        self.title("DirSync — Settings")
        self.geometry("500x560")
        self.resizable(False, False)
        self._center()
        self._build_ui()
        self.grab_set()
        self.focus()

    def _center(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 500) // 2
        y = (sh - 560) // 2
        self.geometry(f"500x560+{x}+{y}")

    def _build_ui(self):
        ctk.set_appearance_mode("dark")

        # Header
        header = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="DirSync / Settings",
            font=ctk.CTkFont(family="Courier New", size=15, weight="bold"),
            text_color="#8888ff",
        ).pack(padx=20, pady=16, anchor="w")

        # Form
        form = ctk.CTkScrollableFrame(self, fg_color="#12121f", corner_radius=0)
        form.pack(fill="both", expand=True)

        self.fields = {}

        def section(label):
            ctk.CTkLabel(
                form,
                text=label,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#5555aa",
            ).pack(padx=20, pady=(16, 4), anchor="w")

        def field(parent_frame, key, label, placeholder="", show=None):
            ctk.CTkLabel(
                parent_frame,
                text=label,
                font=ctk.CTkFont(size=12),
                text_color="#aaaacc",
            ).pack(padx=20, pady=(6, 2), anchor="w")
            entry = ctk.CTkEntry(
                parent_frame,
                placeholder_text=placeholder,
                font=ctk.CTkFont(family="Courier New", size=12),
                fg_color="#1c1c30",
                border_color="#3a3a55",
                show=show,
            )
            entry.pack(fill="x", padx=20, pady=(0, 2))
            if self.config.get(key):
                entry.insert(0, self.config[key])
            self.fields[key] = entry

        section("Cloudflare R2")
        field(form, "r2_account_id", "Account ID", "abc123xyz...")
        field(form, "r2_bucket", "Bucket Name", "my-sync-bucket")
        field(form, "r2_access_key", "Access Key ID", "R2_ACCESS_KEY_ID")
        field(form, "r2_secret_key", "Secret Access Key", "••••••••", show="*")

        section("Device")
        field(form, "device_name", "Device Name", "MacBook Pro")

        section("Sync Directory")
        dir_row = ctk.CTkFrame(form, fg_color="transparent")
        dir_row.pack(fill="x", padx=20, pady=(6, 2))

        self.dir_entry = ctk.CTkEntry(
            dir_row,
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color="#1c1c30",
            border_color="#3a3a55",
        )
        self.dir_entry.pack(side="left", fill="x", expand=True)
        if self.config.get("sync_dir"):
            self.dir_entry.insert(0, self.config["sync_dir"])

        ctk.CTkButton(
            dir_row,
            text="Browse",
            width=70,
            fg_color="#2a2a45",
            hover_color="#3a3a60",
            command=self._browse,
        ).pack(side="left", padx=(8, 0))

        # Footer buttons
        btn_frame = ctk.CTkFrame(self, fg_color="#0f0f1e", corner_radius=0)
        btn_frame.pack(fill="x")

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=90,
            fg_color="#2a2a40",
            hover_color="#3a3a55",
            command=self.destroy,
        ).pack(side="right", padx=(8, 16), pady=14)

        ctk.CTkButton(
            btn_frame,
            text="Save",
            width=90,
            fg_color="#2a3a6a",
            hover_color="#3a4a8a",
            command=self._save,
        ).pack(side="right", padx=0, pady=14)

    def _browse(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select sync directory")
        if path:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, path)

    def _save(self):
        for key, entry in self.fields.items():
            self.config[key] = entry.get().strip()
        self.config["sync_dir"] = self.dir_entry.get().strip()
        save_config(self.config)
        self.saved = True
        if self.on_save:
            self.on_save(self.config)
        self.destroy()
