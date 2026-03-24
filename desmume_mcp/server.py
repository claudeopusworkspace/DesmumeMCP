"""MCP server exposing DeSmuME emulator control tools for LLM gameplay."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .constants import FRAMES_PER_SECOND, VALID_BUTTONS
from .emulator import EmulatorState

# Limits
MAX_ADVANCE_FRAMES = 3600  # 60 seconds at 60fps
MAX_MEMORY_READ_COUNT = 256
MAX_MACRO_STEPS = 100
MAX_MACRO_REPEAT = 100

# Valid macro step actions and their required/optional fields
_MACRO_STEP_SCHEMA: dict[str, dict[str, Any]] = {
    "press": {"required": ["buttons"], "optional": ["frames"]},
    "hold": {"required": [], "optional": ["buttons", "frames", "touch_x", "touch_y"]},
    "wait": {"required": [], "optional": ["frames"]},
    "tap": {"required": ["x", "y"], "optional": ["frames"]},
}


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


# ── Macro helpers ────────────────────────────────────────────────


def _validate_macro_steps(steps: list[dict]) -> None:
    """Validate macro steps against the schema."""
    if not steps:
        raise ValueError("Macro must have at least one step.")
    if len(steps) > MAX_MACRO_STEPS:
        raise ValueError(f"Macro can have at most {MAX_MACRO_STEPS} steps.")

    for i, step in enumerate(steps):
        if "action" not in step:
            raise ValueError(f"Step {i}: missing 'action' field.")
        action = step["action"]
        if action not in _MACRO_STEP_SCHEMA:
            raise ValueError(
                f"Step {i}: unknown action {action!r}. "
                f"Valid: {list(_MACRO_STEP_SCHEMA.keys())}"
            )
        schema = _MACRO_STEP_SCHEMA[action]
        for field in schema["required"]:
            if field not in step:
                raise ValueError(
                    f"Step {i} ({action}): missing required field {field!r}."
                )
        valid_fields = {"action"} | set(schema["required"]) | set(schema["optional"])
        for field in step:
            if field not in valid_fields:
                raise ValueError(
                    f"Step {i} ({action}): unknown field {field!r}. "
                    f"Valid: {sorted(valid_fields)}"
                )
        if "frames" in step:
            f = step["frames"]
            if not isinstance(f, int) or f < 1 or f > MAX_ADVANCE_FRAMES:
                raise ValueError(f"Step {i}: frames must be 1-{MAX_ADVANCE_FRAMES}.")


def _tool_create_macro(
    holder: EmulatorState,
    name: str,
    description: str,
    steps: list[dict],
) -> dict[str, Any]:
    _validate_macro_steps(steps)
    macro = {"name": name, "description": description, "steps": steps}
    path = holder.macros_dir / f"{name}.json"
    path.write_text(json.dumps(macro, indent=2))
    return {
        "success": True,
        "name": name,
        "description": description,
        "step_count": len(steps),
        "path": str(path),
    }


def _tool_list_macros(holder: EmulatorState) -> dict[str, Any]:
    macros = []
    if holder.macros_dir.exists():
        for f in sorted(holder.macros_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                macros.append({
                    "name": data["name"],
                    "description": data["description"],
                    "step_count": len(data["steps"]),
                })
            except (json.JSONDecodeError, KeyError):
                continue
    return {"macros": macros}


def _tool_run_macro(
    holder: EmulatorState, name: str, repeat: int
) -> dict[str, Any]:
    holder._require_rom()
    if repeat < 1 or repeat > MAX_MACRO_REPEAT:
        raise ValueError(f"repeat must be 1-{MAX_MACRO_REPEAT}")
    path = holder.macros_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Macro not found: {name!r}")
    data = json.loads(path.read_text())
    steps = data["steps"]
    _validate_macro_steps(steps)

    total_frames = 0
    for _ in range(repeat):
        total_frames += holder.run_macro_steps(steps)

    return {
        "name": name,
        "repeat": repeat,
        "frames_advanced": total_frames,
        "total_frame": holder.frame_count,
    }


def _tool_delete_macro(holder: EmulatorState, name: str) -> dict[str, Any]:
    path = holder.macros_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Macro not found: {name!r}")
    path.unlink()
    return {"success": True, "name": name}


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

    # ── Macros ──

    @mcp.tool()
    def create_macro(
        name: str,
        description: str,
        steps: list[dict],
    ) -> dict[str, Any]:
        """Create a reusable input macro. Macros are saved to disk and persist across sessions.

        Each step is a dict with an "action" and its parameters. Available actions:

        - {"action": "press", "buttons": ["a"], "frames": 1}
          Press and release buttons (hold for N frames, then release for 1 frame).

        - {"action": "hold", "buttons": ["right"], "frames": 60}
          Hold buttons for N frames WITHOUT releasing. Can also include touch_x/touch_y.

        - {"action": "wait", "frames": 30}
          Advance N frames with no input (all buttons released).

        - {"action": "tap", "x": 128, "y": 96, "frames": 1}
          Tap the touchscreen for N frames, then release for 1 frame.

        Example — mash A through dialogue (press A, wait, repeat 5 times):
          steps=[
            {"action": "press", "buttons": ["a"]},
            {"action": "wait", "frames": 15},
            {"action": "press", "buttons": ["a"]},
            {"action": "wait", "frames": 15},
            {"action": "press", "buttons": ["a"]},
            {"action": "wait", "frames": 15},
            {"action": "press", "buttons": ["a"]},
            {"action": "wait", "frames": 15},
            {"action": "press", "buttons": ["a"]},
            {"action": "wait", "frames": 15},
          ]

        Args:
            name: Unique name for the macro (used as filename, e.g. "mash_a", "walk_right").
            description: Short description of what the macro does.
            steps: List of step dicts. Max 100 steps per macro.
        """
        return _tool_create_macro(holder, name, description, steps)

    @mcp.tool()
    def list_macros() -> dict[str, Any]:
        """List all saved macros with their names, descriptions, and step counts."""
        return _tool_list_macros(holder)

    @mcp.tool()
    def run_macro(name: str, repeat: int = 1) -> dict[str, Any]:
        """Execute a saved macro. Optionally repeat it multiple times.

        Args:
            name: Name of the macro to run.
            repeat: Number of times to run the macro (1-100). Useful for repeated
                    actions like mashing A through long dialogue.
        """
        return _tool_run_macro(holder, name, repeat)

    @mcp.tool()
    def delete_macro(name: str) -> dict[str, Any]:
        """Delete a saved macro.

        Args:
            name: Name of the macro to delete.
        """
        return _tool_delete_macro(holder, name)

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
