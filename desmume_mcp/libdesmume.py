"""Thin ctypes wrapper around libdesmume.so — 1:1 mapping of the C interface."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def _find_library() -> str:
    """Search for libdesmume.so in known locations."""
    # Check env var first
    env_path = os.environ.get("DESMUME_LIB_PATH")
    if env_path and os.path.exists(env_path):
        return os.path.realpath(env_path)

    project_root = Path(__file__).parent.parent
    candidates = [
        project_root / "build" / "libdesmume.so",
        project_root
        / "desmume-src"
        / "desmume"
        / "src"
        / "frontend"
        / "interface"
        / "build"
        / "libdesmume.so",
    ]
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists():
            return str(resolved)

    raise FileNotFoundError(
        "libdesmume.so not found. Build it first with: ./scripts/build_libdesmume.sh"
    )


class DeSmuME:
    """Python wrapper around the DeSmuME C interface library.

    This is a thin, low-level wrapper. Each method maps directly to a
    C function in interface.h. Higher-level logic belongs in emulator.py.
    """

    # Pre-allocated screenshot buffer (256x384 RGB = 294912 bytes)
    _SCREENSHOT_SIZE = 98304 * 3

    def __init__(self) -> None:
        lib_path = _find_library()
        self._lib = ctypes.CDLL(lib_path)
        self._setup_signatures()
        self._screenshot_buf = (ctypes.c_char * self._SCREENSHOT_SIZE)()

    def _setup_signatures(self) -> None:
        lib = self._lib

        # ── Lifecycle ──
        lib.desmume_init.argtypes = []
        lib.desmume_init.restype = ctypes.c_int

        lib.desmume_free.argtypes = []
        lib.desmume_free.restype = None

        lib.desmume_open.argtypes = [ctypes.c_char_p]
        lib.desmume_open.restype = ctypes.c_int

        lib.desmume_pause.argtypes = []
        lib.desmume_pause.restype = None

        lib.desmume_resume.argtypes = []
        lib.desmume_resume.restype = None

        lib.desmume_reset.argtypes = []
        lib.desmume_reset.restype = None

        lib.desmume_running.argtypes = []
        lib.desmume_running.restype = ctypes.c_int

        lib.desmume_cycle.argtypes = [ctypes.c_int]
        lib.desmume_cycle.restype = None

        lib.desmume_skip_next_frame.argtypes = []
        lib.desmume_skip_next_frame.restype = None

        # ── Display ──
        lib.desmume_screenshot.argtypes = [ctypes.c_char_p]
        lib.desmume_screenshot.restype = None

        lib.desmume_draw_raw.argtypes = []
        lib.desmume_draw_raw.restype = ctypes.POINTER(ctypes.c_ushort)

        # ── Input ──
        lib.desmume_input_keypad_update.argtypes = [ctypes.c_ushort]
        lib.desmume_input_keypad_update.restype = None

        lib.desmume_input_keypad_get.argtypes = []
        lib.desmume_input_keypad_get.restype = ctypes.c_ushort

        lib.desmume_input_set_touch_pos.argtypes = [
            ctypes.c_ushort,
            ctypes.c_ushort,
        ]
        lib.desmume_input_set_touch_pos.restype = None

        lib.desmume_input_release_touch.argtypes = []
        lib.desmume_input_release_touch.restype = None

        # ── Savestates ──
        lib.desmume_savestate_save.argtypes = [ctypes.c_char_p]
        lib.desmume_savestate_save.restype = ctypes.c_int

        lib.desmume_savestate_load.argtypes = [ctypes.c_char_p]
        lib.desmume_savestate_load.restype = ctypes.c_int

        lib.desmume_savestate_slot_save.argtypes = [ctypes.c_int]
        lib.desmume_savestate_slot_save.restype = None

        lib.desmume_savestate_slot_load.argtypes = [ctypes.c_int]
        lib.desmume_savestate_slot_load.restype = None

        lib.desmume_savestate_slot_exists.argtypes = [ctypes.c_int]
        lib.desmume_savestate_slot_exists.restype = ctypes.c_int

        # ── Memory ──
        lib.desmume_memory_read_byte.argtypes = [ctypes.c_int]
        lib.desmume_memory_read_byte.restype = ctypes.c_ubyte

        lib.desmume_memory_read_byte_signed.argtypes = [ctypes.c_int]
        lib.desmume_memory_read_byte_signed.restype = ctypes.c_byte

        lib.desmume_memory_read_short.argtypes = [ctypes.c_int]
        lib.desmume_memory_read_short.restype = ctypes.c_ushort

        lib.desmume_memory_read_short_signed.argtypes = [ctypes.c_int]
        lib.desmume_memory_read_short_signed.restype = ctypes.c_short

        lib.desmume_memory_read_long.argtypes = [ctypes.c_int]
        lib.desmume_memory_read_long.restype = ctypes.c_ulong

        lib.desmume_memory_read_long_signed.argtypes = [ctypes.c_int]
        lib.desmume_memory_read_long_signed.restype = ctypes.c_long

        lib.desmume_memory_write_byte.argtypes = [ctypes.c_int, ctypes.c_ubyte]
        lib.desmume_memory_write_byte.restype = None

        lib.desmume_memory_write_short.argtypes = [
            ctypes.c_int,
            ctypes.c_ushort,
        ]
        lib.desmume_memory_write_short.restype = None

        lib.desmume_memory_write_long.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        lib.desmume_memory_write_long.restype = None

        # ── Settings ──
        lib.desmume_set_language.argtypes = [ctypes.c_ubyte]
        lib.desmume_set_language.restype = None

        lib.desmume_volume_get.argtypes = []
        lib.desmume_volume_get.restype = ctypes.c_int

        lib.desmume_volume_set.argtypes = [ctypes.c_int]
        lib.desmume_volume_set.restype = None

        # ── Movies ──
        lib.desmume_movie_record_simple.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        lib.desmume_movie_record_simple.restype = None

        lib.desmume_movie_play.argtypes = [ctypes.c_char_p]
        lib.desmume_movie_play.restype = ctypes.c_char_p

        lib.desmume_movie_stop.argtypes = []
        lib.desmume_movie_stop.restype = None

        lib.desmume_movie_is_active.argtypes = []
        lib.desmume_movie_is_active.restype = ctypes.c_int

        lib.desmume_movie_is_recording.argtypes = []
        lib.desmume_movie_is_recording.restype = ctypes.c_int

        lib.desmume_movie_is_playing.argtypes = []
        lib.desmume_movie_is_playing.restype = ctypes.c_int

        # ── Backup (battery save) ──
        lib.desmume_backup_import_file.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        lib.desmume_backup_import_file.restype = ctypes.c_int

        lib.desmume_backup_export_file.argtypes = [ctypes.c_char_p]
        lib.desmume_backup_export_file.restype = ctypes.c_int

    # ── Lifecycle ──

    def init(self) -> int:
        return self._lib.desmume_init()

    def free(self) -> None:
        self._lib.desmume_free()

    def open(self, filename: str) -> int:
        """Load a ROM. Returns >= 1 on success."""
        return self._lib.desmume_open(filename.encode("utf-8"))

    def pause(self) -> None:
        self._lib.desmume_pause()

    def resume(self) -> None:
        self._lib.desmume_resume()

    def reset(self) -> None:
        self._lib.desmume_reset()

    def running(self) -> bool:
        return bool(self._lib.desmume_running())

    def cycle(self, with_joystick: bool = False) -> None:
        """Advance one frame of emulation."""
        self._lib.desmume_cycle(1 if with_joystick else 0)

    def skip_next_frame(self) -> None:
        self._lib.desmume_skip_next_frame()

    # ── Display ──

    def screenshot(self) -> bytes:
        """Capture both screens as raw RGB bytes (294912 bytes, 256x384)."""
        self._lib.desmume_screenshot(self._screenshot_buf)
        return bytes(self._screenshot_buf)

    # ── Input ──

    def input_keypad_update(self, keys: int) -> None:
        """Set the keypad state bitmask for the current/next frame."""
        self._lib.desmume_input_keypad_update(ctypes.c_ushort(keys))

    def input_keypad_get(self) -> int:
        return self._lib.desmume_input_keypad_get()

    def input_set_touch_pos(self, x: int, y: int) -> None:
        """Set touchscreen press position (bottom screen coords: 0-255, 0-191)."""
        self._lib.desmume_input_set_touch_pos(
            ctypes.c_ushort(x), ctypes.c_ushort(y)
        )

    def input_release_touch(self) -> None:
        self._lib.desmume_input_release_touch()

    # ── Savestates ──

    def savestate_save(self, filename: str) -> bool:
        return bool(self._lib.desmume_savestate_save(filename.encode("utf-8")))

    def savestate_load(self, filename: str) -> bool:
        return bool(self._lib.desmume_savestate_load(filename.encode("utf-8")))

    def savestate_slot_save(self, index: int) -> None:
        self._lib.desmume_savestate_slot_save(index)

    def savestate_slot_load(self, index: int) -> None:
        self._lib.desmume_savestate_slot_load(index)

    def savestate_slot_exists(self, index: int) -> bool:
        return bool(self._lib.desmume_savestate_slot_exists(index))

    # ── Memory ──

    def memory_read_byte(self, address: int) -> int:
        return self._lib.desmume_memory_read_byte(address)

    def memory_read_byte_signed(self, address: int) -> int:
        return self._lib.desmume_memory_read_byte_signed(address)

    def memory_read_short(self, address: int) -> int:
        return self._lib.desmume_memory_read_short(address)

    def memory_read_short_signed(self, address: int) -> int:
        return self._lib.desmume_memory_read_short_signed(address)

    def memory_read_long(self, address: int) -> int:
        # c_ulong is 8 bytes on 64-bit Linux but the NDS value is 32-bit
        return self._lib.desmume_memory_read_long(address) & 0xFFFFFFFF

    def memory_read_long_signed(self, address: int) -> int:
        val = self._lib.desmume_memory_read_long_signed(address)
        # Mask to 32-bit and sign-extend
        val &= 0xFFFFFFFF
        if val >= 0x80000000:
            val -= 0x100000000
        return val

    def memory_write_byte(self, address: int, value: int) -> None:
        self._lib.desmume_memory_write_byte(address, ctypes.c_ubyte(value))

    def memory_write_short(self, address: int, value: int) -> None:
        self._lib.desmume_memory_write_short(address, ctypes.c_ushort(value))

    def memory_write_long(self, address: int, value: int) -> None:
        self._lib.desmume_memory_write_long(address, ctypes.c_ulong(value))

    # ── Settings ──

    def set_language(self, language: int) -> None:
        self._lib.desmume_set_language(ctypes.c_ubyte(language))

    def volume_get(self) -> int:
        return self._lib.desmume_volume_get()

    def volume_set(self, volume: int) -> None:
        self._lib.desmume_volume_set(volume)

    # ── Movies ──

    def movie_record_simple(self, filename: str, author: str) -> None:
        self._lib.desmume_movie_record_simple(
            filename.encode("utf-8"), author.encode("utf-8")
        )

    def movie_play(self, filename: str) -> str | None:
        """Play a movie. Returns None on success, error string on failure."""
        result = self._lib.desmume_movie_play(filename.encode("utf-8"))
        if result:
            return result.decode("utf-8")
        return None

    def movie_stop(self) -> None:
        self._lib.desmume_movie_stop()

    def movie_is_active(self) -> bool:
        return bool(self._lib.desmume_movie_is_active())

    def movie_is_recording(self) -> bool:
        return bool(self._lib.desmume_movie_is_recording())

    def movie_is_playing(self) -> bool:
        return bool(self._lib.desmume_movie_is_playing())

    # ── Backup (battery save) ──

    def backup_import_file(self, filename: str, force_size: int = 0) -> bool:
        return bool(
            self._lib.desmume_backup_import_file(
                filename.encode("utf-8"), force_size
            )
        )

    def backup_export_file(self, filename: str) -> bool:
        return bool(
            self._lib.desmume_backup_export_file(filename.encode("utf-8"))
        )
