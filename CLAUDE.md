# DeSmuME MCP Server

A Model Context Protocol server that wraps DeSmuME's C interface library, enabling LLMs to play Nintendo DS games headlessly.

## Architecture

- `libdesmume.so` — DeSmuME compiled as a shared library (built from `desmume-src/` via meson)
- `desmume_mcp/libdesmume.py` — Python ctypes wrapper (1:1 mapping of C interface)
- `desmume_mcp/emulator.py` — Stateful emulator holder (lifecycle, input helpers, screenshot capture)
- `desmume_mcp/server.py` — FastMCP tool definitions
- `desmume_mcp/__main__.py` — Entry point (`python -m desmume_mcp`)

## Build

```bash
# System deps (once):
sudo apt install meson ninja-build libsdl2-dev libpcap-dev libglib2.0-dev zlib1g-dev

# Build libdesmume.so:
./scripts/build_libdesmume.sh
```

## Run

```bash
source .venv/bin/activate
python -m desmume_mcp
```

## Key Conventions

- DS runs at 60fps. One `desmume_cycle()` = one frame.
- Input bitmask uses `KEYMASK_(k) = (1 << k)` where k is the key index (0-indexed, matching ctrlssdl.cpp).
- `desmume_open()` returns >= 1 on success (not 0).
- Screenshot buffer is 256x384 RGB (both screens stacked vertically), 294912 bytes.
- Headless mode requires `SDL_VIDEODRIVER=dummy` and `SDL_AUDIODRIVER=dummy` env vars.
- Memory reads (`read_memory`, `dump_memory`, etc.) work across the full ARM9 address space — not just main RAM. VRAM (0x06000000), I/O registers (0x04000000), and cartridge-mapped regions are all accessible.

## Custom Scripts (Bridge Client)

For complex logic that goes beyond what MCP tools can express (e.g., "walk a path and verify each step succeeded"), write standalone Python scripts that connect to the running emulator via the IPC bridge.

When `init_emulator` is called, the MCP server starts a Unix domain socket bridge at `<data_dir>/.desmume_bridge.sock`. External scripts connect to it and control the **same emulator instance** the MCP is using — no savestate handoff, no separate emulator, zero overhead.

**Example** — walk a path with position verification:

```python
#!/usr/bin/env python3
"""Walk a path, verifying each step moved the player."""
import sys
sys.path.insert(0, "/path/to/DesmumeMCP")

from desmume_mcp.client import connect

emu = connect()  # Auto-discovers the bridge socket

POSITION_ADDR = 0x02345678  # Use actual address from your memory watch

def read_position():
    x = emu.read_memory(POSITION_ADDR, size="long")
    y = emu.read_memory(POSITION_ADDR + 4, size="long")
    return (x, y)

path = ["right", "right", "up", "up", "up", "left"]

for i, direction in enumerate(path):
    old_pos = read_position()
    # Hold direction for 16 frames (one tile), then wait 8 for step to complete
    emu.advance_frames(16, buttons=[direction])
    emu.advance_frames(8)
    new_pos = read_position()
    if old_pos == new_pos:
        print(f"Step {i} ({direction}): BLOCKED at {old_pos}")
        break
    print(f"Step {i} ({direction}): {old_pos} -> {new_pos}")

print(f"Final position: {read_position()}")
```

Run via Bash while the MCP server is active — the script operates on the same emulator frame state.

**Bridge client API:**

| Method | Returns | Description |
|--------|---------|-------------|
| `advance_frames(count, buttons, touch_x, touch_y)` | dict | Advance N frames with input |
| `advance_frame(buttons, touch_x, touch_y)` | dict | Advance 1 frame |
| `press_buttons(buttons, frames)` | dict | Press+release pattern |
| `tap_touch_screen(x, y, frames)` | dict | Touch+release |
| `read_memory(address, size, signed)` | int | Read single value |
| `read_memory_range(address, size, count, signed)` | list[int] | Read consecutive values |
| `write_memory(address, value, size)` | None | Write value |
| `cycle()` | int | Raw frame advance (no input change) |
| `save_state(path)` / `load_state(path)` | bool | Savestate management |
| `get_screenshot(screen, fmt)` | (mime, bytes) | Screenshot capture |
| `get_frame_count()` | int | Current frame number |

**Fallback: savestate handoff** (if the bridge isn't available):

Scripts can also create their own emulator instance using `desmume_mcp.emulator.EmulatorState` directly, coordinating with the MCP via savestates. See `libdesmume.py` and `emulator.py` for the module API.
