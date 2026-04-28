"""
Status window — opened from the tray icon. Shows current sync state and
provides primary action buttons.

Uses the shared WebViewWindow base so it inherits the same CSS, button
styles, and JS bridge as the rest of the app's windows.

A 1-second timer pushes get_state() into the page. The engine also pushes
state changes through callbacks, but the timer covers the case where the
user opens the window mid-syncing — they should see live progress
immediately.

Multi-profile update: reads from app._active_engine() and
app._active_profile_name() so it always reflects the currently-selected
profile without needing to be rebuilt on profile switch.
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
    SIZE = (460, 360)

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
      <span id="profile-badge" class="muted" style="font-size:11px; margin-left:4px;"></span>
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
    <button id="quick-pull-btn" class="btn-secondary" style="flex:0 0 100px;"
            onclick="post('quick-pull')">Quick Pull</button>
    <button id="folder-btn" class="btn-secondary" style="flex:0 0 100px;"
            onclick="post('open-folder')">Open Folder</button>
  </div>

<script>
__JS__

function updateUI(state) {{
  document.getElementById("dot").style.color          = state.dot_color;
  document.getElementById("status-label").textContent = state.status_label;
  document.getElementById("detail").textContent       = state.detail;
  document.getElementById("path").textContent         = state.path;
  document.getElementById("profile-badge").textContent =
    state.profile_name ? "(" + state.profile_name + ")" : "";

  document.getElementById("progress-wrap")
    .classList.toggle("hidden", !state.show_progress);

  const btn = document.getElementById("action-btn");
  btn.textContent = state.action_text;
  btn.disabled    = state.action_disabled;
  // Swap variant class while preserving flex sizing.
  btn.className   = "btn-" + state.action_variant;
  btn.style.flex  = "1";

  const qp = document.getElementById("quick-pull-btn");
  qp.disabled = state.quick_pull_disabled;
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
        elif action == "quick-pull":
            self.app._on_quick_pull(None)
        elif action == "open-folder":
            self.app._on_open_folder(None)

    # ── State ──────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        app = self.app
        status = app._current_status
        active_name = app._active_profile_name()
        session_active = active_name in app._active_sessions
        is_busy = status in (
            SyncStatus.SYNCING, SyncStatus.ENDING, SyncStatus.STARTING
        )

        engine = app._active_engine()

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

        # Quick Pull is available when: not busy, not in a session, engine ready.
        quick_pull_disabled = (
            is_busy
            or session_active
            or app._operation_in_progress
            or engine is None
        )

        return {
            "dot_color":           dot_color,
            "status_label":        label,
            "detail":              detail,
            "show_progress":       is_busy,
            "action_text":         action_text,
            "action_variant":      action_variant,
            "action_disabled":     action_disabled,
            "quick_pull_disabled": quick_pull_disabled,
            "path":                str(engine.sync_dir) if engine else "",
            "profile_name":        active_name,
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
