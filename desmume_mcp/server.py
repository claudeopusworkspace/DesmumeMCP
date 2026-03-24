"""MCP server exposing DeSmuME emulator control tools for LLM gameplay."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .constants import FRAMES_PER_SECOND, VALID_BUTTONS
from .emulator import EmulatorState

# Limits
MAX_ADVANCE_FRAMES = 3600  # 60 seconds at 60fps
MAX_MEMORY_READ_COUNT = 256


# ── Tool logic functions (testable without MCP protocol) ──────────


def _tool_init_emulator(holder: EmulatorState) -> dict[str, Any]:
    msg = holder.initialize()
    return {"success": True, "message": msg}


def _tool_load_rom(holder: EmulatorState, rom_path: str) -> dict[str, Any]:
    msg = holder.load_rom(rom_path)
    return {"success": True, "rom_path": holder.rom_path, "message": msg}


def _tool_advance_frames(
    holder: EmulatorState,
    count: int,
    buttons: list[str],
    touch_x: int | None,
    touch_y: int | None,
) -> dict[str, Any]:
    if count < 1:
        raise ValueError("count must be >= 1")
    if count > MAX_ADVANCE_FRAMES:
        raise ValueError(f"count must be <= {MAX_ADVANCE_FRAMES}")
    advanced = holder.advance_frames(count, buttons or None, touch_x, touch_y)
    return {
        "frames_advanced": advanced,
        "total_frame": holder.frame_count,
        "buttons": buttons,
    }


def _tool_press_buttons(
    holder: EmulatorState, buttons: list[str], frames: int
) -> dict[str, Any]:
    if not buttons:
        raise ValueError("Must specify at least one button.")
    if frames < 1 or frames > MAX_ADVANCE_FRAMES:
        raise ValueError(f"frames must be 1-{MAX_ADVANCE_FRAMES}")
    holder.press_buttons(buttons, frames)
    return {
        "buttons": buttons,
        "held_frames": frames,
        "total_frame": holder.frame_count,
    }


def _tool_tap_touch_screen(
    holder: EmulatorState, x: int, y: int, frames: int
) -> dict[str, Any]:
    if not (0 <= x <= 255):
        raise ValueError("x must be 0-255")
    if not (0 <= y <= 191):
        raise ValueError("y must be 0-191")
    if frames < 1 or frames > MAX_ADVANCE_FRAMES:
        raise ValueError(f"frames must be 1-{MAX_ADVANCE_FRAMES}")
    holder.tap_touch_screen(x, y, frames)
    return {"x": x, "y": y, "held_frames": frames, "total_frame": holder.frame_count}


def _tool_get_screenshot(
    holder: EmulatorState, screen: str
) -> tuple[str, bytes]:
    if screen not in ("top", "bottom", "both"):
        raise ValueError("screen must be 'top', 'bottom', or 'both'")
    return holder.capture_screenshot(screen, fmt="png")


def _tool_save_screenshot(
    holder: EmulatorState, file_path: str, screen: str
) -> dict[str, Any]:
    if screen not in ("top", "bottom", "both"):
        raise ValueError("screen must be 'top', 'bottom', or 'both'")
    mime, image_bytes = holder.capture_screenshot(screen, fmt="png")
    p = Path(file_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(image_bytes)
    return {
        "success": True,
        "path": str(p),
        "size_bytes": len(image_bytes),
        "screen": screen,
        "frame": holder.frame_count,
    }


def _tool_get_status(holder: EmulatorState) -> dict[str, Any]:
    status: dict[str, Any] = {
        "initialized": holder.is_initialized,
        "rom_loaded": holder.is_rom_loaded,
        "frame_count": holder.frame_count,
        "fps": FRAMES_PER_SECOND,
    }
    if holder.rom_path:
        status["rom_path"] = holder.rom_path
    if holder.emu and holder.is_rom_loaded:
        status["running"] = holder.emu.running()
        status["movie_active"] = holder.emu.movie_is_active()
        status["movie_recording"] = holder.emu.movie_is_recording()
    return status


def _tool_save_state(holder: EmulatorState, name: str) -> dict[str, Any]:
    holder._require_rom()
    path = str(holder.savestates_dir / f"{name}.dst")
    success = holder.emu.savestate_save(path)
    return {"success": success, "name": name, "path": path}


def _tool_load_state(holder: EmulatorState, name: str) -> dict[str, Any]:
    holder._require_rom()
    path = str(holder.savestates_dir / f"{name}.dst")
    if not Path(path).exists():
        raise FileNotFoundError(f"Savestate not found: {name}")
    success = holder.emu.savestate_load(path)
    return {"success": success, "name": name, "total_frame": holder.frame_count}


def _tool_list_states(holder: EmulatorState) -> dict[str, Any]:
    states = []
    if holder.savestates_dir.exists():
        for f in sorted(holder.savestates_dir.glob("*.dst")):
            states.append({
                "name": f.stem,
                "path": str(f),
                "size_bytes": f.stat().st_size,
            })
    return {"states": states}


def _tool_reset(holder: EmulatorState) -> dict[str, Any]:
    emu = holder._require_rom()
    emu.reset()
    holder.frame_count = 0
    return {"success": True, "message": "NDS reset.", "total_frame": 0}


def _tool_read_memory(
    holder: EmulatorState,
    address: int,
    size: str,
    count: int,
    signed: bool,
) -> dict[str, Any]:
    emu = holder._require_rom()
    if count < 1 or count > MAX_MEMORY_READ_COUNT:
        raise ValueError(f"count must be 1-{MAX_MEMORY_READ_COUNT}")

    read_fns = {
        ("byte", False): emu.memory_read_byte,
        ("byte", True): emu.memory_read_byte_signed,
        ("short", False): emu.memory_read_short,
        ("short", True): emu.memory_read_short_signed,
        ("long", False): emu.memory_read_long,
        ("long", True): emu.memory_read_long_signed,
    }
    fn = read_fns.get((size, signed))
    if fn is None:
        raise ValueError(f"size must be 'byte', 'short', or 'long'")

    size_bytes = {"byte": 1, "short": 2, "long": 4}[size]
    values = []
    for i in range(count):
        values.append(fn(address + i * size_bytes))

    return {
        "address": f"0x{address:08X}",
        "size": size,
        "signed": signed,
        "values": values,
        "hex_values": [f"0x{v & ((1 << (size_bytes * 8)) - 1):0{size_bytes * 2}X}" for v in values],
    }


def _tool_write_memory(
    holder: EmulatorState,
    address: int,
    value: int,
    size: str,
) -> dict[str, Any]:
    emu = holder._require_rom()
    if size == "byte":
        emu.memory_write_byte(address, value)
    elif size == "short":
        emu.memory_write_short(address, value)
    elif size == "long":
        emu.memory_write_long(address, value)
    else:
        raise ValueError("size must be 'byte', 'short', or 'long'")

    return {
        "success": True,
        "address": f"0x{address:08X}",
        "value": value,
        "size": size,
    }


def _tool_start_recording(
    holder: EmulatorState, name: str, author: str
) -> dict[str, Any]:
    emu = holder._require_rom()
    path = str(holder.movies_dir / f"{name}.dsm")
    emu.movie_record_simple(path, author)
    return {"success": True, "name": name, "path": path}


def _tool_stop_recording(holder: EmulatorState) -> dict[str, Any]:
    emu = holder._require_rom()
    emu.movie_stop()
    return {"success": True}


def _tool_backup_save_import(
    holder: EmulatorState, path: str
) -> dict[str, Any]:
    emu = holder._require_rom()
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Save file not found: {p}")
    success = emu.backup_import_file(str(p))
    return {"success": success, "path": str(p)}


def _tool_backup_save_export(
    holder: EmulatorState, path: str
) -> dict[str, Any]:
    emu = holder._require_rom()
    p = Path(path).resolve()
    success = emu.backup_export_file(str(p))
    return {"success": success, "path": str(p)}


# ── Server factory ───────────────────────────────────────────────


def create_server(data_dir: Path | None = None) -> FastMCP:
    """Create the DeSmuME MCP server."""
    holder = EmulatorState(data_dir=data_dir or Path.cwd())

    mcp = FastMCP(name="DeSmuME MCP")

    # ── Core emulation ──

    @mcp.tool()
    def init_emulator() -> dict[str, Any]:
        """Initialize the DeSmuME emulation engine. Must be called before any other tool."""
        return _tool_init_emulator(holder)

    @mcp.tool()
    def load_rom(rom_path: str) -> dict[str, Any]:
        """Load a Nintendo DS ROM (.nds) file. Requires init_emulator first."""
        return _tool_load_rom(holder, rom_path)

    @mcp.tool()
    def advance_frames(
        count: int = 1,
        buttons: list[str] = [],
        touch_x: int | None = None,
        touch_y: int | None = None,
    ) -> dict[str, Any]:
        """Advance emulation by N frames, holding the given inputs throughout.

        Args:
            count: Number of frames to advance (1-3600). DS runs at 60fps.
            buttons: Buttons to hold. Valid: a, b, x, y, l, r, start, select, up, down, left, right.
            touch_x: Touchscreen X position (0-255). Both touch_x and touch_y required for touch input.
            touch_y: Touchscreen Y position (0-191).
        """
        return _tool_advance_frames(holder, count, buttons, touch_x, touch_y)

    @mcp.tool()
    def press_buttons(buttons: list[str], frames: int = 1) -> dict[str, Any]:
        """Press and release buttons. Holds for N frames then releases for 1 frame.

        This is the natural "press a button" action. For example, press_buttons(["a"])
        taps A once. press_buttons(["a"], frames=30) holds A for half a second.

        Args:
            buttons: Buttons to press. Valid: a, b, x, y, l, r, start, select, up, down, left, right.
            frames: How many frames to hold before releasing (1-3600).
        """
        return _tool_press_buttons(holder, buttons, frames)

    @mcp.tool()
    def tap_touch_screen(x: int, y: int, frames: int = 1) -> dict[str, Any]:
        """Tap the touchscreen (bottom screen) at a position. Holds for N frames then releases.

        The bottom screen is 256x192 pixels. Coordinates are relative to the bottom screen.

        Args:
            x: X position (0-255).
            y: Y position (0-191).
            frames: How many frames to hold the tap (1-3600).
        """
        return _tool_tap_touch_screen(holder, x, y, frames)

    @mcp.tool()
    def get_screenshot(screen: str = "both") -> Any:
        """Capture the current display as a PNG image.

        Args:
            screen: Which screen to capture: "top", "bottom", or "both" (stacked vertically).
        """
        from mcp.types import ImageContent

        mime, image_bytes = _tool_get_screenshot(holder, screen)
        import base64

        return ImageContent(
            type="image",
            data=base64.b64encode(image_bytes).decode("ascii"),
            mimeType=mime,
        )

    @mcp.tool()
    def save_screenshot(file_path: str, screen: str = "both") -> dict[str, Any]:
        """Save the current display as a PNG file on disk. Useful for visual documentation.

        Args:
            file_path: Where to save the PNG (e.g. "/workspace/screenshots/frame_100.png").
            screen: Which screen to capture: "top", "bottom", or "both" (stacked vertically).
        """
        return _tool_save_screenshot(holder, file_path, screen)

    @mcp.tool()
    def get_status() -> dict[str, Any]:
        """Get the current emulator status: initialization state, ROM info, frame count, etc."""
        return _tool_get_status(holder)

    # ── State management ──

    @mcp.tool()
    def save_state(name: str) -> dict[str, Any]:
        """Save the current emulator state to a named file. Use before risky actions.

        Args:
            name: Name for the savestate (e.g. "before_boss", "checkpoint_1").
        """
        return _tool_save_state(holder, name)

    @mcp.tool()
    def load_state(name: str) -> dict[str, Any]:
        """Load a previously saved emulator state.

        Args:
            name: Name of the savestate to load.
        """
        return _tool_load_state(holder, name)

    @mcp.tool()
    def list_states() -> dict[str, Any]:
        """List all available savestates."""
        return _tool_list_states(holder)

    @mcp.tool()
    def reset_emulator() -> dict[str, Any]:
        """Reset the NDS. Equivalent to power cycling the console."""
        return _tool_reset(holder)

    # ── Memory ──

    @mcp.tool()
    def read_memory(
        address: int,
        size: str = "byte",
        count: int = 1,
        signed: bool = False,
    ) -> dict[str, Any]:
        """Read values from emulator memory. Useful for checking game state (HP, score, position, etc.)
        when you know the memory addresses.

        Args:
            address: Memory address to start reading from (e.g. 0x02000000).
            size: Size of each read: "byte" (1), "short" (2), or "long" (4 bytes).
            count: Number of consecutive values to read (1-256).
            signed: If True, interpret values as signed integers.
        """
        return _tool_read_memory(holder, address, size, count, signed)

    @mcp.tool()
    def write_memory(
        address: int,
        value: int,
        size: str = "byte",
    ) -> dict[str, Any]:
        """Write a value to emulator memory.

        Args:
            address: Memory address to write to.
            value: Value to write.
            size: Size of the write: "byte" (1), "short" (2), or "long" (4 bytes).
        """
        return _tool_write_memory(holder, address, value, size)

    # ── Movie recording ──

    @mcp.tool()
    def start_recording(
        name: str, author: str = "DeSmuME MCP"
    ) -> dict[str, Any]:
        """Start recording inputs to a movie file (.dsm). Can be played back in DeSmuME GUI later.

        Args:
            name: Name for the movie file.
            author: Author name embedded in the movie metadata.
        """
        return _tool_start_recording(holder, name, author)

    @mcp.tool()
    def stop_recording() -> dict[str, Any]:
        """Stop the current movie recording or playback."""
        return _tool_stop_recording(holder)

    # ── Battery save (backup) ──

    @mcp.tool()
    def backup_save_import(path: str) -> dict[str, Any]:
        """Import a battery save file (.sav, .dsv). The emulator will reset after import.

        Args:
            path: Path to the save file.
        """
        return _tool_backup_save_import(holder, path)

    @mcp.tool()
    def backup_save_export(path: str) -> dict[str, Any]:
        """Export the current battery save to a .dsv file.

        Args:
            path: Destination path for the save file.
        """
        return _tool_backup_save_export(holder, path)

    return mcp
