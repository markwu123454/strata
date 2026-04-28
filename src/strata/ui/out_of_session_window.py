"""
Out-of-session changes window — shown in two scenarios:

  1. Start Session detected local files modified since the last session.
     User chooses Keep (upload local) or Discard (overwrite from R2).

  2. Quick Pull detected local files modified since the last session.
     User chooses Continue (overwrite local with remote) or Cancel.

Same window, two modes — the data is identical (a list of changed paths)
and rendering it once means visual consistency across both flows. The
button row swaps based on `mode`.
"""
from __future__ import annotations

import html as _html

from strata.core.engine import OutOfSessionChange
from strata.core.lock import LockInfo
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


STATUS_LABEL = {
    "modified": ("Modified", "pill-modified"),
    "added":    ("Added",    "pill-added"),
    "deleted":  ("Deleted",  "pill-deleted"),
}


class OutOfSessionWindow(WebViewWindow):
    TITLE = "Files Modified Outside Session"
    SIZE = (640, 580)
    RESIZABLE = True

    def __init__(
        self,
        app,
        profile_name: str,
        changes: list[OutOfSessionChange],
        mode: str = "start_session",
        lock_info: LockInfo | None = None,
    ):
        # mode is "start_session" or "quick_pull". Drives both the copy
        # and the action-button set. Anything else is treated as
        # start_session for backwards compat.
        self.profile_name = profile_name
        self.changes = changes
        self.mode = mode if mode in ("start_session", "quick_pull") else "start_session"
        # Only used in quick_pull mode: surface the existing lock holder so
        # the user knows they're pulling stale-but-consistent state, not the
        # in-flight session.
        self.lock_info = lock_info
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
        profile = _html.escape(self.profile_name)

        # Mode-specific copy and buttons.
        if self.mode == "quick_pull":
            heading = "⚠ Local Changes Will Be Overwritten"
            intro = (
                f"{count} file{'s' if count != 1 else ''} changed since your "
                f"last session ended. Quick Pull will replace them with the "
                f"version stored in R2."
            )
            footer = (
                "<strong>Continue</strong> overwrites local files · "
                "<strong>Cancel</strong> leaves everything as-is"
            )
            actions = """
              <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
              <button class="btn-danger"    onclick="post('proceed')">Continue Quick Pull</button>
            """
            # Note about a foreign session, if any.
            lock_block = ""
            if self.lock_info is not None:
                holder = _html.escape(self.lock_info.device_name)
                when = _html.escape(self.lock_info.acquired_at_str())
                lock_block = f"""
                  <p class="muted" style="margin-top:8px;">
                    Note: <strong>{holder}</strong> has a session open
                    (started {when}). You'll receive their last-uploaded
                    state, not their in-flight changes.
                  </p>
                """
        else:
            heading = "⚠ Files Modified Outside Session"
            intro = (
                f"{count} file{'s' if count != 1 else ''} changed since your "
                f"last session ended. Choose how to handle them before starting."
            )
            footer = (
                "<strong>Keep local</strong> uploads your changes · "
                "<strong>Discard local</strong> overwrites with R2 version"
            )
            actions = """
              <button class="btn-secondary" onclick="post('cancel')">Cancel</button>
              <button class="btn-danger"    onclick="post('discard')">Discard Local</button>
              <button class="btn-success"   onclick="post('keep')">Keep Local</button>
            """
            lock_block = ""

        body = f"""
<body>
  <h1 class="warn">{heading}</h1>
  <div class="muted" style="margin-top:2px;">Profile: {profile}</div>
  <p class="muted" style="margin: 8px 0 12px 0;">{intro}</p>
  {lock_block}

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

  <p class="muted" style="margin-top:10px;">{footer}</p>

  <div class="actions">
    {actions}
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
            if self.mode == "quick_pull":
                # Tell the app explicitly so it can clean up any pending
                # state. Currently a no-op but keeps the API symmetric.
                self.app.quick_pull_after_choice(self.profile_name, proceed=False)
            self.close()
        elif action == "discard":
            self.app.start_session_after_choice(
                self.profile_name, discard=True, changes=self.changes
            )
            self.close()
        elif action == "keep":
            self.app.start_session_after_choice(
                self.profile_name, discard=False, changes=self.changes
            )
            self.close()
        elif action == "proceed":
            self.app.quick_pull_after_choice(self.profile_name, proceed=True)
            self.close()

    @classmethod
    def open(
        cls,
        app,
        profile_name: str,
        changes: list[OutOfSessionChange],
        mode: str = "start_session",
        lock_info: LockInfo | None = None,
    ):
        cls(app, profile_name, changes, mode=mode, lock_info=lock_info)
