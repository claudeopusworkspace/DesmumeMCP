"""Lightweight web viewer — streams DS screenshots via Server-Sent Events."""

from __future__ import annotations

import json
import logging
import queue
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .emulator import EmulatorState

logger = logging.getLogger(__name__)

_HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DeSmuME Viewer</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #1a1a2e;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    font-family: 'Courier New', monospace;
    color: #e0e0e0;
}
#container {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
}
#screen {
    image-rendering: pixelated;
    border: 2px solid #333;
    border-radius: 4px;
    width: 512px;
    height: 768px;
    background: #000;
}
#divider {
    position: relative;
    width: 512px;
    margin-top: -12px;
    margin-bottom: -12px;
    z-index: 1;
}
#divider hr {
    border: none;
    border-top: 1px dashed #444;
}
#status-bar {
    display: flex;
    gap: 24px;
    font-size: 14px;
    color: #888;
}
.dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #666;
    margin-right: 6px;
    vertical-align: middle;
}
.dot.connected    { background: #4caf50; }
.dot.disconnected { background: #f44336; }
h1 {
    font-size: 16px;
    font-weight: normal;
    color: #666;
    letter-spacing: 2px;
    text-transform: uppercase;
}
</style>
</head>
<body>
<div id="container">
    <h1>DeSmuME Viewer</h1>
    <img id="screen" alt="DS Screen" src="/screenshot?t=0">
    <div id="divider"><hr></div>
    <div id="status-bar">
        <span><span id="dot" class="dot disconnected"></span><span id="status-text">Connecting\u2026</span></span>
        <span>Frame: <span id="frame-count">\u2014</span></span>
    </div>
</div>
<script>
(function() {
    var screen    = document.getElementById('screen');
    var dot       = document.getElementById('dot');
    var statusTxt = document.getElementById('status-text');
    var frameTxt  = document.getElementById('frame-count');

    function update(frame) {
        screen.src    = '/screenshot?t=' + frame;
        frameTxt.textContent = frame;
    }

    function connect() {
        var es = new EventSource('/stream');

        es.onopen = function() {
            dot.className   = 'dot connected';
            statusTxt.textContent = 'Connected';
        };

        es.addEventListener('frame', function(e) {
            update(JSON.parse(e.data).frame);
        });

        es.addEventListener('init', function(e) {
            update(JSON.parse(e.data).frame);
        });

        es.onerror = function() {
            dot.className   = 'dot disconnected';
            statusTxt.textContent = 'Reconnecting\u2026';
            es.close();
            setTimeout(connect, 2000);
        };
    }

    connect();
})();
</script>
</body>
</html>
"""


class _ViewerHandler(BaseHTTPRequestHandler):
    """Serves the viewer page, current screenshot, and SSE stream."""

    # Silence per-request log lines
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._serve_html()
        elif path == "/screenshot":
            self._serve_screenshot()
        elif path == "/stream":
            self._serve_sse()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # -- endpoints ---------------------------------------------------------

    def _serve_html(self):
        body = _HTML_PAGE.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_screenshot(self):
        viewer: ViewerServer = self.server.viewer  # type: ignore[attr-defined]
        data = viewer.get_current_screenshot()
        if data is None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _serve_sse(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        viewer: ViewerServer = self.server.viewer  # type: ignore[attr-defined]
        q: queue.Queue[str] = queue.Queue()
        viewer._register_client(q)

        try:
            # Send current frame immediately so the page is up-to-date
            frame = viewer.get_current_frame()
            self._sse_write("init", json.dumps({"frame": frame}))

            while True:
                try:
                    event_data = q.get(timeout=30)
                    self._sse_write("frame", event_data)
                except queue.Empty:
                    # keepalive comment prevents proxy/browser timeouts
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            viewer._unregister_client(q)

    def _sse_write(self, event: str, data: str):
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()


# -- Public API ------------------------------------------------------------


class ViewerServer:
    """Streams DS screenshots to a browser via SSE.

    Usage::

        viewer = ViewerServer(holder, port=8090)
        viewer.start()          # background thread
        viewer.notify()         # call after frame changes
        viewer.stop()
    """

    def __init__(self, holder: EmulatorState, port: int = 8090):
        self._holder = holder
        self._port = port
        self._clients: list[queue.Queue[str]] = []
        self._clients_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._current_screenshot: bytes | None = None
        self._screenshot_lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._port

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Start serving in a daemon thread."""
        if self._thread is not None:
            return
        srv = ThreadingHTTPServer(("0.0.0.0", self._port), _ViewerHandler)
        srv.viewer = self  # type: ignore[attr-defined]
        srv.daemon_threads = True
        self._server = srv
        self._thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Viewer started on http://0.0.0.0:%d", self._port)

    def stop(self):
        """Shut down the server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self._thread = None
            logger.info("Viewer stopped")

    # -- frame notification ------------------------------------------------

    def notify(self):
        """Capture a fresh screenshot and push an SSE event to all clients."""
        # Grab screenshot (while caller already holds the emulator lock)
        try:
            _, data = self._holder.capture_screenshot("both", "png")
            with self._screenshot_lock:
                self._current_screenshot = data
        except Exception:
            return

        frame = self._holder.frame_count
        event_data = json.dumps({"frame": frame})

        with self._clients_lock:
            for q in self._clients:
                # Drop stale events so a slow client doesn't queue up
                while not q.empty():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
                q.put(event_data)

    # -- helpers used by handler -------------------------------------------

    def get_current_screenshot(self) -> bytes | None:
        with self._screenshot_lock:
            if self._current_screenshot is not None:
                return self._current_screenshot
        # No cached screenshot — try to capture one now
        try:
            with self._holder.lock:
                _, data = self._holder.capture_screenshot("both", "png")
            with self._screenshot_lock:
                self._current_screenshot = data
            return data
        except Exception:
            return None

    def get_current_frame(self) -> int:
        return self._holder.frame_count

    def _register_client(self, q: queue.Queue[str]):
        with self._clients_lock:
            self._clients.append(q)
        logger.info("Viewer client connected (%d total)", len(self._clients))

    def _unregister_client(self, q: queue.Queue[str]):
        with self._clients_lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass
        logger.info("Viewer client disconnected (%d remaining)", len(self._clients))
