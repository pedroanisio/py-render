"""2D geometry shared by shapes, masks, vector assets, motion paths and text paths.

A path is a list of commands in absolute coordinates:
("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y) | ("Z",).
Shape geometry lives in the node box (0,0)-(w,h); the anchor is applied by the transform.
"""
from __future__ import annotations

import bisect
import math
import re

import cairo

Cmd = tuple
_TOK = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
KAPPA = 0.5522847498


# ---------------------------------------------------------------- SVG path data
def parse_svg_path(d: str | None) -> list[Cmd]:
    if not d:
        return []
    toks = _TOK.findall(d)
    out: list[Cmd] = []
    i, cmd = 0, None
    cx = cy = sx = sy = 0.0
    last_c2 = last_q = None

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                out.append(("Z",))
                cx, cy = sx, sy
                last_c2 = last_q = None
                continue
        if cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if c == "M":
            cx, cy = ox + num(), oy + num()
            sx, sy = cx, cy
            out.append(("M", cx, cy))
            cmd = "l" if rel else "L"
            last_c2 = last_q = None
        elif c == "L":
            cx, cy = ox + num(), oy + num()
            out.append(("L", cx, cy))
            last_c2 = last_q = None
        elif c == "H":
            cx = ox + num()
            out.append(("L", cx, cy))
            last_c2 = last_q = None
        elif c == "V":
            cy = (cy if rel else 0.0) + num()
            out.append(("L", cx, cy))
            last_c2 = last_q = None
        elif c in "CS":
            if c == "C":
                x1, y1 = ox + num(), oy + num()
            else:
                x1, y1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
            x2, y2 = ox + num(), oy + num()
            x, y = ox + num(), oy + num()
            out.append(("C", x1, y1, x2, y2, x, y))
            last_c2, last_q = (x2, y2), None
            cx, cy = x, y
        elif c in "QT":
            if c == "Q":
                qx, qy = ox + num(), oy + num()
            else:
                qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
            x, y = ox + num(), oy + num()
            out.append(("C", cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy),
                        x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y))
            last_q, last_c2 = (qx, qy), None
            cx, cy = x, y
        elif c == "A":
            rx, ry, rot = num(), num(), num()
            large, sweep = num() != 0, num() != 0
            x, y = ox + num(), oy + num()
            out.extend(_arc_to_beziers(cx, cy, rx, ry, rot, large, sweep, x, y))
            cx, cy = x, y
            last_c2 = last_q = None
        else:
            i += 1
    return out


def _arc_to_beziers(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2) -> list[Cmd]:
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [("L", x2, y2)]
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2))))
    out = []
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    for j in range(n):
        a0, a1 = t1 + j * step, t1 + (j + 1) * step
        e0 = (math.cos(a0), math.sin(a0))
        e1 = (math.cos(a1), math.sin(a1))
        p = lambda ex, ey: (cx + rx * ex * cp - ry * ey * sp, cy + rx * ex * sp + ry * ey * cp)  # noqa: E731
        c1 = p(e0[0] - k * e0[1], e0[1] + k * e0[0])
        c2 = p(e1[0] + k * e1[1], e1[1] - k * e1[0])
        end = p(*e1)
        out.append(("C", *c1, *c2, *end))
    return out


# ---------------------------------------------------------------- shape kinds
def rect(x, y, w, h) -> list[Cmd]:
    return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]


def rounded_rect(x, y, w, h, radii) -> list[Cmd]:
    tl, tr, br, bl = (max(0.0, min(r, w / 2, h / 2)) for r in radii)
    k = 1 - KAPPA
    return [("M", x + tl, y), ("L", x + w - tr, y), ("C", x + w - tr * k, y, x + w, y + tr * k, x + w, y + tr),
            ("L", x + w, y + h - br), ("C", x + w, y + h - br * k, x + w - br * k, y + h, x + w - br, y + h),
            ("L", x + bl, y + h), ("C", x + bl * k, y + h, x, y + h - bl * k, x, y + h - bl),
            ("L", x, y + tl), ("C", x, y + tl * k, x + tl * k, y, x + tl, y), ("Z",)]


def box_outline(w, h, radii=(0.0, 0.0, 0.0, 0.0)) -> list[Cmd]:
    """A rect shape's outline (radii TL TR BR BL, capped at half the shorter side), starting at the
    top-left corner (after its rounding) and running clockwise on screen, as SVG 2 draws a rect: where
    trim and dashes start (CONVENTIONS 5.21)."""
    tl, tr, br, bl = (max(0.0, min(r, w / 2, h / 2)) for r in radii)
    k = 1 - KAPPA
    out = [("M", tl, 0), ("L", w - tr, 0)]
    if tr:
        out.append(("C", w - tr * k, 0, w, tr * k, w, tr))
    out.append(("L", w, h - br))
    if br:
        out.append(("C", w, h - br * k, w - br * k, h, w - br, h))
    out.append(("L", bl, h))
    if bl:
        out.append(("C", bl * k, h, 0, h - bl * k, 0, h - bl))
    out.append(("L", 0, tl))
    if tl:
        out.append(("C", 0, tl * k, tl * k, 0, tl, 0))
    return out + [("Z",)]


def ellipse(cx, cy, rx, ry) -> list[Cmd]:
    kx, ky = rx * KAPPA, ry * KAPPA
    return [("M", cx + rx, cy), ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
            ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
            ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
            ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy), ("Z",)]


def star(cx, cy, n, r_out, r_in, sx=1.0, sy=1.0, round_out=0.0, round_in=0.0) -> list[Cmd]:
    """Star (r_in given) or regular polygon (r_in None); first point straight up. Roundness as in Lottie."""
    pts = []
    count = n * 2 if r_in is not None else n
    for i in range(count):
        r = r_out if (r_in is None or i % 2 == 0) else r_in
        a = -math.pi / 2 + 2 * math.pi * i / count
        pts.append((cx + r * math.cos(a) * sx, cy + r * math.sin(a) * sy, r, a, i % 2 == 0 or r_in is None))
    if not round_out and not round_in:
        return [("M", pts[0][0], pts[0][1])] + [("L", p[0], p[1]) for p in pts[1:]] + [("Z",)]
    seg = 2 * math.pi / count
    cmds: list[Cmd] = [("M", pts[0][0], pts[0][1])]
    for j in range(count):
        p0, p1 = pts[j], pts[(j + 1) % count]
        def handle(p, sign):
            rnd = round_out if p[4] else round_in
            h = 4 / 3 * math.tan(seg / 4) * p[2] * rnd
            tx, ty = -math.sin(p[3]) * sign, math.cos(p[3]) * sign
            return p[0] + tx * h * sx, p[1] + ty * h * sy
        c1, c2 = handle(p0, 1), handle(p1, -1)
        cmds.append(("C", *c1, *c2, p1[0], p1[1]))
    cmds.append(("Z",))
    return cmds


def shape_commands(kind: str, w: float, h: float, a: dict) -> list[Cmd]:
    """Geometry for shape/vector/mask kinds in the box (0,0)-(w,h). `a` holds evaluated attributes."""
    if kind in ("rect", "rounded-rect"):
        # cornerRadii (TL TR BR BL) overrides radius, on rect as on rounded-rect (XSD).
        radii = a.get("cornerRadii") or [a.get("radius", 0.0)] * 4
        radii = list(radii) + [radii[-1]] * (4 - len(radii))
        return box_outline(w, h, radii[:4])
    if kind == "ellipse":
        return ellipse(w / 2, h / 2, w / 2, h / 2)
    if kind in ("polygon", "star"):
        n = max(3 if kind == "polygon" else 2, int(a.get("points", 5)))
        r_out = a.get("outerRadius") or min(w, h) / 2
        r_in = None
        if kind == "star":
            r_in = a.get("innerRadius") if a.get("innerRadius") is not None else r_out / 2
        # Non-square boxes stretch the figure to fill the box.
        sx = (w / 2) / r_out if a.get("outerRadius") is None and w != h else 1.0
        sy = (h / 2) / r_out if a.get("outerRadius") is None and w != h else 1.0
        return star(w / 2, h / 2, n, r_out, r_in, sx, sy, a.get("outerRoundness", 0.0), a.get("innerRoundness", 0.0))
    if kind == "line":
        # Horizontal line through the middle of the box (the schema leaves the direction open).
        return [("M", 0.0, h / 2), ("L", w, h / 2)]
    if kind == "path":
        return parse_svg_path(a.get("path"))
    return rect(0, 0, w, h)


# ---------------------------------------------------------------- cairo glue
def emit(cr: cairo.Context, cmds: list[Cmd]) -> None:
    for c in cmds:
        op = c[0]
        if op == "M":
            cr.move_to(c[1], c[2])
        elif op == "L":
            cr.line_to(c[1], c[2])
        elif op == "C":
            cr.curve_to(*c[1:])
        elif op == "Z":
            cr.close_path()


_SCRATCH = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))


def flatten(cmds: list[Cmd], tolerance: float = 0.1) -> list[tuple[list[tuple[float, float]], bool]]:
    """Polylines [(points, closed)] via cairo's flattener."""
    cr = _SCRATCH
    cr.new_path()
    cr.set_tolerance(tolerance)
    emit(cr, cmds)
    out: list[tuple[list[tuple[float, float]], bool]] = []
    cur: list[tuple[float, float]] = []
    for op, pts in cr.copy_path_flat():
        if op == cairo.PATH_MOVE_TO:
            if len(cur) > 1:
                out.append((cur, False))
            cur = [pts]
        elif op == cairo.PATH_LINE_TO:
            cur.append(pts)
        elif op == cairo.PATH_CLOSE_PATH:
            if cur:
                if cur[-1] != cur[0]:
                    cur.append(cur[0])
                out.append((cur, True))
                cur = [cur[0]]
    if len(cur) > 1:
        out.append((cur, False))
    cr.new_path()
    return out


def bounds(cmds: list[Cmd]) -> tuple[float, float, float, float]:
    cr = _SCRATCH
    cr.new_path()
    emit(cr, cmds)
    b = cr.path_extents()
    cr.new_path()
    return b


def _cum(pts):
    acc = [0.0]
    for a, b in zip(pts, pts[1:]):
        acc.append(acc[-1] + math.dist(a, b))
    return acc


def _cut(pts, acc, d0, d1):
    """Sub-polyline between arc lengths d0 < d1."""
    out = []
    for i in range(len(pts) - 1):
        a0, a1 = acc[i], acc[i + 1]
        if a1 < d0 or a0 > d1:
            continue
        seg = a1 - a0 or 1e-12
        u0, u1 = max(0.0, (d0 - a0) / seg), min(1.0, (d1 - a0) / seg)
        p = lambda u: (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * u, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * u)  # noqa: E731
        if not out:
            out.append(p(u0))
        out.append(p(u1))
    return out


def trim(cmds: list[Cmd], start: float, end: float, offset: float = 0.0, mode: str = "simultaneous") -> list[Cmd]:
    """Trim paths: keep the fraction [start, end] (shifted by offset turns) of each subpath, or of all
    subpaths laid end to end when mode is sequential."""
    if start <= 0 and end >= 1 and offset % 1 == 0:
        return cmds
    lines = flatten(cmds)
    if not lines:
        return []
    out: list[Cmd] = []

    def piece(pts, acc, total, s, e):
        s, e = s + offset, e + offset
        spans = []
        k = math.floor(s)
        s, e = s - k, e - k
        if e <= 1:
            spans.append((s, e))
        else:
            spans += [(s, 1.0), (0.0, e - 1)]
        for a, b in spans:
            if b - a <= 1e-9:
                continue
            seg = _cut(pts, acc, a * total, b * total)
            if len(seg) > 1:
                out.append(("M", *seg[0]))
                out.extend(("L", *p) for p in seg[1:])

    if end - start >= 1 - 1e-9:
        return cmds
    if mode == "sequential":
        all_pts, acc_all = [], []
        total = sum(_cum(p)[-1] for p, _ in lines)
        base = 0.0
        for pts, _closed in lines:
            acc = _cum(pts)
            L = acc[-1]
            if L <= 0:
                continue
            s = (start * total - base) / L
            e = (end * total - base) / L
            if e > 0 and s < 1:
                seg = _cut(pts, acc, max(0.0, s) * L, min(1.0, e) * L)
                if len(seg) > 1:
                    out.append(("M", *seg[0]))
                    out.extend(("L", *p) for p in seg[1:])
            base += L
        del all_pts, acc_all
        return out
    for pts, _closed in lines:
        acc = _cum(pts)
        if acc[-1] > 0:
            piece(pts, acc, acc[-1], start, end)
    return out


class PathSampler:
    """Arc-length parameterised point and tangent on an SVG path (all subpaths joined)."""

    def __init__(self, d: str | list[Cmd], constant_speed: bool = True):
        cmds = parse_svg_path(d) if isinstance(d, str) else d
        self.pts: list[tuple[float, float]] = []
        for pts, _ in flatten(cmds, 0.05):
            self.pts.extend(pts if not self.pts else pts[1:] if pts[0] == self.pts[-1] else pts)
        if not self.pts:
            self.pts = [(0.0, 0.0)]
        self.acc = _cum(self.pts)
        self.total = self.acc[-1]
        self.constant_speed = constant_speed

    def at(self, u: float) -> tuple[float, float, float]:
        if len(self.pts) == 1:
            return self.pts[0][0], self.pts[0][1], 0.0
        u = min(1.0, max(0.0, u))
        if self.constant_speed:
            d = u * self.total
            i = min(len(self.pts) - 2, max(0, bisect.bisect_right(self.acc, d) - 1))
            seg = (self.acc[i + 1] - self.acc[i]) or 1e-12
            f = (d - self.acc[i]) / seg
        else:
            x = u * (len(self.pts) - 1)
            i = min(len(self.pts) - 2, int(x))
            f = x - i
        (x0, y0), (x1, y1) = self.pts[i], self.pts[i + 1]
        return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, math.degrees(math.atan2(y1 - y0, x1 - x0))

    def at_distance(self, d: float) -> tuple[float, float, float]:
        return self.at(d / self.total if self.total else 0.0)
