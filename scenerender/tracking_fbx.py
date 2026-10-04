"""Legacy FBX Takes, sampled at requested times through ufbx scene evaluation."""
from collections import OrderedDict, defaultdict
import json
import struct

import numpy as np

from .ufbx_bridge import Keep

_TRANSFORM = {'Lcl Translation', 'Lcl Rotation', 'Lcl Scaling', 'PreRotation',
              'PostRotation', 'RotationOrder', 'RotationActive'}
_CAMERA = {'FocalLength', 'FieldOfView', 'ApertureMode', 'FilmWidth', 'FilmHeight'}
_ORDERS = {0: 'XYZ', 1: 'XZY', 2: 'YZX', 3: 'YXZ', 4: 'ZXY', 5: 'ZYX', 6: 'XYZ'}


def _keyver_compat(data):
    """Translate older KeyVer implicit weights for older ufbx releases.

    ufbx 0.0.5 reads weights and constant-next flags without consulting KeyVer.
    Newer upstream readers recognize omitted default weights/flags.
    Only retry this lossless spelling conversion after native decoding fails.
    Keep all other scene data, key values and times intact.
    """
    from .tracking import _fbx_ascii, _fbx_binary, _FBX_MAGIC, _text, TrackError
    binary = data.startswith(_FBX_MAGIC)
    tree = _fbx_binary(data) if binary else _fbx_ascii(_text(data))
    changed = False

    def patch(node):
        nonlocal changed
        version, keys, count = node.child('KeyVer'), node.child('Key'), node.child('KeyCount')
        if (version is not None and version.props and 4002 <= version.props[0] <= 4004
                and keys is not None and count is not None):
            keyver = version.props[0]
            values = iter(keys.props)
            out = []

            def letter():
                value = next(values)
                return value if isinstance(value, str) else chr(int(value))

            def weight():
                mode = letter()
                if mode not in ('n', 'c', 'a', 'l', 'r'):
                    raise TrackError(f'fbx: unknown legacy weight {mode!r}')
                out.append(mode)
                out.extend(next(values) for _ in range({'a': 2, 'l': 1, 'r': 1}.get(mode, 0)))

            for _ in range(int(count.props[0])):
                out.extend((next(values), next(values)))
                mode = letter()
                out.append(mode)
                if mode == 'C':
                    out.append(letter() if keyver >= 4004 else 's')
                elif mode == 'U':
                    slope = letter()
                    out.append(slope)
                    if slope in ('s', 'b'):
                        out.extend((next(values), next(values)))
                        if keyver == 4003:
                            out.append('n')
                        else:
                            weight()
                    elif slope == 'a':
                        out.append('n')
                    elif slope in ('p', 'q'):
                        out.extend((next(values), next(values)))
                        weight()
                        out.append('n')
                    elif slope == 't':
                        out.extend((next(values), next(values), next(values)))
                    else:
                        raise TrackError(f'fbx: unknown KeyVer {keyver} slope {slope!r}')
                elif mode != 'L':
                    raise TrackError(f'fbx: unknown legacy interpolation {mode!r}')
            if next(values, None) is not None:
                raise TrackError(f'fbx: excess KeyVer {keyver} values')
            version.props = [4005]
            keys.props = out
            changed = True
        for child in node.children:
            patch(child)

    for node in tree:
        if node.name == 'Takes':
            patch(node)
    if not changed:
        return None

    if not binary:
        def ascii_node(node):
            def value(v):
                if isinstance(v, np.ndarray):
                    return '*'+str(len(v))+' { a: '+', '.join(str(x) for x in v)+' }'
                return v if node.name == 'Key' and isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            line = node.name+': '+', '.join(value(v) for v in node.props)
            return line+(' {\n'+''.join(ascii_node(c) for c in node.children)+'}' if node.children else '')+'\n'
        return ('; FBX 6.1.0 project file\n'+''.join(ascii_node(n) for n in tree)).encode('utf-8')

    # Rebuild binary record offsets after inserting weight bytes. Numeric
    # scalar types are interchangeable to FBX; preserve arrays/blobs exactly.
    def property_bytes(v, name):
        if isinstance(v, str):
            if name == 'Key':
                return b'C'+bytes([ord(v)])
            v = v.encode('utf-8')
            return b'S'+struct.pack('<I', len(v))+v
        if isinstance(v, bytes):
            return b'R'+struct.pack('<I', len(v))+v
        if isinstance(v, np.ndarray):
            code = {('f', 4): b'f', ('f', 8): b'd', ('i', 4): b'i', ('i', 8): b'l', ('u', 1): b'b'}[
                v.dtype.kind, v.dtype.itemsize]
            raw = v.tobytes()
            return code+struct.pack('<III', len(v), 0, len(raw))+raw
        return b'D'+struct.pack('<d', v) if isinstance(v, float) else b'L'+struct.pack('<q', v)

    def binary_node(node, start):
        name = node.name.encode('ascii')
        props = b''.join(property_bytes(v, node.name) for v in node.props)
        position = start+13+len(name)+len(props)
        children = []
        for child in node.children:
            raw = binary_node(child, position)
            children.append(raw)
            position += len(raw)
        if children:
            children.append(bytes(13))
            position += 13
        return struct.pack('<IIIB', position, len(node.props), len(props), len(name))+name+props+b''.join(children)

    out = bytearray(data[:27])
    for node in tree:
        out.extend(binary_node(node, len(out)))
    out.extend(bytes(13))
    return bytes(out)


def _vector(props, name, default):
    prop = props.find_prop(name)
    return tuple(prop.value_vec4[:len(default)]) if prop is not None else default


def _number(props, name, default=None):
    prop = props.find_prop(name)
    if prop is None:
        return default
    # VALUE_INT also marks the integer conversion of real properties, so it
    # cannot distinguish enums from focal lengths/film dimensions.
    return float(prop.value_int) if prop.type in (1, 2) else float(prop.value_vec4.x)


class LegacySampler:
    """Keep the source alive; retain at most eight numeric scene samples."""
    def __init__(self, data):
        from .tracking import TrackError
        try:
            import ufbx
        except ImportError as exc:
            raise TrackError('fbx: FBX 6 Takes require ufbx; install scenerender[3d]') from exc
        self.ufbx = ufbx
        self.pool = []
        options = dict(ignore_geometry=True, ignore_embedded=True, load_external_files=False,
                       ignore_missing_external_files=True)
        try:
            try:
                raw = ufbx.load_memory(data, **options)
            except Exception:
                compatible = _keyver_compat(data)
                if compatible is None:
                    raise
                raw = ufbx.load_memory(compatible, **options)
            self.scene = Keep(raw, self.pool)
        except Exception as exc:
            raise TrackError(f'fbx: cannot decode legacy Takes: {exc}') from exc
        stacks = self.scene.anim_stacks
        if not len(stacks):
            raise TrackError('fbx: no animation Takes found')
        self.anim = stacks[0].anim
        times = defaultdict(set)
        for layer in self.anim.layers:
            for prop in layer.anim_props:
                if prop.prop_name not in _TRANSFORM | _CAMERA:
                    continue
                # `curves` is a tuple of native objects; Keep deliberately
                # passes ordinary tuples through, so retain these explicitly.
                for raw in prop.anim_value.curves:
                    if raw is not None:
                        curve = Keep(raw, self.pool)
                        times[prop.element.element_id].update(key.time for key in curve.keyframes)
        self.nodes = {}
        self.times = {}
        self.names = {}
        used = set()
        for node in self.scene.nodes:
            if node.is_root:
                continue
            keys = times[node.element_id].copy()
            if node.camera is not None:
                keys.update(times[node.camera.element_id])
            if not keys:
                continue
            name = base = node.name or f'Model{node.typed_id}'
            suffix = 2
            while name in used:
                name, suffix = f'{base}#{suffix}', suffix+1
            used.add(name)
            self.nodes[node.typed_id] = node
            self.names[node.typed_id] = name
            self.times[node.typed_id] = np.array(sorted(keys), float)
        if not self.nodes:
            raise TrackError('fbx: no animated models found')
        self.samples = OrderedDict()

    def sample(self, time):
        from .tracking import TrackError, _to_xyz
        time = float(time)
        hit = self.samples.pop(time, None)
        if hit is not None:
            self.samples[time] = hit
            return hit
        # evaluate_prop_flags in the 0.0.5 Python binding exposes a temporary
        # by-value C result. Evaluate a scene instead, and copy properties from
        # its stable storage while every native wrapper remains alive.
        pool = []
        try:
            evaluated = Keep(self.ufbx.evaluate_scene(self.scene._o, self.anim._o, time), pool)
            nodes = evaluated.nodes
            hit = {}
            for index in self.nodes:
                node = nodes[index]
                props = node.props
                position = _vector(props, 'Lcl Translation', (0., 0., 0.))
                rotation = _vector(props, 'Lcl Rotation', (0., 0., 0.))
                scale = _vector(props, 'Lcl Scaling', (1., 1., 1.))
                if _number(props, 'RotationActive', 0):
                    rotation = _to_xyz(*rotation, _ORDERS.get(int(_number(props, 'RotationOrder', 0)), 'XYZ'),
                        _vector(props, 'PreRotation', (0., 0., 0.)), _vector(props, 'PostRotation', (0., 0., 0.)))
                result = dict(zip(('x', 'y', 'z', 'rx', 'ry', 'rz', 'scaleX', 'scaleY'),
                                  (*position, *rotation, *scale[:2])))
                camera = node.camera
                if camera is not None:
                    cp = camera.props
                    focal = _number(cp, 'FocalLength')
                    fov = _number(cp, 'FieldOfView')
                    if focal is not None:
                        result['focal'] = focal
                    if fov is not None:
                        if _number(cp, 'ApertureMode') == 1:
                            width, height = _number(cp, 'FilmWidth'), _number(cp, 'FilmHeight')
                            if width and height:
                                fov = np.degrees(2*np.arctan(np.tan(np.radians(fov)/2)*height/width))
                        result['fov'] = float(fov)
                hit[index] = result
        except Exception as exc:
            raise TrackError(f'fbx: cannot evaluate legacy Takes at {time:g}: {exc}') from exc
        self.samples[time] = hit
        while len(self.samples) > 8:
            self.samples.popitem(last=False)
        return hit


def parse_legacy(data):
    from .tracking import Channel, _Fn
    sampler = LegacySampler(data)
    rows = {index: [] for index in sampler.nodes}
    times = defaultdict(list)
    for index, knots in sampler.times.items():
        for time in knots:
            times[float(time)].append(index)
    # Populate every channel's key table in one chronological pass, sharing the
    # evaluated scene across properties and nodes with coincident keys.
    for time, indices in sorted(times.items()):
        values = sampler.sample(time)
        for index in indices:
            rows[index].append(values[index])
    out = {}
    for index, values in rows.items():
        knots = sampler.times[index]
        keys = tuple(values[0])
        data = {key: np.array([row[key] for row in values], float) for key in keys}
        curves = {key: _Fn(knots, lambda t, index=index, key=key: sampler.sample(t)[index][key]) for key in keys}
        name = sampler.names[index]
        out[name] = Channel(name, knots, data, curves)
    return out
