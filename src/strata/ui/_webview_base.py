"""
Shared infrastructure for WebView-based windows.

Provides:
  - SHARED_CSS: common styles every window pulls in
  - WebViewWindow: base class that handles the localhost bridge,
    Python→JS state pushing, and lifecycle.

Design pattern:
  Subclasses override:
    - TITLE, SIZE
    - build_html()  — returns full <!DOCTYPE html>… including SHARED_CSS
    - dispatch(action, payload)  — handles button presses from JS
    - get_state()  — returns dict to feed updateUI() (optional)
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import toga
from toga.style import Pack
from toga.style.pack import COLUMN


# Shared CSS — every window includes this. Keeps the visual language
# consistent across the app.
SHARED_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f7;
    color: #1d1d1f;
    padding: 20px;
    display: flex;
    flex-direction: column;
    user-select: none;
    font-size: 13px;
    line-height: 1.4;
  }

  h1 { font-size: 17px; font-weight: 600; margin-bottom: 4px; }
  h2 {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .04em;
    color: #6e6e73;
    margin: 20px 0 8px 0;
  }
  h2:first-child { margin-top: 0; }

  p { color: #555; }
  .muted { color: #888; font-size: 11px; }
  .warn  { color: #b07a00; }
  .error { color: #c2270a; }
  .ok    { color: #1c7a45; }

  .card {
    background: white;
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
    margin-bottom: 10px;
  }

  .row { display: flex; align-items: center; gap: 8px; }
  .spacer { flex: 1; }

  /* form fields */
  label.field-label {
    display: block;
    font-size: 11px;
    color: #555;
    margin: 10px 0 4px 0;
    font-weight: 500;
  }
  input[type=text], input[type=password] {
    width: 100%;
    padding: 7px 9px;
    border: 1px solid #d2d2d7;
    border-radius: 6px;
    font-size: 13px;
    font-family: inherit;
    background: white;
    transition: border-color .15s, box-shadow .15s;
  }
  input[type=text]:focus, input[type=password]:focus {
    outline: none;
    border-color: #0a6dc2;
    box-shadow: 0 0 0 3px rgba(10,109,194,.15);
  }

  /* switches (checkboxes styled as toggles) */
  .switch {
    display: flex; align-items: center; gap: 10px;
    padding: 6px 0; cursor: pointer;
  }
  .switch input { display: none; }
  .switch .track {
    width: 36px; height: 20px;
    background: #d2d2d7;
    border-radius: 999px;
    position: relative;
    transition: background .2s;
    flex-shrink: 0;
  }
  .switch .track::after {
    content: "";
    position: absolute;
    top: 2px; left: 2px;
    width: 16px; height: 16px;
    background: white;
    border-radius: 50%;
    transition: transform .2s;
    box-shadow: 0 1px 2px rgba(0,0,0,.2);
  }
  .switch input:checked + .track { background: #1c7a45; }
  .switch input:checked + .track::after { transform: translateX(16px); }

  /* buttons */
  button {
    padding: 8px 14px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: opacity .15s, background .2s;
  }
  button:active:not(:disabled) { opacity: .75; }
  button:disabled { opacity: .4; cursor: default; }

  .btn-primary { background: #0a6dc2; color: white; }
  .btn-primary:hover:not(:disabled) { background: #0958a3; }
  .btn-success { background: #1c7a45; color: white; }
  .btn-success:hover:not(:disabled) { background: #146238; }
  .btn-danger  { background: #c2270a; color: white; }
  .btn-danger:hover:not(:disabled)  { background: #a01f08; }
  .btn-secondary { background: #e5e5ea; color: #1d1d1f; }
  .btn-secondary:hover:not(:disabled) { background: #d1d1d6; }

  .actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 12px;
  }

  /* progress */
  .progress-track {
    height: 6px;
    background: #e5e5ea;
    border-radius: 3px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: #0a6dc2;
    border-radius: 3px;
    transition: width .2s;
  }
  .progress-indeterminate {
    width: 30%;
    animation: slide 1.2s infinite ease-in-out;
  }
  @keyframes slide {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(430%); }
  }

  .hidden { display: none !important; }

  /* tables */
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  thead th {
    text-align: left;
    padding: 8px 10px;
    background: #f0f0f3;
    color: #555;
    font-weight: 600;
    border-bottom: 1px solid #d2d2d7;
  }
  tbody td {
    padding: 7px 10px;
    border-bottom: 1px solid #ececef;
    color: #333;
  }
  tbody tr:hover { background: #f9f9fb; }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .03em;
  }
  .pill-modified { background: #fef3c7; color: #92400e; }
  .pill-added    { background: #dcfce7; color: #166534; }
  .pill-deleted  { background: #fee2e2; color: #991b1b; }

  .scroll {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }
  .scroll::-webkit-scrollbar { width: 8px; }
  .scroll::-webkit-scrollbar-thumb {
    background: #c7c7cc; border-radius: 4px;
  }

  .icon-large { font-size: 36px; line-height: 1; }
"""


# JS bridge — included in every window's HTML. Defines post() for
# JS→Python actions and a stub updateUI() for Python→JS state pushes
# that subclasses can override.
SHARED_JS = """
  const PORT = __PORT__;

  function post(action, payload) {
    return fetch("http://127.0.0.1:" + PORT + "/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, payload: payload || null})
    }).catch(function() {});
  }

  // Override in each window if dynamic state updates are needed.
  function updateUI(state) {}
"""


class WebViewWindow:
    """Base class for HTML/CSS-styled Toga windows."""

    TITLE: str = "Strata"
    SIZE: tuple[int, int] = (480, 360)
    RESIZABLE: bool = False

    def __init__(self, app):
        self.app = app
        self._ready = False
        self._server: HTTPServer | None = None
        self._port: int = 0

        # Capture the running asyncio loop for thread-safe scheduling.
        self._loop = asyncio.get_event_loop()

        self._start_bridge_server()

        self._webview = toga.WebView(
            style=Pack(flex=1),
            on_webview_load=self._on_load,
        )

        self.window = toga.Window(
            title=self.TITLE,
            size=self.SIZE,
            resizable=self.RESIZABLE,
            on_close=self._on_close,
        )
        outer = toga.Box(style=Pack(direction=COLUMN, flex=1))
        outer.add(self._webview)
        self.window.content = outer

        html = self.build_html().replace("__PORT__", str(self._port))
        self._webview.set_content("https://strata.local", html)
        self.window.show()

    # ── Subclass hooks ─────────────────────────────────────────────────────

    def build_html(self) -> str:
        """Return full HTML document. Use SHARED_CSS and SHARED_JS."""
        raise NotImplementedError

    def dispatch(self, action: str, payload):
        """Handle a button press / action from JS. Runs on the UI loop."""
        pass

    def get_state(self) -> dict | None:
        """Return state to push to updateUI(). None = no push needed."""
        return None

    # ── Bridge ─────────────────────────────────────────────────────────────

    def _start_bridge_server(self):
        window_ref = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/action":
                    length = int(self.headers.get("Content-Length", 0))
                    try:
                        body = json.loads(self.rfile.read(length))
                        action = body.get("action")
                        payload = body.get("payload")
                    except Exception:
                        action, payload = None, None

                    if action:
                        window_ref._loop.call_soon_threadsafe(
                            window_ref._safe_dispatch, action, payload
                        )

                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _safe_dispatch(self, action, payload):
        try:
            self.dispatch(action, payload)
        except Exception as e:
            print(f"[{self.TITLE}] dispatch error: {e}")

    def _on_load(self, webview):
        self._ready = True
        self.push_state()

    def push_state(self):
        """Push current get_state() to JS. Safe to call before ready."""
        if not self._ready:
            return
        state = self.get_state()
        if state is None:
            return
        try:
            js = f"updateUI({json.dumps(state)})"
            self._webview.evaluate_javascript(js)
        except Exception:
            pass

    def eval_js(self, js: str):
        """Run arbitrary JS. Used for one-off updates."""
        if not self._ready:
            return
        try:
            self._webview.evaluate_javascript(js)
        except Exception:
            pass

    def close(self):
        self.window.close()

    def _on_close(self, window):
        if self._server is not None:
            threading.Thread(target=self._server.shutdown, daemon=True).start()
            self._server = None
        self.on_closed()
        return True

    def on_closed(self):
        """Override for cleanup (clearing _instance, etc.)."""
        pass
