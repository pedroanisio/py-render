# glTF materials, textures and animation pointers

The glTF/GLB reader retains the core material maps and these extension inputs.
Each texture has its own UV set, `KHR_texture_transform`, axis wraps and min/mag
filters. The extension's factor multiplies the sampled value unless noted below.

| Extension | Texture input | Interpretation |
|---|---|---|
| clearcoat | `clearcoatTexture` | Linear R, coating weight |
| clearcoat | `clearcoatRoughnessTexture` | Linear G, coating roughness |
| clearcoat | `clearcoatNormalTexture` | Linear RGB tangent normal, independent `scale` |
| transmission | `transmissionTexture` | Linear R, transmission weight |
| volume | `thicknessTexture` | Linear G, volume thickness |
| sheen | `sheenColorTexture` | sRGB RGB decoded before filtering |
| sheen | `sheenRoughnessTexture` | Linear alpha |
| specular | `specularTexture` | Linear alpha, dielectric specular weight |
| specular | `specularColorTexture` | sRGB RGB decoded before filtering |
| iridescence | `iridescenceTexture` | Linear R, interference weight |
| iridescence | `iridescenceThicknessTexture` | Linear G interpolates the thickness limits |
| anisotropy | `anisotropyTexture` | Linear RG maps to a signed direction; B multiplies strength |

Iridescence thickness limits default to 100 and 400 nm. Without a thickness
texture, the maximum is used. Reversed limits are retained. Anisotropy rotation
rotates the texture's direction. Clearcoat normals use their own map and scale;
the base normal texture does not perturb the clearcoat geometry normal.
Authored glTF tangents and handedness survive texture-coordinate transforms.

The conventions follow the ratified Khronos specifications for
[clearcoat](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_clearcoat/README.md),
[transmission](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_transmission/README.md),
[volume](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_volume/README.md),
[sheen](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_sheen/README.md),
[specular](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_specular/README.md),
[iridescence](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_iridescence/README.md)
and [anisotropy](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_anisotropy/README.md).

Extension maps share an RGBA atlas with the core metallic/roughness map. Every
source retains its dimensions, floating-point samples and independent mip chain;
odd-size mip levels integrate the whole source area. Shader interpolation uses
float texel weights, as described in [SAMPLING.md](SAMPLING.md). Metadata is stored
inside the atlas, with separate integer-valued X/Y addresses that remain exact in
float32. Exceeding GPU texture capacity raises an error instead of resizing maps.

Independent extension UVs use a vertex texture indexed by the original vertex
index. The main program still uses 14 vertex attributes and 16 fragment texture
samplers, with one additional sampler in the vertex stage. The mesh owns the UV
texture and releases it with its buffers. Material atlases share the bounded
texture cache; changing sampler metadata selects a new atlas.

[test_gltf_material_textures.py](../tests/test_gltf_material_textures.py) has 100
regressions: import channels and metadata, constant-factor render controls under
three lighting types, all 12 texture inputs with transformed UVs, the complete
core/extension texture stack, independent CPU filter controls, authored tangent
frames and cache eviction. The CPU normal controls exposed a prior defect that
replaced authored tangents when a base normal texture had a coordinate transform.

## KTX2/Basis Universal textures

`KHR_texture_basisu` selects its extension image, including an external URI, a
data URI or an `image/ktx2` GLB buffer view. ETC1S/BasisLZ and UASTC, with or
without Zstandard, transcode to RGBA8 through libktx. RGB, RGBA, red and red/green
payloads retain their channel meanings; the green-in-alpha packing used by
ETC1S and legacy UASTC is unpacked before material sampling. RGB color maps are
decoded from sRGB while alpha remains linear. Data maps remain linear.

The decoder checks dimensions, orientation, swizzle, alpha and color metadata against the
[Khronos extension specification](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_texture_basisu/README.md):
2D textures, base dimensions divisible by four, `rd` orientation, `rgba` swizzle,
straight alpha and color metadata matching the map's usage. It additionally
accepts legacy UASTC `RRRG` channel packing as a compatibility input; this packing
is outside the current extension's channel declarations. Malformed or unsupported
payloads raise an error. The native dependency and optional-fallback
behavior are described in [RUNTIME.md](RUNTIME.md).

Every authored mip is retained, including during channel extraction for
clearcoat maps. Ordinary GPU textures and both material atlases use those
levels. If a pyramid is incomplete, only its missing tail is generated by area
integration in linear space. No authored level is replaced. Sampler settings,
UV transforms, material animation and texture-cache identity continue to apply
to the whole image. GPU storage uses the decoded float images, rather than
platform-specific compressed texture formats.

[test_gltf_ktx.py](../tests/test_gltf_ktx.py) uses 21 generated KTX2 fixtures with
deliberately different mip colors. The retained generator and CLI-extracted
pixels are in [the fixture directory](../tests/fixtures/media/ktx). Comparisons
also check the original input channels independently of the CLI, which is
needed to detect packed-channel mistakes. Tests exercise all 17 material maps,
source selection, fallback errors, authored-mip filtering and rendered controls.
The fixture manifest retains KTX-Software 4.4.2 validator output: 19 fixtures pass
its glTF checks; the legacy `RRRG` fixture and two-level partial pyramid are
separate compatibility controls. That validator requires a single level or a
complete pyramid. The current extension text recommends a full pyramid and
explicitly permits consumers to generate missing mips; the renderer preserves
partial pyramids and fills their tail as described above.

## Animation pointers

`KHR_animation_pointer` channels can animate node translation, rotation, scale
and morph weights, the imported core and extension material factors, and the
offset, rotation and scale of all 17 texture inputs. Whole morph-weight arrays
and individual weights are supported. A matrix node permits translation updates
while preserving its linear transform; it has no mutable rotation or scale.

Targets follow the [Khronos Asset Object Model](https://github.com/KhronosGroup/glTF/blob/main/specification/2.0/ObjectModel.adoc)
and [animation-pointer extension](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_animation_pointer/README.md).
Defaulted leaves require their enclosing objects to exist. Vector components are
not independently addressable. Invalid node/material pointers, overlapping targets,
malformed key times and output shape mismatches raise errors. Normalized accessor conversion
precedes STEP, LINEAR or CUBICSPLINE interpolation.

Material samples belong to their animation clip and use the object's playback
clock, including speed, offset, looping and backward seeks. An eight-entry cache
keeps samples per material and time; imported parameters remain unchanged and
source image arrays are shared. Variant and implicit unlit point/line materials
retain their own settings. Animated UVs update geometry uploads; animated alpha
cutoffs affect both color and shadow depth.

[test_gltf_animation_pointer.py](../tests/test_gltf_animation_pointer.py) contains
131 cases covering factors, texture transforms, interpolation, morph weights,
matrix translation, invalid inputs, clip/variant isolation and rendered controls.
Camera, light and application-specific pointer targets are not consumed by the
mesh renderer. They issue a warning and retain their clip duration.

These checks do not establish full glTF conformance. Missing tangents use the existing
tangent generator; equivalence to MikkTSpace remains unverified. Transmission is
screen-space and shadow decisions are binary; a transmission texture does not
provide spatially varying transmitted shadow color. Remaining BRDF and extension
combinations still need independent reference verification.
