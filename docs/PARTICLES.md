# Particle evaluation and replay

Emitters sample birth properties at each emission time, including distinct bursts
within the same 1/60-second simulation step. Paths use the evaluated path string;
their parsed geometry cache retains eight entries. Negative speed reverses the
authored direction. Seeds include project seed, emitter ID and the evaluated
emitter seed, including instance overrides. Each particle retains its birth seed
for its palette, twinkle phase and random sprite cell. Turbulence samples the seed
at its simulation step.

Display properties use the current render context: colors, color/size curves,
end opacity, particle shape, velocity orientation, trail length and sprite
selection/grid/rate. Preset defaults are resolved at the relevant birth or display
time. Keyframes, expressions and numeric links use each instance's clock.

Sprites and emission masks retain their consumer's evaluation context and use
media time since the emitter's start. This permits image sequences and animated
assets, including independent instances and subframe emission-mask changes.
Sprites use an eight-entry cache; emission masks retain at most five samples per
history. These keys include exact time, frame, scope and variables. Asset readers
may also maintain their own source caches.

Replay histories distinguish the composition-clock branch, evaluated start/end,
preroll and required trail storage. Each instance/repeat binding retains at most
eight histories. Changing preroll or trail storage reconstructs the requested
history; backward seeks may reuse the corresponding cached history. Trail lengths
have no additional two- or ten-second cap. Longer trails require proportionally
more storage per live particle and checkpoint.

Drawing bounds include recorded trail positions, the largest parent-transform
scale and the sprite-cell aspect ratio. Curved trails remain visible when the
particle slows or turns, and tall sprites are not clipped to their width.

`color` and `colorEnd` accept spatial linear, radial, conic and mesh gradients,
patterns and style-token references. A particle's paint box follows its size,
rotation and sprite-cell aspect ratio. A trail uses the bounding box of its
requested trajectory, expanded by its width. Object units are relative to that
box; user units are local pixels from its upper-left corner. Paints retain the
consumer's context, so their stops and source assets follow instance clocks.

Paints are sampled at device pixels into floating-point RGBA. `colorCurve`
interpolates their straight sRGB colors and alpha over lifetime, matching the
flat-color path. Sprite tint multiplies the premultiplied paint and texture
samples. Softness and trail fading are separate coverage masks. A whole trail's
coverage is accumulated before applying its paint and opacity, preventing joins
from repeatedly applying paint alpha. Palette particles can also fade to a
spatial end paint. Bubbles retain white highlights with the paint's local alpha.

The shared conic-gradient renderer now evaluates pixel-center angles directly;
it no longer depends on a 96-patch mesh radius. Equivalent object/user units and
the angular seam remain consistent under affine transforms. Color-space and
midpoint interpolation follow the same stop rules as other gradients.

[test_particle_evaluation.py](../tests/test_particle_evaluation.py) covers these
paths with static render controls, analytic positions, separate instance clocks,
backward seeks and bounded-cache checks. Existing particle, physics and timing
tests remain complementary evidence. [test_particle_paints.py](../tests/test_particle_paints.py)
adds 60 cases for all five paint families, rotated shapes, analytic pixel
controls, lifetime fades, sprites, trails, instance clocks, style tokens and
all four conic interpolation spaces. Shape and particle edge tessellations are
checked separately; comparisons between Cairo's float and eight-bit pattern
filters allow their small numerical differences. Final particle output still
uses the existing eight-bit Cairo canvas.

Collision silhouettes are sampled at each simulation step through the node renderer,
including current geometry, paint/asset alpha, node opacity, visibility/windows,
masks, effects and mattes. The selected body's primary contribution traverses its
group/sequence ancestors, including ancestor opacity, clipping, masks, effects,
mattes, adjustments and transitions. Siblings retain their layout slots and
scheduled durations without supplying unrelated collision pixels. Referenced
matte and effect inputs render their complete subtrees. Samples use one pixel per composition or symbol unit,
independent of output resolution, instance fit and placement. Each emitter history
retains at most two mask samples; temporary render caches are isolated. Sensor
bodies, hidden matte bodies and bodies below hidden/excluded groups do not deflect
particles. Each body supplies a separate contribution: this is not a decomposition
of the final composited frame when sibling effects or blends are nonlinear.

Contacts use the actual composition time corresponding to each emitter step and
the symbol's own simulation clock. Relative velocities include static/kinematic
keyframes, dynamic body poses and parent motion, in units per emitter-local
second. This also avoids a shared activation-transform cache crossing instances
or loop histories. Bounds use the scene canvas and transform back into the
emitter parent's coordinates.

Segments are swept relative to body motion, sampled at most half a scene pixel
apart inside the mask bounds, then bisected to the first sampled alpha crossing.
This catches tested thin-body crossings even when both endpoints lie outside.
Normal velocity reflects with `bounce`; approaching tangential velocity retains
80 percent. Engulfed particles follow the outward alpha gradient to the first
exterior point, refined to the alpha threshold. Flat interiors use a nearest
exterior pixel to select a search direction, with stable ordering for numerical
ties; the response uses the normal at the exit. The mask threshold remains alpha
0.5. The discrete mask and sweep
are numerical approximations; arbitrary subpixel geometry or deformation between
timesteps is not an exact continuous contact surface.

[test_particle_collisions.py](../tests/test_particle_collisions.py) adds rendered
alpha controls, analytic moving-wall velocities, deep-interior recovery, sensors,
parent motion, independent instance overrides, output-scale invariance and
explicit clock controls for static, kinematic and dynamic bodies under speed,
reverse, remap and loops, including backward seeks.

Particle transforms now separate the emitter's birth transform from its inherited
parent frame. Explicit parent references, instance overrides (including targets in
other instances) and parent constraints move all living particles. Layout slots,
percentage positions, animated parent dimensions, anchors, motion paths and
nonparent constraints are sampled at each birth. Changing the emitter's placement
therefore leaves earlier particles behind. Constraint ordering and parent influence
are preserved. Each history retains eight exact-time transform samples; temporary
reference caches do not accumulate every replay step.

Forces, collision bounds and drawing use the same effective parent. The retained
40-pixel parent-reference reproducer now reflects the particle with velocity
-30 px/s instead of letting it pass through the wall. Force-field velocities
include inherited parent motion, so drag and wind respond to movement even when a
particle is stationary in its parent's coordinates. Parent motion is sampled over
the existing 1/60-second simulation step.

[test_particle_transforms.py](../tests/test_particle_transforms.py) adds 36 cases
using nested-parent controls, analytic force-field motion, explicit birth positions,
static pixel controls, backward seeks, retimed symbols and independent cross-instance
references. Nonparent constraints are checked against analytically placed births;
parent constraints are checked with both ordering and partial influence. Births
also remain valid while an outer instance has zero width and is not drawn.

Collider shapes and layers now retain particle geometry in direct and grouped
mattes. Independent particle histories supply their current alpha or luma,
including inverted modes, motion, display opacity and symbol clocks. A matte
particle can also collide inside its own symbol world when that world's
colliders do not depend on the consuming emitter. The retained group-matte
reproducer now reflects the crossing particle to -60 px/s, at x=31.75 at time
0.8 seconds; the wall's rendered alpha remains one.

Each emitter retains its two most recently queried fixed-step states. Alternating
collider endpoint queries can reuse these states without replaying an older
checkpoint. Repeat bindings are keyed by their effective last values, so repeated
reference traversal does not create duplicate histories for the same bindings.

Re-entering a history while it is being evaluated raises a `SceneError` identifying
a cyclic particle collision/matte dependency. Interrupted simulation state and
recent collision/state samples are discarded, so subsequent earlier-time queries
remain deterministic. This is a diagnostic for unsupported feedback, not an
implementation of cyclic feedback simulation. It applies regardless of query
order or already cached samples.

[test_particle_mattes.py](../tests/test_particle_mattes.py) adds 29 cases: 23
functional controls for direct/group alpha/luma mattes, animated shape/layer
colliders and independent colliding matte particles, plus six cycle-diagnostic
and recovery cases across composition, instance, cross-world and repeat contexts.
Analytic native-shape mattes, rendered alpha and fresh-renderer replay provide
independent controls.

[test_particle_ancestors.py](../tests/test_particle_ancestors.py) adds 45 cases for
ancestor opacity, clips, masks, effects, mattes, nested clocks, sequence windows,
echo tails, adjustments, projection alpha, transition weights, sibling isolation,
all four layouts and layer colliders at three output scales. The retained opacity
reproducer now matches its node-opacity control: both particles pass a wall with
rendered alpha 0.2 and reach x=78, vx=60 at 0.8 seconds. Controls include independent
full renders, equivalent flat opacity and fresh-renderer backward seeks.

[test_particle_projection.py](../tests/test_particle_projection.py) adds 56 cases
for projected collider motion. Snapshot rendering captures the body's actual
local-to-scene matrix after camera projection, including flattened ancestor
buffers. References cannot overwrite the selected body's capture, and temporal
samples at other times do not replace its current matrix. Relative sweeps and
contact-point velocities divide by the homogeneous coordinate. Points mapping to
infinity are omitted from that contact calculation instead of contaminating the
particle state with nonfinite coordinates.

The retained orthographic moving-wall reproducer now matches its affine control:
a 30 px/s wall projected at scale 0.5 reflects an incoming 100 px/s particle to
-70 px/s. Independent pinhole equations verify tilted-plane matrices and contact
velocities. Tests include direct, flattened, collapsed and nested placements,
camera translation/zoom/dolly, simulated dynamic bodies, retimed symbols, output
scales and fresh backward seeks.

The standalone [projection motion probe](audits/scene-render-1.1/particle_projection_probe.py)
adds six XSD-valid tilted-wall scenes with camera depth motion. It verifies that
the relative mappings are non-affine and compares first-contact velocities with
independent pinhole equations, exercising the homogeneous division directly.

Emitter contacts now reconstruct the same parent projection used to draw live
particles, without rendering their pixels or reference dependencies. This follows
ancestor clocks, layout slots, parent links, constraints, repeats and projective
intermediate-buffer scales. Each emitter retains at most eight contact-transform
samples. Interrupted simulations discard these samples alongside their state and
collider caches.

Positions use homogeneous coordinates and velocities use the homography's
Jacobian. After a contact corrects a position, its velocity is transported to that
point before reflection, then transformed back to the emitter's parent frame.
This preserves local speed on the tested stationary tilted planes. Invisible
camera-culled planes keep their finite local history without screen contacts;
points at projective poles are excluded from that contact calculation. Births and
force-field integration retain their existing physical coordinate conventions.

[test_particle_projected_emitters.py](../tests/test_particle_projected_emitters.py)
adds 58 cases for projected/affine scene equivalence, direct and nested emitters,
collapsed groups, independent pinhole coordinates and reflection speeds, scene
bounds, moving groups/cameras, zoom/dolly, binding traversal and camera culling.
It covers retimed symbols, output scales, fresh-frame-first replay, backward
seeks and cache bounds. The retained projected-emitter reproducer now agrees
exactly with its affine control in both pixels and state: x=19.4922, vx=-100 at
0.6 seconds. Moving-camera controls also guard against the former pixel-grid tie
that ejected equivalent particles in opposite vertical directions.

Symbol-local history now starts geometric traversal at the symbol boundary,
retaining its clocks and overrides without inverting its outer display matrix.
An instance can have zero horizontal or vertical scale and later reappear while
its particles continue colliding locally. References between nested instances
cancel their shared instance prefix before computing the relative transform.
[test_particle_scene_space.py](../tests/test_particle_scene_space.py) adds 34
controls for both collapsed axes, reappearance, nested instances, retiming,
output scales, orthographic and tilted projection, parent links, constraints,
mattes and force fields. The original collapsed-instance reproducer now matches
its standalone control at symbol time 0.6: x=19.75, y=50, vx=-100, vy=0.

External raster references now also survive a collapsed canvas. The renderer
samples the referenced scene using the forward map from each consumer pixel;
neither collapsed axis requires an inverse. Sources retain their own clocks,
effects and opacity, and requested sampling bounds include off-frame geometry
and padded effect inputs. Projected consumers sample frame-space dependencies
in their current intermediate tile, including when their instance is noncollapsed.

[test_reference_pullback.py](../tests/test_reference_pullback.py) adds 59 cases
for alpha/luma and inverted mattes, single/double-axis collapse, shared instance
boundaries, particle mattes, difference/displacement effect sources, transition
mattes, off-frame shape/particle/layer sources, tilted consumers, and independent
numerical interpolation controls. The retained external-matte reproducer now
matches its collision-window control exactly, including pixels: at symbol time
0.6 its particle is x=90, y=50, vx=100, vy=0. While the instance is collapsed, the
wall at that particle row maps outside the composition mask, so no earlier
collision should occur.

Referenced sources now follow ancestor camera canvases through groups and
instance boundaries. Traversal retains the same intermediate resolution,
projection stages, fit transforms, clocks and scoped cameras as normal drawing.
Each stage applies its camera optics in the enclosing scene before returning to
the next canvas. The source supplies its own pixels and subtree; its parent's
later opacity, matte and effect composition remain separate operations.
Empty projected groups, including groups whose only child is used as a hidden
matte, produce transparent output safely.

[test_reference_source_projection.py](../tests/test_reference_source_projection.py)
adds 109 controls: six source kinds through orthographic/perspective, nested and
collapsed groups; instance clocks and output scales; alpha/luma consumers;
depth of field and lens distortion; effect and transition inputs; independent
particle collision controls; and projected/nested instances with fit modes and
different inner/outer cameras. Both retained group and instance reproducers now
match their complete source renders exactly: alpha sum 150.118 and no differing
pixels, replacing the previous unprojected alpha sum 600.

This is not a declaration of complete particle conformance. Cyclic matte/physics
feedback is rejected. General singular transform dependencies and wider
projection/reference combinations remain unverified. Nonlinear lens
distortion, temporal compound surfaces, nonlinear sibling interactions,
deformation and wider parent/reference combinations still need work. The emitter and
collision registry entries continue to report partial support.
