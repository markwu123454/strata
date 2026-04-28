"""
Confirm-quit window — small modal warning when user tries to quit with an
active session, since quitting leaves the lock held.
"""
from __future__ import annotations

from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>__CSS__</style></head>
<body style="padding:24px; align-items:center; text-align:center;">
  <div class="icon-large warn" style="margin-bottom:8px;">⚠</div>
  <h1 class="warn">Session still active</h1>
  <p style="margin-top:8px;">
    End your session before quitting<br>
    to avoid leaving the lock open.
  </p>

  <div class="spacer"></div>

  <div class="actions" style="width:100%;">
    <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
    <button class="btn-danger"    onclick="post('quit')">Quit Anyway</button>
  </div>

<script>__JS__</script>
</body></html>
""".replace("__CSS__", SHARED_CSS).replace("__JS__", SHARED_JS)


class ConfirmQuitWindow(WebViewWindow):
    TITLE = "Quit Strata"
    SIZE = (400, 240)

    def build_html(self) -> str:
        return _HTML

    def dispatch(self, action: str, payload):
        if action == "cancel":
            self.close()
        elif action == "quit":
            self.close()
            self.app._do_quit()

    @classmethod
    def open(cls, app):
        cls(app)
