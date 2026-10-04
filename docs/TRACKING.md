# Tracking imports

`trackData format="fbx"` reads FBX 6 Takes and FBX 7 animation curves from ASCII
and binary files. Tracking uses local transform properties in the file's original
scene units. It does not convert scene units to pixels, apply parent transforms,
or subtract the first key time. `trackData/@timeOffset` shifts sampling from the
composition timeline. The first take/animation stack is used; a file's `Current`
take setting does not change that selection.

Each animated model supplies a named channel with `x/y/z`, XYZ-order Euler
`rx/ry/rz`, and `scaleX/scaleY`. Camera models also expose authored focal length
and field of view. Horizontal field of view is converted to vertical using the
film dimensions when `ApertureMode=1`. Static models are omitted. Unkeyed
components use their channel/property defaults, and key times from the model's
transform and camera curves are combined. Camera-only animation is retained.

FBX 6 requires `ufbx`, included by `pip install 'scenerender[3d]'`. The importer
evaluates the selected take at the requested time; it does not bake to a fixed
frame rate. It retains the native source and at most eight copied numeric scene
samples. External files and geometry are unnecessary for this tracking path and
are not loaded. The native wrapper lifetime guard is shared with the mesh reader.
The existing FBX 7 parser remains independent of this optional dependency.

The legacy regression suite checks linear keys, constant previous/next keys,
explicit cubic slopes and one-sided/two-sided weights against analytic curves.
An independent polynomial root solution checks weighted interpolation between
keys. It also checks six rotation orders with pre/post rotations, fractional
camera optics, sparse keys, positive starting times, multiple takes, encodings,
source changes, hashes, backward seeks, native object lifetimes and rendered
tracking constraints. Both ASCII and binary fixtures are generated without the
production parser or native decoder.

Some older curves omit weight or constant-mode fields. The installed ufbx 0.0.5
reader does not inspect `KeyVer` when decoding them. If native loading fails,
the importer can translate the KeyVer 4002–4004 spelling to explicit fields and
retry. The conversion preserves authored times, values and tangent parameters;
it rebuilds binary offsets as needed. Mixed curve versions, constant keys,
unweighted cubic keys and camera properties have regression controls.

FBX tracking remains **partial**. Native acceptance is not independent proof of
all interpolation semantics. ufbx 0.0.5 approximates or ignores parameters in
some old tangent modes, and rejects others. Its successor also identifies
unresolved modes. Those cases still need authoritative exporter/SDK controls and
implementation work. FBX 7 velocity-flag fidelity also remains unverified;
the current reader does not explicitly adjust curves by those fields. Neither
this tracking work nor successful mesh loading establishes full FBX support.

Implementation references: [ufbx animation evaluation](https://ufbx.github.io/elements/animation/),
[ufbx 0.0.5 source distribution](https://pypi.org/project/ufbx/0.0.5/#files),
and the upstream `ufbxi_read_take_anim_channel` reader in
[ufbx.c](https://github.com/ufbx/ufbx/blob/main/ufbx.c).
Behavioral evidence is in [test_tracking_fbx6.py](../tests/test_tracking_fbx6.py),
[test_tracking.py](../tests/test_tracking.py), and the FBX mesh regression in
[test_3d_loaders.py](../tests/test_3d_loaders.py).
