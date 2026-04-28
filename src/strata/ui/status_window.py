"""
Status window — shows all profiles simultaneously, each as its own card
with live status and action buttons (Start/End Session, Quick Pull, Open Folder).

Button actions use data-action / data-name attributes on a single delegated
click listener on the container, avoiding any quoting issues with profile
names that contain characters special to HTML attributes or JS strings.
"""
from __future__ import annotations

import asyncio
import html as _html
import json

from strata.core.engine import SyncStatus
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


DOT_COLOR = {
    "session": "#1c7a45",
    "busy":    "#0a6dc2",
    "idle":    "#aaaaaa",
    "error":   "#c2270a",
}


class StatusWindow(WebViewWindow):
    TITLE = "Strata"
    SIZE = (480, 400)
    RESIZABLE = True

    _instance: "StatusWindow | None" = None

    def __init__(self, app):
        self._refresh_task: asyncio.Task | None = None
        super().__init__(app)
        self._refresh_task = asyncio.ensure_future(self._tick())

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
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None
        StatusWindow._instance = None

    # ── HTML ───────────────────────────────────────────────────────────────

    def build_html(self) -> str:
        device = _html.escape(self.app.config.get("device_name", ""))

        body = f"""
<body style="padding:16px;">
  <div class="row" style="margin-bottom:14px; align-items:baseline;">
    <h1 style="flex:1;">Strata</h1>
    <span class="muted">{device}</span>
  </div>

  <div id="profiles-container">
    <p class="muted">Loading…</p>
  </div>

  <div class="spacer"></div>

  <div style="margin-top:10px; text-align:right;">
    <button class="btn-secondary" style="font-size:11px; padding:4px 12px;"
            data-action="settings">Settings…</button>
  </div>

<style>
{_EXTRA_CSS}
</style>

<script>
__JS__

// ── Event delegation ────────────────────────────────────────────────────
// All buttons use data-action and data-name attributes.
// One listener on document handles everything — no inline onclick with
// profile names that could break HTML attribute quoting.

document.addEventListener("click", function(e) {{
  const btn = e.target.closest("[data-action]");
  if (!btn || btn.disabled) return;
  const action = btn.dataset.action;
  const name   = btn.dataset.name || null;
  if (action === "settings") {{
    post("settings");
  }} else if (name) {{
    post(action, {{ name: name }});
  }}
}});

// ── Rendering ───────────────────────────────────────────────────────────

let _renderedProfiles = [];

function updateUI(state) {{
  const profiles = state.profiles;
  const names = profiles.map(p => p.name);

  const same = names.length === _renderedProfiles.length &&
    names.every((n, i) => n === _renderedProfiles[i]);

  if (!same) {{
    renderAll(profiles);
    _renderedProfiles = names.slice();
    return;
  }}
  for (const p of profiles) {{ patchCard(p); }}
}}

function renderAll(profiles) {{
  const container = document.getElementById("profiles-container");
  if (!profiles.length) {{
    container.innerHTML = "<p class='muted'>No configured profiles. Open Settings to add one.</p>";
    return;
  }}
  container.innerHTML = profiles.map(cardHTML).join("");
}}

function cardHTML(p) {{
  const dot    = dotColor(p);
  const label  = statusLabel(p);
  const detail = p.message || "";
  const id     = cssId(p.name);

  // Disabled states
  const sesDisabled = (p.busy || p.op_in_progress) ? " disabled" : "";
  const qpDisabled  = (p.busy || p.session_active || p.op_in_progress) ? " disabled" : "";

  // Session button — action and name go in data attributes, no quoting needed
  const sessionAction = p.session_active ? "toggle" : "toggle";
  const sessionClass  = p.session_active ? "btn-danger btn-sm" : "btn-primary btn-sm";
  const sessionLabel  = p.session_active ? "End Session" : "Start Session";

  const progressBar = p.busy
    ? `<div class="progress-track" style="margin:6px 0 2px;">
         <div class="progress-fill progress-indeterminate"></div>
       </div>`
    : "";

  const detailRow = detail
    ? `<div class="profile-detail muted">${{esc(detail)}}</div>`
    : "";

  // Profile name is placed in data-name. esc() only HTML-escapes for display;
  // data attributes are set as text content so special chars are safe.
  return `
    <div class="profile-card" id="card-${{id}}">
      <div class="profile-card-header">
        <span class="dot" style="color:${{dot}}">●</span>
        <span class="profile-name">${{esc(p.name)}}</span>
        <span class="status-label">${{label}}</span>
      </div>
      ${{detailRow}}
      ${{progressBar}}
      <div class="profile-path muted">${{esc(p.sync_dir)}}</div>
      <div class="profile-actions">
        <button class="${{sessionClass}}"
                data-action="toggle"
                data-name="${{esc(p.name)}}"
                ${{sesDisabled}}>${{sessionLabel}}</button>
        <button class="btn-secondary btn-sm"
                data-action="quick-pull"
                data-name="${{esc(p.name)}}"
                ${{qpDisabled}}>Quick Pull</button>
        <button class="btn-secondary btn-sm"
                data-action="open-folder"
                data-name="${{esc(p.name)}}">Open Folder</button>
      </div>
    </div>`;
}}

function patchCard(p) {{
  const card = document.getElementById("card-" + cssId(p.name));
  if (!card) return;
  card.outerHTML = cardHTML(p);
}}

function dotColor(p) {{
  if (p.error)          return "{DOT_COLOR['error']}";
  if (p.busy)           return "{DOT_COLOR['busy']}";
  if (p.session_active) return "{DOT_COLOR['session']}";
  return "{DOT_COLOR['idle']}";
}}

function statusLabel(p) {{
  if (p.busy)           return p.status_text;
  if (p.session_active) return "Session active";
  if (p.error)          return "Error";
  return "No session";
}}

function cssId(name) {{
  return name.replace(/[^a-zA-Z0-9_-]/g, "_");
}}

function esc(s) {{
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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

    def dispatch(self, action: str, payload):
        name = (payload or {}).get("name", "")
        if action == "toggle" and name:
            self.app.toggle_session(name)
        elif action == "quick-pull" and name:
            self.app.quick_pull(name)
        elif action == "open-folder" and name:
            self.app.open_folder(name)
        elif action == "settings":
            self.app._on_open_settings(None)

    # ── State ──────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        app = self.app
        profiles = []

        for profile in app.config.get("profiles", []):
            name = profile.get("name", "")
            if not name:
                continue

            engine = app.engines.get(name)
            status = app._profile_status.get(name, SyncStatus.IDLE)
            message = app._profile_message.get(name, "")
            session_active = name in app._active_sessions
            op_in_progress = name in app._operation_in_progress
            busy = status in (SyncStatus.STARTING, SyncStatus.SYNCING, SyncStatus.ENDING)
            error = status == SyncStatus.ERROR

            status_text_map = {
                SyncStatus.STARTING: "Starting…",
                SyncStatus.SYNCING:  "Downloading…",
                SyncStatus.ENDING:   "Uploading…",
            }

            profiles.append({
                "name":           name,
                "sync_dir":       str(engine.sync_dir) if engine else profile.get("sync_dir", ""),
                "session_active": session_active,
                "busy":           busy,
                "op_in_progress": op_in_progress,
                "error":          error,
                "status_text":    status_text_map.get(status, ""),
                "message":        message,
                "configured":     engine is not None,
            })

        return {"profiles": profiles}

    # ── Refresh tick ───────────────────────────────────────────────────────

    async def _tick(self):
        try:
            while True:
                await asyncio.sleep(1.0)
                self.push_state()
        except asyncio.CancelledError:
            pass


# ── Extra CSS ──────────────────────────────────────────────────────────────────

_EXTRA_CSS = """
  .profile-card {
    background: white;
    border-radius: 10px;
    padding: 12px 14px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
    margin-bottom: 10px;
  }
  .profile-card-header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 2px;
  }
  .dot { font-size: 11px; flex-shrink: 0; }
  .profile-name {
    font-weight: 600;
    font-size: 14px;
    flex: 1;
  }
  .status-label {
    font-size: 11px;
    color: #888;
  }
  .profile-detail {
    font-size: 11px;
    margin: 2px 0 4px 17px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .profile-path {
    font-size: 10px;
    margin: 3px 0 6px 17px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .profile-actions {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }
  .btn-sm {
    font-size: 11px;
    padding: 4px 10px;
  }
"""
