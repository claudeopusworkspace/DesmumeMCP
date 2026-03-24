"""DS button bitmasks, screen dimensions, and related constants."""

from enum import IntEnum, IntFlag

# Screen dimensions (per screen)
SCREEN_WIDTH = 256
SCREEN_HEIGHT = 192

# Both screens stacked vertically
TOTAL_WIDTH = SCREEN_WIDTH
TOTAL_HEIGHT = SCREEN_HEIGHT * 2  # 384

# Pixel counts
SCREEN_PIXEL_COUNT = SCREEN_WIDTH * SCREEN_HEIGHT  # 49152
TOTAL_PIXEL_COUNT = SCREEN_PIXEL_COUNT * 2  # 98304

# Screenshot buffer sizes
SCREENSHOT_RGB_SIZE = TOTAL_PIXEL_COUNT * 3  # 294912 bytes
SCREENSHOT_RGBX_SIZE = TOTAL_PIXEL_COUNT * 4  # 393216 bytes

# DS runs at ~60fps
FRAMES_PER_SECOND = 60


class Key(IntEnum):
    """Key indices matching ctrlssdl.h definitions."""

    NONE = 0
    A = 1
    B = 2
    SELECT = 3
    START = 4
    RIGHT = 5
    LEFT = 6
    UP = 7
    DOWN = 8
    R = 9
    L = 10
    X = 11
    Y = 12
    DEBUG = 13
    BOOST = 14
    LID = 15


def keymask(key: Key) -> int:
    """Convert key index to bitmask: KEYMASK_(k) = (1 << k)."""
    return 1 << key


class KeyMask(IntFlag):
    """Pre-computed key bitmasks for desmume_input_keypad_update."""

    NONE = 0
    A = 1 << Key.A  # 0x0002
    B = 1 << Key.B  # 0x0004
    SELECT = 1 << Key.SELECT  # 0x0008
    START = 1 << Key.START  # 0x0010
    RIGHT = 1 << Key.RIGHT  # 0x0020
    LEFT = 1 << Key.LEFT  # 0x0040
    UP = 1 << Key.UP  # 0x0080
    DOWN = 1 << Key.DOWN  # 0x0100
    R = 1 << Key.R  # 0x0200
    L = 1 << Key.L  # 0x0400
    X = 1 << Key.X  # 0x0800
    Y = 1 << Key.Y  # 0x1000


# String-to-bitmask lookup for MCP tool convenience
BUTTON_MAP: dict[str, int] = {
    "a": KeyMask.A,
    "b": KeyMask.B,
    "x": KeyMask.X,
    "y": KeyMask.Y,
    "l": KeyMask.L,
    "r": KeyMask.R,
    "start": KeyMask.START,
    "select": KeyMask.SELECT,
    "up": KeyMask.UP,
    "down": KeyMask.DOWN,
    "left": KeyMask.LEFT,
    "right": KeyMask.RIGHT,
}

# All valid button names (for error messages)
VALID_BUTTONS = sorted(BUTTON_MAP.keys())


def buttons_to_bitmask(buttons: list[str]) -> int:
    """Convert a list of button names to a u16 bitmask.

    Args:
        buttons: List of button names like ["a", "up", "r"].

    Returns:
        Combined bitmask for desmume_input_keypad_update.

    Raises:
        ValueError: If an unknown button name is provided.
    """
    mask = 0
    for btn in buttons:
        btn_lower = btn.lower().strip()
        if btn_lower not in BUTTON_MAP:
            raise ValueError(
                f"Unknown button: {btn!r}. Valid buttons: {VALID_BUTTONS}"
            )
        mask |= BUTTON_MAP[btn_lower]
    return mask


class Language(IntEnum):
    """Firmware language settings."""

    JAPANESE = 0
    ENGLISH = 1
    FRENCH = 2
    GERMAN = 3
    ITALIAN = 4
    SPANISH = 5
