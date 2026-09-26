"""Safe-area insets (fractions of the frame: left, top, right, bottom).

Preset values are this renderer's own approximations of each platform's
published overlay zones; explicit top/right/bottom/left attributes win.
"""
from __future__ import annotations

PRESETS = {
    "custom": (0.0, 0.0, 0.0, 0.0),
    "title-safe": (0.1, 0.1, 0.1, 0.1),
    "action-safe": (0.05, 0.05, 0.05, 0.05),
    "instagram-reels": (0.06, 0.14, 0.12, 0.20),
    "instagram-stories": (0.06, 0.14, 0.06, 0.20),
    "instagram-feed": (0.04, 0.04, 0.04, 0.04),
    "facebook-reels": (0.06, 0.14, 0.12, 0.20),
    "tiktok": (0.06, 0.12, 0.15, 0.20),
    "youtube-shorts": (0.06, 0.12, 0.15, 0.18),
    "snapchat": (0.06, 0.10, 0.06, 0.18),
    "pinterest-idea": (0.06, 0.12, 0.06, 0.20),
}


def insets(sa, width: int, height: int) -> tuple[float, float, float, float]:
    l, t, r, b = PRESETS.get(sa.get("preset", "custom"), PRESETS["custom"])
    return (float(sa.get("left", l)), float(sa.get("top", t)), float(sa.get("right", r)), float(sa.get("bottom", b)))
