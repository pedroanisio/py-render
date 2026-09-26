#!/usr/bin/env python3
"""Build every production asset for "The Digital Front" from sources in assets/source.

Deterministic: all randomness is seeded, so rebuilding produces identical files.
Photos come from Wikimedia Commons under the licences recorded in assets.manifest.json;
the map is drawn from Natural Earth 1:50m (public domain) with Crimea shown within
Ukraine's internationally recognised borders; all other art and all audio are original.
"""
import json
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = lambda *p: os.path.join(ROOT, "assets", *p)
F_HEAD = A("fonts", "Poppins-Bold.ttf")
F_BODY = A("fonts", "Lora-Variable.ttf")

PAPER = (239, 230, 210)
INK = (30, 30, 30)
RED = (200, 53, 43)
MAT = (34, 52, 48)


def font(path, size):
    return ImageFont.truetype(path, size)


def noise(w, h, seed, amp=10, blur=0.8):
    rng = np.random.default_rng(seed)
    n = rng.normal(0, amp, (h // 2 + 1, w // 2 + 1)).astype(np.float32)
    img = Image.fromarray(np.clip(n + 128, 0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
    return np.asarray(img.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32) - 128


def paper(w, h, color, seed, amp=9, fibres=True):
    base = np.zeros((h, w, 3), np.float32) + np.array(color, np.float32)
    n = noise(w, h, seed, amp) + noise(w, h, seed + 1, amp * 0.6, 6)
    base += n[..., None]
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")
    if fibres:
        d = ImageDraw.Draw(img)
        rng = random.Random(seed)
        for _ in range(w * h // 9000):
            x, y = rng.uniform(0, w), rng.uniform(0, h)
            a, L = rng.uniform(0, math.pi), rng.uniform(6, 22)
            c = tuple(max(0, min(255, int(v + rng.uniform(-18, 10)))) for v in color)
            d.line([(x, y), (x + L * math.cos(a), y + L * math.sin(a))], fill=c, width=1)
    return img


def torn_mask(w, h, pad, seed, rough=6):
    """Alpha mask of a paper rectangle with torn edges inside a (w+2pad) x (h+2pad) canvas."""
    rng = random.Random(seed)
    pts = []
    def edge(x0, y0, x1, y1, n):
        for i in range(n):
            t = i / n
            pts.append((x0 + (x1 - x0) * t + rng.uniform(-rough, rough) * (y0 == y1 and 0 or 1),
                        y0 + (y1 - y0) * t + rng.uniform(-rough, rough) * (x0 == x1 and 0 or 1)))
    n = max(40, (w + h) // 12)
    edge(pad, pad, pad + w, pad, n)
    edge(pad + w, pad, pad + w, pad + h, n * h // w + 8)
    edge(pad + w, pad + h, pad, pad + h, n)
    edge(pad, pad + h, pad, pad, n * h // w + 8)
    m = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m.filter(ImageFilter.GaussianBlur(0.7))


def paper_card(w, h, color, seed, pad=20, rough=5):
    sheet = paper(w + 2 * pad, h + 2 * pad, color, seed)
    sheet.putalpha(torn_mask(w, h, pad, seed + 7, rough))
    return sheet


def cover_crop(img, w, h, fx=0.5, fy=0.5):
    s = max(w / img.width, h / img.height)
    im = img.resize((math.ceil(img.width * s), math.ceil(img.height * s)), Image.LANCZOS)
    x = int((im.width - w) * fx)
    y = int((im.height - h) * fy)
    return im.crop((x, y, x + w, y + h))


def photo_print(src, pw, ph, credit, caption, seed, fx=0.5, fy=0.5, pad=40, tone=1.0):
    """Printed photo with white border, printed caption strip and two tape strips."""
    b, strip = 26, 64
    W, H = pw + 2 * b, ph + b + strip
    photo = cover_crop(Image.open(src).convert("RGB"), pw, ph, fx, fy)
    photo = ImageEnhance.Contrast(photo).enhance(0.92)
    photo = ImageEnhance.Color(photo).enhance(0.9 * tone)
    grain = noise(pw, ph, seed, 6)
    photo = Image.fromarray(np.clip(np.asarray(photo, np.float32) + grain[..., None], 0, 255).astype(np.uint8))
    card = paper(W, H, (246, 243, 236), seed + 3, amp=4, fibres=False)
    card.paste(photo, (b, b))
    d = ImageDraw.Draw(card)
    d.text((b, b + ph + 12), caption, fill=(60, 58, 55), font=font(F_BODY, 24))
    fc = font(F_BODY, 22)
    d.text((W - b - d.textlength(credit, font=fc), b + ph + 38), credit, fill=(110, 106, 100), font=fc)
    out = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    out.paste(card, (pad, pad))
    rng = random.Random(seed)
    for cx in (pad + 70, pad + W - 70):
        tw, th = 150, 44
        tape = Image.new("RGBA", (tw, th), (226, 214, 180, 170))
        tape = tape.rotate(rng.uniform(-14, 14), expand=True, resample=Image.BICUBIC)
        out.alpha_composite(tape, (int(cx - tape.width / 2), int(pad - tape.height / 2 + 4)))
    return out


# ------------------------------------------------------------------ plates
def build_desk():
    w, h = 2400, 1350
    img = paper(w, h, MAT, 101, amp=6, fibres=False)
    d = ImageDraw.Draw(img)
    for x in range(0, w, 60):
        d.line([(x, 0), (x, h)], fill=(52, 74, 68) if x % 300 else (70, 96, 88), width=1 if x % 300 else 2)
    for y in range(0, h, 60):
        d.line([(0, y), (w, y)], fill=(52, 74, 68) if y % 300 else (70, 96, 88), width=1 if y % 300 else 2)
    f = font(F_HEAD, 18)
    for i, x in enumerate(range(300, w, 300)):
        d.text((x + 6, 8), str(i + 1), fill=(80, 108, 100), font=f)
    # wear: a few faint cut scratches
    rng = random.Random(5)
    for _ in range(40):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        L, a = rng.uniform(40, 260), rng.uniform(0, math.pi)
        d.line([(x, y), (x + L * math.cos(a), y + L * math.sin(a))], fill=(60, 80, 74), width=1)
    img.save(A("plates", "desk_mat.png"))


LON0, LON1, LAT0, LAT1 = 13.5, 41.0, 43.0, 55.5
MAP_W = 4000
COSP = math.cos(math.radians(49.25))
K = MAP_W / ((LON1 - LON0) * COSP)
MAP_H = int(round((LAT1 - LAT0) * K))


def proj(lon, lat):
    return ((lon - LON0) * COSP * K, (LAT1 - lat) * K)


def build_map():
    gj = json.load(open(A("source", "ne_50m_admin_0_countries.geojson")))
    feats = {f["properties"]["NAME"]: f for f in gj["features"]}

    def rings(name):
        g = feats[name]["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        return [p[0] for p in polys]

    from matplotlib.path import Path
    ukr = rings("Ukraine")
    rus = []
    for r in rings("Russia"):
        if Path(r).contains_point((34.1, 44.9)):
            ukr.append(r)          # Crimea within Ukraine's internationally recognised borders
        else:
            rus.append(r)

    sea = paper(MAP_W, MAP_H, (150, 175, 186), 201, amp=5)
    img = sea.convert("RGBA")
    shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    neighbours = ["Poland", "Belarus", "Moldova", "Romania", "Slovakia", "Hungary", "Lithuania", "Latvia",
                  "Germany", "Czechia", "Austria", "Serbia", "Bulgaria", "Turkey", "Georgia", "Estonia",
                  "Croatia", "Slovenia", "Bosnia and Herz.", "Montenegro", "Kosovo", "Macedonia", "North Macedonia",
                  "Albania", "Greece", "Denmark", "Sweden", "Italy"]
    land = Image.new("L", img.size, 0)
    dl = ImageDraw.Draw(land)
    borders = []
    for name in neighbours:
        if name not in feats:
            continue
        for r in rings(name):
            pts = [proj(*c) for c in r]
            dl.polygon(pts, fill=255)
            borders.append(pts)
    for r in rus:
        pts = [proj(*c) for c in r]
        dl.polygon(pts, fill=255)
        borders.append(pts)
    grey = paper(MAP_W, MAP_H, (196, 190, 176), 202)
    img.paste(grey, (0, 0), land)
    # Ukraine as a raised paper cut-out: shadow, then sheet
    umask = Image.new("L", img.size, 0)
    du = ImageDraw.Draw(umask)
    for r in ukr:
        du.polygon([proj(*c) for c in r], fill=255)
    sh = umask.filter(ImageFilter.GaussianBlur(14))
    shadow = Image.new("RGBA", img.size, (20, 20, 20, 0))
    shadow.putalpha(sh.point(lambda v: int(v * 0.45)))
    img.alpha_composite(shadow, (10, 16))
    img.paste(paper(MAP_W, MAP_H, (236, 222, 176), 203), (0, 0), umask)
    d = ImageDraw.Draw(img)
    for pts in borders:
        d.line(pts + [pts[0]], fill=(150, 142, 128), width=3)
    for r in ukr:
        pts = [proj(*c) for c in r]
        d.line(pts + [pts[0]], fill=(120, 96, 50), width=5)
    # graticule
    for lon in range(15, 41, 5):
        x, _ = proj(lon, 0)
        d.line([(x, 0), (x, MAP_H)], fill=(255, 255, 255, 60), width=2)
    for lat in range(45, 56, 5):
        _, y = proj(0, lat)
        d.line([(0, y), (MAP_W, y)], fill=(255, 255, 255, 60), width=2)
    lab = font(F_HEAD, 64)
    small = font(F_HEAD, 44)

    def label(text, lon, lat, f=lab, fill=(95, 88, 76), spacing=10):
        x, y = proj(lon, lat)
        total = sum(d.textlength(ch, font=f) + spacing for ch in text) - spacing
        cx = x - total / 2
        for ch in text:
            d.text((cx, y), ch, font=f, fill=fill)
            cx += d.textlength(ch, font=f) + spacing

    label("UKRAINE", 31.5, 49.3, font(F_HEAD, 96), (120, 96, 50), 18)
    label("POLAND", 19.3, 51.4)
    label("BELARUS", 28.0, 53.6)
    label("RUSSIA", 38.3, 53.9)
    label("MOLDOVA", 28.6, 47.35, small)
    label("ROMANIA", 24.8, 45.9)
    label("SLOVAKIA", 19.6, 48.95, small)
    label("HUNGARY", 19.3, 47.3, small)
    label("BLACK SEA", 34.0, 43.6, small, (240, 246, 248))
    city = font(F_BODY, 52)
    for name, lon, lat, dx in (("Kyiv", 30.5234, 50.4501, 34), ("Warsaw", 21.0122, 52.2297, 30)):
        x, y = proj(lon, lat)
        d.ellipse([x - 14, y - 14, x + 14, y + 14], fill=INK)
        d.text((x + dx, y - 34), name, font=city, fill=INK)
    d.text((MAP_W - 560, MAP_H - 70), "Map data: Natural Earth", font=font(F_BODY, 34), fill=(60, 70, 76))
    img.convert("RGB").save(A("plates", "map_region.png"))
    return {"kyiv": proj(30.5234, 50.4501), "warsaw": proj(21.0122, 52.2297), "central_poland": proj(19.4, 52.0),
            "ukraine_centre": proj(31.15, 48.4), "size": (MAP_W, MAP_H)}


def build_pencil():
    w, h = 1700, 120
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(0, 60), (150, 22), (150, 98)], fill=(214, 180, 130))
    d.polygon([(0, 60), (46, 49), (46, 71)], fill=(40, 40, 40))
    d.rectangle([150, 22, 1560, 98], fill=(212, 160, 40))
    for y in (47, 73):
        d.line([(150, y), (1560, y)], fill=(180, 130, 30), width=3)
    d.rectangle([1560, 22, 1620, 98], fill=(170, 170, 176))
    d.rounded_rectangle([1620, 24, 1698, 96], 14, fill=(220, 120, 130))
    img.save(A("plates", "pencil.png"))


# ------------------------------------------------------------------ prints and cards
PRINTS = {
    "print_kyiv_night": ("kyiv_night.jpg", 1320, 600, "Photo: spoilt.exile · CC BY-SA 2.0",
                         "Kyiv, Podil at night, 2020", 0.5, 0.55),
    "print_datacentre": ("datacentre.jpg", 900, 600, "Photo: BalticServers.com · CC BY-SA 3.0",
                         "A datacentre server hall (illustrative)", 0.4, 0.5),
    "print_starlink": ("starlink.jpg", 560, 680, "Photo: Steve Jurvetson · CC BY 2.0",
                       "A Starlink user terminal (illustrative)", 0.5, 0.45),
    "print_blackout": ("kyiv_blackout.jpg", 880, 660, "Photo: Bektour · CC BY 4.0",
                       "Kyiv during blackout, November 2022 (archive)", 0.5, 0.6),
    "print_port": ("port.jpg", 1040, 540, "Photo: George Chernilevsky · public domain",
                   "Port of Odesa, 2017 (archive)", 0.5, 0.55),
}


def build_prints():
    dims = {}
    for i, (name, (src, pw, ph, credit, caption, fx, fy)) in enumerate(PRINTS.items()):
        im = photo_print(A("source", src), pw, ph, credit, caption, 300 + i, fx, fy)
        im.save(A("photos", name + ".png"))
        dims[name] = im.size
    return dims


def build_cards():
    specs = {
        "card_wide": (1400, 300, PAPER, 401),
        "card_dark": (1400, 300, (38, 36, 34), 402),
        "card_stat": (940, 460, PAPER, 403),
        "card_title": (1500, 380, PAPER, 404),
        "card_kicker": (620, 86, RED, 405),
        "card_credits": (1560, 860, PAPER, 406),
    }
    dims = {}
    for name, (w, h, c, seed) in specs.items():
        im = paper_card(w, h, c, seed)
        im.save(A("cards", name + ".png"))
        dims[name] = im.size
    return dims


# ------------------------------------------------------------------ replacement sequences
def house(d, x, y, w, h, body, roof, lit_windows, rng):
    d.polygon([(x - 8, y + h * 0.42), (x + w / 2, y), (x + w + 8, y + h * 0.42)], fill=roof)
    d.rectangle([x, y + h * 0.4, x + w, y + h], fill=body)
    ww, wh = w * 0.24, h * 0.2
    for i, lit in enumerate(lit_windows):
        wx = x + w * (0.16 if i == 0 else 0.6)
        wy = y + h * 0.55
        d.rectangle([wx, wy, wx + ww, wy + wh], fill=(255, 214, 110) if lit else (52, 56, 66))
    d.rectangle([x + w * 0.4, y + h * 0.78, x + w * 0.58, y + h], fill=(90, 70, 55))


def wifi_off(d, cx, cy, r, col):
    for k in (1.0, 0.68, 0.36):
        d.arc([cx - r * k, cy - r * k, cx + r * k, cy + r * k], 225, 315, fill=col, width=int(r * 0.12))
    d.ellipse([cx - r * 0.1, cy - r * 0.1, cx + r * 0.1, cy + r * 0.1], fill=col)
    d.line([(cx - r * 0.75, cy - r * 0.95), (cx + r * 0.75, cy + r * 0.25)], fill=RED, width=int(r * 0.14))


def build_houses():
    W, H = 1100, 560
    os.makedirs(A("puppets", "houses_outage"), exist_ok=True)
    rng = random.Random(77)
    bodies = [(226, 206, 170), (214, 190, 160), (200, 214, 206), (232, 220, 196), (210, 196, 176)]
    roofs = [(150, 70, 55), (110, 90, 80), (170, 96, 64), (96, 104, 120), (140, 60, 50)]
    slots = [(40 + c * 205, 40 + r * 250) for r in range(2) for c in range(5)]
    windows = [(h, i) for h in range(10) for i in range(2)]
    order = windows[:]
    rng.shuffle(order)
    for f in range(1, 13):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        lit_count = 20 if f <= 3 else round(20 * (12 - f) / 9)
        off = set(order[: 20 - lit_count])
        jr = random.Random(900 + f)
        for hidx, (x, y) in enumerate(slots):
            jx, jy = jr.uniform(-2, 2), jr.uniform(-2, 2)
            lw = [(hidx, 0) not in off, (hidx, 1) not in off]
            house(d, x + jx, y + 60 + jy, 170, 170, bodies[hidx % 5], roofs[hidx % 5], lw, jr)
            if f >= 10:
                wifi_off(d, x + 85 + jx, y + 26 + jy, 34, (240, 236, 226))
        img.save(A("puppets", "houses_outage", f"houses_{f:02d}.png"))
    return (W, H)


def build_network():
    W, H = 1300, 760
    os.makedirs(A("puppets", "network_reroute"), exist_ok=True)
    nodes = {"A": (110, 380), "B": (360, 160), "C": (360, 600), "D": (650, 380), "E": (940, 160),
             "F": (940, 600), "G": (1190, 380), "H": (650, 110), "I": (650, 660)}
    edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "E"), ("D", "F"), ("E", "G"), ("F", "G"),
             ("B", "H"), ("H", "E"), ("C", "I"), ("I", "F")]
    route1 = ["A", "B", "D", "E", "G"]
    route2 = ["A", "C", "I", "F", "G"]

    def along(route, t):
        segs = list(zip(route, route[1:]))
        t = t % 1.0 * len(segs)
        i = int(t)
        (x0, y0), (x1, y1) = nodes[segs[i][0]], nodes[segs[i][1]]
        u = t - i
        return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u

    for f in range(1, 25):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        jr = random.Random(500 + f)
        hit = f >= 9
        for a, b in edges:
            (x0, y0), (x1, y1) = nodes[a], nodes[b]
            dead = hit and "D" in (a, b)
            mx, my = (x0 + x1) / 2 + jr.uniform(-6, 6), (y0 + y1) / 2 + jr.uniform(-6, 6) + 10
            pts = [(x0 + (mx - x0) * t * 2, y0 + (my - y0) * t * 2) if t <= 0.5 else
                   (mx + (x1 - mx) * (t - 0.5) * 2, my + (y1 - my) * (t - 0.5) * 2) for t in np.linspace(0, 1, 12)]
            d.line(pts, fill=(120, 110, 100) if dead else (214, 190, 150), width=5)
        for k, (x, y) in nodes.items():
            dead = hit and k == "D"
            r = 46
            d.ellipse([x - r, y - r, x + r, y + r], fill=(90, 90, 90) if dead else PAPER, outline=(70, 60, 50), width=4)
            for j in range(3):
                d.rectangle([x - 22, y - 20 + j * 14, x + 22, y - 10 + j * 14], fill=(60, 60, 60) if dead else (70, 90, 110))
            if dead:
                d.line([(x - 40, y - 40), (x + 40, y + 40)], fill=RED, width=12)
                d.line([(x - 40, y + 40), (x + 40, y - 40)], fill=RED, width=12)
        route = route1 if f < 9 else route2
        phase = (f - 1) / 8.0
        for p in range(3):
            x, y = along(route, phase + p / 3)
            d.ellipse([x - 13, y - 13, x + 13, y + 13], fill=(255, 214, 110), outline=(150, 100, 20), width=3)
        img.save(A("puppets", "network_reroute", f"network_{f:02d}.png"))
    return (W, H)


# ------------------------------------------------------------------ icons and pin
def icon_canvas():
    return Image.new("RGBA", (280, 280), (0, 0, 0, 0))


def finish_icon(img, seed, color=PAPER):
    alpha = img.getchannel("A")
    tex = paper(280, 280, color, seed)
    tex.putalpha(alpha)
    edge = alpha.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 40 else 0)
    out = tex
    ink = Image.new("RGBA", (280, 280), (60, 50, 40, 255))
    ink.putalpha(edge)
    out.alpha_composite(ink)
    return out


def build_icons():
    shapes = {}
    im = icon_canvas(); d = ImageDraw.Draw(im)                                   # bank
    d.polygon([(30, 100), (140, 30), (250, 100)], fill="white")
    for x in (50, 100, 150, 200):
        d.rectangle([x, 110, x + 30, 220], fill="white")
    d.rectangle([30, 225, 250, 250], fill="white"); shapes["icon_bank"] = im
    im = icon_canvas(); d = ImageDraw.Draw(im)                                   # payment card
    d.rounded_rectangle([20, 70, 260, 220], 22, fill="white")
    d.rectangle([20, 100, 260, 130], fill=(0, 0, 0, 0)); d.rectangle([50, 165, 130, 190], fill=(0, 0, 0, 0))
    shapes["icon_payment"] = im
    im = icon_canvas(); d = ImageDraw.Draw(im)                                   # public services document
    d.polygon([(60, 20), (190, 20), (230, 60), (230, 260), (60, 260)], fill="white")
    for y in (80, 115, 150):
        d.rectangle([90, y, 200, y + 12], fill=(0, 0, 0, 0))
    d.ellipse([140, 180, 210, 250], fill=(0, 0, 0, 0)); shapes["icon_services"] = im
    im = icon_canvas(); d = ImageDraw.Draw(im)                                   # warehouse
    d.polygon([(20, 110), (140, 40), (260, 110), (260, 250), (20, 250)], fill="white")
    d.rectangle([90, 150, 190, 250], fill=(0, 0, 0, 0))
    for y in (165, 190, 215):
        d.rectangle([90, y, 190, y + 10], fill="white"); shapes["icon_warehouse"] = im
    im = icon_canvas(); d = ImageDraw.Draw(im)                                   # fuel pump
    d.rounded_rectangle([50, 40, 180, 260], 14, fill="white")
    d.rectangle([75, 70, 155, 130], fill=(0, 0, 0, 0))
    d.line([(180, 90), (230, 120), (230, 220), (205, 230)], fill="white", width=16); shapes["icon_fuel"] = im
    im = icon_canvas(); d = ImageDraw.Draw(im)                                   # factory
    d.polygon([(20, 250), (20, 140), (90, 180), (90, 140), (160, 180), (160, 140), (230, 180), (230, 250)], fill="white")
    d.rectangle([185, 40, 225, 180], fill="white"); d.rectangle([140, 80, 170, 150], fill="white")
    shapes["icon_factory"] = im
    for i, (name, im) in enumerate(shapes.items()):
        finish_icon(im, 600 + i).save(A("icons", name + ".png"))
    pin = Image.new("RGBA", (110, 180), (0, 0, 0, 0))
    d = ImageDraw.Draw(pin)
    d.line([(55, 90), (55, 178)], fill=(150, 150, 156), width=6)
    d.ellipse([10, 5, 100, 95], fill=RED)
    d.ellipse([28, 20, 50, 42], fill=(240, 150, 140))
    pin.save(A("icons", "map_pin.png"))


# ------------------------------------------------------------------ audio (original synthesis)
SR = 48000


def write_wav(path, stereo):
    import wave
    data = np.clip(stereo, -1, 1)
    pcm = (data * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def build_audio():
    rng = np.random.default_rng(11)
    dur = 104.0
    t = np.arange(int(SR * dur)) / SR
    # bed: minor drone (D2/A2/F3) with slow swell and a soft 90 bpm pulse
    drone = sum(a * np.sin(2 * np.pi * f * t + p) for f, a, p in ((73.42, .20, 0), (110.0, .12, 1), (174.61, .07, 2), (220.0, .04, .5)))
    drone *= 0.85 + 0.15 * np.sin(2 * np.pi * t / 16.0)
    beat = 60 / 90
    ph = (t % beat) / beat
    pulse = np.sin(2 * np.pi * 55 * t) * np.exp(-ph * 18) * 0.28
    tick = rng.normal(0, 1, t.size) * np.exp(-((t + beat / 2) % beat) / beat * 60) * 0.03
    bed = drone + pulse + tick
    env = np.minimum(1, t / 2.0) * np.minimum(1, (dur - t) / 3.0)
    bed = bed * env * 0.55
    write_wav(A("audio", "music_bed.wav"), np.stack([bed, np.roll(bed, 240)], 1))
    # room tone: pink-ish noise, 20 s
    n = rng.normal(0, 1, int(SR * 20))
    pink = np.cumsum(n); pink -= np.convolve(pink, np.ones(4800) / 4800, "same"); pink /= np.abs(pink).max()
    write_wav(A("audio", "room_tone.wav"), np.stack([pink, np.roll(pink, 999)], 1) * 0.05)
    # paper slide / rustle, 0.8 s
    L = int(SR * 0.8); tt = np.arange(L) / SR
    rustle = rng.normal(0, 1, L) * np.exp(-tt * 5) * (0.6 + 0.4 * np.sin(2 * np.pi * 23 * tt))
    rustle = np.diff(rustle, prepend=0) * 0.5
    write_wav(A("audio", "sfx_paper.wav"), np.stack([rustle, rustle * 0.9], 1))
    # pin tap, 0.4 s
    L = int(SR * 0.4); tt = np.arange(L) / SR
    tap = (np.sin(2 * np.pi * 1800 * tt) * 0.4 + rng.normal(0, .3, L)) * np.exp(-tt * 60)
    write_wav(A("audio", "sfx_pin.wav"), np.stack([tap, tap], 1) * 0.8)
    # low thud for strike markers, 1.2 s (muted, not an explosion)
    L = int(SR * 1.2); tt = np.arange(L) / SR
    thud = np.sin(2 * np.pi * (60 - 25 * tt) * tt) * np.exp(-tt * 4) * 0.7
    write_wav(A("audio", "sfx_thud.wav"), np.stack([thud, thud], 1))


if __name__ == "__main__":
    for sub in ("plates", "photos", "cards", "icons", "puppets", "audio"):
        os.makedirs(A(sub), exist_ok=True)
    build_desk(); build_pencil()
    geo = build_map()
    prints = build_prints()
    cards = build_cards()
    houses = build_houses(); net = build_network()
    build_icons(); build_audio()
    info = {"map": geo, "prints": prints, "cards": cards, "houses": houses, "network": net}
    json.dump(info, open(os.path.join(ROOT, "tools", "layout_info.json"), "w"), indent=1)
    print(json.dumps(info, indent=1))
