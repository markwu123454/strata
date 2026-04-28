"""
Status window — opened from the tray icon. Shows current sync state and
provides a primary action button.

Uses the shared WebViewWindow base so it inherits the same CSS, button
styles, and JS bridge as the rest of the app's windows.

A 1-second timer pushes get_state() into the page. The engine also pushes
state changes through callbacks, but the timer covers the case where the
user opens the window mid-syncing — they should see live progress
immediately.
"""
from __future__ import annotations

import asyncio
import html as _html

from strata.core.engine import SyncStatus
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


# Label text and dot color per non-idle status.
STATUS_DISPLAY = {
    SyncStatus.STARTING: ("Starting…",    "#b07a00"),
    SyncStatus.SYNCING:  ("Downloading…", "#0a6dc2"),
    SyncStatus.ENDING:   ("Uploading…",   "#0a6dc2"),
    SyncStatus.ERROR:    ("Error",        "#c2270a"),
}


class StatusWindow(WebViewWindow):
    TITLE = "Strata"
    SIZE = (440, 340)

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
<body>
  <div class="row" style="margin-bottom:18px; align-items:baseline;">
    <h1 style="flex:1;">Strata</h1>
    <span class="muted">{device}</span>
  </div>

  <div class="card">
    <div class="row">
      <span id="dot" style="font-size:11px; transition:color .3s;">●</span>
      <span id="status-label" style="font-size:14px; font-weight:600;">Loading…</span>
    </div>
    <div id="detail" class="muted"
         style="margin-top:4px; line-height:1.5; white-space:pre-line;"></div>
    <div id="progress-wrap" class="hidden" style="margin-top:10px;">
      <div class="progress-track">
        <div class="progress-fill progress-indeterminate"></div>
      </div>
    </div>
  </div>

  <div id="path" class="muted"
       style="font-size:10px; padding:0 2px; margin-bottom:10px;
              overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"></div>

  <div class="spacer"></div>

  <div class="row" style="gap:8px;">
    <button id="action-btn" class="btn-primary" style="flex:1;"
            onclick="post('toggle')">Start Session</button>
    <button id="folder-btn" class="btn-secondary" style="flex:0 0 110px;"
            onclick="post('open-folder')">Open Folder</button>
  </div>

<script>
__JS__

function updateUI(state) {{
  document.getElementById("dot").style.color          = state.dot_color;
  document.getElementById("status-label").textContent = state.status_label;
  document.getElementById("detail").textContent       = state.detail;
  document.getElementById("path").textContent         = state.path;

  document.getElementById("progress-wrap")
    .classList.toggle("hidden", !state.show_progress);

  const btn = document.getElementById("action-btn");
  btn.textContent = state.action_text;
  btn.disabled    = state.action_disabled;
  // Swap variant class while preserving flex sizing.
  btn.className   = "btn-" + state.action_variant;
  btn.style.flex  = "1";
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
        if action == "toggle":
            self.app._on_toggle_session(None)
        elif action == "open-folder":
            self.app._on_open_folder(None)

    # ── State ──────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        app = self.app
        status = app._current_status
        session_active = app._session_active
        is_active = status in (
            SyncStatus.SYNCING, SyncStatus.ENDING, SyncStatus.STARTING
        )

        if status == SyncStatus.IDLE:
            if session_active:
                dot_color = "#1c7a45"
                label = "Session Active"
                detail = (
                    "Files are checked out to this device.\n"
                    "End session to upload changes."
                )
                action_text, action_variant, action_disabled = (
                    "End Session", "danger", False
                )
            else:
                dot_color = "#0a6dc2"
                label = "No Active Session"
                detail = "Start a session to check out files."
                action_text, action_variant, action_disabled = (
                    "Start Session", "primary", False
                )
        else:
            label, dot_color = STATUS_DISPLAY[status]
            detail = app._status_message
            action_text, action_variant, action_disabled = (
                "Working…", "secondary", True
            )

        return {
            "dot_color":       dot_color,
            "status_label":    label,
            "detail":          detail,
            "show_progress":   is_active,
            "action_text":     action_text,
            "action_variant":  action_variant,
            "action_disabled": action_disabled,
            "path":            str(app.engine.sync_dir) if app.engine else "",
        }

    # ── Refresh tick ───────────────────────────────────────────────────────

    async def _tick(self):
        """Refresh on a 1-second cadence while the window is open."""
        try:
            while True:
                await asyncio.sleep(1.0)
                self.push_state()
        except asyncio.CancelledError:
            pass
