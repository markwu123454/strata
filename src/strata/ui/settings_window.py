"""
Settings window — profile list + per-profile R2 credentials and sync dir,
plus device-level preferences.

Layout:
  Left panel (180px): profile list with Add / Delete buttons
  Right panel (flex):  form for the selected profile
  Bottom bar:          device settings + Cancel / Save

Profile switch fix: password fields (r2_secret_key) are not gathered on
profile switch because browsers/WebViews may return empty string for
password inputs read via JS. We only overwrite a credential field on
switch if the value is non-empty, so existing secrets are preserved.
On Save we always write what the form contains (user intentionally saved).
"""
from __future__ import annotations

import html as _html
import json
import copy

import toga

from strata.config import (
    PROFILE_REQUIRED,
    is_valid_profile_name,
    save_config,
)
from strata.core import autostart
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


class SettingsWindow(WebViewWindow):
    TITLE = "Strata — Settings"
    SIZE = (680, 700)

    _instance: "SettingsWindow | None" = None

    def __init__(self, app):
        self.draft = copy.deepcopy(app.config)
        profiles = self.draft.get("profiles", [])
        self.selected_profile = (
            self.draft.get("active_profile")
            or (profiles[0]["name"] if profiles else "")
        )
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

    # ── Helpers ────────────────────────────────────────────────────────────

    def _profile_by_name(self, name: str) -> dict | None:
        for p in self.draft.get("profiles", []):
            if p.get("name") == name:
                return p
        return None

    def _profile_names(self) -> list[str]:
        return [p.get("name", "") for p in self.draft.get("profiles", [])]

    def _apply_profile_form(self, form: dict, *, on_save: bool = False):
        """Write profile form values into self.draft for the currently
        selected profile.

        on_save=False (profile switch): skip empty values so we don't clobber
        stored credentials with empty strings returned by the WebView for
        password fields. The profile name field is also ignored on switch —
        renames only happen on explicit Save.

        on_save=True: write all fields as-is (user deliberately saved).
        """
        p = self._profile_by_name(self.selected_profile)
        if p is None:
            return
        for k in ("r2_account_id", "r2_bucket", "r2_access_key", "r2_secret_key", "sync_dir"):
            if k not in form:
                continue
            v = form[k]
            if on_save:
                p[k] = v
            else:
                # On switch: only overwrite if non-empty, to protect secrets
                # that the WebView may have blanked out for security.
                if v:
                    p[k] = v

    # ── HTML ───────────────────────────────────────────────────────────────

    def build_html(self) -> str:
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{SHARED_CSS}{_EXTRA_CSS}</style></head>"
            + self._build_body()
            + "</html>"
        )

    def _build_body(self) -> str:
        selected = self.selected_profile
        sidebar_html = self._build_sidebar_items()
        form_html = self._build_form_panel(selected)

        autostart_block = ""
        if autostart.is_supported():
            checked = "checked" if self.draft.get("autostart_enabled", False) else ""
            autostart_block = f"""
              <label class="switch">
                <input type="checkbox" id="f_autostart_enabled" {checked}>
                <span class="track"></span>
                <span>Run Strata when I sign in to Windows</span>
              </label>
            """
        updates_checked = "checked" if self.draft.get("check_for_updates", True) else ""
        device_name = _html.escape(str(self.draft.get("device_name", "")))

        body = f"""
<body style="padding:0; flex-direction:column;">
  <div class="split" style="flex:1; overflow:hidden;">

    <!-- LEFT: profile list -->
    <div class="sidebar">
      <div class="sidebar-header">Profiles</div>
      <ul id="profile-list" class="profile-list">
        {sidebar_html}
      </ul>
      <div class="sidebar-footer">
        <button class="btn-secondary btn-sm" onclick="post('add_profile')">+ Add</button>
        <button class="btn-danger btn-sm" onclick="deleteSelected()">Delete</button>
      </div>
    </div>

    <!-- RIGHT: form -->
    <div id="form-panel" class="form-panel scroll">
      {form_html}
    </div>

  </div>

  <!-- BOTTOM BAR -->
  <div style="border-top:1px solid #d2d2d7; padding:14px 24px; background:white; flex-shrink:0;">

    <h2 style="margin-top:0;">Device</h2>
    <label class="field-label">Device Name</label>
    <input type="text" id="f_device_name" value="{device_name}">

    <h2>Preferences</h2>
    {autostart_block}
    <div class="row" style="justify-content:space-between; align-items:center; padding:4px 0;">
      <label class="switch" style="flex:1; margin:0;">
        <input type="checkbox" id="f_check_for_updates" {updates_checked}>
        <span class="track"></span>
        <span>Automatically check for updates</span>
      </label>
      <button class="btn-secondary" id="check-update-btn"
              style="font-size:11px; padding:4px 12px; white-space:nowrap;"
              onclick="checkForUpdates()">Check Now</button>
    </div>
    <div id="update-status" class="muted" style="font-size:11px; margin-top:4px; min-height:14px;"></div>

    <div class="actions" style="margin-top:14px; margin-bottom:0;">
      <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
      <button class="btn-primary"   onclick="saveAll()">Save</button>
    </div>
  </div>

<script>
__JS__

// Track which profile name is currently shown so Delete/switch send the right one.
let _selectedProfile = {json.dumps(selected)};

// ── Delete ──────────────────────────────────────────────────────────────

function deleteSelected() {{
  post('delete_profile', {{ name: _selectedProfile }});
}}

// ── Profile switch ──────────────────────────────────────────────────────

function selectProfile(name) {{
  if (name === _selectedProfile) return;
  // Gather the current form so Python can stash non-empty values before
  // switching. Password fields may return empty string from the WebView —
  // Python will skip overwriting them (see _apply_profile_form).
  post('select', {{ name: name, current_form: gatherProfile() }});
}}

// ── Form gathering ──────────────────────────────────────────────────────

function gatherProfile() {{
  const fields = ["r2_account_id", "r2_bucket", "r2_access_key", "r2_secret_key", "sync_dir", "profile_name"];
  const out = {{}};
  for (const f of fields) {{
    const el = document.getElementById("f_" + f);
    if (el) out[f] = el.value.trim();
  }}
  return out;
}}

function gatherGlobals() {{
  const out = {{}};
  const dn = document.getElementById("f_device_name");
  if (dn) out.device_name = dn.value.trim();
  const cu = document.getElementById("f_check_for_updates");
  if (cu) out.check_for_updates = cu.checked;
  const ae = document.getElementById("f_autostart_enabled");
  out.autostart_enabled = ae ? ae.checked : false;
  return out;
}}

function saveAll() {{
  post("save", {{ profile: gatherProfile(), globals: gatherGlobals() }});
}}

// ── Check for updates ───────────────────────────────────────────────────

function checkForUpdates() {{
  const btn = document.getElementById("check-update-btn");
  const status = document.getElementById("update-status");
  btn.disabled = true;
  status.textContent = "Checking…";
  status.className = "muted";
  post('check_for_updates').then(() => {{
    // Re-enable after a short delay — the actual result comes back via
    // the tray update slot if a new version is found.
    setTimeout(() => {{
      btn.disabled = false;
      status.textContent = "Done — if a new version is available, you'll see it in the tray.";
    }}, 2000);
  }});
}}

// ── Folder picker callback ──────────────────────────────────────────────

function setSyncDir(path) {{
  const el = document.getElementById("f_sync_dir");
  if (el) el.value = path;
}}

// ── Dynamic updates from Python ─────────────────────────────────────────

function updateFormPanel(html, newSelected) {{
  document.getElementById("form-panel").innerHTML = html;
  if (newSelected !== undefined) _selectedProfile = newSelected;
}}

function updateProfileList(html, newSelected) {{
  document.getElementById("profile-list").innerHTML = html;
  if (newSelected !== undefined) _selectedProfile = newSelected;
}}
</script>
</body>
"""
        return body.replace("__JS__", SHARED_JS)

    def _build_form_panel(self, profile_name: str) -> str:
        p = self._profile_by_name(profile_name)
        if p is None:
            if not self.draft.get("profiles"):
                return "<p class='muted' style='padding:24px;'>No profiles yet — click + Add to create one.</p>"
            return "<p class='muted' style='padding:24px;'>Select a profile from the list.</p>"

        def val(key):
            return _html.escape(str(p.get(key, "")))

        def field(key, label, password=False):
            t = "password" if password else "text"
            # For password fields, use a placeholder instead of the real value
            # so the WebView doesn't blank it out on read. The actual value is
            # stored in self.draft and written to the file on Save.
            if password:
                stored = p.get(key, "")
                # Show placeholder if value is set; show empty if not.
                placeholder = "••••••••" if stored else ""
                return f"""
                  <label class="field-label">{label}</label>
                  <input type="password" id="f_{key}" value=""
                         placeholder="{_html.escape(placeholder)}"
                         autocomplete="new-password">
                """
            return f"""
              <label class="field-label">{label}</label>
              <input type="{t}" id="f_{key}" value="{val(key)}">
            """

        sync_dir = val("sync_dir")
        name_val = _html.escape(profile_name)

        return f"""
          <h2 style="margin-top:0;">Profile Name</h2>
          <input type="text" id="f_profile_name" value="{name_val}"
                 placeholder="e.g. work, personal">

          <h2>Cloudflare R2</h2>
          {field("r2_account_id", "Account ID")}
          {field("r2_bucket",     "Bucket Name")}
          {field("r2_access_key", "Access Key ID")}
          {field("r2_secret_key", "Secret Access Key", password=True)}

          <h2>Sync Directory</h2>
          <label class="field-label">Folder</label>
          <div class="row" style="gap:8px;">
            <input type="text" id="f_sync_dir" value="{sync_dir}" style="flex:1;">
            <button class="btn-secondary" onclick="post('browse')">Browse…</button>
          </div>
        """

    def _build_sidebar_items(self) -> str:
        selected = self.selected_profile
        items = []
        for p in self.draft.get("profiles", []):
            name = p.get("name", "")
            active_cls = "profile-item active" if name == selected else "profile-item"
            badge = (
                " <span class='badge'>active</span>"
                if name == self.draft.get("active_profile") else ""
            )
            # json.dumps produces "name" with double quotes. The onclick
            # attribute must use single quotes so the inner double quotes
            # don't break the HTML parser.
            items.append(
                f"<li class='{active_cls}' onclick='selectProfile({json.dumps(name)})'>"
                f"<span>{_html.escape(name)}</span>{badge}</li>"
            )
        return "\n".join(items) or "<li class='muted' style='padding:8px;'>No profiles</li>"

    # ── Dispatch ───────────────────────────────────────────────────────────

    def dispatch(self, action, payload):
        if action == "cancel":
            self.close()

        elif action == "select":
            name = (payload or {}).get("name", "")
            current_form = (payload or {}).get("current_form") or {}
            if name and self._profile_by_name(name) is not None:
                # Stash current form (skipping empty password values).
                self._apply_profile_form(current_form, on_save=False)
                self.selected_profile = name
                form_html = self._build_form_panel(name)
                sidebar_html = self._build_sidebar_items()
                self.eval_js(f"updateFormPanel({json.dumps(form_html)}, {json.dumps(name)})")
                self.eval_js(f"updateProfileList({json.dumps(sidebar_html)}, {json.dumps(name)})")

        elif action == "add_profile":
            self._do_add_profile()

        elif action == "delete_profile":
            name = (payload or {}).get("name", "")
            self._do_delete_profile(name)

        elif action == "browse":
            import asyncio
            asyncio.ensure_future(self._do_browse())

        elif action == "check_for_updates":
            self.app.check_for_updates()

        elif action == "save":
            self._do_save(payload or {})

    def _do_add_profile(self):
        existing = set(self._profile_names())
        base = "new-profile"
        name = base
        i = 2
        while name in existing:
            name = f"{base}-{i}"
            i += 1
        new_profile = {
            "name": name,
            "r2_account_id": "",
            "r2_access_key": "",
            "r2_secret_key": "",
            "r2_bucket": "",
            "sync_dir": "",
        }
        self.draft.setdefault("profiles", []).append(new_profile)
        self.selected_profile = name
        form_html = self._build_form_panel(name)
        sidebar_html = self._build_sidebar_items()
        self.eval_js(f"updateFormPanel({json.dumps(form_html)}, {json.dumps(name)})")
        self.eval_js(f"updateProfileList({json.dumps(sidebar_html)}, {json.dumps(name)})")

    def _do_delete_profile(self, name: str):
        profiles = self.draft.get("profiles", [])
        if not name or self._profile_by_name(name) is None:
            return
        if len(profiles) <= 1:
            _msg = json.dumps(
                "<p class='error' style='padding:24px;'>Cannot delete the last profile.</p>"
            )
            self.eval_js(f"updateFormPanel({_msg})")
            return
        self.draft["profiles"] = [p for p in profiles if p.get("name") != name]
        remaining = self._profile_names()
        self.selected_profile = remaining[0] if remaining else ""
        if self.draft.get("active_profile") == name:
            self.draft["active_profile"] = self.selected_profile
        form_html = self._build_form_panel(self.selected_profile)
        sidebar_html = self._build_sidebar_items()
        self.eval_js(f"updateFormPanel({json.dumps(form_html)}, {json.dumps(self.selected_profile)})")
        self.eval_js(f"updateProfileList({json.dumps(sidebar_html)}, {json.dumps(self.selected_profile)})")

    async def _do_browse(self):
        import sys
        current_profile = self._profile_by_name(self.selected_profile)
        current_dir = current_profile.get("sync_dir") if current_profile else None

        if sys.platform.startswith("win"):
            from strata.ui.win_folder_picker import pick_folder
            hwnd = 0
            try:
                hwnd = int(self.window._impl.native.Handle.ToInt64())
            except Exception:
                pass
            import asyncio
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(
                None, pick_folder, "Select sync directory", current_dir, hwnd
            )
            if path:
                self.eval_js(f"setSyncDir({json.dumps(path)})")
            return

        try:
            path = await self.window.dialog(
                toga.SelectFolderDialog(title="Select sync directory")
            )
            if path:
                self.eval_js(f"setSyncDir({json.dumps(str(path))})")
        except Exception:
            pass

    def _do_save(self, form: dict):
        globals_ = form.get("globals", {})
        if "device_name" in globals_:
            self.draft["device_name"] = globals_["device_name"]
        if "check_for_updates" in globals_:
            self.draft["check_for_updates"] = bool(globals_["check_for_updates"])
        self.draft["autostart_enabled"] = bool(globals_.get("autostart_enabled", False))

        profile_form = form.get("profile", {})
        p = self._profile_by_name(self.selected_profile)
        if p is not None:
            new_name = profile_form.get("profile_name", "").strip()
            old_name = self.selected_profile
            if new_name and new_name != old_name:
                if not is_valid_profile_name(new_name):
                    _err = json.dumps(
                        "<p class='error' style='padding:24px;'>"
                        "Profile name must contain only letters, digits, hyphens, and underscores."
                        "</p>"
                    )
                    self.eval_js(f"updateFormPanel({_err})")
                    return
                if new_name in self._profile_names() and new_name != old_name:
                    _err = json.dumps(
                        "<p class='error' style='padding:24px;'>"
                        "A profile with that name already exists."
                        "</p>"
                    )
                    self.eval_js(f"updateFormPanel({_err})")
                    return
                p["name"] = new_name
                if self.draft.get("active_profile") == old_name:
                    self.draft["active_profile"] = new_name
                self.selected_profile = new_name

            # on_save=True: write all fields including empty ones
            # (user explicitly cleared a field).
            # Exception: if secret key is empty AND we already have one stored,
            # treat it as "unchanged" — the placeholder showed bullets so the
            # user didn't intentionally clear it.
            for k in ("r2_account_id", "r2_bucket", "r2_access_key", "sync_dir"):
                if k in profile_form:
                    p[k] = profile_form[k]

            # Secret key: only overwrite if the field isn't empty.
            secret = profile_form.get("r2_secret_key", "")
            if secret:
                p["r2_secret_key"] = secret
            # If empty and we have a stored value, keep the stored value.

        save_config(self.draft)

        if autostart.is_supported():
            if self.draft.get("autostart_enabled"):
                autostart.enable()
            else:
                autostart.disable()

        self.app.on_settings_saved(self.draft)
        self.close()


# ── Extra CSS ──────────────────────────────────────────────────────────────────

_EXTRA_CSS = """
  .split {
    display: flex;
    flex-direction: row;
    height: 100%;
  }
  .sidebar {
    width: 180px;
    min-width: 160px;
    flex-shrink: 0;
    background: #e8e8ed;
    border-right: 1px solid #d2d2d7;
    display: flex;
    flex-direction: column;
  }
  .sidebar-header {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .04em;
    color: #6e6e73;
    padding: 14px 12px 6px 12px;
  }
  .profile-list {
    list-style: none;
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }
  .profile-item {
    display: flex;
    align-items: center;
    padding: 7px 12px;
    cursor: pointer;
    border-radius: 6px;
    margin: 1px 6px;
    font-size: 13px;
    gap: 6px;
  }
  .profile-item:hover { background: rgba(0,0,0,.06); }
  .profile-item.active {
    background: #007aff;
    color: white;
  }
  .profile-item.active .badge { background: rgba(255,255,255,.25); color: white; }
  .badge {
    font-size: 9px;
    font-weight: 600;
    background: rgba(0,122,255,.15);
    color: #007aff;
    border-radius: 4px;
    padding: 1px 4px;
    text-transform: uppercase;
    letter-spacing: .03em;
    white-space: nowrap;
  }
  .sidebar-footer {
    display: flex;
    gap: 6px;
    padding: 10px;
    border-top: 1px solid #d2d2d7;
  }
  .btn-sm {
    font-size: 11px;
    padding: 4px 10px;
    flex: 1;
  }
  .form-panel {
    flex: 1;
    padding: 20px 24px;
    overflow-y: auto;
  }
"""