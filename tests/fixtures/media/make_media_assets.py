"""Regenerate the media used by tests/test_assets_full.py and tests/fixtures/assets_full.xml.

    .venv/bin/python tests/fixtures/media/make_media_assets.py

lottie_demo.json   200x150, 30 fps, 60 frames: a text layer "title" ("Hello", DejaVu Sans Bold, a range
                   selector fading/raising characters in over frames 0-40), a shape layer "box" (rounded
                   square moving x 40 -> 160 over frames 0-30, fill colour in Lottie slot "accent"), a solid
                   layer "panel"; markers intro [0, 30) and outro [30, 60).
lottie_demo.lottie dotLottie 1 container with animations "demo" (as above) and "alt" (box green), active "alt".
log10.mkv          64x16, 10 fps, 0.5 s, ffv1 gbrp10le (10-bit RGB): left half S-Log3 18 % grey, right half a
                   32-step code ramp 0..1023 (known code values, no YUV matrix involved).
hlg10.mkv          64x36, 10 fps, 1 s, lavfi testsrc2 -> yuv422p10le ffv1, tagged BT.2020 / ARIB STD-B67 (HLG).
chart_data.json    labels + two series + categoryTitle / valueTitle.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _kf(t0, v0, t1, v1):
    return {"a": 1, "k": [{"t": t0, "s": v0, "o": {"x": [0.33], "y": [0]}, "i": {"x": [0.67], "y": [1]}},
                          {"t": t1, "s": v1}]}


def _st(v):
    return {"a": 0, "k": v}


def _ks(p, s=(100, 100, 100)):
    return {"o": _st(100), "r": _st(0), "p": p, "a": _st([0, 0, 0]), "s": _st(list(s))}


def lottie(box_colour=(1, 0.2, 0.2, 1)) -> dict:
    text = {"ddd": 0, "ind": 1, "ty": 5, "nm": "title", "sr": 1, "ip": 0, "op": 60, "st": 0, "ks": _ks(_st([100, 45, 0])),
            "t": {"d": {"k": [{"s": {"s": 28, "f": "Sans-Bold", "t": "Hello", "j": 2, "tr": 0, "lh": 34, "ls": 0,
                                     "fc": [1, 1, 1]}, "t": 0}]},
                  "p": {}, "m": {"g": 1, "a": _st([0, 0])},
                  "a": [{"nm": "fade", "s": {"t": 0, "xe": _st(0), "ne": _st(0), "a": _st(100), "b": 1, "rn": 0, "sh": 1,
                                             "r": 1, "s": _kf(0, [0], 40, [100]), "e": _st(100), "o": _st(0)},
                         "a": {"o": _st(0), "p": _st([0, -20])}}]}}
    box = {"ddd": 0, "ind": 2, "ty": 4, "nm": "box", "sr": 1, "ip": 0, "op": 60, "st": 0,
           "ks": _ks(_kf(0, [40, 100, 0], 30, [160, 100, 0])),
           "shapes": [{"ty": "gr", "nm": "g", "it": [
               {"ty": "rc", "nm": "r", "p": _st([0, 0]), "s": _st([40, 40]), "r": _st(6)},
               {"ty": "fl", "nm": "boxfill", "c": {"a": 0, "k": list(box_colour), "sid": "accent"}, "o": _st(100), "r": 1},
               {"ty": "tr", "p": _st([0, 0]), "a": _st([0, 0]), "s": _st([100, 100]), "r": _st(0), "o": _st(100)}]}]}
    panel = {"ddd": 0, "ind": 3, "ty": 1, "nm": "panel", "sr": 1, "ip": 0, "op": 60, "st": 0,
             "ks": _ks(_st([100, 140, 0])), "sw": 200, "sh": 20, "sc": "#203040"}
    panel["ks"]["a"] = _st([100, 10, 0])
    return {"v": "5.7.0", "fr": 30, "ip": 0, "op": 60, "w": 200, "h": 150, "nm": "demo", "ddd": 0, "assets": [],
            "fonts": {"list": [{"fName": "Sans-Bold", "fFamily": "DejaVu Sans", "fStyle": "Bold", "ascent": 75}]},
            "markers": [{"tm": 0, "cm": "intro", "dr": 30}, {"tm": 30, "cm": "outro", "dr": 30}],
            "layers": [text, box, panel]}


def main() -> None:
    d = lottie()
    with open(os.path.join(HERE, "lottie_demo.json"), "w") as f:
        json.dump(d, f, separators=(",", ":"))
    alt = copy.deepcopy(lottie((0.2, 0.9, 0.3, 1)))
    alt["nm"] = "alt"
    with zipfile.ZipFile(os.path.join(HERE, "lottie_demo.lottie"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"version": "1", "generator": "scenerender tests",
                                                "animations": [{"id": "demo"}, {"id": "alt"}],
                                                "activeAnimationId": "alt"}))
        z.writestr("animations/demo.json", json.dumps(d))
        z.writestr("animations/alt.json", json.dumps(alt))
    # 10-bit S-Log3 clip with known code values
    import sys
    sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
    from scenerender import color
    w, h = 64, 16
    code = np.zeros((h, w, 3))
    code[:, :32] = float(color.encode(np.array(0.18), "slog3"))
    code[:, 32:] = np.linspace(0, 1, 32)[None, :, None]
    q = (np.round(code * 1023).astype(np.uint16) * 64).astype("<u2")
    frames = np.repeat(q[None], 5, 0)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}",
                    "-r", "10", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gbrp10le", os.path.join(HERE, "log10.mkv")],
                   input=frames.tobytes(), check=True)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=10:duration=1",
                    "-vf", "format=yuv422p10le", "-c:v", "ffv1", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
                    "-color_primaries", "bt2020", "-color_range", "tv", os.path.join(HERE, "hlg10.mkv")], check=True)
    with open(os.path.join(HERE, "chart_data.json"), "w") as f:
        json.dump({"labels": ["2022", "2023", "2024", "2025"], "categoryTitle": "Year", "valueTitle": "Renders (k)",
                   "series": [{"name": "Scenes", "values": [12, 30, 71, 140]},
                              {"name": "Templates", "values": [2, 6, 15, 33], "color": "#FF6B6BFF"}]}, f, indent=1)


if __name__ == "__main__":
    main()
