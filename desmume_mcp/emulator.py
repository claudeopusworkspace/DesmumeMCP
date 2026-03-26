"""Stateful emulator holder — lifecycle management, input helpers, screenshot capture."""

from __future__ import annotations

import base64
import io
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .constants import (
    SCREENSHOT_RGB_SIZE,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    TOTAL_HEIGHT,
    TOTAL_WIDTH,
    buttons_to_bitmask,
)
from .libdesmume import DeSmuME


def _ensure_headless_env() -> None:
    """Set SDL env vars for headless operation if no display is detected."""
    if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@dataclass
class EmulatorState:
    """Singleton holder for DeSmuME instance and associated state."""

    emu: DeSmuME | None = None
    rom_path: str | None = None
    is_initialized: bool = False
    is_rom_loaded: bool = False
    frame_count: int = 0
    data_dir: Path = field(default_factory=lambda: Path.cwd())
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def savestates_dir(self) -> Path:
        d = self.data_dir / "savestates"
        d.mkdir(exist_ok=True)
        return d

    @property
    def movies_dir(self) -> Path:
        d = self.data_dir / "movies"
        d.mkdir(exist_ok=True)
        return d

    @property
    def macros_dir(self) -> Path:
        d = self.data_dir / "macros"
        d.mkdir(exist_ok=True)
        return d

    @property
    def watches_dir(self) -> Path:
        d = self.data_dir / "watches"
        d.mkdir(exist_ok=True)
        return d

    @property
    def snapshots_dir(self) -> Path:
        d = self.data_dir / "snapshots"
        d.mkdir(exist_ok=True)
        return d

    def initialize(self) -> str:
        """Initialize the DeSmuME engine. Must be called first."""
        if self.is_initialized:
            return "Already initialized."

        _ensure_headless_env()
        self.emu = DeSmuME()
        result = self.emu.init()
        if result == -1:
            raise RuntimeError("desmume_init() failed (SDL init error?)")

        self.is_initialized = True
        return "DeSmuME initialized successfully."

    def load_rom(self, rom_path: str) -> str:
        """Load a ROM file. Requires initialization first."""
        if not self.is_initialized or self.emu is None:
            raise RuntimeError("Call init_emulator first.")

        path = Path(rom_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"ROM not found: {path}")

        result = self.emu.open(str(path))
        if result < 1:
            raise RuntimeError(f"Failed to load ROM: {path} (error code: {result})")

        self.rom_path = str(path)
        self.is_rom_loaded = True
        self.frame_count = 0
        self.emu.resume()
        return f"ROM loaded: {path.name}"

    def _require_rom(self) -> DeSmuME:
        """Guard: require a ROM to be loaded. Returns the emu instance."""
        if not self.is_rom_loaded or self.emu is None:
            raise RuntimeError("No ROM loaded. Call load_rom first.")
        return self.emu

    def advance_frame(
        self,
        buttons: list[str] | None = None,
        touch_x: int | None = None,
        touch_y: int | None = None,
    ) -> None:
        """Set input and advance one frame."""
        emu = self._require_rom()

        # Set keypad
        bitmask = buttons_to_bitmask(buttons) if buttons else 0
        emu.input_keypad_update(bitmask)

        # Set touch
        if touch_x is not None and touch_y is not None:
            emu.input_set_touch_pos(touch_x, touch_y)
        else:
            emu.input_release_touch()

        emu.cycle(with_joystick=False)
        self.frame_count += 1

    def advance_frames(
        self,
        count: int,
        buttons: list[str] | None = None,
        touch_x: int | None = None,
        touch_y: int | None = None,
    ) -> int:
        """Advance multiple frames holding the same input. Returns frames advanced."""
        for _ in range(count):
            self.advance_frame(buttons, touch_x, touch_y)
        return count

    def press_buttons(self, buttons: list[str], frames: int = 1) -> None:
        """Press buttons for N frames, then release for 1 frame."""
        # Hold
        for _ in range(frames):
            self.advance_frame(buttons)
        # Release
        self.advance_frame()

    def tap_touch_screen(self, x: int, y: int, frames: int = 1) -> None:
        """Tap the touchscreen for N frames, then release for 1 frame."""
        for _ in range(frames):
            self.advance_frame(touch_x=x, touch_y=y)
        # Release
        self.advance_frame()

    def run_macro_steps(self, steps: list[dict]) -> int:
        """Execute a list of macro steps. Returns total frames advanced."""
        frames_before = self.frame_count
        for step in steps:
            action = step["action"]
            if action == "press":
                self.press_buttons(step["buttons"], step.get("frames", 1))
            elif action == "hold":
                self.advance_frames(
                    step.get("frames", 1),
                    step.get("buttons"),
                    step.get("touch_x"),
                    step.get("touch_y"),
                )
            elif action == "wait":
                self.advance_frames(step.get("frames", 1))
            elif action == "tap":
                self.tap_touch_screen(
                    step["x"], step["y"], step.get("frames", 1)
                )
            else:
                raise ValueError(f"Unknown macro action: {action!r}")
        return self.frame_count - frames_before

    def capture_screenshot(
        self, screen: str = "both", fmt: str = "png"
    ) -> tuple[str, bytes]:
        """Capture the current screen as a base64-encoded image.

        Args:
            screen: "top", "bottom", or "both".
            fmt: "png" or "jpeg".

        Returns:
            Tuple of (mime_type, image_bytes).
        """
        emu = self._require_rom()
        raw_rgb = emu.screenshot()

        assert len(raw_rgb) == SCREENSHOT_RGB_SIZE

        img = Image.frombytes("RGB", (TOTAL_WIDTH, TOTAL_HEIGHT), raw_rgb)

        if screen == "top":
            img = img.crop((0, 0, SCREEN_WIDTH, SCREEN_HEIGHT))
        elif screen == "bottom":
            img = img.crop((0, SCREEN_HEIGHT, SCREEN_WIDTH, SCREEN_HEIGHT * 2))

        buf = io.BytesIO()
        if fmt == "jpeg":
            img.save(buf, format="JPEG", quality=85)
            mime = "image/jpeg"
        else:
            img.save(buf, format="PNG")
            mime = "image/png"

        return mime, buf.getvalue()

    def capture_screenshot_base64(
        self, screen: str = "both", fmt: str = "png"
    ) -> str:
        """Capture the current screen as a base64-encoded string."""
        _, image_bytes = self.capture_screenshot(screen, fmt)
        return base64.b64encode(image_bytes).decode("ascii")
