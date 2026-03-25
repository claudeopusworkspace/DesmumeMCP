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

## Custom Scripts (Beyond MCP Tools)

For complex logic that goes beyond what MCP tools can express (e.g., "walk a path and verify each step succeeded"), you can write standalone Python scripts that use the emulator modules directly.

**The pattern: savestate handoff.**

The MCP server owns the running emulator instance — a standalone script can't share it. Instead:

1. Save state in MCP: `save_state("before_script")`
2. Run your script (it creates its own emulator, loads that savestate, does the work, saves a new state)
3. Load the result in MCP: `load_state("after_script")`

**Example script** — walk a path with position verification:

```python
#!/usr/bin/env python3
"""Walk a path, verifying each step moved the player."""
import os, sys, struct

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

# Add the DesmumeMCP package to the path
sys.path.insert(0, "/path/to/DesmumeMCP")

from desmume_mcp.emulator import EmulatorState
from desmume_mcp.constants import buttons_to_bitmask

POSITION_ADDR = 0x02345678  # Example — use actual address from your watch

emu = EmulatorState()
emu.initialize()
emu.load_rom("/path/to/game.nds")

# Load the savestate from MCP
emu.emu.savestate_load("/path/to/savestates/before_script.dst")
emu.is_rom_loaded = True

def read_position():
    x = emu.emu.memory_read_long(POSITION_ADDR) & 0xFFFFFFFF
    y = emu.emu.memory_read_long(POSITION_ADDR + 4) & 0xFFFFFFFF
    return (x, y)

path = ["right", "right", "up", "up", "up", "left"]

for i, direction in enumerate(path):
    old_pos = read_position()
    emu.advance_frame(buttons=[direction])
    # Hold for 16 frames (one tile)
    emu.advance_frames(15, buttons=[direction])
    # Wait for movement to complete
    emu.advance_frames(8)
    new_pos = read_position()
    if old_pos == new_pos:
        print(f"Step {i} ({direction}): BLOCKED at {old_pos}")
        break
    print(f"Step {i} ({direction}): {old_pos} -> {new_pos}")

# Save the result for MCP to pick up
emu.emu.savestate_save("/path/to/savestates/after_script.dst")
print(f"Final position: {read_position()}")
```

Run via Bash, then `load_state("after_script")` in MCP to continue.

**Available modules:**

| Module | What it provides |
|--------|-----------------|
| `desmume_mcp.libdesmume.DeSmuME` | Low-level ctypes wrapper — direct 1:1 C function calls |
| `desmume_mcp.emulator.EmulatorState` | Higher-level holder — `advance_frame()`, `press_buttons()`, `capture_screenshot()` |
| `desmume_mcp.constants` | `Key`, `KeyMask`, `BUTTON_MAP`, `buttons_to_bitmask()`, screen dimensions |
