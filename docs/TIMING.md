# Runtime timing

`Document.windows` and `Document.clock_shift` are the initial timing snapshot
prepared when a document loads. Runtime consumers use `Evaluator.window` and
`Evaluator.enter_node`; the snapshot does not include instance overrides or the
current values of animated timing properties.

A sequence schedules each child after the previous child's end plus `timeGap`,
then adds the child's own start offset. Negative gaps overlap children. The
occupied duration uses an explicit end when supplied, otherwise media duration,
clip bounds, speed, time stretch and repeat count. A time-remapped layer uses the
last remap key's local time. Nested groups and sequences include their own
`timeOffset` and `timeScale`. The sequence remains active through the latest
child's end, including overlap and evaluated duration changes.

Entering a scheduled child subtracts its evaluated offset exactly once. Its
media and local animation then run on that clock. The evaluation context carries
the applied offset and each enclosing sequence's clock before its time warp.
The latter keeps sequence attributes on the sequence's clock while children use
the transformed clock. The schedule cache includes scope and repeat bindings,
and retains at most 128 recent schedules.

Every adjacent pair has a prepared default transition junction, even when the
sequence initially has no transition. Runtime evaluation determines whether the
junction is used and obtains its type and duration from the sequence. An explicit
transition between the same evaluated pair replaces the default. Transition
windows, type, curve, alignment and members use evaluated values. Video-layer
audio traverses the same windows and clocks, including inherited transition gain.

`tests/test_sequence_scheduling.py` covers these behaviors with rendered pixels,
explicit timeline controls, reference lookups, backward seeks and deterministic
PCM sources. Existing timing, transition and audio suites provide additional
integration coverage.

Normalized timing properties that also determine their own window are solved
together. At local time 0.5, a one-second source whose speed rises from 1 to 2
over normalized time resolves to a 0.5-second span and speed 2. The solver starts
from the authored window on every seek, checks its residual, and retains at most
128 resolved windows. Contradictory held keys can have no solution: evaluation is
bounded and emits a `timing-cycle` diagnostic instead of hanging. Tests include
independent numerical solutions for speed, stretch, trim, start/end, gap and group
scale, including normalized descendant ends.

Temporal rendering reconstructs the ancestor traversal at each composition
sample. Echo intervals and motion-blur shutters use composition seconds; echo
tails test the actual past samples. Posterize-time quantizes the node's clock and
finds a corresponding composition sample on the nearby clock branch, including
reverse playback, loops and remaps. If a discontinuity makes the held value
unreachable, its local clock is still held explicitly while the closest sampled
composition context supplies references and parent transforms. The inverse search
is bounded; it is not a proof of finding every root of an arbitrary timing
expression. `tests/test_temporal_clocks.py` compares these operations with complete
renders at independently calculated sample times.

Particle parameters and rigid-body poses replay history using the composition
time associated with each local step. Global force fields retain composition
time. Simulation caches include instance/repeat bindings and the replay branch;
each binding retains at most eight branches, so loop cycles with changing global
inputs do not reuse a different cycle's history. `tests/test_simulation_clocks.py`
compares particle arrays, rigid-body states and rendered frames with explicit
clock controls across speed, reverse playback, piecewise remaps and loops.

Remaining timing work includes arbitrary nonmonotonic/discontinuous clock
expressions, simulation preroll and window changes, delay/smoothing links and
`valueAtTime` across warped clocks. Video-layer audio still samples its control clock at 1 kHz;
general track transition automation is evaluated at track placement. These are
outstanding conformance requirements, not claims of complete timing support.
