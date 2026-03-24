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
- Input bitmask uses `KEYMASK_(k) = (1 << k)` where k is the key index from ctrlssdl.h.
- `desmume_open()` returns >= 1 on success (not 0).
- Screenshot buffer is 256x384 RGB (both screens stacked vertically), 294912 bytes.
- Headless mode requires `SDL_VIDEODRIVER=dummy` and `SDL_AUDIODRIVER=dummy` env vars.
