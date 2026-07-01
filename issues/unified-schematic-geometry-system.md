# Unified Schematic Geometry System

## Goal

Replace the current pile of partial geometry systems with one canonical geometry model
that every placement, routing, emission, validation, and reporting path uses.

The invariant is simple: generated output must not accept visible overlap. Symbol
bodies, pin-label areas, visible fields, visible labels, hierarchical labels, wires,
stubs, junctions, and no-connect markers all need to be represented in one geometry
graph before a candidate layout is accepted.

This is a replacement project, not a new heuristic layer. The partial systems below
should either be carried into the canonical model or deleted.

## Existing Geometry Systems

### `src/ksch/geometry.py`

What it does now:

- Defines tuple aliases for `Coordinate`, `WireSegment`, and `Rect`.
- Defines `PinPoint`, which combines a pin contact point and a label/stub endpoint.
- Parses symbol graphics from KiCad S-expressions to compute graphic extents.
- Computes rough symbol extents and body rectangles.
- Guesses pin-label keepout rectangles with fixed text widths.

Worth carrying over:

- Symbol graphic parsing and symbol pin coordinate transforms.
- Symbol body/extent extraction, but only as inputs to canonical symbol geometry.
- `PinPoint` intent, probably renamed or replaced with explicit `PinContact`.

Delete/replace:

- Tuple `Rect` as a separate rectangle representation.
- Fixed-width pin-label keepout guesses that do not know actual pin label text,
  visibility, orientation, owner, or clearance policy.
- Any standalone collision helpers that compete with the canonical geometry API.

### `src/ksch/layout.py`

What it does now:

- Defines `Point`, `Rect`, `LayoutNode`, `ContactLink`, and a force-like contact solver.
- Scores node/link layouts with `layout_energy()`.

Worth carrying over:

- `Point` and `Rect` are useful primitive types.
- The contact solver may remain as a backend for node compaction, but it must consume
  canonical geometry boxes and must not define schematic legality.

Delete/replace:

- Layout-node overlap as an independent idea of readability.
- Pairwise link/energy acceptance that ignores final emitted text, pin labels, and
  routed output.

### `src/ksch/layout_problem.py`

What it does now:

- Defines `LayoutElement`, `LayoutSegment`, `LayoutProblem`, overlaps, cross-net
  contacts, and text rectangles.
- Sees a subset of final geometry when called from validation.

Worth carrying over:

- The high-level concepts: boxes, segments, overlap/contact reports.
- `text_rect()` as a stopgap text estimator until KiCad-accurate font metrics exist.

Delete/replace:

- `LayoutProblem` as a partial model built differently by each caller.
- Same-owner blanket overlap ignoring; the canonical model needs explicit allowed
  contacts/overlaps instead.

### `src/ksch/placement.py`

What it does now:

- Computes symbol body/readability rectangles in `_symbol_readability_rects_at()`.
- Uses those rectangles in local support placement, tap-stack placement, rail-bank
  placement, and several fallback loops.
- Runs `_resolve_symbol_body_overlaps()` after some placement paths.
- Runs `_relax_symbol_positions()` with force links, keepouts, and route-risk scoring.

Worth carrying over:

- Semantic placement inputs: anchor assignments, motifs, circuit regions, rail-bank
  detection, passive continuations, local topology hints.
- Candidate generation ideas for support passives, tap stacks, and rail banks.

Delete/replace:

- `_symbol_readability_rects_at()` and every placement-local duplicate of emitted
  field geometry.
- `_symbol_body_layout_problem()`, `_resolve_symbol_body_overlaps()`, and body-only
  overlap cleanup.
- Placement fallback acceptance that returns a candidate after local checks but before
  full-sheet geometry legality.
- Route-risk scoring based on synthetic Manhattan routes instead of actual routed
  output geometry.

### `src/ksch/compiler.py`

What it does now:

- Emits actual visible symbol fields using `_symbol_property_points()`.
- Computes `PinPoint` for every endpoint in `_sheet_symbol_pin_point()`.
- Routes nets after symbol placement and then emits hierarchical labels.

Worth carrying over:

- The final place where generated KiCad objects are materialized.
- Existing symbol/property emission structs and stable UUID flow.

Delete/replace:

- `_symbol_property_points()` as an independent geometry rule. Emitted properties
  must be materialized from canonical symbol geometry.
- `_sheet_symbol_pin_point()` label-endpoint placement as an independent text/stub
  geometry rule. It should request pin-contact geometry from the canonical model.
- Hierarchical label placement after routing without a full final legality pass.

### `src/ksch/net_routing.py`

What it does now:

- Builds `route_blockers`, `label_blockers`, and `occupied_segments` from a partial
  `placed_items_layout_problem()`.
- Has separate label clearance helpers such as `_point_with_clear_label()`,
  `_label_clears_blockers()`, `_visible_label_blockers()`, and
  `_symbol_body_label_blockers()`.
- Routes each net by trying many local topologies with local blocker sets.

Worth carrying over:

- Electrical routing motifs: direct nets, rails, tap stacks, contact trees, passive
  rail banks.
- Segment normalization and cross-net contact checks.

Delete/replace:

- Local blocker tuple plumbing.
- Label hiding/moving decisions made inside individual routing helpers.
- Separate symbol-body blockers for labels only.
- Any route acceptance that does not score the full canonical geometry after routing.

### `src/ksch/validation.py`

What it does now:

- Converts placed wires into segments.
- Converts visible labels, hierarchical labels, symbol fields, and sheet properties
  into `LayoutElement`s.
- Validates cross-net wire contacts only.

Worth carrying over:

- The public validation entry point and cross-net contact reporting.

Delete/replace:

- Partial placed-item-to-layout conversion.
- Validation that omits symbol bodies, pin labels, no-connect markers, and route/text
  blocking.
- Validation that lets visible overlaps pass.

## Replacement Architecture

### Canonical Module

Add `src/ksch/schematic_geometry.py` as the only source of schematic occupied geometry.

It should own:

- `GeometryBox`: id, owner, kind, rect, visibility, nets, clearance, allowed-overlap
  group.
- `GeometrySegment`: id, owner, kind, start, end, nets, terminals, clearance.
- `GeometryProblem`: boxes, segments, overlaps, route contacts, segment blockers,
  scoring, and stable reports.
- `SymbolGeometry`: body box, full symbol box, pin contacts, pin-label boxes/stubs,
  visible property candidates, emitted properties.
- `LabelGeometry`: local and hierarchical label candidates, boxes, stubs, visibility.

Everything else calls this module. No other module creates ad hoc text/body blockers.

### Pipeline

1. Build semantic placement intent from motifs, anchors, regions, interfaces, and nets.
2. Generate candidate symbol positions.
3. Materialize canonical symbol geometry for the candidate.
4. Legalize symbol geometry: no body/body, field/body, field/field, pin-label/field,
   or pin-label/body overlaps unless explicitly allowed.
5. Route using canonical boxes and segments as blockers.
6. Materialize canonical label geometry for routed nets.
7. Legalize labels and route stubs.
8. Run final full-sheet geometry validation.
9. Accept only candidates whose canonical score is legal and improved.
10. Emit KiCad objects from the accepted geometry.

### Acceptance Gate

Generated output must fail validation when:

- Visible boxes overlap without an explicit same-owner/same-object allowance.
- A segment crosses a visible box that it is not allowed to cross.
- Cross-net wire contacts occur.
- Hidden labels are used as a fallback for ordinary readability instead of an
  explicitly allowed electrical stub.

### Migration Order

1. Add canonical geometry data types and placed-item conversion.
2. Make validation report visible overlaps and route blockers from canonical geometry.
3. Move symbol body, field, and pin-label geometry out of placement/compiler into the
   canonical module.
4. Replace routing blocker inputs with canonical `GeometryProblem`.
5. Delete local blocker helpers from routing.
6. Delete placement-local readability/body problem helpers.
7. Delete independent property-point placement from compiler.
8. Add candidate legalizer and scoring.
9. Gate `build_placed_project()` on canonical legality.
10. Rebuild placement strategy on top of the canonical model.

## Immediate Regression Targets

- `/lvds_display` must report the current visible overlaps as geometry errors before
  any fix is attempted.
- `C80` and `R85` fields over `U18` body must be caught as field/body overlap.
- `C76/R82`, `C78/R78/R81/R79` collisions must be caught as field/field or field/body
  overlap.
- `R67` field over the `DISP_BRIDGE_EN` hierarchical label must be caught as
  field/hierarchical-label overlap.
- A final generated sheet with any visible overlap must be rejected.

## Current Implementation Status

- The old partial geometry modules have been removed from production source:
  `routing.py`, `net_routing.py`, `local_topology.py`, `circuit_regions.py`, and
  `circuit_motifs.py` are deleted.
- `src/ksch/schematic_geometry.py` is the canonical conversion and legality module.
  It materializes placed items, symbol body/readability boxes, fields, labels, label
  stubs, sheet blocks, no-connect markers, wires, junctions, route blockers, and final
  legality reports. The former `layout_problem.py` data types now live here too.
- `src/ksch/segment_geometry.py` carries only primitive segment intersection math.
  It is not a routing or placement state.
- `src/ksch/layout.py` now carries only primitive `Point`/`Rect` geometry. The old
  contact-node solver is gone, and `layout_problem.py` is deleted.
- `src/ksch/placement.py` owns placement strategy: symbol orientation, first-class
  group assembly, group-local owned labels, boundary labels, power flag positioning,
  and no-connect pin geometry. These solvers consume canonical `SchematicGeometry`
  and canonical label/readability elements instead of constructing separate body/text
  blockers.
- Group assembly now solves labels before group envelopes are distributed. Group
  envelopes include symbol readability and owned label/stub geometry, so group packing
  reserves label space instead of discovering label failure after distribution.
- Boundary and power labels use the same owned-label candidate solver as group labels.
  They are not hidden or rescued by a late whole-sheet cleanup path.
- `src/ksch/compiler.py` now emits the placement plan. It no longer owns route
  blockers, power-flag collision offsets, no-connect pin geometry, endpoint pin
  geometry, or label candidate placement. Its remaining geometry calls are canonical
  property materialization and the final canonical legality gate.
- `build_placed_project()` still gates every sheet through `legalize_sheet_geometry()`
  and then validates the normalized placed project through canonical visible-overlap,
  route-blocker, and cross-net-contact checks.
- No runtime verification status belongs in this issue until the source migration is
  complete and verification is explicitly re-enabled.

## Non-Goals

- Do not add another special-case placement detector.
- Do not keep old blocker/readability systems alive beside the canonical model.
- Do not treat island planning as the primary fix for overlap legality.
