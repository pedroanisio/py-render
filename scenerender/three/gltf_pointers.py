"""glTF animation-pointer resolution for imported mesh nodes and materials.

Targets follow the Khronos Asset Object Model. Vector components are not array
elements; only the explicitly array-valued morph weights admit scalar targets.
Material samplers belong to a clip and never mutate the shared imported asset.
"""
from collections import OrderedDict
from copy import deepcopy
import logging
import math
import re

import numpy as np

from .model import Channel, Clip, MaterialSpec

log = logging.getLogger('scenerender')

# Relative material pointer -> internal parameter, component count, conversion.
MATERIAL_FIELDS = {
    'alphaCutoff': ('alphaCutoff', 1, 1.),
    'emissiveFactor': ('emissive', 3, 1.),
    'normalTexture/scale': ('normalScale', 1, 1.),
    'occlusionTexture/strength': ('occlusionStrength', 1, 1.),
    'pbrMetallicRoughness/baseColorFactor': ('baseColor', 4, 1.),
    'pbrMetallicRoughness/metallicFactor': ('metallic', 1, 1.),
    'pbrMetallicRoughness/roughnessFactor': ('roughness', 1, 1.),
}
for extension, fields in {
    'clearcoat': [('clearcoatFactor', 'clearcoat', 1), ('clearcoatRoughnessFactor', 'clearcoatRoughness', 1),
                  ('clearcoatNormalTexture/scale', 'clearcoatNormalScale', 1)],
    'transmission': [('transmissionFactor', 'transmission', 1)],
    'ior': [('ior', 'ior', 1)],
    'volume': [('thicknessFactor', 'thickness', 1), ('attenuationDistance', 'attenuationDistance', 1),
               ('attenuationColor', 'attenuationColor', 3)],
    'sheen': [('sheenColorFactor', 'sheenColor', 3), ('sheenRoughnessFactor', 'sheenRoughness', 1)],
    'specular': [('specularFactor', 'specular', 1), ('specularColorFactor', 'specularColor', 3)],
    'iridescence': [('iridescenceFactor', 'iridescence', 1), ('iridescenceIor', 'iridescenceIor', 1),
                    ('iridescenceThicknessMinimum', 'iridescenceThicknessMinimum', 1),
                    ('iridescenceThicknessMaximum', 'iridescenceThicknessMaximum', 1)],
    'anisotropy': [('anisotropyStrength', 'anisotropy', 1), ('anisotropyRotation', 'anisotropyRotation', 1)],
    'dispersion': [('dispersion', 'dispersion', 1)],
    'emissive_strength': [('emissiveStrength', 'emissiveStrength', 1)],
}.items():
    for source, key, width in fields:
        MATERIAL_FIELDS['extensions/KHR_materials_'+extension+'/'+source] = (
            key, width, 180/math.pi if key == 'anisotropyRotation' else 1.)

TEXTURE_FIELDS = {
    'pbrMetallicRoughness/baseColorTexture': 'baseColorMap',
    'pbrMetallicRoughness/metallicRoughnessTexture': 'metallicRoughnessMap',
    'normalTexture': 'normalMap', 'occlusionTexture': 'occlusionMap', 'emissiveTexture': 'emissiveMap',
}
for extension, fields in {
    'clearcoat': [('clearcoatTexture', 'clearcoatMap'), ('clearcoatRoughnessTexture', 'clearcoatRoughnessMap'),
                  ('clearcoatNormalTexture', 'clearcoatNormalMap')],
    'transmission': [('transmissionTexture', 'transmissionMap')],
    'volume': [('thicknessTexture', 'thicknessMap')],
    'sheen': [('sheenColorTexture', 'sheenColorMap'), ('sheenRoughnessTexture', 'sheenRoughnessMap')],
    'specular': [('specularTexture', 'specularMap'), ('specularColorTexture', 'specularColorMap')],
    'iridescence': [('iridescenceTexture', 'iridescenceMap'), ('iridescenceThicknessTexture', 'iridescenceThicknessMap')],
    'anisotropy': [('anisotropyTexture', 'anisotropyMap')],
}.items():
    for source, key in fields:
        TEXTURE_FIELDS['extensions/KHR_materials_'+extension+'/'+source] = key


def tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
        raise ValueError(f'Invalid glTF animation pointer: {pointer!r}')
    return tuple(part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/'))


def index(value, count):
    if not re.fullmatch(r'0|[1-9][0-9]*', value) or int(value) >= count:
        raise ValueError(f'Invalid glTF animation pointer index {value!r} (length {count})')
    return int(value)


def parent_defined(raw, parts):
    value = raw
    for part in parts[:-1]:
        if isinstance(value, list):
            value = value[index(part, len(value))]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ValueError('glTF animation pointer has an undefined enclosing object: /'+'/'.join(parts))
    if not isinstance(value, dict):
        raise ValueError('glTF animation pointer targets a component rather than a property: /'+'/'.join(parts))


def resolve(loader, nodes, meshes, materials, parts):
    """(node, channel path, width, accessor type, component, material, destination, multiplier)."""
    if len(parts) >= 3 and parts[0] == 'nodes':
        ni = index(parts[1], len(nodes))
        node, field = nodes[ni], parts[2]
        if len(parts) == 3 and field in ('translation', 'rotation', 'scale'):
            if node.matrix is not None and field != 'translation':
                raise ValueError('glTF matrix nodes have no mutable rotation or scale pointer')
            path = 'matrixTranslation' if node.matrix is not None else field
            width = 4 if field == 'rotation' else 3
            return ni, path, width, 'VEC'+str(width), None, None, None, 1.
        if field == 'weights' and len(parts) in (3, 4):
            count = max((len(p.morph_positions) for p in meshes[node.mesh]), default=0) if node.mesh is not None else 0
            if not count:
                raise ValueError('glTF weights pointer requires a mesh with morph targets')
            component = index(parts[3], count) if len(parts) == 4 else None
            return ni, 'weights', 1 if component is not None else count, 'SCALAR', component, None, None, 1.
    elif len(parts) >= 3 and parts[0] == 'materials':
        mi = index(parts[1], len(materials))
        parent_defined(loader.raw, parts)
        path = '/'.join(parts[2:])
        entry = MATERIAL_FIELDS.get(path)
        if entry:
            key, width, multiplier = entry
            return -1, path, width, 'SCALAR' if width == 1 else 'VEC'+str(width), None, materials[mi], (key,), multiplier
        marker = '/extensions/KHR_texture_transform/'
        if marker in path:
            source, field = path.rsplit(marker, 1)
            if source in TEXTURE_FIELDS and field in ('offset', 'rotation', 'scale'):
                width = 1 if field == 'rotation' else 2
                return -1, path, width, 'SCALAR' if width == 1 else 'VEC2', None, materials[mi], ('mapUV', TEXTURE_FIELDS[source], field), 1.
    if parts and (parts[0] in ('cameras', 'extensions') or 'extras' in parts):
        raise NotImplementedError('camera, light or application-specific pointer target is not consumed by the mesh renderer')
    raise ValueError('glTF animation pointer is not a supported mutable property: /'+'/'.join(parts))


class MaterialSampler:
    def __init__(self, material, tracks):
        self.material, self.tracks, self.samples = material, tracks, OrderedDict()

    def __call__(self, time, material=None):
        from .animation import sample_channel
        material = self.material if material is None else material
        key = (id(material), time)
        hit = self.samples.pop(key, None)
        if hit is None:
            params = deepcopy(material.params)
            for destination, multiplier, channel in self.tracks:
                value = sample_channel(channel, time)*multiplier
                value = float(value[0]) if len(value) == 1 else tuple(float(v) for v in value)
                if len(destination) == 1 and isinstance(value, tuple) and len(value) == 3:
                    value += (1.,)
                parent = params
                for part in destination[:-1]:
                    parent = parent.setdefault(part, {})
                parent[destination[-1]] = value
            hit = MaterialSpec(params, material.textures, material.name, dynamic=True)
        self.samples[key] = hit
        while len(self.samples) > 8:
            self.samples.popitem(last=False)
        return hit


def load_clips(loader, nodes, meshes, materials):
    clips = []
    for ai, animation in enumerate(loader.g.animations or []):
        channels, tracks, targets = [], {}, []
        for source in animation.channels:
            target = source.target
            if target is None:
                continue
            pointer = target.path == 'pointer'
            component = material = destination = None
            multiplier = 1.
            if pointer:
                if target.node is not None:
                    raise ValueError('glTF animation pointer target must not also specify a node')
                parts = tokens(((target.extensions or {}).get('KHR_animation_pointer') or {}).get('pointer'))
                try:
                    ni, path, width, type_, component, material, destination, multiplier = resolve(loader, nodes, meshes, materials, parts)
                except NotImplementedError as error:
                    log.warning('glTF animation pointer /%s: %s', '/'.join(parts), error)
                    ni, path, width, type_ = -1, '/'.join(parts), None, None
            else:
                if target.node is None:
                    continue
                ni, path = target.node, target.path
                parts = ('nodes', str(ni), path)
            if any(parts == old or parts[:len(old)] == old or old[:len(parts)] == parts for old in targets):
                raise ValueError('Overlapping glTF animation targets: /'+'/'.join(parts))
            targets.append(parts)
            sampler = animation.samplers[source.sampler]
            times = loader.accessor(sampler.input)[:, 0].astype(np.float64)
            interpolation = (sampler.interpolation or 'LINEAR').upper()
            values = loader.accessor(sampler.output).astype(np.float64)
            rows = len(times)*(3 if interpolation == 'CUBICSPLINE' else 1)
            if pointer:
                input_ = loader.g.accessors[sampler.input]
                output = loader.g.accessors[sampler.output]
                if input_.type != 'SCALAR' or input_.componentType != 5126 or not len(times) or not np.isfinite(times).all() or np.any(times < 0) or np.any(np.diff(times) <= 0):
                    raise ValueError('Invalid glTF animation pointer key times')
                if interpolation not in ('STEP', 'LINEAR', 'CUBICSPLINE') or not np.isfinite(values).all():
                    raise ValueError('Invalid glTF animation pointer values/interpolation')
                if width is not None and (output.type != type_ or values.size != rows*width):
                    raise ValueError('glTF animation pointer output accessor does not match the property type')
            channel = Channel(ni, path, times, values.reshape(rows, -1), interpolation, component=component)
            channels.append(channel)
            if material is not None:
                tracks.setdefault(id(material), (material, []))[1].append((destination, multiplier, channel))
        duration = max((float(c.times[-1]) for c in channels if len(c.times)), default=0.)
        clips.append(Clip(animation.name or str(ai), channels, duration,
                          material_samplers={key: MaterialSampler(material, paths) for key, (material, paths) in tracks.items()}))
    return clips
