"""xdotool-style key names ("ctrl+shift+x", "F12", "Return") -> Windows virtual-key codes. Pure Python, testable on any OS."""
from __future__ import annotations

MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "super": 0x5B, "meta": 0x5B}
NAMED = {"return": 0x0D, "enter": 0x0D, "escape": 0x1B, "esc": 0x1B, "space": 0x20, "tab": 0x09, "backspace": 0x08, "delete": 0x2E,
         "home": 0x24, "end": 0x23, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "page_up": 0x21, "page_down": 0x22, "prior": 0x21,
         "next": 0x22, "insert": 0x2D, "kp_add": 0x6B, "kp_subtract": 0x6D, "plus": 0xBB, "minus": 0xBD, "pause": 0x13}


def vk(name: str) -> int:
    n = name.lower()
    if n in NAMED:
        return NAMED[n]
    if n in MODIFIERS:
        return MODIFIERS[n]
    if len(n) == 1 and n.isalnum():
        return ord(n.upper())                     # VK_A..VK_Z / VK_0..VK_9 equal their ASCII codes
    if n.startswith("f") and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x70 + int(n[1:]) - 1
    raise ValueError(f"unknown key {name!r}")


def parse(combo: str) -> list[int]:
    """"ctrl+shift+x" -> [VK_CONTROL, VK_SHIFT, VK_X] (modifiers first, in the order written; the last item is the main key)."""
    keys = [vk(p) for p in combo.split("+") if p]
    if not keys:
        raise ValueError("empty key")
    return keys


def sequence(keys: str) -> list[list[int]]:
    """Space separated combos ("Return F12 ctrl+o") -> list of combos."""
    return [parse(c) for c in keys.split()]
