from __future__ import annotations

import hashlib
import logging
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scenerender import document, tracking
from scenerender.registry import FEATURES, FULL
from scenerender.tracking import Ctx, load_track, mask_path, find_track

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "tracking")
sys.path.insert(0, FIX)
import make_fbx  # noqa: E402


@pytest.fixture(scope="module")
def doc():
    return document.load(os.path.join(FIX, "track_scene.xml"), strict=True)


def track(doc, tid):
    tracking._CACHE.clear()
    return load_track(doc, find_track(doc, tid))


def xy(s):
    return s["x"], s["y"]


# ---------------------------------------------------------------- json
def test_json_point_frames_interp_hold(doc):
    tr = track(doc, "td-json")
    assert list(tr.channels) == ["eye_left", "eye_right"] and tr.format == "json"
    ch = tr.channels["eye_left"]
    np.testing.assert_allclose(ch.times, [0, 0.2])              # file fps 25: frame 5 -> 0.2 s
    assert xy(tr.sample("eye_left", 0.1)) == pytest.approx((125, 190))
    s = tr.sample(None, 0.1)
    assert s["rotation"] == pytest.approx(5) and s["scaleX"] == s["scaleY"] == pytest.approx(1.5)
    assert xy(tr.sample("eye_left", -3)) == (100, 200)          # hold before
    assert xy(tr.sample("eye_left", 9)) == (150, 180)           # hold after
    r = tr.sample("eye_right", 0.2)                              # sparse key: scaleX only on frame 10
    assert xy(r) == pytest.approx((350, 225)) and r["scaleX"] == 1.5
    np.testing.assert_allclose(tr.times(), [0, 0.2, 0.4])


def test_footage_fps_wins_and_time_offset(doc):
    tr = track(doc, "td-json-plate")                            # plate asset fps 24 beats file fps 25
    np.testing.assert_allclose(tr.channels["eye_left"].times, [0, 5 / 24])
    off = track(doc, "td-json-offset")
    assert off.time_offset == 1.5
    assert xy(off.sample("eye_left", 1.6)) == pytest.approx((125, 190))
    assert xy(off.sample("eye_left", 0.0)) == (100, 200)


def test_point_resolution(doc):
    tr = track(doc, "td-json")
    assert tr.channel(None)[0].name == tr.channel("")[0].name == "eye_left"
    assert tr.channel("1") == (tr.channels["eye_right"], None)
    assert tr.channel("eye_right:3") == (tr.channels["eye_right"], "3")
    assert tr.channel(":center") == (tr.channels["eye_left"], "center")
    assert tr.channel("nope") == (None, None) and tr.sample("nope", 0) is None
    assert tr.sample("eye_left:tl", 0) is None                   # no corners on a point track


def test_planar_corners(doc):
    tr = track(doc, "td-planar")
    s = tr.sample("screen", 0.5)
    np.testing.assert_allclose(s["corners"], [[150, 100], [550, 120], [530, 400], [170, 380]])
    assert xy(tr.sample(":tl", 0.5)) == (150, 100)
    assert xy(tr.sample("screen:br", 1.0)) == (580, 400)
    assert xy(tr.sample("center", 0.0)) == pytest.approx((300, 250))   # bare sub keyword
    assert xy(tr.sample("0:bl", 0.0)) == (120, 380)


def test_mask_points_and_path(doc):
    td = find_track(doc, "td-mask")
    tracking._CACHE.clear()
    assert mask_path(doc, td, None, 1.0) == [("M", 20, 10), ("L", 120, 10), ("L", 120, 60), ("L", 10, 60), ("Z",)]
    tr = load_track(doc, td)
    assert tr.kind == "mask" and xy(tr.sample(":2", 2.0)) == (130, 60)
    assert mask_path(doc, find_track(doc, "td-planar"), None, 0)[0] == ("M", 100, 100)
    with pytest.raises(tracking.TrackError):
        tracking.parse_json('{"tracks": {"m": [{"t": 0, "path": "M 0 0 C 1 1 2 2 3 3"}]}}')
    with pytest.raises(tracking.TrackError):                     # vertex count must be fixed
        tracking.parse_json('{"tracks": {"m": [{"t": 0, "points": [[0,0],[1,1]]}, {"t": 1, "points": [[0,0]]}]}}')


def test_face_landmarks(doc):
    tr = track(doc, "td-face")
    s = tr.sample("face0:2", 0.5)
    assert xy(s) == pytest.approx((650, 375)) and s["points"].shape == (5, 2)
    assert tr.sample("face0:9", 0.5) is None
    assert xy(tr.sample(":center", 0)) == pytest.approx((640, 376))


# ---------------------------------------------------------------- csv
def test_csv_interleaved(doc):
    for tid in ("td-csv", "td-csv-sniff"):
        tr = track(doc, tid)
        assert tr.format == "csv" and list(tr.channels) == ["A", "B"]
        a = tr.channels["A"]
        np.testing.assert_allclose(a.times, [0, 1 / 30, 2 / 30])    # frames at the document fps
        np.testing.assert_allclose(a.data["scaleX"], [1, 1.1, 1.2])  # "Scale (%)" is percent
        assert "rotation" not in tr.channels["B"].data               # empty cells are no keys
        assert xy(tr.sample("B", 2 / 30)) == pytest.approx((512.5, 575))


def test_csv_variants():
    ch = tracking.parse_csv("t,x0,y0,x1,y1,x2,y2,x3,y3,scale\n0,0,0,10,0,10,10,0,10,2\n", Ctx())
    c = ch["track"]
    assert c.data["corners"].shape == (1, 4, 2) and c.data["scaleX"][0] == 2
    ch = tracking.parse_csv("Time\tTL_X\tTL_Y\tTR_X\tTR_Y\tBR_X\tBR_Y\tBL_X\tBL_Y\n1\t1\t2\t3\t4\t5\t6\t7\t8\n")
    np.testing.assert_allclose(ch["track"].data["corners"][0], [[1, 2], [3, 4], [5, 6], [7, 8]])
    with pytest.raises(tracking.TrackError):
        tracking.parse_csv("x,y\n1,2\n")


# ---------------------------------------------------------------- nuke
def test_nuke_script(doc):
    tr = track(doc, "td-nk")                                     # plate: 1920x1080 @ 24
    t1 = tr.channels["Tracker1/track1"]
    np.testing.assert_allclose(t1.times, [1 / 24, 2 / 24, 3 / 24])   # frame f -> f / fps (1-based kept)
    np.testing.assert_allclose(t1.data["y"], [880, 879, 878])        # y-up -> 1080 - y
    assert xy(tr.sample("track1", 0)) == (100, 880)                   # lookup by last path component
    assert xy(tr.sample("Tracker1/track2", 5)) == (300, 680)          # constant knob
    t4 = tr.sample("track 1", 11 / 24)                                # Tracker4 table
    assert xy(t4) == pytest.approx((510, 490))
    assert xy(tr.sample("Tracker2/track 2", 0)) == (700, 280)
    tf = tr.sample("Transform1", 3 / 24)
    assert xy(tf) == pytest.approx((980, 550))                        # center + translate, flipped
    assert (tf["anchorX"], tf["anchorY"]) == (960, 540)
    assert tf["rotation"] == pytest.approx(-30)                       # CCW y-up -> clockwise screen
    assert (tf["scaleX"], tf["scaleY"]) == (1.5, 2)
    cp = tr.sample("CornerPin1", 2 / 24)["corners"]                   # to4,to3,to2,to1 -> TL,TR,BR,BL
    np.testing.assert_allclose(cp, [[100, 380], [900, 380], [910, 980], [110, 980]])


def _hermite(p0, p1, m0, m1, h, u):
    return ((2 * u**3 - 3 * u**2 + 1) * p0 + (u**3 - 2 * u**2 + u) * h * m0
            + (-2 * u**3 + 3 * u**2) * p1 + (u**3 - u**2) * h * m1)


def _bezier_at(P, x):
    """Independent cubic Bezier evaluation: bisection on x(s) then y(s)."""
    b = lambda c, s: (1 - s)**3 * c[0] + 3 * (1 - s)**2 * s * c[1] + 3 * (1 - s) * s * s * c[2] + s**3 * c[3]
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if b([p[0] for p in P], mid) < x else (lo, mid)
    return b([p[1] for p in P], (lo + hi) / 2)


def test_nuke_curve_interpolation(doc):
    # {curve L x1 0 x5 40 S x9 60 x13 20 R x17 30 x21 50 K x25 50 S x29 10 s-5 t2 u2 v0.5 x33 30}
    # slopes per frame: k0 L r=10 | k1 L l=10 r=5 | k2 S max -> 0 | k3 S min -> 0 | k4 R (50-20)/8=3.75
    # k5 R (50-30)/8=2.5 | k6 K hold | k7 explicit l=-5 r=2, handles u2 v0.5 | k8 S end -> 0
    tr = track(doc, "td-nk")
    x = lambda f: tr.sample("Mixed/track1", f / 24)["x"]
    assert x(0) == 0 and x(40) == 30                                   # constant extrapolation
    assert x(3) == pytest.approx(20)                                   # L-L segment is exactly linear
    assert x(7) == pytest.approx(_hermite(40, 60, 5, 0, 4, .5))        # 52.5
    assert x(10) == pytest.approx(_hermite(60, 20, 0, 0, 4, .25))      # 53.75
    assert x(15) == pytest.approx(_hermite(20, 30, 0, 3.75, 4, .5))    # 23.125
    assert x(19) == pytest.approx(_hermite(30, 50, 3.75, 2.5, 4, .5))  # 40.625
    assert x(23) == pytest.approx(_hermite(50, 50, 2.5, 0, 4, .5))     # 51.25
    assert x(27) == 50                                                 # K holds
    P = [(29, 10), (29 + 0.5 * 4 / 3, 10 + 0.5 * 4 / 3 * 2), (33 - 4 / 3, 30), (33, 30)]
    assert x(31) == pytest.approx(_bezier_at(P, 31))                   # weighted handle (v0.5)
    assert tr.channels["Mixed/track1"].data["x"].tolist() == [0, 40, 60, 20, 30, 50, 50, 10, 30]
    # {curve C x0 0 1 0}: natural spline slopes 1.5, 0, -1.5; x inferred 0, 1 (first + 1), 2 (extrapolated)
    c = tr.channels["Mixed/track2"]
    np.testing.assert_allclose(c.times, [0, 1 / 24, 2 / 24])
    assert c.sample(0.5 / 24)["x"] == pytest.approx(_hermite(0, 1, 1.5, 0, 1, .5))   # 0.6875
    # {curve l L x1 0 x2 10}: linear extrapolation (y flipped against 1080)
    assert (c.sample(0)["y"], c.sample(3 / 24)["y"]) == pytest.approx((1090, 1060))


def test_nuke_curve_grammar():
    k = tracking._nk_curve(["curve", "x10", "1", "2", "x20", "3", "4", "5"]).keys
    assert [q["x"] for q in k] == [10, 11, 20, 29, 38]                 # NDK: extrapolate the last two
    assert tracking._nk_curve(["curve", "1", "2"]).keys[0]["x"] == 0
    assert tracking._nk_curve(["curve", "x1", "5", "s2", "t3", "u0.5", "v1.5"]).keys[0] | {} == {
        "x": 1, "y": 5, "i": "S", "e": "k", "s": 2, "t": 3, "u": .5, "v": 1.5}
    assert tracking._nk_comps([["curve(frame*2)", "x1", "0", "x2", "1"]]) == [None]   # time-remap expression
    assert tracking._nk_comps([["parent.Transform1.rotate"]]) == [None]              # knob expression


def test_nuke_height_precedence():
    text = open(os.path.join(FIX, "track.nk")).read()
    root = tracking.parse_nuke(text, Ctx(doc_height=720))              # Root format 1080 beats doc
    assert root["Tracker1/track2"].data["y"][0] == 680
    asset = tracking.parse_nuke(text, Ctx(doc_height=720, asset_height=2000))
    assert asset["Tracker1/track2"].data["y"][0] == 1600
    noroot = tracking.parse_nuke("CornerPin2D {\n to1 {10 20}\n name P\n}\n", Ctx(doc_width=200, doc_height=100))
    np.testing.assert_allclose(noroot["P"].data["corners"][0], [[0, 0], [200, 0], [200, 100], [10, 80]])


def test_nuke_chan(doc):
    tr = track(doc, "td-chan")
    c = tr.channels["camera"]
    np.testing.assert_allclose(c.times, [1 / 30, 2 / 30, 3 / 30])
    s = tr.sample("camera", 2.5 / 30)
    assert (s["x"], s["z"], s["ry"], s["fov"]) == pytest.approx((0.75, 9.25, -3, 39))


# ---------------------------------------------------------------- after effects / mocha
def test_after_effects(doc):
    tr = track(doc, "td-ae")
    assert list(tr.channels)[:2] == ["Tracker #1/Track Point #1", "Tracker #1/Track Point #2"]
    np.testing.assert_allclose(tr.channels["Tracker #1/Track Point #1"].times, [0, 1 / 24, 2 / 24, 3 / 24])
    assert xy(tr.sample("Track Point #1", 1.5 / 24)) == pytest.approx((966, 537))
    assert xy(tr.sample("Track Point #2", 1 / 24)) == pytest.approx((20, 30))   # attach point fallback
    t = tr.sample("transform", 2 / 24)
    assert xy(t) == (120, 220) and t["rotation"] == pytest.approx(5)
    assert (t["scaleX"], t["scaleY"]) == pytest.approx((1.25, 1.5)) and (t["anchorX"], t["anchorY"]) == (50, 50)
    # corner pin composed with the layer transform: P + R S (c - A); at frame 0 that is c + (50, 150)
    np.testing.assert_allclose(tr.sample("cornerpin", 0)["corners"], [[50, 150], [450, 150], [450, 450], [50, 450]])
    c4 = tr.sample("cornerpin", 4 / 24)["corners"]
    th = np.radians(10)
    ul = np.array([8 - 50, 4 - 50]) * [1.5, 2]
    np.testing.assert_allclose(c4[0], [120 + ul[0] * np.cos(th) - ul[1] * np.sin(th),
                                       220 + ul[0] * np.sin(th) + ul[1] * np.cos(th)])


AE_MATCH = """Adobe After Effects 9.0 Keyframe Data

\tUnits Per Second\t30
\tSource Height\t1080

Effects\tADBE Corner Pin #1\tADBE Corner Pin-0001
\tFrame\tX pixels\tY pixels\t
\t0\t1\t2\t

Effects\tADBE Corner Pin #1\tADBE Corner Pin-0002
\tFrame\tX pixels\tY pixels\t
\t0\t3\t4\t

Effects\tADBE Corner Pin #1\tADBE Corner Pin-0003
\tFrame\tX pixels\tY pixels\t
\t0\t7\t8\t

Effects\tADBE Corner Pin #1\tADBE Corner Pin-0004
\tFrame\tX pixels\tY pixels\t
\t0\t5\t6\t

Camera Options\tZoom
\tFrame\tpixels\t
\t0\t1000\t
\t30\t2000\t

Transform\tOrientation
\tFrame\tX degrees\tY degrees\tZ degrees\t
\t0\t10\t0\t0\t

Transform\tX Rotation
\tFrame\tdegrees\t
\t0\t5\t


End of Keyframe Data
"""


def test_after_effects_matchnames_camera():
    ch = tracking.parse_after_effects(AE_MATCH, Ctx(doc_fps=24))
    np.testing.assert_allclose(ch["cornerpin"].data["corners"][0], [[1, 2], [3, 4], [5, 6], [7, 8]])
    tr = tracking.Track("x", "camera", "after-effects", 0.0, ch)
    s = tr.sample("transform", 0.5)                              # Units Per Second 30 beats doc 24
    assert s["zoom"] == pytest.approx(1500) and s["rx"] == 15
    with pytest.raises(tracking.TrackError):
        tracking.parse_after_effects("not keyframe data")


def test_mocha(doc):
    tr = track(doc, "td-mocha-tr")
    s = tr.sample("transform", 1 / 25)
    assert xy(s) == (970, 545) and s["rotation"] == -2 and s["scaleX"] == pytest.approx(1.01)
    pin = track(doc, "td-mocha-pin")                              # CC Power Pin + motion-blur transform
    np.testing.assert_allclose(pin.sample("cornerpin", 2 / 25)["corners"],
                               [[822, 410], [1122, 410], [1122, 710], [822, 710]])
    assert xy(pin.sample("cornerpin:tr", 1 / 25)) == (1111, 405)
    nk = tracking.parse_mocha("CornerPin2D {\n to1 {0 0}\n to2 {10 0}\n to3 {10 10}\n to4 {0 10}\n name mocha_pin\n}\n",
                              Ctx(doc_height=10))
    np.testing.assert_allclose(nk["mocha_pin"].data["corners"][0], [[0, 0], [10, 0], [10, 10], [0, 10]])


# ---------------------------------------------------------------- fbx
def _check_fbx_scene(ch):
    null, cam = ch["TrackNull"], ch["Camera01"]
    np.testing.assert_allclose(null.times, [0, 1 / 24, 2 / 24])
    np.testing.assert_allclose(null.data["x"], [0, 10, 20])
    assert null.data["z"][0] == 2.5                              # curve-node default
    assert (null.data["rz"][0], null.data["scaleX"][0], null.data["scaleY"][0]) == (45, 2, 3)  # Properties70
    s = cam.sample(1 / 24)
    # z = 10 (base layer) + 50 % x 4 (additive layer "Add"); stack "Take 002" (x 100..300) is ignored
    assert (s["x"], s["y"], s["z"], s["focal"], s["fov"]) == pytest.approx((0.5, 1, 12, 42.5, 40))


def test_fbx_binary(doc, tmp_path):
    tr = track(doc, "td-fbx")
    assert list(tr.channels) == ["TrackNull", "Camera01", "Spinner"]
    _check_fbx_scene(tr.channels)
    p = tmp_path / "wide.fbx"
    p.write_bytes(make_fbx.scene_fbx(7500, compress=True))      # 64-bit offsets + zlib arrays
    _check_fbx_scene(tracking.parse_fbx(p.read_bytes(), Ctx()))
    with pytest.raises(tracking.TrackError):
        tracking.parse_fbx(make_fbx.scene_fbx(7400)[:200])


def test_fbx_key_attributes(doc):
    sp = track(doc, "td-fbx").channels["Spinner"]
    assert sp.sample(0.5)["x"] == pytest.approx(_hermite(0, 10, 30, 0, 1, .5))       # cubic user, 8.75
    wr, wl = 5000 / 9999, 2000 / 9999                                # weights 0.5 / 0.2 as stored (x 9999)
    P = [(1, 10), (1 + wr, 10 + wr * 0), (2 - wl, 0 - wl * -20), (2, 0)]
    assert sp.sample(1.5)["x"] == pytest.approx(_bezier_at(P, 1.5), abs=1e-6)
    assert sp.sample(1.25)["x"] == pytest.approx(_bezier_at(P, 1.25), abs=1e-6)
    ys = [sp.sample(t)["y"] for t in (0.5, 1.0, 1.5, 2.0)]
    assert ys == [5, 7, 9, 9]                                        # constant, then constant-next


def test_fbx_auto_tangents():
    n = tracking._FNode
    curve = n("AnimationCurve", [1], [n("KeyTime", [np.array([0, 1, 2]) * tracking.FBX_TICKS], []),
                                      n("KeyValueFloat", [np.array([0.0, 10.0, 0.0], np.float32)], []),
                                      n("KeyAttrFlags", [np.array([0x108])], []),
                                      n("KeyAttrDataFloat", [np.zeros(4, np.float32)], []),
                                      n("KeyAttrRefCount", [np.array([3])], [])])
    f = tracking._fbx_curve(curve)                   # auto: cardinal inside (0 here), secant at the ends
    assert f(0.5) == pytest.approx(_hermite(0, 10, 10, 0, 1, .5)) and f(1.5) == pytest.approx(_hermite(10, 0, 0, -10, 1, .5))


def test_fbx_rotation_order(doc):
    s = track(doc, "td-fbx").channels["Spinner"].sample(0.0)
    R = lambda ax, d: tracking._rot(ax, d)
    # FBX: RotationActive, RotationOrder 4 = ZXY (Z applied first), PreRotation (0,0,90), Lcl Rotation (30,45,20)
    want = R("Z", 90) @ (R("Y", 45) @ R("X", 30) @ R("Z", 20))
    got = R("Z", s["rz"]) @ R("Y", s["ry"]) @ R("X", s["rx"])      # output is XYZ order (X first)
    np.testing.assert_allclose(got, want, atol=1e-9)
    assert (s["rx"], s["ry"], s["rz"]) != pytest.approx((30, 45, 110))


def test_fbx_ascii(doc):
    tr = track(doc, "td-fbx-ascii")
    assert list(tr.channels) == ["Tracker_Null"]                 # static model skipped
    s = tr.sample(None, 1.5)
    assert s["rz"] == pytest.approx(135) and xy(s) == (1.5, 2.5) and s["scaleX"] == 0.5
    # key 0: weighted cubic, weights 218434821 = 3333 | 3333 << 16 -> 1/3 (plain Hermite), slopes 0 / 180
    assert tr.sample(None, 0.5)["rz"] == pytest.approx(_hermite(0, 90, 0, 180, 1, .5))   # 22.5


# ---------------------------------------------------------------- loading
def test_sha256_and_errors(doc, caplog):
    with caplog.at_level(logging.ERROR, logger="scenerender"):
        assert track(doc, "td-bad-sha") is None
    assert "sha256" in caplog.text
    assert track(doc, "td-missing") is None
    td = find_track(doc, "td-json")
    good = td.__copy__()
    good.set("sha256", hashlib.sha256(open(os.path.join(FIX, "track_point.json"), "rb").read()).hexdigest())
    assert load_track(doc, good) is not None


def test_cache_and_find(doc):
    rc = SimpleNamespace(doc=doc, cache={})
    td = find_track(doc, "td-ae")
    a = load_track(rc, td)
    assert a is load_track(rc, td) and len(rc.cache) == 1
    assert find_track(doc, "nope") is None and find_track(doc, "dot") is None


def test_sniff_and_registry(tmp_path):
    ae = open(os.path.join(FIX, "ae_keyframes.txt"), "rb").read()
    assert tracking.sniff_format(ae, "x.json") == "after-effects"
    assert tracking.sniff_format(b"1 0 0 0 0 0 0\n", "cam.txt") == "nuke"
    assert tracking.sniff_format(b"frame,x,y\n", "a.txt") == "csv"
    assert tracking.sniff_format(make_fbx.scene_fbx(), "a.bin") == "fbx"
    assert tracking.sniff_format(b' {"tracks": {}}', "a.csv") == "json"
    for f in tracking.FORMATS:
        assert FEATURES.level(f"trackData:{f}") == FULL
    for k in tracking.KINDS:
        assert FEATURES.level(f"trackData:kind:{k}") == FULL
