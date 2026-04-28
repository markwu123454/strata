"""
Update window — shown when the user clicks the "Update to X.Y.Z" tray item.

Two stages:
  1. Show release notes + Download button
  2. After Download is clicked: show progress bar, on completion launch
     installer and quit the app

The download runs on a background thread; progress is pushed into the
WebView via evaluate_javascript on the UI loop.
"""
from __future__ import annotations

import html as _html
import json
import threading

from strata.config import APP_VERSION
from strata.core.updater import (
    UpdateInfo,
    download_installer,
    launch_installer_and_quit,
)
from strata.ui._webview_base import WebViewWindow, SHARED_CSS, SHARED_JS


class UpdateWindow(WebViewWindow):
    TITLE = "Strata Update"
    SIZE = (540, 480)

    def __init__(self, app, info: UpdateInfo):
        self.info = info
        super().__init__(app)

    def build_html(self) -> str:
        notes = self.info.notes.strip() or "(No release notes)"
        body = f"""
<body>
  <h1>A new version is available</h1>
  <p class="muted">
    {_html.escape(APP_VERSION)} &nbsp;→&nbsp;
    <strong style="color:#1d1d1f;">{_html.escape(self.info.version)}</strong>
  </p>

  <div class="card scroll" style="margin-top:16px; flex:1; white-space:pre-wrap; font-size:12px; line-height:1.5; color:#333;">{_html.escape(notes)}</div>

  <div id="progress-block" class="hidden" style="margin-top:8px;">
    <div class="progress-track"><div id="progress-fill" class="progress-fill" style="width:0%"></div></div>
    <div id="progress-label" class="muted" style="margin-top:6px;"></div>
  </div>

  <div class="actions">
    <button id="later-btn"    class="btn-secondary" onclick="post('later')">Later</button>
    <button id="download-btn" class="btn-primary"   onclick="post('download')">Download &amp; Install</button>
  </div>

<script>
__JS__

function setProgress(percent, label) {{
  document.getElementById("progress-block").classList.remove("hidden");
  document.getElementById("progress-fill").style.width = percent + "%";
  document.getElementById("progress-label").textContent = label;
}}

function setProgressError(label) {{
  const el = document.getElementById("progress-label");
  el.textContent = label;
  el.classList.remove("muted");
  el.classList.add("error");
}}

function setDownloadingState(downloading) {{
  const dl = document.getElementById("download-btn");
  const lt = document.getElementById("later-btn");
  if (downloading) {{
    dl.textContent = "Downloading…";
    dl.disabled = true;
    lt.disabled = true;
  }} else {{
    dl.textContent = "Retry";
    dl.disabled = false;
    lt.disabled = false;
  }}
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

    def dispatch(self, action, payload):
        if action == "later":
            self.close()
        elif action == "download":
            self._start_download()

    # ── Download flow ──────────────────────────────────────────────────────

    def _start_download(self):
        self.eval_js("setDownloadingState(true)")
        self.eval_js("setProgress(0, 'Starting download…')")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        def on_progress(downloaded: int, total: int):
            self._loop.call_soon_threadsafe(self._update_progress, downloaded, total)

        path = download_installer(self.info, on_progress=on_progress)
        self._loop.call_soon_threadsafe(self._download_done, path)

    def _update_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = (downloaded / total) * 100
            mb_d = downloaded / (1024 * 1024)
            mb_t = total / (1024 * 1024)
            label = f"{mb_d:.1f} / {mb_t:.1f} MB"
        else:
            pct = 0
            mb_d = downloaded / (1024 * 1024)
            label = f"{mb_d:.1f} MB"
        self.eval_js(f"setProgress({pct}, {json.dumps(label)})")

    def _download_done(self, path):
        if path is None:
            self.eval_js(
                f"setProgressError({json.dumps('Download failed. Check connection and retry.')});"
                f"setDownloadingState(false);"
            )
            return

        self.eval_js(f"setProgress(100, {json.dumps('Launching installer…')})")
        ok = launch_installer_and_quit(path, self.app._do_quit)
        if not ok:
            self.eval_js(
                f"setProgressError({json.dumps('Could not launch installer.')});"
                f"setDownloadingState(false);"
            )

    @classmethod
    def open(cls, app, info: UpdateInfo):
        cls(app, info)
