# Automatic Layout Should Plan Functional Islands Before Symbol Lanes

## Context

The GMSL2 camera sheet comparison in
`snapshots/cm5-hudsp-gmsl2-camera-baseline/` shows a useful manual correction pattern:
the manual pass did not merely nudge symbols. It changed the layout from prefix-driven
columns into functional islands:

- AP63200 switcher support moved into a compact power island.
- PoC/coax parts moved together instead of being distributed across generic L/F/R/C lanes.
- MAX96714-adjacent items, crystal, configuration straps, and SIO/term parts moved near
  the deserializer pins they support.
- Label count dropped from 108 to 84 and wires from 146 to 142, which is the signal we
  want the automatic layout to optimize for.

The current engine already has useful local machinery: anchor assignments, circuit
regions, tap-stack motifs, rail-bank motifs, passive continuations, and pin-aligned
support placement. The gap is earlier: initial staging is still mostly based on symbol
reference prefix and sheet size. That means later smart passes inherit bad anchor
geometry.

Relevant current code:

- `src/ksch/placement.py::_symbol_lane_index` maps J/F/Q/U/L/passives to fixed lanes.
- `src/ksch/placement.py::_layout_low_interface_local_circuit` has a small-sheet
  staged flow, but it is gated to small/simple sheets and still uses fixed stages.
- `src/ksch/placement.py::_layout_sheet_symbols` large-sheet path stacks by lane.
- `src/ksch/placement.py::place_passive_rail_bank` defaults rail banks to the right
  side of their existing source points, which can preserve stranded cap columns.

## Desired Direction

Introduce a functional-island planning layer before concrete symbol placement.

For a sheet like GMSL2 camera, the planner should infer islands such as:

- primary controller island: MAX96714 plus crystal, CFG straps, SIO termination, local
  rail decoupling, reset/GPIO/I2C support
- upstream power island: AP63200 plus inductor, bootstrap, feedback, input/output caps
- downstream regulator islands: TLV733 rails with their input/output caps
- interface path island: VBAT/PoC fuse/filter/inductor/coax connector
- external sheet interface edge: CSI/I2C/GPIO/power labels arranged by signal group

Then the placer should position islands relative to each other:

- high-pin-count controller near the center/right
- sheet-interface outputs on the side implied by pin/interface direction
- upstream power and PoC path on the left, flowing toward the controlled/load island
- local support passives placed around their owning anchor pin, not in global passive
  lanes
- rail banks attached to the rail owner/load they decouple, not globally placed by
  passive prefix

## Proposed Implementation Steps

1. Add `FunctionalIsland` data structures, probably near `circuit_regions.py`, that
   group anchors, support refs, rail-bank refs, continuation refs, interface nets, and
   a semantic kind.
2. Promote existing motifs/regions into island ownership:
   - controller-owned support from `_symbol_anchor_assignments`
   - rail banks from `build_sheet_circuit_motifs().rail_banks`
   - passive continuations from `_passive_continuation_placements`
   - interface nets from `sheet.interface`
3. Replace the large-sheet first placement pass for clustered sheets with island
   placement. Keep the current lane stacker as a fallback, not the primary strategy.
4. Add an island-level graph/order:
   - power/interface sources left
   - controller/load center or right
   - sheet outputs right
   - support refs on the side of the owning pin
5. Score candidate island placements using measurable schematic-readability costs:
   - fewer local labels
   - shorter same-island routed segments
   - fewer cross-net contacts and blocked routes
   - lower distance from support passive pins to owner pins
   - rail bank proximity to the rail's owner/load
6. Add fixture/regression tests from the GMSL2 camera sheet:
   - assert AP63200 support components are closer to AP63200 than to the deserializer
   - assert PoC/coax components are grouped along one local path
   - assert MAX96714 support passives/straps are within a bounded distance of MAX96714 pins
   - assert generated labels/wires do not regress above the baseline counts without a
     deliberate test update

## Acceptance Criteria

- A generated GMSL2 camera layout produces the same broad island structure as the manual
  halfway pass without hand-authored coordinates.
- Passive banks no longer remain in generic far-right columns when they clearly belong
  to a local rail owner.
- The layout diff metrics improve against the captured baseline: fewer labels, fewer
  long same-island jumps, and support passives closer to owner pins.
- Existing smaller-sheet behavior remains covered and does not regress.

## Initial Slice Implemented

The first implementation slice detects power-conditioning islands from switcher-style
local nets (`SW`, `FB`, `BOOT`, `COMP`, `COMP_RC`) and uses that signal in placement:

- large sheets with a bounded number of anchors can use the semantic peripheral
  placement path instead of falling straight to prefix-lane stacking
- power-conditioning controller anchors get an earlier schematic lane than downstream
  controller/load anchors
- power-path anchors such as inductors/ferrites connected to that island are carried
  with the island
- rail-bank placement prefers power-path source points and centers the bank around the
  source point instead of always starting to the right
- connector-to-controller interface paths are discovered from the non-ground anchor
  graph, so fuse/filter/inductor anchors stay before the downstream controller instead
  of falling into the generic L/FB right-side lane

The second implementation slice starts moving this from rigid placement rules toward an
optimizer:

- `layout_energy()` scores candidate node/link layouts and lets placement reject a
  solver result when the force pass makes the weighted layout worse
- interface path discovery now traverses movable/non-boundary refs while treating
  connectors and controllers as per-net boundary evidence, so controller refs do not
  become graph hubs across all pins
- interface path compaction uses generic local net springs plus a component centroid
  force; this is intentionally name-agnostic and operates on connected path islands,
  not on PoC-specific net names
- `L`, `F`, and similar two-pin path parts are no longer fixed just because their
  reference prefix is anchor-like; hard fixed refs are sheet boundaries/controllers
- on the live CM5 HUDSP GMSL2 camera scratch regenerate, the PoC/coax path island
  validates and the F4/L4/L5/L6/L7/L2 vertical span dropped from about `172.72mm` to
  about `104.14mm`

Regression coverage:

- `test_large_functional_sheet_places_power_island_before_controller`
- `test_large_functional_sheet_keeps_interface_filter_path_before_controller`
- `test_large_functional_sheet_compacts_generic_connected_two_pin_chain`

Remaining work: generalize from this power-island slice into explicit `FunctionalIsland`
objects with scored placement across controller, interface, regulator, PoC/coax, and
sheet-edge groups.

Next optimizer work:

- add explicit flow/order energy so connector-to-controller islands prefer monotonic
  source-to-load order instead of only compactness
- score routed output, not just symbol-node geometry, before accepting an optimization
  pass
- add label/field overlap terms to the energy model so local-label placement competes
  with symbol readability instead of being handled by isolated routing fallbacks

## Optimizer Slices Added After CM5 Regenerate

The next implementation slice added generic solver/acceptance mechanics rather than a
PoC-specific detector:

- `ContactLink` can now carry directed x/y flow constraints, and `layout_energy()`
  scores those constraints so a solver pass is accepted only when the weighted layout
  improves.
- Interface-path graph analysis now derives connector-to-load flow distances and adds
  directed order links between connected refs at different path depths.
- `_position_routing_risk_score()` estimates simple same-net route candidates and
  rejects a solved placement when it increases cross-net routing contact risk.
- Topology/local fallback labels now require a clear label site from the call site; if
  no clear site exists, the router emits a no-stub hidden label instead of creating an
  overlapping visible label/stub.
- Single-ended left-facing support labels keep their existing side convention when the
  alternative would flip them toward the parent IC.

Regression coverage added:

- directed x-flow solver behavior in `tests/test_layout.py`
- source-to-load ordering for generic connected two-pin interface chains
- route-risk scoring for crossing candidate nets
- blocked topology labels hiding rather than emitting unsafe visible labels

Current live CM5 HUDSP GMSL2 camera scratch regenerate:

- compile output: `/private/tmp/cm5hudsp-ksch-optimizer-slices`
- SVG export:
  `/private/tmp/cm5hudsp-ksch-optimizer-slices/gmsl2_camera.svg/gmsl2_camera.svg`
- PoC-ish refs remain partially grouped, not solved: `F4/L2/L4/L5/L6/L7` still span
  about `119.38mm` vertically, and the path is not monotonic on the real sheet.

This confirms the direction but also the limitation: local force links are not enough.
The durable next step is an explicit island object model with roles, ownership, and a
routed-output visual score, then the solver can iterate over island placements instead
of trying to infer the whole sheet from pairwise symbol springs.
