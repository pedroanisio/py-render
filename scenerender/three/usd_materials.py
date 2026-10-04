"""UsdPreviewSurface textures and coordinate graphs.

Coordinates are kept as affine maps of named primvars, so filtering happens at
render time with each input's own image, transform and wrap modes. USD uses a
lower-left image origin; the model's UV convention uses an upper-left origin.
"""
from collections import OrderedDict
import io
import logging
import math

import numpy as np

from ..raster import srgb_to_linear
from .model import MaterialSpec

log = logging.getLogger('scenerender')
FLIP_V = np.array([[1., 0., 0.], [0., -1., 1.], [0., 0., 1.]])
WRAP = {'black': 33069, 'clamp': 33071, 'repeat': 10497, 'mirror': 33648}


class PreviewMaterial:
    def __init__(self, material, start, rate):
        from pxr import Usd
        self.material = material
        self.stage = material.GetPrim().GetStage()
        self.shader = material.ComputeSurfaceSource()[0]
        self.start, self.rate = start, rate
        self.time = Usd.TimeCode.Default()
        self.times = set()
        self.images = OrderedDict()
        self.processed = OrderedDict()
        self.samples = OrderedDict()

    def terminal(self, attr, seen=()):
        if not attr:
            return None
        path = str(attr.GetPath())
        if path in seen:
            raise ValueError(f'USD material connection cycle at {path}')
        self.times.update(attr.GetTimeSamples())
        sources = attr.GetConnections()
        if sources:
            return self.terminal(self.stage.GetAttributeAtPath(sources[0]), (*seen, path))
        return attr

    def attribute_value(self, attr, default=None):
        value = attr.Get(self.time) if attr else None
        if value is None and attr and self.time.IsDefault() and attr.GetTimeSamples():
            from pxr import Usd
            value = attr.Get(Usd.TimeCode(self.start))
        return default if value is None else value

    def value(self, shader, name, default=None):
        inp = shader.GetInput(name)
        attr = self.terminal(inp.GetAttr()) if inp else None
        return self.attribute_value(attr, default)

    def coordinates(self, inp, seen=()):
        """(primvar name or None, fallback/constant st, USD-space affine matrix)."""
        from pxr import UsdShade
        attr = self.terminal(inp.GetAttr()) if inp else None
        if attr is None:
            return None, (0., 0.), np.eye(3)
        path = str(attr.GetPath())
        if path in seen:
            raise ValueError(f'USD texture coordinate cycle at {path}')
        shader = UsdShade.Shader(attr.GetPrim())
        kind = shader.GetIdAttr().Get() if shader else None
        if attr.GetName().startswith('outputs:') and kind == 'UsdPrimvarReader_float2':
            return str(self.value(shader, 'varname', '')), self.value(shader, 'fallback', (0., 0.)), np.eye(3)
        if attr.GetName().startswith('outputs:') and kind == 'UsdTransform2d':
            name, fallback, previous = self.coordinates(shader.GetInput('in'), (*seen, path))
            scale = self.value(shader, 'scale', (1., 1.))
            angle = math.radians(self.value(shader, 'rotation', 0.))
            shift = self.value(shader, 'translation', (0., 0.))
            c, s = math.cos(angle), math.sin(angle)
            matrix = np.array([[c*scale[0], -s*scale[1], shift[0]],
                               [s*scale[0], c*scale[1], shift[1]], [0., 0., 1.]])
            return name, fallback, matrix @ previous
        return None, tuple(self.attribute_value(attr, (0., 0.))), np.eye(3)

    def image(self, path, space):
        from PIL import Image
        from .loaders import _usd_asset_bytes, decode_image
        key = (path, space)
        if key in self.images:
            self.images.move_to_end(key)
            return self.images[key]
        try:
            data = _usd_asset_bytes(path)
            if data is None:
                raise FileNotFoundError(path)
            metadata = {}
            automatic_srgb = False
            try:
                with Image.open(io.BytesIO(data)) as image:
                    metadata = dict(image.info)
                    automatic_srgb = image.mode in ('RGB', 'RGBA', 'P')
                    if image.format == 'PNG' and data[24] != 8:
                        automatic_srgb = False
                    if 'srgb' in metadata:
                        automatic_srgb = True
                    elif 'icc_profile' in metadata:
                        from PIL import ImageCms
                        profile = ImageCms.ImageCmsProfile(io.BytesIO(metadata['icc_profile']))
                        automatic_srgb = 'srgb' in ImageCms.getProfileDescription(profile).lower()
                    elif 'gamma' in metadata:
                        automatic_srgb = abs(float(metadata['gamma']) - 1/2.2) < .001
            except (OSError, ValueError):
                pass  # HDR/EXR/NumPy decoders consume their native linear samples.
            if data.startswith(b'\x89PNG') and data[24] == 16:
                import png
                from ..assets.image_io import _rgba
                width, height, rows, info = png.Reader(bytes=data).asDirect()
                values = np.asarray(list(rows), np.uint16).reshape(height, width, info['planes'])
                pixels = _rgba(values, alpha=info['alpha'], maximum=65535)
            else:
                pixels = decode_image(data, False)
            if space.lower() == 'srgb' or (space == 'auto' and automatic_srgb):
                pixels = pixels.copy()
                pixels[..., :3] = srgb_to_linear(pixels[..., :3])
            result = pixels, metadata
        except (OSError, ValueError, KeyError) as exc:
            log.warning('USD texture %r not loaded; using its fallback: %s', path, exc)
            result = None, {}
        self.images[key] = result
        while len(self.images) > 8 and (len(self.images) > 32 or
                sum(a.nbytes for a, _ in self.images.values() if a is not None) > 128*1024**2):
            self.images.popitem(last=False)
        return result

    def texture(self, shader, output, normal=False, color=False):
        asset = self.value(shader, 'file')
        path = (asset.resolvedPath or asset.path) if asset else ''
        pixels, metadata = self.image(path, str(self.value(shader, 'sourceColorSpace', 'auto')))
        missing = pixels is None
        if missing:
            pixels = np.array(self.value(shader, 'fallback', (0., 0., 0., 1.)), np.float32)[None, None, :]
        scale = np.array(self.value(shader, 'scale', (1., 1., 1., 1.)))
        bias = np.array(self.value(shader, 'bias', (0., 0., 0., 0.)))
        original = pixels
        key = (id(original), tuple(scale), tuple(bias), output, normal, color)
        border = bias.copy()
        channel = {'r': 0, 'g': 1, 'b': 2, 'a': 3}.get(output)
        if channel is not None:
            border = np.repeat(border[channel], 4)
        if normal:
            # The USD input is a signed tangent-space vector; the renderer's
            # normal map storage is encoded into [0,1] and decoded in GLSL.
            border[:3] = border[:3]*.5 + .5
        if color or normal:
            border[3] = 1.
        if key not in self.processed or self.processed[key][0] is not original:
            pixels = pixels * scale + bias
            if channel is not None:
                pixels = np.repeat(pixels[..., channel:channel+1], 4, -1)
            if normal:
                pixels[..., :3] = pixels[..., :3]*.5 + .5
            if color or normal:
                pixels[..., 3] = 1.
            self.processed[key] = (original, np.ascontiguousarray(pixels, np.float32))
            while len(self.processed) > 8 and (len(self.processed) > 32 or
                    sum(a.nbytes for _, a in self.processed.values()) > 128*1024**2):
                self.processed.popitem(last=False)
        self.processed.move_to_end(key)
        pixels = self.processed[key][1]
        name, fallback, matrix = self.coordinates(shader.GetInput('st'))
        uv = {'texCoord': name, 'fallback': (float(fallback[0]), 1.-float(fallback[1])),
              'matrix': tuple((FLIP_V @ matrix @ FLIP_V).ravel())}
        if name is None:
            uv['constant'] = uv['fallback']
        sampler = {'minFilter': 9987, 'magFilter': 9729, 'border': tuple(float(v) for v in border)}
        for axis in ('wrapS', 'wrapT'):
            mode = str(self.value(shader, axis, 'useMetadata'))
            if mode == 'useMetadata':
                mode = str(metadata.get(axis, 'black'))
            sampler[axis] = WRAP.get(mode, WRAP['black'])
        if missing:
            sampler['wrapS'] = sampler['wrapT'] = WRAP['clamp']
        return pixels, uv, sampler

    def read(self, seconds=None):
        from pxr import Usd, UsdShade
        self.time = Usd.TimeCode.Default() if seconds is None else Usd.TimeCode(self.start + seconds*self.rate)
        if not self.shader or self.shader.GetIdAttr().Get() != 'UsdPreviewSurface':
            return None
        p = {'surfaceModel': 'usdPreviewSurface',
             'baseColor': (.18, .18, .18, 1.), 'metallic': 0., 'roughness': .5, 'clearcoatRoughness': .01,
             'mapUV': {}, 'mapSamplers': {}}
        maps = {}

        def input_value(name, parameter, map_name=None, default=None, color=False, normal=False):
            inp = self.shader.GetInput(name)
            attr = self.terminal(inp.GetAttr()) if inp else None
            producer = UsdShade.Shader(attr.GetPrim()) if attr else None
            if attr and attr.GetName().startswith('outputs:') and producer and producer.GetIdAttr().Get() == 'UsdUVTexture' and map_name:
                image, uv, sampler = self.texture(producer, attr.GetBaseName(), normal=normal, color=color)
                maps[map_name], p['mapUV'][map_name], p['mapSamplers'][map_name] = image, uv, sampler
                if parameter:
                    p[parameter] = (1., 1., 1., 1.) if color else 1.
                return True
            value = self.value(self.shader, name, default)
            if value is not None and parameter:
                p[parameter] = tuple(value)[:3] + (1.,) if color else float(value)
            return False

        input_value('diffuseColor', 'baseColor', 'baseColorMap', color=True)
        input_value('emissiveColor', 'emissive', 'emissiveMap', color=True)
        input_value('roughness', 'roughness', 'roughnessMap')
        input_value('metallic', 'metallic', 'metallicMap')
        input_value('specularColor', 'specularColor', 'specularColorMap', default=(0., 0., 0.), color=True)
        p['useSpecularWorkflow'] = int(self.value(self.shader, 'useSpecularWorkflow', 0)) == 1
        # Read both branches to discover their animated inputs before selecting
        # the active workflow. The inactive branch needs no GPU sampler or UVs.
        inactive = 'metallicMap' if p['useSpecularWorkflow'] else 'specularColorMap'
        maps.pop(inactive, None)
        p['mapUV'].pop(inactive, None)
        p['mapSamplers'].pop(inactive, None)
        has_normal = input_value('normal', None, 'normalMap', normal=True)
        if not has_normal:
            normal = np.asarray(self.value(self.shader, 'normal', (0., 0., 1.)))
            if not np.array_equal(normal, [0., 0., 1.]):
                maps['normalMap'] = np.r_[normal*.5+.5, 1.].astype(np.float32)[None, None]
        input_value('occlusion', 'occlusion', 'occlusionMap')
        has_displacement = input_value('displacement', 'displacementScale', 'displacementMap')
        if not has_displacement and p.get('displacementScale', 0.) != 0.:
            maps['displacementMap'] = np.ones((1, 1, 4), np.float32)
            p['mapUV']['displacementMap'] = {'constant': (.5, .5)}
        for name in ('ior', 'clearcoat', 'clearcoatRoughness'):
            input_value(name, name, name+'Map')
        textured_alpha = input_value('opacity', 'opacity', 'opacityMap', default=1.)
        p['opacityMode'] = str(self.value(self.shader, 'opacityMode', 'transparent'))
        threshold = float(self.value(self.shader, 'opacityThreshold', 0.))
        if threshold > 0:
            p['alphaMode'], p['alphaCutoff'] = 'mask', threshold
        elif textured_alpha or p['opacity'] < 1.:
            p['alphaMode'] = 'blend'
        return MaterialSpec(p, maps, self.material.GetPrim().GetName())

    def sample(self, seconds):
        if seconds not in self.samples:
            self.samples[seconds] = self.read(seconds)
            self.samples[seconds].dynamic = True
            while len(self.samples) > 8:
                self.samples.popitem(last=False)
        self.samples.move_to_end(seconds)
        return self.samples[seconds]


def read_material(material, start, rate):
    reader = PreviewMaterial(material, start, rate)
    spec = reader.read()
    if spec is not None and reader.times:
        spec.sampler = reader.sample
        spec.sample_times = tuple((time-start)/rate for time in sorted(reader.times))
    return spec
