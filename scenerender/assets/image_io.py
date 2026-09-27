"""Decode still images without reducing their samples to eight bits.

Pixels are code values, with alpha association reported separately. The asset's
colorSpace/transfer declarations remain authoritative (including schema defaults).
Selectors are names, slash-separated PSD group paths, EXR part names or indices,
and EXR channel prefixes; ``part:prefix`` disambiguates multipart channel sets.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class DecodedImage:
    rgba: np.ndarray
    alpha: str = "straight"


def _rgba(samples, *, alpha=False, maximum=None):
    a = np.asarray(samples)
    if maximum is None:
        maximum = np.iinfo(a.dtype).max if a.dtype.kind in "ui" else 1
    a = a.astype(np.float32) / np.float32(maximum)
    if a.ndim == 2:
        a = a[..., None]
    channels = a.shape[-1] - int(alpha)
    rgb = np.repeat(a[..., :1], 3, -1) if channels == 1 else a[..., :3]
    opacity = a[..., -1:] if alpha else np.ones(a.shape[:2] + (1,), np.float32)
    return np.concatenate((rgb, opacity), -1)


def decode(path: str, selector: str | None = None) -> DecodedImage:
    from PIL import Image
    ext = Path(path).suffix.lower()
    if ext in (".psd", ".psb"):
        return _psd(path, selector)
    if ext == ".exr":
        return _exr(path, selector)
    if ext in (".tif", ".tiff"):
        return _tiff(path, selector)
    if selector:
        raise ValueError(f"layer/channel selector {selector!r} is not supported by {ext} files")
    if ext in (".hdr", ".rgbe"):
        from ..three.loaders import _read_rgbe
        return DecodedImage(_rgba(_read_rgbe(Path(path).read_bytes())))
    with Image.open(path) as im:
        if im.format == "PNG":
            # Pillow converts 16-bit RGB(A) PNGs to 8-bit during decoding.
            with open(path, "rb") as stream:
                header = stream.read(25)
            if len(header) == 25 and header[24] == 16:
                import png
                w, h, rows, info = png.Reader(filename=path).asDirect()
                values = np.asarray(list(rows), np.uint16).reshape(h, w, info["planes"])
                return DecodedImage(_rgba(values, alpha=info["alpha"], maximum=65535))
        return DecodedImage(_rgba(np.asarray(im.convert("RGBA")), alpha=True))


def _tiff(path, selector):
    import tifffile
    with tifffile.TiffFile(path) as tf:
        if selector:
            matches = [p for p in tf.pages if p.description == selector]
            if not matches and selector.isdecimal() and int(selector) < len(tf.pages):
                matches = [tf.pages[int(selector)]]
            if len(matches) != 1:
                raise ValueError(f"TIFF page {selector!r} not found or ambiguous")
            page = matches[0]
        else:
            page = tf.pages[0]
        a = page.asarray()
        if page.planarconfig == 2:
            a = np.moveaxis(a, 0, -1)
        mode = int(page.photometric)
        if mode == 3:  # Palette entries are 16-bit, even for an 8-bit index.
            a = np.moveaxis(page.colormap[:, a], 0, -1)
        elif mode not in (0, 1, 2):
            raise ValueError(f"TIFF photometric mode {page.photometric.name} is not an RGB/grayscale image")
        alpha_index = next((i for i, kind in enumerate(page.extrasamples) if int(kind) in (1, 2)), None)
        associated = alpha_index is not None and int(page.extrasamples[alpha_index]) == 1
        if alpha_index is not None:
            colors = 3 if mode in (2, 3) else 1
            a = np.concatenate((a[..., :colors], a[..., colors + alpha_index:colors + alpha_index + 1]), -1)
        elif a.ndim == 3:
            a = a[..., :3 if mode in (2, 3) else 1]
        rgba = _rgba(a, alpha=alpha_index is not None)
        if mode == 0:
            rgba[..., :3] = 1 - rgba[..., :3]
        orientation = int(page.tags["Orientation"].value) if "Orientation" in page.tags else 1
        if orientation in (2, 3):
            rgba = rgba[:, ::-1]
        if orientation in (3, 4):
            rgba = rgba[::-1]
        if orientation in (5, 6, 7, 8):
            rgba = np.swapaxes(rgba, 0, 1)
            if orientation in (6, 7):
                rgba = rgba[:, ::-1]
            if orientation in (7, 8):
                rgba = rgba[::-1]
        return DecodedImage(np.ascontiguousarray(rgba), "premultiplied" if associated else "straight")


def _psd(path, selector):
    from psd_tools import PSDImage
    from psd_tools.composite import composite
    from psd_tools.constants import ColorMode
    psd = PSDImage.open(path)
    target = psd
    if selector:
        layers = list(psd.descendants())
        def full_name(layer):
            names = [layer.name]
            parent = layer.parent
            while parent is not psd and parent is not None:
                names.append(parent.name)
                parent = parent.parent
            return "/".join(reversed(names))
        matches = [layer for layer in layers if full_name(layer) == selector]
        if not matches:
            matches = [layer for layer in layers if layer.name == selector]
        if not matches and selector.isdecimal() and int(selector) < len(layers):
            matches = [layers[int(selector)]]
        if len(matches) != 1:
            raise ValueError(f"PSD layer {selector!r} not found or ambiguous")
        target = matches[0]
    if not selector and psd.has_preview():
        rgb, a = psd.numpy("color"), psd.numpy("shape")
    else:
        # A specifically requested hidden layer is still drawable. Child visibility
        # is relative to this selection, rather than to its hidden ancestors.
        def visible(layer):
            while layer is not target and layer is not psd:
                if not layer.visible:
                    return False
                layer = layer.parent
            return True
        rgb, _, a = composite(target, viewport=psd.viewbox, layer_filter=visible, as_layer=bool(selector))
    if rgb is None:
        raise ValueError("PSD has no raster or compositable image")
    if psd.color_mode == ColorMode.CMYK:
        # PSD stores inverted ink samples. This conversion preserves float precision;
        # colorSpace still declares the resulting RGB interpretation.
        rgb = rgb[..., :3] * rgb[..., 3:4]
    elif psd.color_mode not in (ColorMode.RGB, ColorMode.GRAYSCALE, ColorMode.BITMAP):
        raise ValueError(f"PSD color mode {psd.color_mode.name} is not supported")
    rgba = _rgba(rgb)
    if a is not None:
        rgba[..., 3:4] = a
    return DecodedImage(rgba)


def _exr(path, selector):
    import OpenEXR
    with OpenEXR.File(path, separate_channels=True) as exr:
        parts = exr.parts
        part = parts[0]
        prefix = ""
        if selector:
            matches = [p for p in parts if p.name() == selector]
            if not matches and selector.isdecimal() and int(selector) < len(parts):
                matches = [parts[int(selector)]]
            if matches:
                if len(matches) != 1:
                    raise ValueError(f"EXR part {selector!r} is ambiguous")
                part = matches[0]
            elif ":" in selector:
                name, prefix = selector.split(":", 1)
                matches = [p for p in parts if p.name() == name]
                if not matches and name.isdecimal() and int(name) < len(parts):
                    matches = [parts[int(name)]]
                if len(matches) != 1:
                    raise ValueError(f"EXR part {name!r} not found or ambiguous")
                part = matches[0]
            else:
                prefix = selector
                matches = [p for p in parts if prefix in p.channels or
                           any(k.startswith(prefix + ".") for k in p.channels)]
                if len(matches) != 1:
                    raise ValueError(f"EXR channel set {selector!r} not found or ambiguous")
                part = matches[0]
        channels = part.channels
        if not prefix and not any(k in channels for k in ("R", "Y")):
            groups = {k[:-2] for k in channels if k.endswith(".R") and
                      all(k[:-1] + c in channels for c in "RGB")}
            if len(groups) == 1:
                prefix = next(iter(groups))
            elif len(channels) == 1:
                prefix = next(iter(channels))
        lead = prefix + "." if prefix else ""
        if prefix in channels:
            keys = [prefix] * 3
            alpha_key = None
        elif all(lead + c in channels for c in "RGB"):
            keys = [lead + c for c in "RGB"]
            alpha_key = lead + "A"
        elif lead + "Y" in channels:
            keys = [lead + "Y"] * 3
            alpha_key = lead + "A"
        else:
            raise ValueError(f"EXR part {part.name()!r} has no RGB/Y channel set {prefix!r}")
        rgb = np.stack([channels[k].pixels for k in keys], -1)
        if rgb.dtype == object:
            raise ValueError("deep EXR samples require flattening before use as an image")
        rgba = _rgba(rgb, maximum=1)  # EXR UINT values are samples, not normalized integer codes.
        if alpha_key in channels:
            rgba[..., 3] = channels[alpha_key].pixels
        dmin, dmax = part.header["dataWindow"]
        vmin, vmax = part.header.get("displayWindow", (dmin, dmax))
        w, h = (vmax - vmin + 1).astype(int)
        canvas = np.zeros((h, w, 4), np.float32)
        x, y = (dmin - vmin).astype(int)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + rgba.shape[1]), min(h, y + rgba.shape[0])
        if x1 > x0 and y1 > y0:
            canvas[y0:y1, x0:x1] = rgba[y0-y:y1-y, x0-x:x1-x]
        return DecodedImage(canvas, "premultiplied")
