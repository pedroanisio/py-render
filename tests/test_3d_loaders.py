"""scenerender.three loaders + animation: glTF (skin, morph, variants, sparse, GLB), OBJ, splats,
USD, FBX, Radiance HDR, OpenEXR and IES — all offline, glTF/USD/FBX/HDR files built in tmp_path."""
from __future__ import annotations

import base64
import io
import json
import math
import os

import numpy as np
import pygltflib as gl
import pytest
from PIL import Image

from scenerender.raster import srgb_to_linear
from scenerender.three.animation import quat_to_mat, sample_channel, trs_matrix
from scenerender.three.loaders import decode_image, load_hdr_image, load_ies, load_model
from scenerender.three.model import Channel

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "media", "3d")
S = math.sqrt(0.5)


# ------------------------------------------------------------------ glTF builder
class Builder:
    """Packs numpy arrays into one buffer and records accessors/bufferViews."""

    def __init__(self) -> None:
        self.g = gl.GLTF2(asset=gl.Asset(version="2.0"))
        self.blob = bytearray()

    def _view(self, data: bytes) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        self.g.bufferViews.append(gl.BufferView(buffer=0, byteOffset=len(self.blob), byteLength=len(data)))
        self.blob += data
        return len(self.g.bufferViews) - 1

    def add(self, arr, type_: str, ctype: int = gl.FLOAT, normalized: bool = False, sparse=None,
            no_view: bool = False) -> int:
        dt = {gl.FLOAT: "<f4", gl.UNSIGNED_BYTE: "u1", gl.UNSIGNED_SHORT: "<u2", gl.UNSIGNED_INT: "<u4"}[ctype]
        a = np.asarray(arr, dtype=dt)
        count = len(a) if a.ndim else 1
        acc = gl.Accessor(componentType=ctype, count=count, type=type_, normalized=normalized or None,
                          bufferView=None if no_view else self._view(a.tobytes()))
        if type_ == "VEC3" and ctype == gl.FLOAT and not no_view:
            acc.min, acc.max = a.min(0).tolist(), a.max(0).tolist()
        if sparse is not None:
            idx, vals = sparse
            iv = self._view(np.asarray(idx, "<u2").tobytes())
            vv = self._view(np.asarray(vals, dt).tobytes())
            acc.sparse = gl.Sparse(count=len(idx), indices=gl.AccessorSparseIndices(bufferView=iv, componentType=gl.UNSIGNED_SHORT),
                                   values=gl.AccessorSparseValues(bufferView=vv))
        self.g.accessors.append(acc)
        return len(self.g.accessors) - 1

    def image_png(self, rgba: np.ndarray) -> int:
        buf = io.BytesIO()
        Image.fromarray(rgba.astype(np.uint8), "RGBA").save(buf, format="PNG")
        self.g.images.append(gl.Image(bufferView=self._view(buf.getvalue()), mimeType="image/png"))
        self.g.textures.append(gl.Texture(source=len(self.g.images) - 1))
        return len(self.g.textures) - 1

    def save(self, path, glb: bool = False, patch=None) -> str:
        self.g.buffers = [gl.Buffer(byteLength=len(self.blob))]
        if glb:
            self.g.set_binary_blob(bytes(self.blob))
            self.g.save_binary(str(path))
            return str(path)
        self.g.buffers[0].uri = "data:application/octet-stream;base64," + base64.b64encode(bytes(self.blob)).decode()
        d = json.loads(self.g.to_json())
        if patch:
            patch(d)
        with open(path, "w") as fh:
            json.dump(d, fh)
        return str(path)


TRI_POS = [[0, 0, 0], [1, 0, 0], [0, 2, 0]]


@pytest.mark.parametrize("size,ctype", [(2, gl.UNSIGNED_BYTE), (3, gl.UNSIGNED_BYTE),
                                         (3, gl.UNSIGNED_SHORT)])
@pytest.mark.parametrize("sparse", [False, True])
def test_gltf_padded_matrix_accessors(tmp_path, size, ctype, sparse):
    from scenerender.three.loaders import _Gltf
    b = Builder()
    dt = np.dtype("u1" if ctype == gl.UNSIGNED_BYTE else "<u2")
    values = np.arange(1, 2 * size * size + 1, dtype=dt).reshape(2, size, size)

    def padded(matrices):
        data = bytearray()
        for mat in matrices:
            for col in mat:
                data += col.tobytes()
                data += b"\xff" * (-len(data) % 4)
        # The last column is permitted to omit its padding.
        padding = -size * dt.itemsize % 4
        return bytes(data[:-padding] if padding else data)

    a = gl.Accessor(componentType=ctype, count=2, type=f"MAT{size}")
    if sparse:
        a.sparse = gl.Sparse(count=1,
                            indices=gl.AccessorSparseIndices(bufferView=b._view(b"\x01"),
                                                            componentType=gl.UNSIGNED_BYTE),
                            values=gl.AccessorSparseValues(bufferView=b._view(padded(values[1:]))))
        expected = values.reshape(2, -1).copy()
        expected[0] = 0
    else:
        a.bufferView = b._view(padded(values))
        expected = values.reshape(2, -1)
    b.g.accessors = [a]
    np.testing.assert_array_equal(_Gltf(b.save(tmp_path / "mat.gltf")).accessor(0), expected)


@pytest.mark.parametrize("glb", [False, True])
def test_gltf_all_joint_sets_affect_pose(tmp_path, glb):
    b = Builder()
    attrs = {"POSITION": b.add(TRI_POS, "VEC3")}
    for i in range(2):
        attrs[f"JOINTS_{i}"] = b.add([list(range(i * 4, i * 4 + 4))] * 3,
                                     "VEC4", gl.UNSIGNED_SHORT)
        attrs[f"WEIGHTS_{i}"] = b.add([[.125] * 4] * 3, "VEC4")
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(attributes=gl.Attributes(**attrs))])]
    b.g.nodes = [gl.Node(mesh=0, skin=0)] + [gl.Node(translation=[float(i), 0, 0]) for i in range(8)]
    b.g.skins = [gl.Skin(joints=list(range(1, 9)))]
    b.g.scenes = [gl.Scene(nodes=list(range(9)))]
    model = load_model(b.save(tmp_path / ("all.glb" if glb else "all.gltf"), glb=glb))
    assert model.meshes[0][0].weights.shape == (3, 8)
    expected = np.asarray(TRI_POS) + [3.5, 0, 0]
    np.testing.assert_allclose(model.pose(None, 0)[0].positions, expected)


def skinned_gltf(tmp_path) -> str:
    b = Builder()
    pos = b.add(TRI_POS, "VEC3")
    idx = b.add([0, 1, 2], "SCALAR", gl.UNSIGNED_SHORT)
    joints = b.add([[0, 0, 0, 0], [0, 0, 0, 0], [1, 0, 0, 0]], "VEC4", gl.UNSIGNED_BYTE)
    weights = b.add([[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]], "VEC4")
    ibm = np.stack([np.eye(4), np.eye(4)])
    ibm[1, 3, 1] = -1.0                                  # column-major: translation (0, -1, 0)
    ibm_acc = b.add(ibm.reshape(2, 16), "MAT4")
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(attributes=gl.Attributes(POSITION=pos, JOINTS_0=joints, WEIGHTS_0=weights),
                                                   indices=idx)])]
    b.g.nodes = [gl.Node(name="j0", children=[1]), gl.Node(name="j1", translation=[0, 1, 0]),
                 gl.Node(name="body", mesh=0, skin=0, translation=[5, 0, 0])]
    b.g.skins = [gl.Skin(joints=[0, 1], inverseBindMatrices=ibm_acc)]
    b.g.scenes = [gl.Scene(nodes=[0, 2])]
    b.g.scene = 0
    t01 = b.add([0.0, 1.0], "SCALAR")
    rot = b.add([[0, 0, 0, 1], [0, 0, S, S]], "VEC4")
    t02 = b.add([0.0, 2.0], "SCALAR")
    cub = b.add([[0, 0, 0], [0, 1, 0], [0, 1, 0],        # key 0: in, value, out
                 [0, 0, 0], [0, 3, 0], [0, 0, 0]], "VEC3")  # key 1
    mk = lambda name, inp, out, path, interp: gl.Animation(  # noqa: E731
        name=name, samplers=[gl.AnimationSampler(input=inp, output=out, interpolation=interp)],
        channels=[gl.AnimationChannel(sampler=0, target=gl.AnimationChannelTarget(node=1, path=path))])
    b.g.animations = [mk("wave", t01, rot, "rotation", "LINEAR"), mk("step", t01, rot, "rotation", "STEP"),
                      mk("cubic", t02, cub, "translation", "CUBICSPLINE")]
    return b.save(tmp_path / "skin.gltf")


def test_skin_linear_step_cubic(tmp_path):
    m = load_model(skinned_gltf(tmp_path))
    assert [c.name for c in m.clips] == ["wave", "step", "cubic"]
    assert m.clips[0].duration == 1.0 and m.clips[2].duration == 2.0
    rest = m.pose(None, 0.0)[0]
    np.testing.assert_allclose(rest.positions, TRI_POS, atol=1e-6)   # mesh node transform ignored
    assert rest.static
    it = m.pose(m.clip("wave"), 0.5)[0]
    np.testing.assert_allclose(it.positions[2], [-S, 1 + S, 0], atol=1e-5)
    np.testing.assert_allclose(it.positions[:2], TRI_POS[:2], atol=1e-6)
    assert not it.static and it.key == (id(m), 2, 0)
    np.testing.assert_allclose(m.pose(m.clip("wave"), 7.0)[0].positions[2], [-1, 1, 0], atol=1e-5)  # clamped
    step = m.clip("step")
    np.testing.assert_allclose(m.pose(step, 0.5)[0].positions[2], [0, 2, 0], atol=1e-6)
    np.testing.assert_allclose(m.pose(step, 1.0)[0].positions[2], [-1, 1, 0], atol=1e-5)
    # Hermite at u = 0.25 over a 2 s interval: out-tangent 1/s scaled by dt = 2
    y = 0.84375 * 1 + 0.140625 * 2 + 0.15625 * 3
    np.testing.assert_allclose(m.pose(m.clip("cubic"), 0.5)[0].positions[2], [0, y + 1, 0], atol=1e-6)
    lo, hi = m.bounds()
    np.testing.assert_allclose(lo, [0, 0, 0]) and np.testing.assert_allclose(hi, [1, 2, 0])


def test_sampling_helpers():
    ch = Channel(0, "rotation", np.array([0.0, 1.0]), np.array([[0, 0, 0, 1], [0, 0, -S, -S]]))  # antipodal
    q = sample_channel(ch, 0.5)
    np.testing.assert_allclose(abs(q[2]), math.sin(math.pi / 8), atol=1e-9)   # shortest path: 45 deg
    np.testing.assert_allclose(quat_to_mat([0, 0, S, S]) @ [1, 0, 0], [0, 1, 0], atol=1e-12)
    m = trs_matrix([1, 2, 3], [0, 0, 0, 1], [2, 2, 2])
    np.testing.assert_allclose(m @ [1, 1, 1, 1], [3, 4, 5, 1])


def morph_gltf(tmp_path) -> str:
    b = Builder()
    pos = b.add(TRI_POS, "VEC3")
    t0 = b.add([[0, 0, 1]] * 3, "VEC3")
    t1 = b.add([[0, 0, 0]] * 3, "VEC3", sparse=([0], [[1, 0, 0]]), no_view=True)   # sparse, no bufferView
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(attributes=gl.Attributes(POSITION=pos),
                                                   targets=[{"POSITION": t0}, {"POSITION": t1}])],
                          weights=[0.5, 0.0])]
    b.g.nodes = [gl.Node(mesh=0), gl.Node(mesh=0)]
    b.g.scenes = [gl.Scene(nodes=[0, 1])]
    times = b.add([0.0, 1.0], "SCALAR")
    w = b.add([0, 0, 1, 1], "SCALAR")
    b.g.animations = [gl.Animation(samplers=[gl.AnimationSampler(input=times, output=w)],
                                   channels=[gl.AnimationChannel(sampler=0, target=gl.AnimationChannelTarget(node=0, path="weights"))])]

    def patch(d):
        d["nodes"][1]["weights"] = [0.0, 1.0]           # node weights override mesh.weights
    return b.save(tmp_path / "morph.gltf", patch=patch)


def test_morph_targets(tmp_path):
    m = load_model(morph_gltf(tmp_path))
    a, b = m.pose(None, 0.0)
    np.testing.assert_allclose(a.positions[:, 2], 0.5)            # mesh.weights default
    np.testing.assert_allclose(b.positions[0], [1, 0, 0])          # node.weights, sparse target
    assert a.static
    o = m.pose(None, 0.0, morph_override=[0.0, 1.0, 9.0])[0]       # truncated
    np.testing.assert_allclose(o.positions[0], [1, 0, 0]) and np.testing.assert_allclose(o.positions[1], [1, 0, 0])
    assert not o.static
    np.testing.assert_allclose(m.pose(None, 0.0, morph_override=[1.0])[0].positions[0], [0, 0, 1])  # padded
    clip = m.clip("0")
    it = m.pose(clip, 0.5)
    np.testing.assert_allclose(it[0].positions[0], [0.5, 0, 0.5]) and not it[0].static
    assert it[1].static                                             # clip does not touch node 1
    np.testing.assert_allclose(it[0].normals, [[0, 0, 1]] * 3, atol=1e-6)   # computed normals


def test_variants_materials_textures_glb(tmp_path):
    b = Builder()
    pos = b.add(TRI_POS, "VEC3")
    uv = b.add([[0, 0], [1, 0], [0, 1]], "VEC2")
    col = b.add([[255, 0, 0, 255]] * 3, "VEC4", gl.UNSIGNED_BYTE, normalized=True)
    tex = b.image_png(np.array([[[255, 128, 0, 255], [0, 0, 0, 255]]]))
    red = gl.Material(name="red", pbrMetallicRoughness=gl.PbrMetallicRoughness(
        baseColorFactor=[1, 0, 0, 1], metallicFactor=0.2, roughnessFactor=0.7,
        baseColorTexture=gl.TextureInfo(index=tex, extensions={"KHR_texture_transform": {"scale": [2, 2]}})),
        alphaMode="MASK", alphaCutoff=0.3, doubleSided=True,
        extensions={"KHR_materials_emissive_strength": {"emissiveStrength": 4.0},
                    "KHR_materials_anisotropy": {"anisotropyStrength": 0.5, "anisotropyRotation": math.pi / 2},
                    "KHR_materials_unlit": {}})
    blue = gl.Material(name="blue", pbrMetallicRoughness=gl.PbrMetallicRoughness(baseColorFactor=[0, 0, 1, 1]))
    b.g.materials = [red, blue]
    b.g.extensions = {"KHR_materials_variants": {"variants": [{"name": "warm"}, {"name": "cool"}]}}
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(
        attributes=gl.Attributes(POSITION=pos, TEXCOORD_0=uv, COLOR_0=col), material=0,
        extensions={"KHR_materials_variants": {"mappings": [{"material": 1, "variants": [1]},
                                                            {"material": 0, "variants": [0]}]}})])]
    b.g.nodes = [gl.Node(mesh=0, matrix=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 0, 0, 1])]
    b.g.scenes = [gl.Scene(nodes=[0])]
    path = b.save(tmp_path / "var.gltf")
    m = load_model(path)
    assert m.variants == ["warm", "cool"]
    it = m.pose(None, 0.0)[0]
    np.testing.assert_allclose(it.positions[:, 0], [2, 3, 2])                    # matrix node
    p = it.material.params
    assert p["baseColor"] == (1, 0, 0, 1) and p["alphaMode"] == "mask" and p["alphaCutoff"] == pytest.approx(0.3)
    assert p["emissiveStrength"] == 4.0 and p["unlit"] and p["doubleSided"]
    assert p["anisotropyRotation"] == pytest.approx(90.0) and p["mapUV"]["baseColorMap"]["scale"] == (2, 2)
    bc = it.material.textures["baseColorMap"]
    assert bc.shape == (1, 2, 4) and bc.dtype == np.float16                     # 8-bit source: half floats
    np.testing.assert_allclose(bc[0, 0, 1], srgb_to_linear(np.array(128 / 255)), atol=2e-4)
    np.testing.assert_allclose(it.colors[0], [1, 0, 0, 1])
    assert m.pose(None, 0.0, variant="cool")[0].material.params["baseColor"] == (0, 0, 1, 1)
    assert m.pose(None, 0.0, variant="warm")[0].material.name == "red"
    # GLB round trip of the same content (binary chunk + image bufferView)
    glb = load_model(b.save(tmp_path / "var.glb", glb=True))
    g0 = glb.pose(None, 0.0, variant="cool")[0]
    np.testing.assert_allclose(g0.positions, it.positions)
    assert g0.material.params["baseColor"] == (0, 0, 1, 1)
    np.testing.assert_allclose(glb.meshes[0][0].material.textures["baseColorMap"], bc)


def test_sparse_accessor_and_strip(tmp_path):
    b = Builder()
    pos = b.add([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], "VEC3", sparse=([3], [[5, 5, 5]]))
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(attributes=gl.Attributes(POSITION=pos), mode=gl.TRIANGLE_STRIP),
                                      gl.Primitive(attributes=gl.Attributes(POSITION=pos), mode=gl.LINES)])]
    b.g.nodes = [gl.Node(mesh=0)]
    b.g.scenes = [gl.Scene(nodes=[0])]
    m = load_model(b.save(tmp_path / "sparse.gltf"))
    prim = m.meshes[0]
    assert len(prim) == 2
    assert prim[1].mode == gl.LINES
    np.testing.assert_array_equal(prim[1].indices, [0, 1, 2, 3])
    np.testing.assert_allclose(prim[0].positions[prim[0].indices],
                               [[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 0], [1, 0, 0], [5, 5, 5]]])


def test_gltf_flat_normals_and_default_material_follow_morph(tmp_path):
    b = Builder()
    pos = b.add([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], "VEC3")
    tangent = b.add([[1, 0, 0, 1]] * 4, "VEC4")
    uv = b.add([[0, 0], [1, 0], [0, 1], [1, 1]], "VEC2")
    idx = b.add([0, 1, 2, 0, 3, 1], "SCALAR", gl.UNSIGNED_SHORT)
    delta = b.add([[0, 0, 0], [0, 0, 0], [0, 0, 1], [0, 0, 0]], "VEC3")
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(indices=idx,
        attributes=gl.Attributes(POSITION=pos, TEXCOORD_0=uv, TANGENT=tangent), targets=[{"POSITION": delta}])])]
    b.g.nodes, b.g.scenes = [gl.Node(mesh=0)], [gl.Scene(nodes=[0])]
    model = load_model(b.save(tmp_path / "flat.gltf"))
    for weight in (0., 1.):
        item = model.pose(None, 0, morph_override=[weight])[0]
        faces = item.positions[item.indices]
        normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        np.testing.assert_allclose(item.normals[item.indices], np.repeat(normals[:, None], 3, axis=1), atol=1e-6)
        assert not np.allclose(normals[0], normals[1])
        assert item.tangents is None
        assert item.material.params["baseColor"] == (1, 1, 1, 1)
        assert item.material.params["metallic"] == item.material.params["roughness"] == 1
        np.testing.assert_array_equal(item.uvs, [[0, 0], [1, 0], [0, 1], [0, 0], [1, 1], [1, 0]])


def test_gltf_partial_normal_and_tangent_morph_targets(tmp_path):
    b = Builder()
    pos = b.add(TRI_POS, "VEC3")
    nrm = b.add([[0, 0, 1]] * 3, "VEC3")
    tan = b.add([[1, 0, 0, -1]] * 3, "VEC4")
    dn = b.add([[0, 1, 0]] * 3, "VEC3")
    dt = b.add([[0, 0, -1]] * 3, "VEC3")
    b.g.meshes = [gl.Mesh(primitives=[gl.Primitive(attributes=gl.Attributes(POSITION=pos, NORMAL=nrm, TANGENT=tan),
                                                 targets=[{"NORMAL": dn}, {"TANGENT": dt}])])]
    b.g.nodes, b.g.scenes = [gl.Node(mesh=0)], [gl.Scene(nodes=[0])]
    model = load_model(b.save(tmp_path / "normal-tangent.gltf"))
    item = model.pose(None, 0, morph_override=[1, .5])[0]
    np.testing.assert_allclose(item.normals, [[0, 1 / np.sqrt(2), 1 / np.sqrt(2)]] * 3, atol=1e-6)
    np.testing.assert_allclose(item.tangents, [[1 / np.sqrt(1.25), 0, -.5 / np.sqrt(1.25), -1]] * 3, atol=1e-6)


def test_model_cache(tmp_path):
    path = morph_gltf(tmp_path)
    assert load_model(path) is load_model(path, "gltf")


# ------------------------------------------------------------------ OBJ / splats
def test_obj_trimesh():
    m = load_model(os.path.join(FIX, "cube.obj"))
    items = m.pose(None, 0.0)
    assert len(items) == 1 and len(items[0].indices) == 12
    lo, hi = m.bounds()
    np.testing.assert_allclose(lo, [-0.5] * 3) and np.testing.assert_allclose(hi, [0.5] * 3)
    bc = items[0].material.params["baseColor"]
    np.testing.assert_allclose(bc[:3], srgb_to_linear(np.array([1.0, 0.5, 0.0])), atol=1e-2)


def test_splat_file():
    s = load_model(os.path.join(FIX, "two.splat")).splats
    np.testing.assert_allclose(s.positions[1], [1, 2, 3])
    np.testing.assert_allclose(s.colors[0], [1, 0, 0, 1])
    np.testing.assert_allclose(s.colors[1, 3], 64 / 255, atol=1e-6)
    np.testing.assert_allclose(s.rotations[0], [0, 0, 0, 1], atol=1e-2)
    np.testing.assert_allclose(np.abs(s.rotations[1]), [1, 0, 0, 0], atol=1e-2)


@pytest.mark.parametrize("ascii_", [False, True])
def test_ply_splats(tmp_path, ascii_):
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    rows = np.array([[1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0, math.log(2), 0, 2, 0, 0, 0],
                     [0, 0, 0, 0, 0, 0, 10, -10, 0, 100, 0, 0, 0, 0, 0, 0, 3]], np.float32)
    head = "ply\nformat %s 1.0\nelement vertex 2\n" % ("ascii" if ascii_ else "binary_little_endian")
    head += "".join(f"property float {n}\n" for n in names) + "end_header\n"
    body = "\n".join(" ".join(repr(float(v)) for v in r) for r in rows).encode() + b"\n" if ascii_ else rows.tobytes()
    p = tmp_path / "g.ply"
    p.write_bytes(head.encode() + body)
    s = load_model(str(p)).splats
    np.testing.assert_allclose(s.positions[0], [1, 2, 3])
    np.testing.assert_allclose(s.scales[0], [1, 2, 1], rtol=1e-6)
    np.testing.assert_allclose(s.rotations[0], [0, 0, 0, 1]) and np.testing.assert_allclose(s.rotations[1], [0, 0, 1, 0])
    np.testing.assert_allclose(s.colors[0, :3], srgb_to_linear(np.array([0.5] * 3)), atol=1e-6)
    np.testing.assert_allclose(s.colors[:, 3], [0.5, 1.0], atol=1e-6)
    np.testing.assert_allclose(s.colors[1, :3], [1, 0, srgb_to_linear(np.array(0.5))], atol=1e-6)


# ------------------------------------------------------------------ USD
USDA = """#usda 1.0
(
    upAxis = "Z"
    metersPerUnit = 0.01
    startTimeCode = 0
    endTimeCode = 24
    timeCodesPerSecond = 24
)
def Xform "root"
{
    double3 xformOp:translate.timeSamples = { 0: (0, 0, 0), 24: (0, 0, 10) }
    uniform token[] xformOpOrder = ["xformOp:translate"]
    def Mesh "quad" (prepend apiSchemas = ["MaterialBindingAPI"])
    {
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        normal3f[] normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1)] (interpolation = "faceVarying")
        texCoord2f[] primvars:st = [(0, 0), (1, 0), (1, 1), (0, 1)] (interpolation = "faceVarying")
        rel material:binding = </mats/red>
    }
    def Mesh "hidden"
    {
        token visibility = "invisible"
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (1, 1, 0)]
    }
}
def Scope "mats"
{
    def Material "red"
    {
        token outputs:surface.connect = </mats/red/pbr.outputs:surface>
        def Shader "pbr"
        {
            uniform token info:id = "UsdPreviewSurface"
            color3f inputs:diffuseColor = (0.8, 0.1, 0.1)
            float inputs:metallic = 0.25
            float inputs:roughness = 0.4
            float inputs:roughness.connect = </mats/red/rtex.outputs:g>
            float inputs:opacity = 0.5
            token outputs:surface
        }
        def Shader "rtex"
        {
            uniform token info:id = "UsdUVTexture"
            asset inputs:file = @rough.png@
            float outputs:g
        }
    }
}
"""


def test_usd_stage(tmp_path):
    Image.fromarray(np.full((2, 2, 3), [0, 51, 0], np.uint8)).save(tmp_path / "rough.png")
    (tmp_path / "s.usda").write_text(USDA)
    m = load_model(str(tmp_path / "s.usda"))
    assert len(m.roots) == 1 and m.nodes[m.roots[0]].name == "__zup_to_yup"
    items = m.pose(None, 0.0)
    assert len(items) == 1                                           # hidden mesh skipped
    it = items[0]
    assert len(it.indices) == 2
    # Z-up -> Y-up: USD (x, y, z) -> (x, z, -y); quad in the USD XY plane lands in XZ
    np.testing.assert_allclose(np.sort(it.positions[:, 2]), [-1, -1, 0, 0], atol=1e-6)
    np.testing.assert_allclose(it.positions[:, 1], 0, atol=1e-6)
    np.testing.assert_allclose(it.normals, [[0, 1, 0]] * len(it.normals), atol=1e-6)
    assert it.uvs is not None and sorted(it.uvs[:, 1].tolist()) == [0, 0, 1, 1]
    p = it.material.params
    np.testing.assert_allclose(p["baseColor"], (0.8, 0.1, 0.1, 1.0), atol=1e-6)
    assert p["metallic"] == 0.25 and p["roughness"] == 1.0 and p["opacity"] == 0.5 and p["alphaMode"] == "blend"
    mr = it.material.textures["metallicRoughnessMap"]
    np.testing.assert_allclose(mr[0, 0], [1, 0.2, 1, 1], atol=1e-4)            # 8-bit texel held as float16
    clip = m.clip("default")
    assert clip.duration == pytest.approx(1.0)
    moved = m.pose(clip, 0.5)[0]
    np.testing.assert_allclose(moved.positions - it.positions, [[0, 5, 0]] * len(it.positions), atol=1e-5)
    assert not moved.static


def test_usdz_packaged_texture(tmp_path):
    from pxr import Sdf, UsdUtils
    Image.fromarray(np.full((2, 2, 3), [0, 51, 0], np.uint8)).save(tmp_path / "rough.png")
    (tmp_path / "s.usda").write_text(USDA)
    assert UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(tmp_path / "s.usda")), str(tmp_path / "s.usdz"))
    os.remove(tmp_path / "rough.png")                               # must come from the zip
    it = load_model(str(tmp_path / "s.usdz")).pose(None, 0.0)[0]
    np.testing.assert_allclose(it.material.textures["metallicRoughnessMap"][0, 0], [1, 0.2, 1, 1], atol=1e-4)


USD_SKEL = """#usda 1.0
(
    upAxis = "Y"
    timeCodesPerSecond = 24
)
def SkelRoot "Rig"
{
    def Skeleton "Skel" (prepend apiSchemas = ["SkelBindingAPI"])
    {
        uniform token[] joints = ["root", "root/tip"]
        uniform matrix4d[] bindTransforms = [((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)), ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,1,0,1))]
        uniform matrix4d[] restTransforms = [((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)), ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,3,0,1))]
        rel skel:animationSource = </Rig/Skel/Anim>
        def SkelAnimation "Anim"
        {
            uniform token[] joints = ["root/tip"]
            float3[] translations = [(0, 1, 0)]
            quatf[] rotations.timeSamples = { 0: [(1, 0, 0, 0)], 24: [(0.70710678, 0, 0, 0.70710678)] }
        }
    }
    def Mesh "Tri" (prepend apiSchemas = ["SkelBindingAPI"])
    {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 2, 0)]
        int[] primvars:skel:jointIndices = [0, 0, 1] (
            elementSize = 1
            interpolation = "vertex"
        )
        float[] primvars:skel:jointWeights = [1, 1, 1] (
            elementSize = 1
            interpolation = "vertex"
        )
        matrix4d primvars:skel:geomBindTransform = ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1))
        rel skel:skeleton = </Rig/Skel>
    }
}
"""


def test_usd_skel(tmp_path):
    (tmp_path / "skel.usda").write_text(USD_SKEL)
    m = load_model(str(tmp_path / "skel.usda"))
    rest = m.pose(None, 0.0)[0]
    np.testing.assert_allclose(rest.positions, TRI_POS, atol=1e-6)   # constant anim translation = bind
    moved = m.pose(m.clip("default"), 0.5)[0]
    np.testing.assert_allclose(moved.positions[2], [-S, 1 + S, 0], atol=1e-5)
    np.testing.assert_allclose(moved.positions[:2], TRI_POS[:2], atol=1e-6)


def test_usd_native_transform_sampling_preserves_shear_and_rotation_ops(tmp_path):
    from pxr import Gf, Usd, UsdGeom
    path = str(tmp_path / "shear.usda")
    stage = Usd.Stage.CreateNew(path)
    stage.SetTimeCodesPerSecond(24)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    mesh = UsdGeom.Mesh.Define(stage, "/Tri")
    mesh.CreatePointsAttr(TRI_POS)
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    shear = mesh.AddTransformOp()
    for t, v in ((0, .2), (24, .8)):
        m = Gf.Matrix4d(1)
        m[1, 0] = v
        shear.Set(m, t)
    rotate = mesh.AddRotateZOp()
    rotate.Set(0, 0)
    rotate.Set(270, 24)        # Native op interpolation traverses 270°, not the shortest quaternion arc.
    stage.GetRootLayer().Save()
    model = load_model(path)
    clip = model.clip("default")
    for t in (.75, .25, .5, .75):
        matrix = np.asarray(mesh.GetLocalTransformation(Usd.TimeCode(t * 24)))
        expected = np.c_[TRI_POS, np.ones(3)] @ matrix
        posed = model.pose(clip, t)[0]
        np.testing.assert_allclose(posed.positions, expected[:, :3], atol=1e-6)
        assert not posed.static


@pytest.mark.parametrize("face_varying", [False, True])
def test_usd_blend_shapes_with_inbetweens_normals_and_weight_order(tmp_path, face_varying):
    from pxr import Sdf, Usd, UsdGeom, UsdSkel
    path = str(tmp_path / "blends.usda")
    stage = Usd.Stage.CreateNew(path)
    stage.SetTimeCodesPerSecond(24)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    mesh = UsdGeom.Mesh.Define(stage, "/M")
    points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    mesh.CreatePointsAttr(points.tolist())
    mesh.CreateFaceVertexCountsAttr([3, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])
    mesh.CreateNormalsAttr([[0, 0, 1]] * 4)
    mesh.SetNormalsInterpolation("vertex")
    if face_varying:
        uv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying")
        uv.Set([[0, 0]] * 6)
    binding = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
    binding.CreateBlendShapesAttr(["smile"])
    binding.CreateBlendShapeTargetsRel().SetTargets(["/Smile"])
    shape = UsdSkel.BlendShape.Define(stage, "/Smile")
    shape.CreatePointIndicesAttr([1])
    shape.CreateOffsetsAttr([[0, 2, 0]])
    shape.CreateNormalOffsetsAttr([[0, 1, 0]])
    between = shape.CreateInbetween("half")
    between.SetWeight(.5)
    between.SetOffsets([[1, 1, 0]])
    between.SetNormalOffsets([[1, 0, 0]])
    animation = UsdSkel.Animation.Define(stage, "/Anim")
    animation.CreateBlendShapesAttr(["unused", "smile"])
    animation.CreateBlendShapeWeightsAttr().Set([1, 0], 0)
    animation.GetBlendShapeWeightsAttr().Set([0, 1], 24)
    binding.CreateAnimationSourceRel().SetTargets(["/Anim"])
    stage.GetRootLayer().Save()
    model = load_model(path)
    base = model.pose(None, 0)[0]
    np.testing.assert_allclose(base.positions, points[[0, 1, 2, 0, 2, 3]] if face_varying else points)
    for weight, delta, normal in ((.25, [.5, .5, 0], [.5, 0, 1]),
                                  (.75, [.5, 1.5, 0], [.5, .5, 1]),
                                  (1., [0, 2, 0], [0, 1, 1])):
        actual = model.pose(model.clip("default"), weight)[0]
        expected = base.positions.copy()
        expected[1] += delta
        np.testing.assert_allclose(actual.positions, expected, atol=1e-6)
        np.testing.assert_allclose(actual.normals[1], np.array(normal) / np.linalg.norm(normal), atol=1e-6)
        override = model.pose(model.clip("default"), .9, [weight])[0]
        np.testing.assert_allclose(override.positions, expected, atol=1e-6)


# ------------------------------------------------------------------ FBX
FBX = """; FBX 7.4.0 project file
FBXHeaderExtension:  {
	FBXHeaderVersion: 1003
	FBXVersion: 7400
}
GlobalSettings:  {
	Version: 1000
	Properties70:  {
		P: "UpAxis", "int", "Integer", "",1
		P: "UpAxisSign", "int", "Integer", "",1
		P: "FrontAxis", "int", "Integer", "",2
		P: "FrontAxisSign", "int", "Integer", "",1
		P: "CoordAxis", "int", "Integer", "",0
		P: "CoordAxisSign", "int", "Integer", "",1
		P: "UnitScaleFactor", "double", "Number", "",1
		P: "TimeMode", "enum", "", "",11
	}
}
Objects:  {
	Geometry: 100, "Geometry::Tri", "Mesh" {
		Vertices: *9 {
			a: 0,0,0,1,0,0,0,1,0
		}
		PolygonVertexIndex: *3 {
			a: 0,1,-3
		}
		GeometryVersion: 124
	}
	Model: 200, "Model::Tri", "Mesh" {
		Version: 232
		Properties70:  {
			P: "Lcl Translation", "Lcl Translation", "", "A",1,2,3
		}
	}
	Material: 300, "Material::Red", "" {
		Version: 102
		ShadingModel: "phong"
		Properties70:  {
			P: "DiffuseColor", "Color", "", "A",1,0,0
		}
	}
	AnimationStack: 400, "AnimStack::Take1", "" {
		Properties70:  {
			P: "LocalStop", "KTime", "Time", "",46186158000
		}
	}
	AnimationLayer: 500, "AnimLayer::Base", "" {
	}
	AnimationCurveNode: 600, "AnimCurveNode::T", "" {
		Properties70:  {
			P: "d|X", "Number", "", "A",1
			P: "d|Y", "Number", "", "A",2
			P: "d|Z", "Number", "", "A",3
		}
	}
	AnimationCurve: 700, "AnimCurve::", "" {
		Default: 1
		KeyVer: 4009
		KeyTime: *2 {
			a: 0,46186158000
		}
		KeyValueFloat: *2 {
			a: 1,5
		}
		KeyAttrFlags: *1 {
			a: 260
		}
		KeyAttrDataFloat: *4 {
			a: 0,0,0,0
		}
		KeyAttrRefCount: *1 {
			a: 2
		}
	}
}
Connections:  {
	C: "OO",200,0
	C: "OO",100,200
	C: "OO",300,200
	C: "OO",500,400
	C: "OO",600,500
	C: "OP",600,200, "Lcl Translation"
	C: "OP",700,600, "d|X"
}
"""


def test_fbx_ascii(tmp_path):
    p = tmp_path / "tri.fbx"
    p.write_text(FBX)
    m = load_model(str(p))
    items = m.pose(None, 0.0)
    assert len(items) == 1
    it = items[0]
    np.testing.assert_allclose(np.sort(it.positions[:, 0]), [1, 1, 2], atol=1e-5)
    np.testing.assert_allclose(it.positions[:, 2], 3, atol=1e-5)
    np.testing.assert_allclose(it.material.params["baseColor"][:3], [1, 0, 0])
    clip = m.clip("Take1")
    assert clip is not None and clip.duration == pytest.approx(1.0, abs=1e-3)
    moved = m.pose(clip, 1.0)[0]
    np.testing.assert_allclose(moved.positions[:, 0] - it.positions[:, 0], 4, atol=1e-4)


# ------------------------------------------------------------------ HDR / EXR / images
def write_rgbe(path, img: np.ndarray, rle: bool) -> None:
    h, w, _ = img.shape
    mx = img.max(-1)
    e = np.where(mx > 1e-32, np.floor(np.log2(np.maximum(mx, 1e-32))) + 1, -128).astype(np.int32)
    scale = np.ldexp(1.0, 8 - e)
    rgbe = np.zeros((h, w, 4), np.uint8)
    rgbe[..., :3] = np.where(mx[..., None] > 1e-32, np.clip(img * scale[..., None], 0, 255), 0).astype(np.uint8)
    rgbe[..., 3] = np.where(mx > 1e-32, e + 128, 0)
    out = bytearray(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {h} +X {w}\n".encode())
    for y in range(h):
        if not rle:
            out += rgbe[y].tobytes()
            continue
        out += bytes([2, 2, w >> 8, w & 255])
        for c in range(4):
            row = rgbe[y, :, c]
            x = 0
            while x < w:                                  # runs of equal bytes, else literals
                r = 1
                while x + r < w and r < 127 and row[x + r] == row[x]:
                    r += 1
                if r >= 2:
                    out += bytes([128 + r, row[x]])
                    x += r
                else:
                    n = min(128, w - x)
                    out += bytes([n]) + row[x:x + n].tobytes()
                    x += n
    with open(path, "wb") as fh:
        fh.write(out)


@pytest.mark.parametrize("rle", [False, True])
def test_hdr_reader(tmp_path, rle):
    rng = np.random.default_rng(1)
    img = (rng.random((8, 16, 3)) * 20).astype(np.float32)
    img[:, :5] = [2.0, 4.0, 8.0]                          # a run for the RLE path
    img[0, 0] = [0.5, 1000.0, 0.0]
    p = tmp_path / f"env{int(rle)}.hdr"
    write_rgbe(p, img, rle)
    got = load_hdr_image(str(p))
    assert got.shape == (8, 16, 3) and got.dtype == np.float32
    assert np.all(np.abs(got - img) <= img.max(-1, keepdims=True) / 128 + 1e-6)   # 8-bit shared-exponent mantissa
    assert load_hdr_image(str(p)) is got                  # cached
    np.testing.assert_allclose(decode_image(p.read_bytes(), srgb=True)[..., :3], got)


def test_exr_and_npy(tmp_path):
    import OpenEXR
    img = np.random.default_rng(2).random((4, 6, 3)).astype(np.float32) * 5
    f = OpenEXR.File({"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}, {"RGB": img})
    f.write(str(tmp_path / "a.exr"))
    np.testing.assert_allclose(load_hdr_image(str(tmp_path / "a.exr")), img)
    np.save(tmp_path / "a.npy", np.dstack([img, np.ones((4, 6))]))
    np.testing.assert_allclose(load_hdr_image(str(tmp_path / "a.npy")), img, rtol=1e-6)
    Image.fromarray(np.full((2, 2, 3), 128, np.uint8)).save(tmp_path / "g.png")
    np.testing.assert_allclose(load_hdr_image(str(tmp_path / "g.png")), srgb_to_linear(np.full((2, 2, 3), 128 / 255)), atol=1e-6)


def test_hdr_old_runs_and_axis_order():
    from scenerender.three.loaders import _read_rgbe
    header = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n"
    # 1 literal + a 256 repeat (zero low byte), then a new literal and 1 repeat.
    row = bytes([128, 64, 32, 129, 1, 1, 1, 0, 1, 1, 1, 1,
                 64, 128, 32, 130, 1, 1, 1, 1])
    expected = np.array([[1, .5, .25]] * 257 + [[1, 2, .5]] * 2, np.float32)
    got = _read_rgbe(header + b"-Y 2 +X 259\n" + row * 2)
    np.testing.assert_array_equal(got, np.stack([expected, expected]))
    # Slow X, reversed both directions: decoding is independent of orientation.
    got = _read_rgbe(header + b"-X 2 +Y 259\n" + row * 2)
    np.testing.assert_array_equal(got, np.stack([expected[::-1], expected[::-1]], 1))


@pytest.mark.parametrize("payload", [
    bytes([1, 1, 1, 7]),                         # no preceding literal
    bytes([128, 0, 0, 129, 1, 1, 1, 8]),         # old run overflows row
    bytes([128, 0, 0]),                         # truncated literal
    bytes([2, 2, 0, 8, 137, 128]),               # new run overflows row
    bytes([2, 2, 0, 8, 0]),                     # empty new run
    bytes([2, 2, 0, 8, 8, 128]),                # truncated new literal
])
def test_hdr_invalid_runs(payload):
    from scenerender.three.loaders import _read_rgbe
    with pytest.raises(ValueError):
        _read_rgbe(b"#?RADIANCE\n\n-Y 1 +X 8\n" + payload)


def test_hdr_xyze_converts_to_linear_srgb():
    from scenerender.three.loaders import _read_rgbe
    # D65 neutral XYZ, quantized to the shared exponent representation.
    got = _read_rgbe(b"#?RADIANCE\nFORMAT=32-bit_rle_xyze\n\n-Y 1 +X 1\n"
                     + bytes([122, 128, 139, 129]))
    np.testing.assert_allclose(got, np.ones((1, 1, 3)), atol=.015)


# ------------------------------------------------------------------ IES
def test_ies_profile():
    prof = load_ies(os.path.join(FIX, "sample.ies"))
    np.testing.assert_allclose(prof.vertical_angles, [0, 22.5, 45, 67.5, 90])
    np.testing.assert_allclose(prof.horizontal_angles, [0, 45, 90])
    assert prof.candela.shape == (3, 5) and prof.max_candela == 1000.0
    t = prof.table(n_theta=9, n_phi=8)                     # theta step 22.5, phi step 45
    assert t.shape == (8, 9) and t.dtype == np.float32
    np.testing.assert_allclose(t[:, 0], 1.0)               # nadir peak
    assert t.max() == pytest.approx(1.0)
    np.testing.assert_allclose(t[:, 5:], 0.0)              # above the vertical range (> 90 deg)
    np.testing.assert_allclose(t[0, :5], [1, 0.9, 0.6, 0.2, 0])
    np.testing.assert_allclose(t[2, :5], [1, 0.8, 0.4, 0.1, 0])
    for k in range(8):                                     # quadrant symmetry: phi, 180-phi, 360-phi
        np.testing.assert_allclose(t[k], t[(8 - k) % 8])
        np.testing.assert_allclose(t[k], t[(4 - k) % 8])
    t2 = prof.table(n_theta=5, n_phi=16)                   # phi 22.5: halfway between planes 0 and 45
    np.testing.assert_allclose(t2[1, 1], (0.6 + 0.5) / 2)


def test_ies_tilt_include_and_rotational(tmp_path):
    p = tmp_path / "r.ies"
    p.write_text("IESNA91\n[TEST] x\nTILT=INCLUDE\n1\n3\n0 45 90\n1.0 0.9\n0.8\n"
                 "1 -1 2.0 3 1 1 1 0 0 0\n0.5 1 10\n0 90 180\n0\n100 50 10\n")
    prof = load_ies(str(p))
    assert prof.max_candela == 100.0                       # 100 * multiplier 2 * ballast 0.5
    t = prof.table(3, 4)
    np.testing.assert_allclose(t, np.tile([1.0, 0.5, 0.1], (4, 1)))


def test_unknown_format(tmp_path):
    p = tmp_path / "x.abc"
    p.write_text("")
    with pytest.raises(ValueError):
        load_model(str(p))
