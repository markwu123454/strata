"""
Lock conflict window — shown when Start Session fails because another device
already holds the lock. User can wait or force-take.
"""
from __future__ import annotations

import html as _html

from strata.core.lock import LockInfo
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


class LockConflictWindow(WebViewWindow):
    TITLE = "Session In Use"
    SIZE = (460, 320)

    def __init__(self, app, profile_name: str, lock_info: LockInfo):
        # profile_name is which profile this lock belongs to. Carried so
        # force_take_session can target the right engine — relevant now
        # that the app holds multiple engines.
        self.profile_name = profile_name
        self.lock_info = lock_info
        super().__init__(app)

    def build_html(self) -> str:
        info = self.lock_info
        device = _html.escape(info.device_name)
        when = _html.escape(info.acquired_at_str())
        age = info.age_minutes()
        profile = _html.escape(self.profile_name)

        body = f"""
<body style="padding:24px; align-items:center; text-align:center;">
  <div class="icon-large warn" style="margin-bottom:8px;">🔒</div>
  <h1 class="warn">Session Active on Another Device</h1>
  <div class="muted" style="margin-top:2px;">Profile: {profile}</div>

  <div class="card" style="width:100%; margin-top:14px; text-align:left;">
    <div style="font-size:14px; font-weight:600;">{device}</div>
    <div class="muted" style="margin-top:4px;">
      started {when} · {age:.0f} min ago
    </div>
  </div>

  <p class="muted" style="margin-top:8px;">
    End the session on that device,<br>
    or force-take if it crashed.
  </p>

  <div class="spacer"></div>

  <div class="actions" style="width:100%;">
    <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
    <button class="btn-danger"    onclick="post('force')">Force Take</button>
  </div>

<script>__JS__</script>
</body>
"""
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{SHARED_CSS}</style></head>"
            + body.replace("__JS__", SHARED_JS)
            + "</html>"
        )

    def dispatch(self, action, payload):
        if action == "cancel":
            self.close()
        elif action == "force":
            self.app.force_take_session(self.profile_name)
            self.close()

    @classmethod
    def open(cls, app, profile_name: str, lock_info: LockInfo):
        cls(app, profile_name, lock_info)
