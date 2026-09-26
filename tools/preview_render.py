#!/usr/bin/env python3
"""Animatic preview of a scene-render 1.1 document (subset interpreter).

This is a review tool, not the scene-render renderer. It reads scene.xml and draws
the constructs this film uses — groups, layers, image sequences with timeRemap/loop,
shapes (star, ellipse, path with dash and trimEnd), text assets with spans, symbols
and instances with overrides, links, markers, keys with hold/steps/linear/ease
curves, drop shadows, blurs, colour grade, top-level transitions and a simplified
finishing stack — at reduced size, and mixes the audioMix to a WAV. Lighting,
halation, tone mapping and loudness normalisation are approximated.

Usage: python3 preview_render.py scene.xml out.mp4 [--scale 0.5] [--fps 12] [--from S --to E]
"""
import argparse, math, os, re, subprocess, sys, tempfile, wave
from fractions import Fraction
import numpy as np
from lxml import etree
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops, ImageEnhance

def ln(el): return etree.QName(el).localname if isinstance(el.tag, str) else ""

def parse_color(s, tokens):
    s = s.strip()
    m = re.match(r"var\(--([\w-]+)\)", s)
    if m: s = tokens[m.group(1)]
    if s.startswith("#"):
        h = s[1:]
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
        return (r, g, b, a)
    parts = [float(p) for p in s.split(",")]
    parts += [1.0] * (4 - len(parts))
    return tuple(int(round(p * 255)) for p in parts)

def ease(u, kind):
    if kind in ("ease-in-out", "sine-in-out", "cubic-in-out", "quad-in-out"): return u * u * (3 - 2 * u)
    if kind in ("ease-in", "quad-in", "cubic-in"): return u * u
    if kind in ("ease-out", "quad-out", "cubic-out"): return 1 - (1 - u) ** 2
    return u

class Doc:
    def __init__(self, path, scale):
        self.base = os.path.dirname(os.path.abspath(path))
        self.root = etree.parse(path).getroot()
        p = self.root.find("project")
        self.W, self.H = int(p.get("width")), int(p.get("height"))
        self.duration = float(p.get("duration"))
        self.bg = p.get("background", "#000000FF")
        self.scale = scale
        self.ids = {e.get("id"): e for e in self.root.iter() if isinstance(e.tag, str) and e.get("id")}
        self.markers = {m.get("id"): float(m.get("time")) for m in self.root.iter("marker") if m.get("id")}
        self.tokens = {t.get("name"): t.get("value") for t in self.root.iter("token")}
        self.styles = {s.get("id"): s for s in self.root.iter("textStyle")}
        self.fonts = {f.get("id"): os.path.join(self.base, f.get("src")) for f in self.root.find("assets") if ln(f) == "font"}
        self.params = {pp.get("id"): pp.get("default", "") for pp in self.root.iter("param")}
        self.img_cache, self.text_cache = {}, {}
        self.effects = {e.get("id"): e for e in self.root.iter("effect")}

    # ---------------------------------------------------------------- values
    def anim_value(self, el, prop, t, default, local=False):
        for a in el:
            if ln(a) == "animate" and a.get("property") == prop:
                return self.eval_keys(list(a.iter("key")), t, a.get("defaultInterpolation", "linear"), local)
        for a in el:
            if ln(a) == "link" and a.get("property") == prop:
                node, _, sp = a.get("source").partition(".")
                src = self.prop(self.ids[node], sp, t, local)
                return src * float(a.get("scale", 1)) + float(a.get("offset", 0))
            if ln(a) == "expression" and a.get("property") == prop and prop in ("opacity",):
                return default
        v = el.get(prop)
        return float(v) if v is not None and re.match(r"^-?[\d.]+$", v) else default

    def prop(self, el, name, t, local=False):
        d = {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1, "anchorX": 0, "anchorY": 0, "opacity": 1}
        return self.anim_value(el, name, t, d.get(name, 0), local)

    def key_time(self, k, local):
        base = self.markers[k.get("marker")] if (k.get("marker") and not local) else 0.0
        return base + float(k.get("time"))

    def eval_keys(self, keys, t, dflt, local):
        ts = [self.key_time(k, local) for k in keys]
        vs = [float(k.get("value")) for k in keys]
        if t <= ts[0]: return vs[0]
        if t >= ts[-1]: return vs[-1]
        for i in range(len(keys) - 1):
            if ts[i] <= t < ts[i + 1]:
                kind = keys[i].get("interpolation", dflt)
                u = (t - ts[i]) / (ts[i + 1] - ts[i])
                if kind in ("hold", "step"): return vs[i]
                if kind == "steps":
                    n = int(keys[i].get("steps", 1)); u = math.floor(u * n) / n
                else:
                    u = ease(u, kind)
                return vs[i] + (vs[i + 1] - vs[i]) * u
        return vs[-1]

    def window(self, el):
        s = self.markers.get(el.get("startMarker"), float(el.get("start", 0)))
        e = el.get("end")
        e = self.markers.get(el.get("endMarker"), float(e) if e else None)
        return s, e

    # ---------------------------------------------------------------- media
    def image(self, asset, frame=None):
        key = (asset.get("id"), frame)
        if key not in self.img_cache:
            src = asset.get("src")
            if frame is not None:
                src = re.sub(r"%0?(\d*)d", lambda m: f"{frame:0{m.group(1) or 1}d}", src)
            im = Image.open(os.path.join(self.base, src)).convert("RGBA")
            f = self.scale
            im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
            if len(self.img_cache) > 400: self.img_cache.clear()
            self.img_cache[key] = im
        return self.img_cache[key]

    def font_for(self, style_ids, overrides):
        attrs = {}
        for sid in style_ids:
            st = self.styles.get(sid)
            if st is not None:
                attrs.update(st.attrib)
        attrs.update({k: v for k, v in overrides.items() if v is not None})
        return attrs

    def text_image(self, asset):
        aid = asset.get("id")
        if aid in self.text_cache: return self.text_cache[aid]
        W, H = int(asset.get("width")), int(asset.get("height"))
        base = self.font_for([asset.get("style")], {k: asset.get(k) for k in ("size", "color", "fontAsset", "lineHeight")})
        runs = []
        spans = [s for s in asset if ln(s) == "span"]
        if not spans:
            txt = re.sub(r"\{\{(\w+)\}\}", lambda m: self.params.get(m.group(1), ""), asset.get("text", ""))
            runs.append((txt, base))
        for s in spans:
            a = dict(base)
            a.update(self.font_for([s.get("style")], {}))
            for k in ("size", "color", "fontAsset"):
                if s.get(k): a[k] = s.get(k)
            if s.get("style") and not s.get("color") and "color" in self.styles[s.get("style")].attrib:
                a["color"] = self.styles[s.get("style")].get("color")
            if s.get("style") and not s.get("size") and "size" in self.styles[s.get("style")].attrib:
                a["size"] = self.styles[s.get("style")].get("size")
            runs.append((s.text or "", a))
        scale = 1.0
        min_size = float(asset.get("minSize", 8))
        while True:
            lines = self.layout(runs, W, scale)
            h = sum(l[1] for l in lines)
            if h <= H or scale * float(base.get("size", 40)) <= min_size: break
            scale *= 0.94
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        y = (H - h) / 2 if asset.get("verticalAlign") == "middle" else 0
        for words, lh in lines:
            lw = sum(w for _, w, _, _ in words)
            x = (W - lw) / 2 if asset.get("align") == "center" else 0
            for text, w, font, col in words:
                d.text((x, y + lh * 0.1), text, font=font, fill=col)
                x += w
            y += lh
        f = self.scale
        img = img.resize((max(1, round(W * f)), max(1, round(H * f))), Image.LANCZOS)
        self.text_cache[aid] = img
        return img

    def layout(self, runs, W, scale):
        lines, cur, cur_w, cur_h = [], [], 0, 0
        for text, a in runs:
            size = float(a.get("size", 40)) * scale
            font = ImageFont.truetype(self.fonts[a.get("fontAsset", "font-body")], max(6, int(size)))
            col = parse_color(a.get("color", "#1E1E1EFF"), self.tokens)
            lh = size * float(a.get("lineHeight", 1.2))
            for pi, para in enumerate(text.split("\n")):
                if pi > 0:
                    lines.append((cur, cur_h or lh)); cur, cur_w, cur_h = [], 0, 0
                for word in re.findall(r"\S+\s*", para):
                    w = font.getlength(word)
                    if cur_w + w > W and cur:
                        lines.append((cur, cur_h)); cur, cur_w, cur_h = [], 0, 0
                    cur.append((word, w, font, col)); cur_w += w; cur_h = max(cur_h, lh)
        if cur: lines.append((cur, cur_h))
        return lines

    def shape_image(self, el):
        W, H = float(el.get("width")), float(el.get("height"))
        f = self.scale
        pad = 40
        img = Image.new("RGBA", (int(W * f) + 2 * pad, int(H * f) + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fill = parse_color(el.get("fill", "#FFFFFFFF"), self.tokens)
        stroke = parse_color(el.get("stroke", "#00000000"), self.tokens)
        sw = float(el.get("strokeWidth", 0)) * f
        kind = el.get("shape")
        pts = []
        if kind == "star":
            n = int(el.get("points", 5)); ro = float(el.get("outerRadius", W / 2)); ri = float(el.get("innerRadius", ro / 2))
            for i in range(2 * n):
                r = ro if i % 2 == 0 else ri; a = math.pi * i / n - math.pi / 2
                pts.append((pad + (W / 2 + r * math.cos(a)) * f, pad + (H / 2 + r * math.sin(a)) * f))
            d.polygon(pts, fill=fill)
            return img, pad
        if kind == "ellipse":
            pts = [(pad + (W / 2 + W / 2 * math.cos(a)) * f, pad + (H / 2 + H / 2 * math.sin(a)) * f) for a in np.linspace(0, 2 * math.pi, 120)]
            d.polygon(pts, fill=fill)
        if kind == "path":
            nums = [float(v) for v in re.findall(r"-?[\d.]+", el.get("path"))]
            x0, y0, cx, cy, x1, y1 = nums[:6]
            pts = [(pad + ((1 - u) ** 2 * x0 + 2 * (1 - u) * u * cx + u * u * x1) * f,
                    pad + ((1 - u) ** 2 * y0 + 2 * (1 - u) * u * cy + u * u * y1) * f) for u in np.linspace(0, 1, 200)]
        if sw > 0 and pts:
            trim = float(el.get("_trimEnd", el.get("trimEnd", 1)))
            dash = [float(v) * f for v in el.get("dash", "").split()] or [1e9]
            seglen = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
            total = sum(seglen) * trim
            acc, on, di, left = 0.0, True, 0, dash[0]
            for (a, b), L in zip(zip(pts, pts[1:]), seglen):
                if acc >= total: break
                if on: d.line([a, b], fill=stroke, width=max(1, int(sw)))
                acc += L; left -= L
                while left <= 0:
                    di = (di + 1) % len(dash); left += dash[di]; on = not on
        return img, pad

    # ---------------------------------------------------------------- drawing
    def affine(self, el, t, local):
        x, y = self.prop(el, "x", t, local), self.prop(el, "y", t, local)
        r = math.radians(self.prop(el, "rotation", t, local))
        sx, sy = self.prop(el, "scaleX", t, local), self.prop(el, "scaleY", t, local)
        ax, ay = self.prop(el, "anchorX", t, local), self.prop(el, "anchorY", t, local)
        c, s = math.cos(r), math.sin(r)
        M = np.array([[c * sx, -s * sy, 0], [s * sx, c * sy, 0], [0, 0, 1]])
        M[0, 2] = x - (M[0, 0] * ax + M[0, 1] * ay)
        M[1, 2] = y - (M[1, 0] * ax + M[1, 1] * ay)
        return M

    def blit(self, canvas, img, M, pad=0, opacity=1.0):
        f = self.scale
        # img pixels are in local units * f; map local->screen then to canvas pixels * f
        S = np.array([[f, 0, 0], [0, f, 0], [0, 0, 1]])
        Si = np.array([[1 / f, 0, -pad / f], [0, 1 / f, -pad / f], [0, 0, 1]])
        T = S @ M @ Si
        if abs(np.linalg.det(T[:2, :2])) < 1e-9: return
        Ti = np.linalg.inv(T)
        out = img.transform(canvas.size, Image.AFFINE, tuple(Ti[:2].flatten()), resample=Image.BILINEAR)
        if opacity < 1:
            a = out.getchannel("A").point(lambda v: int(v * opacity)); out.putalpha(a)
        canvas.alpha_composite(out)

    def draw_children(self, parent, canvas, t, M, local, overrides, opacity, clock_t):
        kids = [c for c in parent if ln(c) in ("group", "layer", "shape", "instance", "adjustment")]
        kids.sort(key=lambda c: int(c.get("z", 0)))
        for c in kids:
            self.draw_node(c, canvas, t, M, local, overrides, opacity, clock_t)

    def visible(self, el, t, local):
        if local: return True
        s, e = self.window(el)
        return t >= s - 1e-9 and (e is None or t < e)

    def draw_node(self, el, canvas, t, PM, local, overrides, popacity, clock_t=None):
        kind = ln(el)
        if kind == "adjustment": return
        if not local and (el.get("startMarker") or el.get("start")) and not self.visible(el, t, local) and not getattr(self, "_force", False):
            return
        op = popacity * self.prop(el, "opacity", t, local)
        for ta in el:
            if ln(ta) == "textAnimator" and ta.get("preset") == "fade-in":
                st = self.window(el)[0] if not local else 0
                u = (t - st - float(ta.get("presetStart", 0))) / float(ta.get("presetDuration", 1))
                op *= min(1, max(0, u))
        if op <= 0.001: return
        M = PM @ self.affine(el, t, local)
        effects = [self.effects[e] for e in (el.get("effects") or "").split()]
        target = canvas
        if kind == "group" and effects:
            target = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        if kind == "group":
            self.draw_children(el, target, t, M, local, overrides, 1.0 if target is not canvas else op, clock_t)
        elif kind == "instance":
            sym = self.ids[el.get("symbol")]
            s, e = self.window(el)
            lt = t - s
            if lt < 0 or lt > float(sym.get("duration", 1e9)): return
            ov = dict(overrides)
            for o in el.iter("override"): ov[(o.get("target"), o.get("property"))] = o.get("value")
            self.draw_children(sym, canvas, lt, M, True, ov, op, lt)
            return
        elif kind == "layer":
            asset = self.ids[ov_asset] if (ov_asset := overrides.get((el.get("id"), "asset"))) else self.ids[el.get("asset")]
            akind = ln(asset)
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0)) if effects else canvas
            if akind == "image": img, pad = self.image(asset), 0
            elif akind == "text": img, pad = self.text_image(asset), 0
            elif akind == "imageSequence":
                s = self.window(el)[0] if not local else 0
                lt = t - s
                tr = el.find("timeRemap")
                fps = float(Fraction(asset.get("fps")))
                n = int(asset.get("last")) - int(asset.get("first")) + 1
                if tr is not None: src_t = self.eval_keys(list(tr.iter("key")), lt, "linear", True)
                else: src_t = lt
                idx = int(math.floor(src_t * fps + 1e-6))
                idx = idx % n if (tr is None and int(el.get("loop", 0)) > 0) else min(max(idx, 0), n - 1)
                img, pad = self.image(asset, int(asset.get("first")) + idx), 0
            else: return
            self.blit(layer, img, M, pad, 1.0 if effects else op)
            if effects:
                self.apply_effects(layer, effects, t)
                if op < 1: layer.putalpha(layer.getchannel("A").point(lambda v: int(v * op)))
                canvas.alpha_composite(layer)
            return
        elif kind == "shape":
            for a in el:
                if ln(a) == "animate" and a.get("property") == "trimEnd":
                    el.set("_trimEnd", str(self.eval_keys(list(a.iter("key")), t, "linear", local)))
            img, pad = self.shape_image(el)
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0)) if effects else canvas
            self.blit(layer, img, M, pad, 1.0 if effects else op)
            if effects:
                self.apply_effects(layer, effects, t)
                if op < 1: layer.putalpha(layer.getchannel("A").point(lambda v: int(v * op)))
                canvas.alpha_composite(layer)
            return
        if target is not canvas:
            self.apply_effects(target, effects, t)
            if op < 1: target.putalpha(target.getchannel("A").point(lambda v: int(v * op)))
            canvas.alpha_composite(target)

    def apply_effects(self, buf, effects, t):
        f = self.scale
        for e in effects:
            typ = e.get("type")
            if typ == "drop-shadow":
                a = buf.getchannel("A").filter(ImageFilter.GaussianBlur(float(e.get("radius", 4)) * f / 2))
                col = parse_color(e.get("color", "#00000080"), self.tokens)
                a = a.point(lambda v: int(v * col[3] / 255))
                sh = Image.new("RGBA", buf.size, col[:3] + (0,)); sh.putalpha(a)
                sh = ImageChops.offset(sh, int(float(e.get("offsetX", 8)) * f), int(float(e.get("offsetY", 8)) * f))
                sh.alpha_composite(buf); buf.paste(sh)
            elif typ in ("lens-blur", "blur"):
                r = float(e.get("radius", 4))
                for a in e:
                    if ln(a) == "animate" and a.get("property") == "radius":
                        r = self.eval_keys(list(a.iter("key")), t, "linear", False)
                if r * f > 0.3: buf.paste(buf.filter(ImageFilter.GaussianBlur(r * f / 2)))
            elif typ == "color-grade":
                rgb = buf.convert("RGB")
                rgb = ImageEnhance.Color(rgb).enhance(float(e.get("saturation", 1)))
                rgb = ImageEnhance.Contrast(rgb).enhance(float(e.get("contrast", 1)))
                rgb = ImageEnhance.Brightness(rgb).enhance(1 + float(e.get("brightness", 0)) * 2)
                rgb.putalpha(buf.getchannel("A")); buf.paste(rgb)
            elif typ == "lighting":
                buf.paste(self.lamp_light(buf, t))

    def lamp_light(self, buf, t):
        W, H = buf.size
        if not hasattr(self, "_lamp"):
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            d = np.sqrt((xx - 0.16 * W) ** 2 + (yy + 0.2 * H) ** 2) / W
            g = np.clip(1.12 - 0.45 * d, 0.62, 1.1)
            self._lamp = np.stack([g * 1.03, g * 1.0, g * 0.93], -1)
        dim = 0.88 if self.markers["m-s4"] <= t < self.markers["m-s5"] else 1.0
        a = np.asarray(buf, np.float32)
        a[..., :3] = np.clip(a[..., :3] * self._lamp * dim, 0, 255)
        return Image.fromarray(a.astype(np.uint8), "RGBA")

    # ---------------------------------------------------------------- frame
    def scene_buffer(self, g, t, force=False):
        f = self.scale
        buf = Image.new("RGBA", (round(self.W * f), round(self.H * f)), (0, 0, 0, 0))
        self._force = force
        self.draw_node(g, buf, t, np.eye(3), False, {}, 1.0)
        self._force = False
        return buf

    def frame(self, t, fi):
        f = self.scale
        size = (round(self.W * f), round(self.H * f))
        out = Image.new("RGBA", size, parse_color(self.bg, self.tokens))
        comp = self.root.find("composition")
        scenes = [g for g in comp if ln(g) == "group"]
        trans = [tr for tr in comp if ln(tr) == "transition"]
        handled = False
        for tr in trans:
            dur = float(tr.get("duration", 0.5)); typ = tr.get("type")
            A = self.ids.get(tr.get("from")); B = self.ids.get(tr.get("to"))
            cut = self.window(B)[0] if (A is not None and B is not None) else (self.window(B)[0] if B is not None else self.window(A)[1])
            al = tr.get("alignment", "center")
            s0 = cut - dur / 2 if al == "center" else (cut if al == "start" else cut - dur)
            if typ == "cut" or not (s0 <= t < s0 + dur): continue
            p = ease((t - s0) / dur, "ease-in-out")
            col = parse_color(tr.get("color", "#000000FF"), self.tokens)
            if A is not None and B is not None:
                a, b = self.scene_buffer(A, t, True), self.scene_buffer(B, t, True)
                if typ == "dip-to-color":
                    base = a if p < 0.5 else b
                    k = 1 - abs(p - 0.5) * 2
                    out.alpha_composite(base); out = Image.blend(out, Image.new("RGBA", size, col), k)
                elif typ == "crossfade":
                    out.alpha_composite(a); tmp = out.copy(); tmp.alpha_composite(b); out = Image.blend(out, tmp, p)
                elif typ in ("slide", "push"):
                    dx = int(size[0] * (1 - p))
                    if typ == "push": out.alpha_composite(ImageChops.offset(a, -int(size[0] * p), 0).crop((0, 0) + size) if False else a.transform(size, Image.AFFINE, (1, 0, size[0] * p, 0, 1, 0)))
                    else: out.alpha_composite(a)
                    out.alpha_composite(b.transform(size, Image.AFFINE, (1, 0, -dx, 0, 1, 0)))
                else:
                    out.alpha_composite(a if p < 0.5 else b)
            else:
                node = B if B is not None else A
                out.alpha_composite(self.scene_buffer(node, t))
                k = (1 - p) if B is not None else p
                out = Image.blend(out, Image.new("RGBA", size, col), k)
            handled = True
            break
        if not handled:
            for g in scenes:
                s, e = self.window(g)
                if s <= t < (e or 1e9):
                    out.alpha_composite(self.scene_buffer(g, t))
        return self.finish(out.convert("RGB"), fi)

    def finish(self, img, fi):
        img = ImageEnhance.Color(img).enhance(0.95)
        img = ImageEnhance.Contrast(img).enhance(1.06)
        a = np.asarray(img, np.float32)
        rng = np.random.default_rng(fi)
        a *= 2 ** rng.uniform(-0.03, 0.03)
        a += rng.normal(0, 5, a.shape[:2])[..., None]
        if not hasattr(self, "_vig"):
            H, W = a.shape[:2]
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
            self._vig = np.clip(1 - 0.4 * np.clip(r - 0.55, 0, 1) ** 1.5, 0, 1)[..., None]
        a *= self._vig
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

# -------------------------------------------------------------------- audio
def mix_audio(doc, path, dur):
    SR = 48000
    out = np.zeros((int(SR * dur), 2), np.float32)
    def load(p):
        with wave.open(p) as w:
            d = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32767
            return d.reshape(-1, w.getnchannels())
    am = doc.root.find("audioMix")
    buses = {b.get("id"): float(b.get("volume", 1)) for b in am if ln(b) == "bus"}
    for tr in am:
        if ln(tr) != "audioTrack": continue
        a = doc.ids[tr.get("asset")]
        x = load(os.path.join(doc.base, a.get("src")))
        loops = int(tr.get("loop", 0))
        if loops: x = np.concatenate([x] * (loops + 1))
        start = doc.markers.get(tr.get("startMarker"), float(tr.get("start", 0)))
        if tr.get("clipOut"): x = x[: int(float(tr.get("clipOut")) * SR)]
        vol = float(tr.get("volume", 1)) * buses.get(tr.get("bus"), 1)
        i0 = int(start * SR); n = min(len(x), len(out) - i0)
        if n <= 0: continue
        seg = x[:n] * vol
        tt = start + np.arange(n) / SR
        anim = [c for c in tr if ln(c) == "animate" and c.get("property") == "volume"]
        if anim:
            keys = list(anim[0].iter("key"))
            env = np.array([doc.eval_keys(keys, v, "linear", False) for v in tt[::480]])
            env = np.repeat(env, 480)[:n] / float(tr.get("volume", 1))
            seg *= env[:, None]
        fi, fo = float(tr.get("fadeIn", 0)), float(tr.get("fadeOut", 0))
        if fi: seg[: int(fi * SR)] *= np.linspace(0, 1, int(fi * SR))[:, None][: n]
        if fo:
            m = min(n, int(fo * SR)); seg[-m:] *= np.linspace(1, 0, m)[:, None]
        out[i0:i0 + n] += seg
    out /= max(1e-6, np.abs(out).max()) / 0.79
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((out * 32767).astype("<i2").tobytes())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene"); ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=0.5); ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--from", dest="t0", type=float, default=0); ap.add_argument("--to", dest="t1", type=float)
    ap.add_argument("--frames-dir", help="keep frames here and resume from existing ones")
    ap.add_argument("--max-frames", type=int, help="render at most N new frames, then stop (resumable)")
    ap.add_argument("--stills", help="comma-separated times to write as PNG instead of a video")
    args = ap.parse_args()
    doc = Doc(args.scene, args.scale)
    if args.stills:
        for tt in args.stills.split(","):
            doc.frame(float(tt), 0).save(f"{args.out}_{float(tt):05.1f}.png"); print("still", tt)
        return
    t1 = args.t1 or doc.duration
    tmp = args.frames_dir or tempfile.mkdtemp()
    os.makedirs(tmp, exist_ok=True)
    n = int(round((t1 - args.t0) * args.fps))
    for i in range(n):
        t = args.t0 + i / args.fps
        fp = os.path.join(tmp, f"f{i:05d}.jpg")
        if os.path.exists(fp): continue
        if args.max_frames is not None and args.max_frames <= 0: print("partial"); return
        doc.frame(t, i).save(fp + ".tmp.jpg", quality=92); os.replace(fp + ".tmp.jpg", fp)
        if args.max_frames is not None: args.max_frames -= 1
        if i % 24 == 0: print(f"frame {i}/{n}", flush=True)
    wav = os.path.join(tmp, "mix.wav"); mix_audio(doc, wav, doc.duration)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", os.path.join(tmp, "f%05d.jpg"),
                    "-ss", str(args.t0), "-t", str(t1 - args.t0), "-i", wav, "-r", "24", "-c:v", "libx264", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", args.out], check=True)
    print("wrote", args.out)

if __name__ == "__main__":
    main()
