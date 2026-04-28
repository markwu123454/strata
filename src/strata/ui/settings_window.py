"""
Settings window — R2 credentials, sync directory, preferences.

This is the most complex of the windows because:
  - Form has many fields (round-tripped via the bridge on Save)
  - Folder picker requires JS → Python → native dialog → JS round-trip
  - Autostart switch is conditionally shown by platform
"""
from __future__ import annotations

import html as _html
import json

import toga

from strata.config import save_config
from strata.core import autostart
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


class SettingsWindow(WebViewWindow):
    TITLE = "Strata — Settings"
    SIZE = (580, 680)

    _instance: "SettingsWindow | None" = None

    def __init__(self, app):
        # Snapshot config so Cancel just throws it away.
        self.draft = app.config.copy()
        super().__init__(app)

    @classmethod
    def open_or_focus(cls, app):
        if cls._instance is not None:
            try:
                cls._instance.window.show()
                return
            except Exception:
                cls._instance = None
        cls._instance = cls(app)

    def on_closed(self):
        SettingsWindow._instance = None

    # ── HTML ───────────────────────────────────────────────────────────────

    def build_html(self) -> str:
        c = self.draft

        def field(key: str, label: str, password: bool = False) -> str:
            val = _html.escape(str(c.get(key, "")))
            type_ = "password" if password else "text"
            return f"""
              <label class="field-label">{label}</label>
              <input type="{type_}" id="f_{key}" value="{val}">
            """

        autostart_block = ""
        if autostart.is_supported():
            checked = "checked" if c.get("autostart_enabled", False) else ""
            autostart_block = f"""
              <label class="switch">
                <input type="checkbox" id="f_autostart_enabled" {checked}>
                <span class="track"></span>
                <span>Run Strata when I sign in to Windows</span>
              </label>
            """

        updates_checked = "checked" if c.get("check_for_updates", True) else ""
        sync_dir = _html.escape(str(c.get("sync_dir", "")))

        body = f"""
<body style="padding:0;">
  <div class="scroll" style="padding:24px 24px 0 24px;">

    <h2>Cloudflare R2</h2>
    {field("r2_account_id", "Account ID")}
    {field("r2_bucket",     "Bucket Name")}
    {field("r2_access_key", "Access Key ID")}
    {field("r2_secret_key", "Secret Access Key", password=True)}

    <h2>Device</h2>
    {field("device_name", "Device Name")}

    <h2>Sync Directory</h2>
    <label class="field-label">Folder</label>
    <div class="row" style="gap:8px;">
      <input type="text" id="f_sync_dir" value="{sync_dir}" style="flex:1;">
      <button class="btn-secondary" onclick="post('browse')">Browse…</button>
    </div>

    <h2>Preferences</h2>
    {autostart_block}
    <label class="switch">
      <input type="checkbox" id="f_check_for_updates" {updates_checked}>
      <span class="track"></span>
      <span>Automatically check for updates</span>
    </label>

    <div style="height:20px;"></div>
  </div>

  <div style="border-top:1px solid #d2d2d7; padding:14px 24px; background:white;">
    <div class="actions" style="margin-top:0;">
      <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
      <button class="btn-primary"   onclick="saveForm()">Save</button>
    </div>
  </div>

<script>
__JS__

function gather() {{
  const fields = [
    "r2_account_id", "r2_bucket", "r2_access_key", "r2_secret_key",
    "device_name", "sync_dir",
    "autostart_enabled", "check_for_updates"
  ];
  const out = {{}};
  for (const f of fields) {{
    const el = document.getElementById("f_" + f);
    if (!el) continue;
    if (el.type === "checkbox") {{
      out[f] = el.checked;
    }} else {{
      out[f] = el.value.trim();
    }}
  }}
  return out;
}}

function saveForm() {{
  post("save", gather());
}}

function setSyncDir(path) {{
  const el = document.getElementById("f_sync_dir");
  if (el) el.value = path;
}}
</script>
</body>
"""
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{SHARED_CSS}</style></head>"
            + body.replace("__JS__", SHARED_JS)
            + "</html>"
        )

    # ── Dispatch ───────────────────────────────────────────────────────────

    def dispatch(self, action, payload):
        if action == "cancel":
            self.close()
        elif action == "browse":
            # Native folder picker. Must run as a coroutine because
            # Toga dialogs are async.
            import asyncio
            asyncio.ensure_future(self._do_browse())
        elif action == "save":
            self._do_save(payload or {})

    async def _do_browse(self):
        import sys
        if sys.platform.startswith("win"):
            # Native Vista+ folder picker
            from strata.ui.win_folder_picker import pick_folder
            # Get current input value as starting dir
            current = self.draft.get("sync_dir") or None
            # Try to grab the parent HWND from Toga's window for proper modality.
            hwnd = 0
            try:
                hwnd = int(self.window._impl.native.Handle.ToInt64())
            except Exception:
                pass

            # Run the dialog in a thread so we don't block the asyncio loop
            # (Show() is synchronous and blocks until user picks/cancels).
            import asyncio
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(
                None, pick_folder, "Select sync directory", current, hwnd
            )
            if path:
                import json
                self.eval_js(f"setSyncDir({json.dumps(path)})")
            return

        # Fallback for non-Windows: Toga's dialog
        try:
            path = await self.window.dialog(
                toga.SelectFolderDialog(title="Select sync directory")
            )
            if path:
                import json
                self.eval_js(f"setSyncDir({json.dumps(str(path))})")
        except Exception:
            pass

    def _do_save(self, form: dict):
        # Merge form data into draft. We trust the JS to send the right keys.
        for k in (
            "r2_account_id", "r2_bucket", "r2_access_key", "r2_secret_key",
            "device_name", "sync_dir",
        ):
            if k in form:
                self.draft[k] = form[k]

        if "autostart_enabled" in form:
            self.draft["autostart_enabled"] = bool(form["autostart_enabled"])
        else:
            self.draft["autostart_enabled"] = False
        self.draft["check_for_updates"] = bool(form.get("check_for_updates", True))

        save_config(self.draft)

        if autostart.is_supported():
            if self.draft["autostart_enabled"]:
                autostart.enable()
            else:
                autostart.disable()

        self.app.on_settings_saved(self.draft)
        self.close()
