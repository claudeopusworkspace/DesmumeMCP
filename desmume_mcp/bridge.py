"""Lightweight IPC bridge — exposes the running EmulatorState to external scripts.

Runs a Unix domain socket server in a background thread. Scripts connect and
send line-delimited JSON requests, receiving JSON responses. This lets custom
scripts call advance_frames(), read_memory(), etc. on the *same* emulator the
MCP server is driving, with no savestate handoff overhead.

Protocol (line-delimited JSON over Unix domain socket):
  Request:  {"method": "advance_frames", "params": {"count": 16, "buttons": ["right"]}}\n
  Response: {"result": {"frames_advanced": 16, "total_frame": 1234}}\n
  Error:    {"error": "No ROM loaded."}\n
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable

from .constants import buttons_to_bitmask

# Maximum request size (64 KB should be plenty for any single call)
MAX_REQUEST_SIZE = 65536


class BridgeServer:
    """Unix socket server that dispatches JSON-RPC-like calls to EmulatorState."""

    def __init__(self, holder, socket_path: str) -> None:
        from .emulator import EmulatorState

        self._holder: EmulatorState = holder
        self._socket_path = socket_path
        self._lock = threading.Lock()
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._methods: dict[str, Callable[..., Any]] = self._build_dispatch()

    def _build_dispatch(self) -> dict[str, Callable[..., Any]]:
        """Map method names to handler functions."""
        return {
            "advance_frames": self._advance_frames,
            "advance_frame": self._advance_frame,
            "press_buttons": self._press_buttons,
            "tap_touch_screen": self._tap_touch_screen,
            "get_screenshot": self._get_screenshot,
            "read_memory": self._read_memory,
            "read_memory_range": self._read_memory_range,
            "write_memory": self._write_memory,
            "input_keypad_update": self._input_keypad_update,
            "cycle": self._cycle,
            "save_state": self._save_state,
            "load_state": self._load_state,
            "get_status": self._get_status,
            "get_frame_count": self._get_frame_count,
        }

    # ── Method handlers ──

    def _advance_frames(self, count: int = 1, buttons: list[str] | None = None,
                        touch_x: int | None = None, touch_y: int | None = None) -> dict:
        advanced = self._holder.advance_frames(count, buttons, touch_x, touch_y)
        return {"frames_advanced": advanced, "total_frame": self._holder.frame_count}

    def _advance_frame(self, buttons: list[str] | None = None,
                       touch_x: int | None = None, touch_y: int | None = None) -> dict:
        self._holder.advance_frame(buttons, touch_x, touch_y)
        return {"total_frame": self._holder.frame_count}

    def _press_buttons(self, buttons: list[str], frames: int = 1) -> dict:
        self._holder.press_buttons(buttons, frames)
        return {"total_frame": self._holder.frame_count}

    def _tap_touch_screen(self, x: int, y: int, frames: int = 1) -> dict:
        self._holder.tap_touch_screen(x, y, frames)
        return {"total_frame": self._holder.frame_count}

    def _get_screenshot(self, screen: str = "both", fmt: str = "png") -> dict:
        import base64
        mime, data = self._holder.capture_screenshot(screen, fmt)
        return {"mime": mime, "data_b64": base64.b64encode(data).decode("ascii"), "size": len(data)}

    def _read_memory(self, address: int, size: str = "byte", signed: bool = False) -> dict:
        emu = self._holder._require_rom()
        fns = {
            ("byte", False): emu.memory_read_byte,
            ("byte", True): emu.memory_read_byte_signed,
            ("short", False): emu.memory_read_short,
            ("short", True): emu.memory_read_short_signed,
            ("long", False): emu.memory_read_long,
            ("long", True): emu.memory_read_long_signed,
        }
        fn = fns.get((size, signed))
        if fn is None:
            raise ValueError(f"Invalid size: {size}")
        return {"value": fn(address)}

    def _read_memory_range(self, address: int, size: str = "byte",
                           count: int = 1, signed: bool = False) -> dict:
        emu = self._holder._require_rom()
        fns = {
            ("byte", False): emu.memory_read_byte,
            ("byte", True): emu.memory_read_byte_signed,
            ("short", False): emu.memory_read_short,
            ("short", True): emu.memory_read_short_signed,
            ("long", False): emu.memory_read_long,
            ("long", True): emu.memory_read_long_signed,
        }
        fn = fns.get((size, signed))
        if fn is None:
            raise ValueError(f"Invalid size: {size}")
        step = {"byte": 1, "short": 2, "long": 4}[size]
        values = [fn(address + i * step) for i in range(count)]
        return {"values": values}

    def _write_memory(self, address: int, value: int, size: str = "byte") -> dict:
        emu = self._holder._require_rom()
        if size == "byte":
            emu.memory_write_byte(address, value)
        elif size == "short":
            emu.memory_write_short(address, value)
        elif size == "long":
            emu.memory_write_long(address, value)
        else:
            raise ValueError(f"Invalid size: {size}")
        return {"success": True}

    def _input_keypad_update(self, keys: int = 0, buttons: list[str] | None = None) -> dict:
        emu = self._holder._require_rom()
        if buttons:
            keys = buttons_to_bitmask(buttons)
        emu.input_keypad_update(keys)
        return {"keys": keys}

    def _cycle(self) -> dict:
        emu = self._holder._require_rom()
        emu.cycle()
        self._holder.frame_count += 1
        return {"total_frame": self._holder.frame_count}

    def _save_state(self, path: str) -> dict:
        emu = self._holder._require_rom()
        success = emu.savestate_save(path)
        return {"success": success, "path": path}

    def _load_state(self, path: str) -> dict:
        emu = self._holder._require_rom()
        success = emu.savestate_load(path)
        return {"success": success, "path": path}

    def _get_status(self) -> dict:
        return {
            "initialized": self._holder.is_initialized,
            "rom_loaded": self._holder.is_rom_loaded,
            "frame_count": self._holder.frame_count,
            "rom_path": self._holder.rom_path,
        }

    def _get_frame_count(self) -> dict:
        return {"frame_count": self._holder.frame_count}

    # ── Server lifecycle ──

    def start(self) -> str:
        """Start the bridge server. Returns the socket path."""
        # Clean up stale socket
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self._socket_path)
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)  # Allow periodic shutdown checks
        self._running = True

        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        return self._socket_path

    def stop(self) -> None:
        """Stop the bridge server."""
        self._running = False
        if self._server_sock:
            self._server_sock.close()
        if self._thread:
            self._thread.join(timeout=3)
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

    def _serve_loop(self) -> None:
        """Accept connections and handle requests."""
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_connection(conn)
            except Exception:
                pass
            finally:
                conn.close()

    def _handle_connection(self, conn: socket.socket) -> None:
        """Handle a single client connection (may send multiple requests)."""
        buf = b""
        conn.settimeout(30.0)
        while True:
            try:
                chunk = conn.recv(MAX_REQUEST_SIZE)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            # Process complete lines
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                response = self._dispatch(line)
                conn.sendall(response.encode("utf-8") + b"\n")

    def _dispatch(self, raw: bytes) -> str:
        """Parse a JSON request and dispatch to the appropriate handler."""
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        method = req.get("method")
        if not method or method not in self._methods:
            return json.dumps({"error": f"Unknown method: {method!r}. Available: {sorted(self._methods.keys())}"})

        params = req.get("params", {})

        with self._lock:
            try:
                result = self._methods[method](**params)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": f"{type(e).__name__}: {e}"})
