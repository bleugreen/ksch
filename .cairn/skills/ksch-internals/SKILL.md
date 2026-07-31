---
name: ksch-internals
description: Use when modifying the ksch compiler pipeline — the layout solver, block/assembly placement, canonical geometry checks, net-branch pruning, or the verify/drift command. Covers the north-star schematic output style, the Assembly architecture, declared-block scoping, label and overlap gotchas, and verify-artifact placement.
---

# ksch compiler internals

Deep notes for agents changing the `ksch` layout and verification pipeline. The
pipeline is `schema loader -> source model -> project IR -> resolver -> layout solver
-> geometry validation -> emitter`; see `README.md` for the module map.

## North-star output style

The generated schematic is optimized for the human who reads and routes it.
`examples/can-controller` is the reference fixture. Target style:

- Sheets are organized as titled functional-block frames, declared in the schema via
  `blocks:` and rendered as labeled rectangles.
- Repeated sub-circuits (series-clamp dividers, crystal + load caps, decoupling rows)
  are stamped from identical stanza templates with direct local wiring, so the same
  sub-circuit looks identical everywhere it appears.
- Inter-block connectivity is carried by net labels placed flush at pins (about one
  grid unit off the pin), not by long routed wires.
- Power orientation is GND-down / rails-up.
- Block frames tile row-by-row in reading order.

## Declared blocks

`ResolvedSheet.blocks: dict[str, tuple[str, ...]]` maps a block name to its member
refs. Authors declare them as `blocks: {NAME: {members: [U1, C1, ...]}}`. Validation
(`model/source.py`) enforces that every member exists and appears in at most one block
per sheet; symbols may be left undeclared (there is no total-partition requirement).

Regenerate and verify the example projects (each has a `ksch.toml` with `schema=` and
`out=`; the generated `kicad/` directory is gitignored):

    cd examples/<project> && uv run ksch gen          # prints report counts as JSON
    uv run ksch verify --no-erc                        # netlist parity + drift

The layout report exposes these metrics: `layout_errors`, `out_of_bounds`,
`visible_overlaps`, `route_blockers`, `cross_net_contacts`. Regenerate to read the
current values; as a reference point, the can-controller baseline reports 0
`layout_errors`, 0 `out_of_bounds`, and 0 `route_blockers`, with a small number of
`visible_overlaps` and a few `cross_net_contacts`, on the default A3 paper.

## Assembly architecture

The layout pipeline builds each group as a self-contained local-coordinate unit before
packing. `Assembly` (in `layout_solver.py`) owns `.items` that already include its
symbols, fields, local wires, labels, stubs, and power symbols — all produced during
`_root_assembly` / `_assembly_net_items` before any packing. `_normalize_assembly`
shifts each assembly to (0,0); `_pack_assemblies` places each as a rigid envelope and
translates its items via `_translate_item`. There is no late whole-sheet label search.

A block or group is therefore just another Assembly. To scope the entire
grouping/ownership/assembly machinery to a subset of components, restrict
`self.components`, `self.net_records`, and `self.endpoint_to_component` — every helper
reads only those. A net whose in-scope records are a subset of its full records shows
up as a dangling endpoint, and the existing `_label_items` emits a net label at that
pin. That is how cross-boundary connectivity becomes labels-only with no new code.

## Gotchas

- **Diagnose repeated labels as placement first.** `_assembly_net_items` owns the
  wire-versus-label decision. It emits direct local wires only for an in-scope,
  non-power net with at least two local endpoints whose pins remain within
  `DIRECT_LOCAL_WIRE_LIMIT`; otherwise it labels each endpoint. In particular,
  `_floating_assembly` can spread an anchorless passive group beyond that limit.
  Fix the assembly placement before adding a separate label phase.

- **Root-local labels have a scoped-layout exception.** `_label_items()` normally
  anchors root-sheet local labels directly at pins, bypassing candidate search. That
  shortcut is appropriate for whole-root layout, but framed block solves must pass
  `route_root_local=True`; otherwise labels can overlap symbol bodies and pin text.
  Candidate scoring strongly prioritizes clearing overlaps over distance, so labels
  moving away from pins under clutter is expected and should be addressed by
  placement or keepout geometry rather than post-packing repair.

- **Keep framed-block visual behavior scoped.** Stanza layout, connector-only label
  assemblies, junctions, and hidden connectivity assertions introduced for declared
  blocks must remain gated by `_framed_block_scope` unless netlist parity has been
  established for undeclared and imported sheets too. Applying those behaviors
  globally has changed CM5 connectivity in the past.

- **Label-text caches are memoized on `net_records`.** `_local_label_texts()` and
  `_project_label_text_counts()` in `layout_solver.py` depend on `self.net_records`
  and are memoized. If you restrict `net_records` per scope, prime these caches from
  the full sheet first and do not clear them — otherwise the same net gets different
  labels in different scopes and connect-by-name breaks.

- **The overlap report cannot see frames.** `_allowed_element_overlap` in
  `schematic_geometry.py` returns `True` whenever either element is a `graphic_frame`,
  so the layout report's `visible_overlaps` cannot catch frame-vs-frame or
  frame-vs-foreign overlap. The packer does keep frames apart geometrically (raw
  `_overlap_area` over all boxes, including `graphic_frame`, rather than
  `_allowed_element_overlap`), but tests must assert frame non-overlap geometrically,
  not via the report. `PlacedGraphicRectangle` and `PlacedText` geometry, emission,
  and validation are already fully wired in `emit.py` and `schematic_geometry.py`;
  only the solver needs to produce them.

- **Net-branch pruning needs real anchors.** A `PlacedWire` touching a real schematic
  pin will not always have `start_terminals` / `end_terminals` populated.
  `_prune_net_branch` in `layout_solver.py` must derive terminal anchor coordinates
  from resolved net records plus placed symbol/sheet positions before pruning;
  otherwise a branch can look redundant while it is the only connectivity assertion for
  a symbol pin (the can-controller `VEH_REV_12V` regression).

- **Hidden labels assert connectivity, not visible geometry.** A hidden `PlacedLabel`
  may provide the zero-length anchor segment needed for KiCad netlist parity, but
  `placed_items_geometry` must omit its text box so invisible text cannot create
  visible-overlap findings.

## Architecture guards

`tests/test_compiler_architecture.py` protects the single geometry pipeline:
`compiler.py` serializes solver state and must not reconstruct labels or iterate
resolved nets; labels, stubs, wires, and power symbols remain Assembly-owned geometry
created before packing. Grouped motif recognition must flow through
`_stanza_template_assemblies` and `_root_stanza_template_items`. When changing these
checks, assert the live mechanism and call graph. A forbidden-name absence check alone
can be defeated by renaming the same bespoke path and does not prove subsumption. See
`issues/layout-engine-failed-experiment-postmortem.md` before changing this boundary.

## Electrical verification

Treat name-independent netlist parity as the electrical ground truth. KiCad ERC does
not flag two intended nets that silently merge, while `ksch verify` compares each
pin's net-mate set against the resolved schema. KiCad connectivity is coordinate
based: coincident endpoints from different nets can silently short, and a pin touching
the middle of a wire is not connected unless the segment is split or a junction is
present. Layout-report counts are quality witnesses, not electrical proof.

A smoke test in `tests/test_importer.py` reads the live checkout at
`/Users/mitch/projects/cm5-hudsp`; changes outside this repository can therefore alter
its result. Reproduce failures from external projects as committed fixtures before
attributing them to a repository change. The short CM5 fixture is
`tests/fixtures/cm5_gmsl2_u8_short/project.ksch.yaml`; its local `CM5HUDSP` and `CM5IO`
symbol libraries must both be supplied when compiling it directly.

## Verify artifacts

When adding artifacts to `ksch verify`, keep them outside the generated output tree
passed to `compare_dirs`. A temporary export such as `generated.net` written inside
the generated tree is reported by the drift check as a missing generated file in the
configured output directory.

`ksch gen` takes its destination from `ksch.toml`; it has no `--output` option. Run it
from the project directory or pass `--config <project-directory-or-ksch.toml>`.
