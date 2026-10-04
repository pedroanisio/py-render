# Probe-scene prototypes (planning artefacts)

Scenes and helper scripts the planners of the phase 2/3 work packages wrote while measuring the Rust binary against the
Python tree. They are prototypes: they load and render with the Rust `scene-render` (checked at planning time) but have not
been polished or wired into a runner. Each package's acceptance gate in `../WORKPACKAGES-P2-P3.md` names the probe scenes
it needs; when the first package of a track lands (P2-EVAL-00, P3-COMP-01, P3-FX-01) these move into `tools/parity/scenes/`
and become the committed gate set.

- `eval/`: evaluation probes (clocks, sequences, repeats, instances/includes, keyframes, links, motion paths, layouts, fit, safe areas,
  parameters, cycles, 190-expression tables with measured Rust/Python verdicts) and rough dump/diff scripts (`rs.py`, `pydump.py`, `pyworld.py`, `exprdiff.py`, `propdiff.py`).
- `comp/`: compositor probes (blend grid, masks, mattes, paints, generators, 2.5D, fit, isolation).
- `fx/`: effect and transition probes (one scene per effect/transition member on a test card) and generators.
