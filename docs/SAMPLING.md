# Imported material texture sampling

Explicit glTF, USD and MaterialX samplers retain independent wrap axes,
minification/magnification filters and border values. The color and depth passes
use the same sampler settings, including opacity masks and shadows.

The shader fetches original floating-point texels and computes interpolation
weights in floating point. This avoids hardware quantization of the fractional
weights: the inspected Intel device uses eight-bit fractions even for floating
point textures. Nearest sampling selects one texel or the border value; it does
not blend them. Linear sampling, nearest mip selection and interpolation between
mips follow the declared filter. The LOD comes from the backend's queried image
footprint before wrapping or mirroring. That query can differ between backends.
The filter and mip choices follow the texture sampling definitions in
[OpenGL 4.6, sections 8.14–8.15](https://registry.khronos.org/OpenGL/specs/gl/glspec46.core.pdf#page=276).
Native scene texture attributes without an explicit imported sampler retain the
renderer's anisotropic default.

The scalar IOR/coat atlas uses the same floating-point sampling convention and
keeps independent mip chains. Its odd-size mip reducer integrates the full image
area. Ordinary material textures retain their backend-generated mip chain.
The glTF extension atlas likewise preserves independent RGBA mip chains and uses
float interpolation. Its source dimensions, wrap modes and filter metadata live
in the atlas; see [GLTF.md](GLTF.md) for its inputs and resource limits.

[test_scalar_material_maps.py](../tests/test_scalar_material_maps.py) checks both
the atlas and ordinary textures against a separate float64 CPU reference, including
all wrap combinations and min/mag modes. The existing `2e-6` and `3e-5` tolerances
are unchanged. Mip controls cover periodic and constant-border sampling. A second
set compares against native GPU filtering at exactly representable fractions and
integer LODs. Decimal-scale cases avoid ambiguous nearest-texel ties caused by
coordinate rounding; the binary-coordinate controls exercise reproducible ties.

[test_materialx_inputs.py](../tests/test_materialx_inputs.py) retains the rendered
coordinate, color and opacity checks and adds nearest-border cases in both axes
and in shadow depth. The former defect returned `0.28` for a nearest lookup that
should return the constant border `0.2`; it incorrectly used linear edge coverage.
The [sampler probe](audits/scene-render-1.1/sampler_probe.py) retains this control.

These checks verify the stated sampler paths. They do not certify every GPU,
driver, LOD approximation or imported shader model.
