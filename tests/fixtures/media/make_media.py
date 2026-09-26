"""Regenerate the small media files used by tests/test_assets.py and tests/fixtures/assets_demo.xml.

    .venv/bin/python tests/fixtures/media/make_media.py

All files are deterministic and tiny (a few KiB each).
"""
from __future__ import annotations

import math
import os
import subprocess
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def testsrc(path: str) -> None:
    """64x36, 10 fps, 1 s testsrc pattern (h264) with a 440 Hz audio track."""
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "5", "-c:a", "aac", "-b:a", "32k",
                    "-shortest", path], check=True)


def index_video(path: str, n: int, fps: str, w: int = 16, h: int = 8, alpha: int = 255) -> None:
    """Lossless (ffv1, bgra) video whose frame i is a flat grey of level 10*i (alpha constant)."""
    frames = np.zeros((n, h, w, 4), np.uint8)
    for i in range(n):
        frames[i, ..., :3] = min(255, 10 * i)
        frames[i, ..., 3] = alpha
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                    "-r", fps, "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgra", path],
                   input=frames.tobytes(), check=True)


def tone(path: str) -> None:
    """1 s, 8 kHz mono: 0-0.5 s quiet 440 Hz tone; 0.5-1 s loud 220 Hz harmonic stack plus noise."""
    sr = 8000
    t = np.arange(sr) / sr
    rng = np.random.default_rng(1)
    noise = np.convolve(rng.standard_normal(sr), np.ones(4) / 4, mode="same")
    stack = sum(np.sin(2 * math.pi * 220 * k * t) / k for k in range(1, 12))
    loud = 0.35 * stack * (0.6 + 0.4 * np.sin(2 * math.pi * 6 * t)) + 0.15 * noise
    x = np.where(t < 0.5, 0.2 * np.sin(2 * math.pi * 440 * t), loud)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(pcm.tobytes())


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
  <rect x="0" y="0" width="100" height="100" rx="12" fill="#1A2340"/>
  <g transform="translate(50 50)">
    <circle r="30" fill="#3DD6D0" stroke="#F4F6FB" stroke-width="4"/>
    <path d="M-15 0 L-4 11 L16 -12" fill="none" stroke="#0B0F1A" stroke-width="7" stroke-linecap="round"/>
  </g>
  <polygon points="10,90 20,70 30,90" fill="#FFC23D"/>
  <ellipse cx="80" cy="82" rx="12" ry="6" fill="rgb(255,90,90)"/>
</svg>
"""


def gen_png(path: str) -> None:
    """32x32 RGBA picture standing in for a provider-generated image."""
    from PIL import Image
    y, x = np.mgrid[0:32, 0:32]
    a = np.zeros((32, 32, 4), np.uint8)
    a[..., 0] = x * 8
    a[..., 1] = y * 8
    a[..., 2] = 160
    a[..., 3] = 255
    a[(x - 16) ** 2 + (y - 16) ** 2 < 64] = (255, 255, 255, 255)
    Image.fromarray(a, "RGBA").save(path, optimize=False)


CSV = "label,North,South\nQ1,12,8\nQ2,18,11\nQ3,9,15\nQ4,22,19\n"


def main() -> None:
    testsrc(os.path.join(HERE, "testsrc.mp4"))
    index_video(os.path.join(HERE, "index.mkv"), 24, "12")
    index_video(os.path.join(HERE, "index_ntsc.mkv"), 20, "30000/1001")
    index_video(os.path.join(HERE, "index_alpha.mkv"), 4, "4", alpha=128)
    tone(os.path.join(HERE, "tone.wav"))
    with open(os.path.join(HERE, "badge.svg"), "w") as f:
        f.write(SVG)
    gen_png(os.path.join(HERE, "gen.png"))
    with open(os.path.join(HERE, "chart.csv"), "w") as f:
        f.write(CSV)


if __name__ == "__main__":
    main()
