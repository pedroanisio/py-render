# Lighting

`colorTemperature` covers the complete schema range, 1,000–40,000 K, in both
3D lights and the 2D lighting effect. Planck radiation is integrated against the
bundled [CIE 1931 observer data](../scenerender/data/CIE-1931.md) at 1 nm intervals.
The resulting XYZ is converted to linear sRGB; negative components are clipped.
3D uses unit luminance, while the 2D effect retains its unit-peak tint convention.
The previous 1,667–25,000 K polynomial clamp made different valid temperatures
produce the same light color at both ends. White-balance effects have their own
daylight/Bradford convention and do not use this light-emitter conversion.

`tests/test_light_temperature.py` checks eight numerical chromaticity references,
the published dataset checksum, and schema-valid animated endpoint renders for
all eight light types through both lighting consumers. Explicit colored lights
provide equivalent rendered controls; backward seeks exercise cache reuse.
The observer data, metadata and attribution are included in the wheel.

Every evaluated direct light is uploaded and visited by the fragment shader.
Light metadata uses eight float4 records per light in a texture; shadow metadata
uses fourteen records per map. There are no fixed light-count or shadow-count
uniform arrays. IES profile offsets are stored as two exact 16-bit halves so
large profile collections do not lose integer precision in float32 metadata.

Directional and spot shadows render one square face. Point and area shadows
render six square faces. Faces retain their declared `shadowMapSize`, adjusted
by the existing render-scale policy; the previous 2,730-pixel cube-face clamp is
removed. The schema's 8,192-pixel maximum can be requested for all shadow types.
Rendering at that size still requires sufficient GPU memory.

Faces of different sizes are packed into pages of a texture array. Copies use
[GPU buffers](https://moderngl.readthedocs.io/en/latest/reference/texture.html#Texture.read_into)
rather than reading pixels into Python memory. Shadow sampling uses
the face's own placement and resolution, clamping taps within that face. Cube
directions select the face before lookup, including exact face boundaries, so a
tap cannot accidentally read an adjacent light's map. PCSS still crosses cube
faces by perturbing the direction before selecting each sample's face.

Light metadata, shadow metadata and atlas pages belong to the evaluated 3D world
and are shared by its objects and cameras. Temporary face textures are released
after packing. Changing animation time creates a new evaluated world; backward
seeks rebuild or reuse the matching world rather than stale lighting data.
The world releases its uploads when retired. GPU size/layer capacity exhaustion
raises an explicit error instead of silently dropping lights or reducing maps.

Shading remains a single pass per object draw, preserving the existing handling
of alpha blending and transmission. Ambient/environment light remains separate
from the direct-light list. Depth writes are controlled on the active framebuffer
for transparent objects, splats and background draws. Each dome's
dominant-direction shadow uses the same atlas and metadata storage as direct
shadows, with that dome's own bias and map size. A positive dome `shadowSoftness`
sets angular radius in degrees; zero derives the footprint from its radiance's
directional concentration.

Dome contributions remain separate through diffuse, specular, sheen and clearcoat
evaluation. Each has eleven float4 metadata records containing its map layer,
shadow index, diffuse/specular flags, color, dominance and irradiance coefficients.
Only that dome's contribution receives its shadow factor. Ambient illumination
and other domes with `castShadow="false"` remain unshadowed. Dominance is computed
from the dome's radiance regardless of its diffuse/specular flags, allowing a
specular-only dome to cast shadows. A uniform dome uses exact constant radiance
and irradiance instead of a resampled map.

Visible dome backgrounds retain the original image resolution and are accumulated
without a fixed count limit. Additional passes contribute RGB while retaining a
single opaque alpha and background depth value. The camera handler checks
evaluated light type and visibility, so animations and expressions can enable
the background even when the base attributes do not.

Radiance prefilters belong to their GL context's cache, keyed by source-image
identity and exact color/rotation values. Light flags and background visibility
are evaluated separately each frame. The cache retains at most eight entries and
128 MiB of inactive/cached data; entries evicted while a live world uses them stay
pinned until that world releases them. Each world owns its packed environment
array. Cache eviction cannot invalidate a previously evaluated world's maps or
background. Small animated intensity/rotation changes are not rounded out of the
cache key.

`tests/test_light_storage.py` verifies all six direct-light types above the old
limits, 257 lights crossing metadata rows, mixed IES/color/position/attenuation
and diffuse/specular flags against sums of independent renders, mixed shadow
sizes across multiple pages, transparent/transmissive materials, dome and direct
shadows together, animated lights with backward seeks, and cube boundary samples
against explicit pixel values. Maximum-size allocation tests inspect the actual
render-target requests without allocating gigabytes in CI; rendered tests use
smaller maps. These checks cover storage/count behavior, not every remaining
schema light combination or all GPU implementations.

The [light audit probe](audits/scene-render-1.1/light_probe.py) compares repeated
identical lights with a single light of summed intensity. Both the 17-light and
eight-shadow cases now match their controls within float rounding.

The probe's independent red/blue dome comparison now also matches its control.
`tests/test_dome_lights.py` covers independent shadow flags, orientations, map
sizes, biases and softness, all environment material lobes, specular-only shadows,
unshadowed ambient/uniform contributions, 97 dome metadata records, more than two
visible backgrounds, transmission/alpha, animated visibility/type/lobe flags,
sub-micro intensity changes, and live maps surviving cache eviction. A transparent
instance overlap control checks that disabled depth writes reach OpenGL through
the framebuffer API. These are behavioral checks, not a claim that every remaining
schema combination has been verified.
