"""
Confirm-quit window — small modal warning when user tries to quit with one
or more active sessions, since quitting leaves those locks held.

Now receives the list of profiles with active sessions so it can name them
explicitly rather than showing a generic "session still active" message.
"""
from __future__ import annotations

import html as _html

from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


class ConfirmQuitWindow(WebViewWindow):
    TITLE = "Quit Strata"
    SIZE = (400, 280)

    def __init__(self, app, active_profiles: list[str]):
        self.active_profiles = active_profiles
        super().__init__(app)

    def build_html(self) -> str:
        count = len(self.active_profiles)
        if count == 1:
            profile_text = f"<strong>{_html.escape(self.active_profiles[0])}</strong>"
            noun = "session"
        else:
            names = ", ".join(
                f"<strong>{_html.escape(n)}</strong>" for n in self.active_profiles
            )
            profile_text = names
            noun = "sessions"

        body = f"""
<body style="padding:24px; align-items:center; text-align:center;">
  <div class="icon-large warn" style="margin-bottom:8px;">⚠</div>
  <h1 class="warn">{count} active {noun}</h1>
  <p style="margin-top:8px;">
    {profile_text}<br>
    {'has' if count == 1 else 'have'} an active session.
    Quitting will leave {'the lock' if count == 1 else 'those locks'} open.
  </p>
  <p class="muted" style="margin-top:6px;">
    End {'the session' if count == 1 else 'each session'} first,
    or quit anyway and re-take on next launch.
  </p>

  <div class="spacer"></div>

  <div class="actions" style="width:100%;">
    <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
    <button class="btn-danger"    onclick="post('quit')">Quit Anyway</button>
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

    def dispatch(self, action: str, payload):
        if action == "cancel":
            self.close()
        elif action == "quit":
            self.close()
            self.app._do_quit()

    @classmethod
    def open(cls, app, active_profiles: list[str]):
        cls(app, active_profiles)
