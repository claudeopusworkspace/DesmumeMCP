"""HLS video streamer — pipes DS frames + audio through ffmpeg to serve live HLS."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .emulator import EmulatorState

logger = logging.getLogger(__name__)

# DS constants
_FRAME_WIDTH = 256
_FRAME_HEIGHT = 384  # both screens stacked
_FRAME_RGB_SIZE = _FRAME_WIDTH * _FRAME_HEIGHT * 3  # 294912 bytes
_SAMPLE_RATE = 44100
_FPS = 60
_SAMPLES_PER_FRAME = _SAMPLE_RATE // _FPS  # 735
_MAX_BUFFER_SECS = 30.0  # max seconds content can lead wall-clock before throttling

_HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DeSmuME Stream</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #111;
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
video {
    image-rendering: pixelated;
    border: 2px solid #333;
    border-radius: 4px;
    width: 512px;
    height: 768px;
    background: #000;
}
h1 {
    font-size: 16px;
    font-weight: normal;
    color: #666;
    letter-spacing: 2px;
    text-transform: uppercase;
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
    margin-right: 6px;
    vertical-align: middle;
}
.dot.buffering { background: #ff9800; }
.dot.playing   { background: #4caf50; }
.dot.error     { background: #f44336; }
.dot.waiting   { background: #666; }
#unmute-btn {
    padding: 6px 16px;
    border: 1px solid #555;
    border-radius: 4px;
    background: #2e7d32;
    color: #fff;
    font-family: inherit;
    font-size: 13px;
    cursor: pointer;
    letter-spacing: 1px;
}
#unmute-btn:hover { background: #388e3c; }
#unmute-btn.muted { background: #c62828; }
#volume-slider {
    width: 80px;
    vertical-align: middle;
    cursor: pointer;
    accent-color: #4caf50;
}
#vol-label { font-size: 12px; color: #888; }
</style>
</head>
<body>
<div id="container">
    <h1>DeSmuME Stream</h1>
    <video id="player" muted autoplay></video>
    <div id="status-bar">
        <span><span id="dot" class="dot waiting"></span><span id="status">Waiting for stream\u2026</span></span>
        <button id="unmute-btn" class="muted">UNMUTE</button>
        <input id="volume-slider" type="range" min="0" max="100" value="50">
        <span id="vol-label">50%</span>
        <span>Buffer: <span id="buffer-info">\u2014</span></span>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<script>
(function() {
    var video    = document.getElementById('player');
    var dot      = document.getElementById('dot');
    var status   = document.getElementById('status');
    var bufInfo  = document.getElementById('buffer-info');
    var muteBtn  = document.getElementById('unmute-btn');
    var volSlider = document.getElementById('volume-slider');
    var volLabel  = document.getElementById('vol-label');

    video.volume = 0.5;
    var lastFragTime = 0;   // timestamp of last new fragment
    var paused = false;      // true when we've paused due to no new content

    muteBtn.addEventListener('click', function() {
        video.muted = !video.muted;
        muteBtn.textContent = video.muted ? 'UNMUTE' : 'MUTE';
        muteBtn.className = video.muted ? 'muted' : '';
        muteBtn.id = 'unmute-btn';
    });

    volSlider.addEventListener('input', function() {
        video.volume = volSlider.value / 100;
        volLabel.textContent = volSlider.value + '%';
    });

    function updateBufferInfo() {
        if (video.buffered.length > 0) {
            var buffered = video.buffered.end(video.buffered.length - 1);
            var behind = buffered - video.currentTime;
            bufInfo.textContent = behind.toFixed(1) + 's';
        }
        requestAnimationFrame(updateBufferInfo);
    }
    updateBufferInfo();

    function setStatus(cls, text) {
        dot.className = 'dot ' + cls;
        status.textContent = text;
    }

    var playlistUrl = '/hls/stream.m3u8';
    var retryTimer = null;

    function tryLoad() {
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }

        if (Hls.isSupported()) {
            var hls = new Hls({
                liveSyncDurationCount: 3,
                liveMaxLatencyDurationCount: 20,
                enableWorker: true,
                lowLatencyMode: true,
            });

            hls.on(Hls.Events.MEDIA_ATTACHED, function() {
                hls.loadSource(playlistUrl);
            });

            hls.on(Hls.Events.MANIFEST_PARSED, function() {
                setStatus('buffering', 'Buffering\u2026');
                video.play().catch(function() {});
            });

            hls.on(Hls.Events.FRAG_BUFFERED, function() {
                lastFragTime = Date.now();
                if (paused) {
                    paused = false;
                    video.play().catch(function() {});
                }
                setStatus('playing', 'Playing');
            });

            hls.on(Hls.Events.ERROR, function(event, data) {
                if (data.fatal) {
                    hls.destroy();
                    setStatus('error', 'Stream interrupted \u2014 retrying\u2026');
                    retryTimer = setTimeout(tryLoad, 3000);
                }
            });

            // When the player stalls at the end of buffered content and
            // no new fragments have arrived recently, pause instead of
            // letting hls.js loop the live window.
            video.addEventListener('waiting', function() {
                if (lastFragTime && (Date.now() - lastFragTime) > 3000) {
                    paused = true;
                    video.pause();
                    setStatus('buffering', 'Waiting for frames\u2026');
                }
            });

            hls.attachMedia(video);
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            // Safari native HLS
            video.src = playlistUrl;
            video.addEventListener('loadedmetadata', function() {
                setStatus('playing', 'Playing');
                video.play().catch(function() {});
            });
        } else {
            setStatus('error', 'HLS not supported in this browser');
        }
    }

    // Poll for playlist availability before starting
    function waitForStream() {
        setStatus('waiting', 'Waiting for stream\u2026');
        fetch(playlistUrl, {method: 'HEAD'}).then(function(r) {
            if (r.ok) { tryLoad(); }
            else { setTimeout(waitForStream, 1000); }
        }).catch(function() {
            setTimeout(waitForStream, 1000);
        });
    }
    waitForStream();
})();
</script>
</body>
</html>
"""


class _StreamHandler(BaseHTTPRequestHandler):
    """Serves the HLS player page and proxies HLS segment files."""

    def log_message(self, format, *args):
        pass  # silence per-request logs

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._serve_html()
        elif path.startswith("/hls/"):
            self._serve_hls_file(path[5:])  # strip /hls/ prefix
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if path.startswith("/hls/"):
            self._serve_hls_file(path[5:], head_only=True)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_html(self):
        body = _HTML_PAGE.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_hls_file(self, filename: str, head_only: bool = False):
        streamer: HLSStreamer = self.server.streamer  # type: ignore[attr-defined]
        file_path = streamer.hls_dir / filename
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if filename.endswith(".m3u8"):
            content_type = "application/vnd.apple.mpegurl"
            cache = "no-cache, no-store"
        elif filename.endswith(".ts"):
            content_type = "video/mp2t"
            cache = "public, max-age=300"
        elif filename.endswith(".m4s") or filename.endswith(".mp4"):
            content_type = "video/mp4"
            cache = "public, max-age=300"
        else:
            content_type = "application/octet-stream"
            cache = "no-cache"

        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)


class HLSStreamer:
    """Streams DS video + audio via ffmpeg → HLS for browser playback.

    Usage::

        streamer = HLSStreamer(holder, port=8091)
        streamer.start()           # launches ffmpeg + HTTP server
        # ... emulation happens, on_cycle callback feeds frames to ffmpeg ...
        streamer.stop()
    """

    def __init__(self, holder: EmulatorState, port: int = 8091):
        self._holder = holder
        self._port = port
        self._hls_dir = Path(tempfile.mkdtemp(prefix="desmume_hls_"))
        self._video_fifo = self._hls_dir / "video.pipe"
        self._audio_fifo = self._hls_dir / "audio.pipe"
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._video_writer: threading.Thread | None = None
        self._audio_writer: threading.Thread | None = None
        self._video_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=300)
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=300)
        self._running = False
        # Audio normalization buffer — accumulates raw PCM and emits
        # exactly _SAMPLES_PER_FRAME samples per cycle to keep ffmpeg's
        # audio stream perfectly aligned with the video frame rate.
        self._audio_buf = bytearray()
        # Real-time rate limiter — prevents content from being produced
        # faster than 1x, which would cause hls.js to skip to live edge.
        self._rt_origin: float | None = None  # wall-clock time of first frame
        self._rt_frames: int = 0  # frames emitted since origin

    @property
    def port(self) -> int:
        return self._port

    @property
    def hls_dir(self) -> Path:
        return self._hls_dir

    def start(self) -> None:
        """Start ffmpeg pipeline and HTTP server."""
        if self._running:
            return

        self._running = True

        # Enable audio capture in the C library
        emu = self._holder._require_rom()
        emu.audio_enable_capture()

        # Create named pipes for ffmpeg input
        os.mkfifo(str(self._video_fifo))
        os.mkfifo(str(self._audio_fifo))

        # Start ffmpeg
        self._start_ffmpeg()

        # Start FIFO writer threads (must happen after ffmpeg starts
        # since open() on a FIFO blocks until the other end opens)
        self._video_writer = threading.Thread(
            target=self._write_fifo,
            args=(self._video_fifo, self._video_queue, "video"),
            daemon=True,
        )
        self._audio_writer = threading.Thread(
            target=self._write_fifo,
            args=(self._audio_fifo, self._audio_queue, "audio"),
            daemon=True,
        )
        self._video_writer.start()
        self._audio_writer.start()

        # Start HTTP server
        srv = ThreadingHTTPServer(("0.0.0.0", self._port), _StreamHandler)
        srv.streamer = self  # type: ignore[attr-defined]
        srv.daemon_threads = True
        self._http_server = srv
        self._http_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self._http_thread.start()

        # Register per-cycle callback
        self._holder.on_each_cycle(self._on_cycle)

        logger.info(
            "HLS streamer started on http://0.0.0.0:%d (hls dir: %s)",
            self._port,
            self._hls_dir,
        )

    def _start_ffmpeg(self) -> None:
        """Launch the ffmpeg process reading from the two FIFOs."""
        cmd = [
            "ffmpeg",
            "-y",
            # Video input: raw RGB frames from FIFO
            "-f", "rawvideo",
            "-pixel_format", "rgb24",
            "-video_size", f"{_FRAME_WIDTH}x{_FRAME_HEIGHT}",
            "-framerate", str(_FPS),
            "-i", str(self._video_fifo),
            # Audio input: raw s16le stereo PCM from FIFO
            "-f", "s16le",
            "-ar", str(_SAMPLE_RATE),
            "-ac", "2",
            "-i", str(self._audio_fifo),
            # Video encoding
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", str(_FPS * 2),  # keyframe every 2 seconds
            # Audio encoding
            "-c:a", "aac",
            "-b:a", "128k",
            # HLS output — use fMP4 segments for sample-accurate audio
            # timing (MPEG-TS loses ~23ms per segment at AAC frame boundaries)
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "20",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", str(self._hls_dir / "segment_%05d.m4s"),
            str(self._hls_dir / "stream.m3u8"),
        ]

        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        logger.info("ffmpeg started (pid %d)", self._ffmpeg_proc.pid)

    def _write_fifo(
        self, fifo_path: Path, q: queue.Queue[bytes | None], name: str
    ) -> None:
        """Writer thread: drains queue and writes to a named pipe."""
        try:
            with open(fifo_path, "wb") as f:
                while self._running:
                    try:
                        data = q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if data is None:
                        break
                    try:
                        f.write(data)
                    except BrokenPipeError:
                        logger.warning("%s pipe broken", name)
                        break
        except OSError as e:
            if self._running:
                logger.error("Error opening %s fifo: %s", name, e)

    def _on_cycle(self) -> None:
        """Called after each emulator cycle — push frame + audio to ffmpeg."""
        if not self._running:
            return

        emu = self._holder.emu
        if emu is None:
            return

        # Grab raw RGB frame
        raw_rgb = emu.screenshot()

        # Drain audio samples generated by this cycle into the normalization buffer
        audio_data = emu.audio_read()
        if audio_data:
            self._audio_buf.extend(audio_data)

        # Emit exactly _SAMPLES_PER_FRAME stereo samples (4 bytes each) to
        # keep the audio stream perfectly aligned with the video frame rate.
        # 44100 / 60 = 735.0 exactly, so no fractional accumulation needed.
        needed = _SAMPLES_PER_FRAME * 4  # 735 samples * 4 bytes (s16le stereo)
        if len(self._audio_buf) >= needed:
            normalized = bytes(self._audio_buf[:needed])
            del self._audio_buf[:needed]
        else:
            # Pad with silence if SPU produced fewer samples than expected
            normalized = bytes(self._audio_buf) + b"\x00" * (needed - len(self._audio_buf))
            self._audio_buf.clear()

        # Buffer-aware throttle: run full speed until we're _MAX_BUFFER_SECS
        # ahead of wall-clock, then sleep to maintain that lead.  This lets
        # the emulator build a comfortable buffer during LLM think time while
        # preventing hls.js from skipping to the live edge.
        now = time.monotonic()
        if self._rt_origin is None:
            self._rt_origin = now
            self._rt_frames = 0
        else:
            self._rt_frames += 1
            content_secs = self._rt_frames / _FPS
            wall_secs = now - self._rt_origin
            ahead = content_secs - wall_secs
            if ahead > _MAX_BUFFER_SECS:
                time.sleep(ahead - _MAX_BUFFER_SECS)

        # Push to writer queues — block if full so emulation runs at
        # encoding speed (backpressure).  Timeout prevents hanging if
        # ffmpeg dies or the streamer is shutting down.
        try:
            self._video_queue.put(raw_rgb, timeout=2.0)
        except queue.Full:
            pass

        try:
            self._audio_queue.put(normalized, timeout=2.0)
        except queue.Full:
            pass

    def stop(self) -> None:
        """Shut down ffmpeg and HTTP server."""
        if not self._running:
            return

        self._running = False
        self._rt_origin = None
        self._rt_frames = 0
        self._holder.remove_cycle_callback(self._on_cycle)

        # Disable audio capture
        try:
            if self._holder.emu is not None:
                self._holder.emu.audio_disable_capture()
        except Exception:
            pass

        # Signal writer threads to exit
        try:
            self._video_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass

        # Terminate ffmpeg
        if self._ffmpeg_proc is not None:
            self._ffmpeg_proc.terminate()
            try:
                self._ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._ffmpeg_proc.kill()
            self._ffmpeg_proc = None

        # Stop HTTP server
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server = None
            self._http_thread = None

        # Clean up temp files
        try:
            shutil.rmtree(self._hls_dir, ignore_errors=True)
        except Exception:
            pass

        logger.info("HLS streamer stopped")
