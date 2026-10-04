"""Synthetic FBX 6 Takes in ASCII and binary; expected motion is analytic.

Uses the common independent binary record writer, with Properties60, string
object connections and heterogeneous legacy keys (ticks, values, bare letters).
No renderer or native FBX decoder is involved in generating these documents.
"""
import json

from make_fbx import TICKS, write_fbx


def node(name, *values, children=()):
    return name, list(values), list(children)


def prop(name, typ, *values):
    return node('Property', name, typ, 'A+', *values)


def model(name='Tracker', kind='Null', properties=()):
    return node('Model', 'Model::'+name, kind, children=[
        node('Version', 232), node('Properties60', children=properties)])


def curve(name, keys=(), default=0., version=4005):
    children = [node('Default', default)]
    if keys:
        # Each key is (time in seconds, value, interpolation letters/parameters).
        values = []
        for time, value, *attributes in keys:
            values.extend([round(time*TICKS), float(value), *attributes])
        children.extend([node('KeyVer', version), node('KeyCount', len(keys)),
                         node('Key', *values)])
    return node('Channel', name, children=children)


def transform(translation=(), rotation=(), scaling=()):
    return node('Channel', 'Transform', children=[
        node('Channel', name, children=channels) for name, channels in
        (('T', translation), ('R', rotation), ('S', scaling)) if channels])


def take(name='First', channels=(), start=0, end=1):
    return node('Take', name, children=[node('FileName', name+'.tak'),
        node('LocalTime', round(start*TICKS), round(end*TICKS)),
        node('ReferenceTime', round(start*TICKS), round(end*TICKS)),
        *[node('Model', 'Model::'+obj, children=props) for obj, props in channels]])


def scene(models=None, takes=None, binary=False, version=6100, parents=None):
    if models is None:
        models = [model(properties=[prop('Lcl Translation', 'Lcl Translation', 10., 20., 0.),
            prop('Lcl Rotation', 'Lcl Rotation', 0., 0., 45.),
            prop('Lcl Scaling', 'Lcl Scaling', 2., 3., 1.)])]
    if takes is None:
        takes = [take(channels=[('Tracker', [transform(translation=[
            curve('X', [(0, 10, 'L'), (1, 30, 'L')]),
            curve('Y', default=20.), curve('Z', default=0.)])])])]
    parents = parents or {}
    nodes = [node('FBXHeaderExtension', children=[node('FBXHeaderVersion', 1003), node('FBXVersion', version)]),
        node('Objects', children=models),
        node('Connections', children=[node('Connect', 'OO', item[1][0],
            'Model::'+parents.get(item[1][0].split('::', 1)[1], 'Scene')) for item in models]),
        node('Takes', children=[node('Current', takes[-1][1][0] if takes else ''), *takes])]

    def encode(item):
        name, values, children = item
        properties = []
        for value in values:
            if isinstance(value, str):
                if name == 'Key':
                    properties.append(('C', ord(value)))
                else:
                    if value.startswith('Model::'):
                        value = value[7:]+'\x00\x01Model'
                    properties.append(('S', value))
            else:
                properties.append(('D', value) if isinstance(value, float) else ('L', value))
        return name, properties, [encode(child) for child in children]

    def ascii_node(item, depth=0):
        name, values, children = item
        text = ' '*depth+name+': '+', '.join(
            value if name == 'Key' and isinstance(value, str) else
            json.dumps(value, ensure_ascii=False) for value in values)
        if children:
            return text+' {\n'+''.join(ascii_node(child, depth+1) for child in children)+' '*depth+'}\n'
        return text+'\n'

    if binary:
        return write_fbx([encode(item) for item in nodes], version=version)
    return (f'; FBX {version//1000}.{version//100%10}.0 project file\n'+
            ''.join(ascii_node(item) for item in nodes)).encode('utf-8')
