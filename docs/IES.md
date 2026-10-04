# IES light profiles

The loader reads embedded `TILT=INCLUDE` records and external tilt files, resolved
relative to the IES file. It preserves lamp geometry, angle/factor pairs and the
optional `LAMPPOSITION` keyword. Malformed counts, truncated data, unordered
angles and non-finite factors are rejected.

The light evaluator applies the interpolated burning-angle factor to intensity
after normalizing the angular distribution to its peak. Consequently a uniform
factor changes brightness; normalization cannot cancel it. Animated light
orientation updates the correction each frame without reparsing the profile.

In the renderer's local basis, lamp geometry 1 follows the forward axis, geometry
2 the local up axis (the photometric 90-degree plane), and geometry 3 the right
axis (the zero-degree plane). `LAMPPOSITION` supplies horizontal/vertical angles
for the lamp axis and supersedes the geometry. The burning angle is measured
against world nadir, negative Y. A tilt table ending at 90 degrees folds the
opposite hemisphere; a table ending at 180 preserves the full range. This maps
the LM-63 lamp conventions into the renderer's existing light orientation.

Candela values retain the file multiplier and ballast factor. The ballast-lamp
factor also applies to the 1986 and 1991 variants; the later reserved field is
not interpreted as that legacy multiplier.

Type C uses polar angle from the light's forward axis and azimuth from its right
axis toward its up axis. Type A uses elevation from the photometric horizontal
and clockwise azimuth. Type B has its polar axis along the photometric horizontal,
the local right axis. For direction components `(x, y, f)` along right, up and
forward respectively, the coordinate conversion is:

| Type | Vertical angle | Horizontal angle |
|---|---|---|
| C | `atan2(hypot(x,y), f)` | `atan2(y, x)` |
| A | C vertical angle minus 90 degrees | `atan2(-y, x)` |
| B | `atan2(x, hypot(y,f))` | `atan2(-y, f)` |

These equations map the LM-63 definitions into the renderer's light basis. C
profiles support rotational, quadrant and both bilateral symmetries. A/B
nonnegative horizontal ranges mirror across zero; nonnegative B vertical ranges
also mirror. Unmeasured vertical directions, and A/B directions outside the
measured horizontal hemisphere, emit zero. A full-circle C profile that omits
its repeated final meridian interpolates back to the first meridian at the seam.

The GPU interpolates at the original, possibly nonuniform angle knots. A single
packed texture contains the angles and peak-normalized candela for all direct
light profiles; it replaces the previous 64-by-64 resampling and four-profile
limit. Repeated profiles share storage. The upload cache belongs to its GL context
and retains at most eight combinations, evicting older combinations above 128 MiB
(the current combination is retained). Data beyond the GPU's texture capacity
raises an explicit error. Range-reduced angle calculations avoid driver inverse
trigonometric approximations shifting narrow beams. Original angles and candela
are stored in float32 on the GPU, so finite precision still applies.

IES factors apply to diffuse and specular output of point, spot and area lights.
Directional lights use the profile's forward intensity. Ambient and dome lights
retain their existing ambient/environment semantics.

`tests/test_ies_tilt.py` exercises the three lamp geometries, inline/external
records, lamp-position overrides, interpolation, symmetry, legacy multipliers,
invalid records, and rendered animated lights against explicit-intensity
controls. `tests/test_ies_coordinates.py` checks independently constructed
goniometer directions, bilinear intensity oracles, original narrow knots in both
axes, all C symmetries and the azimuth seam, multiple texture rows, nine distinct
profiles, shared/absent profiles, seven rendered IES lights, and all six direct
light types against intensity controls, including their specular-only output.

The renderer's former limits of 16 direct lights and seven direct shadow maps
have also been removed; see [lighting storage](LIGHTING.md). These changes do not
establish conformance of every light/property combination.

The implementation references the [LM-63-2002 definitions](https://webstore.ansi.org/preview-pages/IESNA/preview_ANSI%2BIESNA%2BLM-63-02.pdf),
[Ian Ashdown's parser description](https://seblagarde.wordpress.com/wp-content/uploads/2024/07/iesna.pdf),
and Radiance's [tilt handling](https://github.com/NREL/Radiance/blob/master/src/cv/ies2rad.c)
and [tilt angle functions](https://github.com/NREL/Radiance/blob/master/src/cv/tilt.cal).
