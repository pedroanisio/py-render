"""Typed MaterialX inputs and independent image/coordinate graphs.

Uniform defaults come from the document's resolved node definitions. Affine UV
graphs stay at render time; image channels and linear channel operations retain
the source resolution. Other graphs are handed to the MaterialX baker.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
import math

import numpy as np

from ..color import to_working

FLIP_V = np.array([[1., 0., 0.], [0., -1., 1.], [0., 0., 1.]])
WRAP = {'constant': 33069, 'clamp': 33071, 'periodic': 10497, 'mirror': 33648}
CHANNELS = {'float': 1, 'color3': 3, 'color4': 4, 'vector2': 2, 'vector3': 3, 'vector4': 4}
COLOR_SPACES = {
    'srgb_texture': ('srgb', 'srgb'), 'srgb_rec709_scene': ('srgb', 'srgb'),
    'lin_rec709': ('linear-srgb', 'linear'), 'lin_rec709_scene': ('linear-srgb', 'linear'),
    'acescg': ('acescg', 'linear'), 'lin_ap1': ('acescg', 'linear'), 'lin_ap1_scene': ('acescg', 'linear'),
    'lin_ap0': ('aces2065-1', 'linear'), 'lin_ap0_scene': ('aces2065-1', 'linear'),
    'lin_displayp3': ('display-p3', 'linear'), 'lin_p3d65_scene': ('display-p3', 'linear'),
    'srgb_displayp3': ('display-p3', 'srgb'), 'srgb_p3d65_scene': ('display-p3', 'srgb'),
    'lin_rec2020': ('rec2020', 'linear'), 'lin_rec2020_scene': ('rec2020', 'linear'),
    'lin_ciexyzd65_scene': ('xyz-d65', 'linear'),
    'g22_rec709': ('srgb', 'gamma22'), 'g22_rec709_scene': ('srgb', 'gamma22'),
    'g18_rec709': ('srgb', 'gamma18'), 'g18_rec709_scene': ('srgb', 'gamma18'),
    'g24_rec709_scene': ('srgb', 'gamma24'),
}


class NeedsBake(ValueError):
    pass


@lru_cache(maxsize=1)
def library():
    import MaterialX as mx
    document = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), document)
    return document


def input_port(node, name):
    port = node.getInput(name)
    if port is None:
        definition = node.getNodeDef()
        port = definition.getActiveInput(name) if definition is not None else None
    return port


def terminal(port, seen=()):
    if port is None:
        return None
    path = port.getNamePath()
    if path in seen:
        raise ValueError(f'MaterialX input cycle at {path}')
    interface = port.getInterfaceInput() if hasattr(port, 'getInterfaceInput') else None
    if interface is not None:
        return terminal(interface, (*seen, path))
    output = port.getConnectedOutput()
    if output is not None:
        return terminal(output, (*seen, path))
    return port


def connected(port):
    port = terminal(port)
    return port.getConnectedNode() if port is not None else None


def filename_path(port, base_dir):
    """Resolve image filenames against the authoring document, including includes."""
    source = Path(port.getActiveSourceUri())
    directory = source.parent if source.is_absolute() else Path(base_dir) / source.parent
    return (directory / port.getResolvedValueString()).absolute()


def color_value(value, port):
    a = np.asarray(value, np.float32)
    space = port.getActiveColorSpace()
    if space in ('', 'none', 'data', 'raw'):
        return a
    if space not in COLOR_SPACES:
        raise NeedsBake(f'MaterialX color space {space!r}')
    a = a.copy()
    primaries, transfer = COLOR_SPACES[space]
    rgb = a[..., :3]
    if transfer.startswith('gamma'):
        rgb = np.power(np.maximum(rgb, 0.), float(transfer[5:])/10)
        transfer = 'linear'
    a[..., :3] = to_working(rgb, primaries, transfer)
    return a


@dataclass
class ImageValue:
    pixels: np.ndarray
    uv: dict
    sampler: dict
    normal_scale: tuple | None = None

    def channels(self, fn):
        sampler = dict(self.sampler, border=tuple(np.atleast_1d(fn(np.asarray(self.sampler['border'])))))
        return replace(self, pixels=np.ascontiguousarray(fn(self.pixels), np.float32), sampler=sampler)


class Inputs:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.images = {}

    def value(self, node, name, default=None):
        value = self.signal(input_port(node, name))
        if isinstance(value, ImageValue):
            raise NeedsBake(f'nonuniform {node.getNamePath()}/{name}')
        return default if value is None else value

    def signal(self, port, seen=()):
        port = terminal(port)
        if port is None:
            return None
        node = port.getConnectedNode()
        if node is None:
            value = port.getValue() if hasattr(port, 'getValue') else None
            if hasattr(value, 'asTuple'):
                value = np.asarray(value.asTuple(), np.float32)
            if value is not None and port.getType().startswith('color'):
                value = color_value(value, port)
            return value
        path = node.getNamePath()
        if path in seen:
            raise ValueError(f'MaterialX graph cycle at {path}')
        seen = (*seen, path)
        kind = node.getCategory()
        if kind == 'constant':
            return self.signal(input_port(node, 'value'), seen)
        if kind in ('image', 'tiledimage'):
            return self.image(node)
        if kind == 'normalmap':
            if any(node.getInput(name) is not None for name in ('normal', 'tangent', 'bitangent')):
                raise NeedsBake(f'MaterialX custom normal frame at {path}')
            value = self.signal(input_port(node, 'in'), seen)
            scale = tuple(np.broadcast_to(np.atleast_1d(self.value(node, 'scale', 1.)), (2,)))
            if not isinstance(value, ImageValue):
                value = ImageValue(np.asarray(value, np.float32)[None, None], {'constant': (.5, .5)},
                                   {'wrapS': 33071, 'wrapT': 33071, 'border': tuple(value)})
            return replace(value, normal_scale=scale)
        if kind in ('extract', 'swizzle'):
            value = self.signal(input_port(node, 'in'), seen)
            if isinstance(value, ImageValue) and value.normal_scale is not None:
                raise NeedsBake(f'MaterialX channel operation on a geometric normal at {path}')
            if kind == 'extract':
                index = int(self.value(node, 'index', 0))
                fn = lambda a: a[..., index:index+1]
            else:
                channels = self.value(node, 'channels')
                def fn(a):
                    return np.stack([np.full(a.shape[:-1], float(c)) if c in '01' else
                                     a[..., 'rgba'.index(c) if c in 'rgba' else 'xyzw'.index(c)]
                                     for c in channels], -1)
            return value.channels(fn) if isinstance(value, ImageValue) else np.squeeze(fn(np.asarray(value)))
        raise NeedsBake(f'MaterialX node {path} ({kind})')

    def coordinates(self, port, seen=()):
        """UV set or constant, and a matrix in MaterialX's lower-left coordinates."""
        port = terminal(port)
        if port is None or (hasattr(port, 'getDefaultGeomPropString') and port.getDefaultGeomPropString() == 'UV0'):
            return {'texCoord': 0}, np.eye(3)
        node = port.getConnectedNode()
        if node is None:
            value = self.signal(port)
            return ({'constant': tuple(value)} if value is not None else {'texCoord': 0}), np.eye(3)
        path = node.getNamePath()
        if path in seen:
            raise ValueError(f'MaterialX coordinate cycle at {path}')
        if node.getCategory() == 'texcoord':
            return {'texCoord': int(self.value(node, 'index', 0))}, np.eye(3)
        if node.getCategory() == 'constant':
            return {'constant': tuple(self.value(node, 'value'))}, np.eye(3)
        if node.getCategory() != 'place2d':
            raise NeedsBake(f'MaterialX coordinate node {path}')
        uv, previous = self.coordinates(input_port(node, 'texcoord'), (*seen, path))
        pivot = np.asarray(self.value(node, 'pivot', (0., 0.)))
        offset = np.asarray(self.value(node, 'offset', (0., 0.)))
        scale = np.asarray(self.value(node, 'scale', (1., 1.)))
        angle = float(self.value(node, 'rotate', 0.))
        angle_port = input_port(node, 'rotate')
        if angle_port.getUnit() != 'radian':
            angle = math.radians(angle)
        c, s = math.cos(angle), math.sin(angle)
        rotation = np.array([[c, s], [-s, c]])  # MaterialX rotate2d convention.
        inv_scale = np.diag(1/scale)
        order = int(self.value(node, 'operationorder', 0))
        linear = rotation @ inv_scale if order == 0 else inv_scale @ rotation
        shift = pivot - linear @ pivot - (offset if order == 0 else linear @ offset)
        matrix = np.eye(3); matrix[:2, :2] = linear; matrix[:2, 2] = shift
        return uv, matrix @ previous

    def image(self, node):
        count = CHANNELS[node.getType()]
        file = terminal(input_port(node, 'file'))
        path = str(filename_path(file, self.base_dir)) if file is not None else ''
        default = np.broadcast_to(np.atleast_1d(self.value(node, 'default', 0.)), (count,)).copy()
        layer = str(self.value(node, 'layer', '')) or None
        key = (path, layer, file.getActiveColorSpace() if file is not None else '', node.getType())
        if key not in self.images:
            try:
                a = self.read_image(path, layer)
                a = a[..., :count]
                if node.getType().startswith('color'):
                    a = color_value(a, file)
                self.images[key] = a
            except (OSError, ValueError) as exc:
                if isinstance(exc, NeedsBake):
                    raise
                self.images[key] = None
        image = self.images[key]
        missing = image is None
        if missing:
            image = default[None, None]
        uv, matrix = self.coordinates(input_port(node, 'texcoord'))
        if node.getCategory() == 'tiledimage':
            ratio = np.asarray(self.value(node, 'realworldtilesize')) / self.value(node, 'realworldimagesize')
            m = np.eye(3)
            m[:2, :2] = np.diag(np.asarray(self.value(node, 'uvtiling'))*ratio)
            m[:2, 2] = -np.asarray(self.value(node, 'uvoffset'))*ratio
            matrix = m @ matrix
        if 'constant' in uv:
            uv['constant'] = tuple((FLIP_V @ matrix @ np.r_[uv['constant'], 1.])[:2])
        else:
            uv['matrix'] = tuple((FLIP_V @ matrix @ FLIP_V).ravel())
        filtering = self.value(node, 'filtertype', 'linear')
        if filtering not in ('closest', 'linear'):
            raise NeedsBake(f'MaterialX image filter {filtering}')
        sampler = {'minFilter': 9728 if filtering == 'closest' else 9987,
                   'magFilter': 9728 if filtering == 'closest' else 9729,
                   'border': tuple(default)}
        for source, dest in [('uaddressmode', 'wrapS'), ('vaddressmode', 'wrapT')]:
            sampler[dest] = 33071 if missing else WRAP[self.value(node, source, 'periodic')]
        return ImageValue(np.asarray(image, np.float32), uv, sampler)

    @staticmethod
    def read_image(path, layer):
        from PIL import Image
        if layer and Path(path).suffix.lower() in ('.png', '.npy'):
            raise ValueError(f'Image {path} has no layer {layer!r}')
        if Path(path).suffix.lower() == '.npy':
            a = np.asarray(np.load(path), np.float32)
            if a.ndim == 2:
                a = a[..., None]
        elif Path(path).suffix.lower() == '.png':
            import png
            width, height, rows, info = png.Reader(filename=path).asDirect()
            a = np.asarray(list(rows), np.float32).reshape(height, width, info['planes']) / (2**info['bitdepth']-1)
        else:
            from ..assets.image_io import decode
            a = decode(path, layer).rgba
            # Preserve the source's component count for ordinary monochrome files.
            try:
                with Image.open(path) as image:
                    if image.mode in ('1', 'L', 'I', 'F', 'I;16', 'I;16B', 'I;16L'):
                        a = a[..., :1]
                    elif image.mode == 'LA':
                        a = a[..., [0, 3]]
            except OSError:
                pass
        result = np.zeros((*a.shape[:2], 4), np.float32)
        result[..., 3] = 1.
        result[..., :a.shape[-1]] = a
        return result
