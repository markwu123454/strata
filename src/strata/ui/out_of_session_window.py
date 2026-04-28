"""
Out-of-session changes window — shown when Start Session detects local files
changed outside a session.

Replaces the old Toga Table with a styled HTML table — looks much better
and we control row hover, status pills, etc.
"""
from __future__ import annotations

import html as _html

from strata.core.engine import OutOfSessionChange
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


STATUS_LABEL = {
    "modified": ("Modified", "pill-modified"),
    "added":    ("Added",    "pill-added"),
    "deleted":  ("Deleted",  "pill-deleted"),
}


class OutOfSessionWindow(WebViewWindow):
    TITLE = "Files Modified Outside Session"
    SIZE = (640, 540)
    RESIZABLE = True

    def __init__(self, app, changes: list[OutOfSessionChange]):
        self.changes = changes
        super().__init__(app)

    def build_html(self) -> str:
        rows_html = []
        for c in self.changes:
            label, pill_class = STATUS_LABEL.get(c.status, (c.status, ""))
            rows_html.append(
                f"<tr>"
                f"<td><span class='pill {pill_class}'>{_html.escape(label)}</span></td>"
                f"<td>{_html.escape(c.path)}</td>"
                f"</tr>"
            )
        rows = "\n".join(rows_html)
        count = len(self.changes)

        body = f"""
<body>
  <h1 class="warn">⚠ Files Modified Outside Session</h1>
  <p class="muted" style="margin-bottom:12px;">
    {count} file{'s' if count != 1 else ''} changed since your last session ended.
    Choose how to handle them before starting.
  </p>

  <div class="scroll card" style="padding:0;">
    <table>
      <thead>
        <tr><th style="width:120px;">Change</th><th>Path</th></tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>

  <p class="muted" style="margin-top:10px;">
    <strong>Keep local</strong> uploads your changes ·
    <strong>Discard local</strong> overwrites with R2 version
  </p>

  <div class="actions">
    <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
    <button class="btn-danger"    onclick="post('discard')">Discard Local</button>
    <button class="btn-success"   onclick="post('keep')">Keep Local</button>
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
        elif action == "discard":
            self.app.start_session_after_choice(discard=True, changes=self.changes)
            self.close()
        elif action == "keep":
            self.app.start_session_after_choice(discard=False, changes=self.changes)
            self.close()

    @classmethod
    def open(cls, app, changes: list[OutOfSessionChange]):
        cls(app, changes)
