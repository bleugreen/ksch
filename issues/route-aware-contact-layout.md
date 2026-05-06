# Route-Aware Contact Layout

The schematic compiler now has an explicit placed-model boundary. Emission is serialization only;
placement, routing, geometry, and validation work belong before `ksch.emit.write_project`.
Interface-sheet contact relaxation is still gated because the compiler does not yet feed all
generated route/label geometry back into the layout problem before solving.

What landed:

- `emit.py` is now a serializer over `PlacedProject`; it no longer imports resolver, symbol,
  endpoint, placement, routing, or KiCad symbol geometry concepts.
- `ksch.compiler.build_placed_project` is the compiler-stage entry point from `ResolvedProject` to
  the placed schematic model used by emission.
- `ksch.placed` now contains typed placed schematic objects: symbols, symbol properties/pins, sheet
  blocks/pins, wires, junctions, labels, hierarchical labels, and no-connect markers.
- `compiler.py` now builds `PlacedSheet` objects directly. The prior generated-text-to-S-expression
  bridge has been removed; tests assert the compiler does not call `loads()` on generated schematic
  text and `PlacedSheet` no longer stores a raw `sexpr` field.
- `emit.py` serializes those typed placed objects into KiCad S-expressions and writes project/library
  files. It still owns S-expression formatting, but not placement or routing decisions.
- Symbol placement orchestration moved to `ksch.placement`. `compiler.py` no longer defines the
  placement pass (`_layout_sheet_symbols`, low-interface placement, anchor assignment, symbol-body
  relaxation); it calls the placement stage while assembling the placed schematic model.
- Net-routing orchestration moved to `ksch.net_routing`. Rail-bank generation, direct/compact route
  selection, label placement for net endpoints, and passive-bank rendering are no longer defined in
  `compiler.py`; they return typed placed wires, junctions, and labels.
- Routed placed items now carry net metadata where the route key has a concrete source net.
- Routed wire segments now carry endpoint terminal ownership at their start/end coordinates when
  generated from schema endpoints. This gives validation and future routing passes a typed way to
  distinguish pin-terminal contacts from route-body contacts without parsing emitted KiCad text.
- `ksch.validation.placed_layout_problem()` converts placed sheets into `LayoutProblem` route
  segments and text elements, including generated labels and visible symbol fields.
- `ksch.validation.cross_net_contacts()` and `validate_placed_project()` can report same-sheet
  cross-net route contacts from the typed placed model. The compile path now hard-fails before
  emission when the placed model contains a cross-net route contact.
- Source resolution now rejects a single physical symbol pin or child sheet port connected to
  multiple named nets. This surfaced two old synthetic layout tests that modeled repeated decoupling
  as many net names sharing one IC pin; those tests now use a single fanout net, which matches the
  intended schema semantics.
- Symbol geometry moved to `ksch.geometry`: pin points, symbol graphic extents, symbol/body rects,
  vertical two-pin detection, and pin-label keepout rects.
- Route geometry primitives moved to `ksch.routing`: wire normalization, point-on-segment,
  segment splitting, obstacle checks, and segment contact detection.
- `LayoutProblem` now models both rectangular elements and route segments, with cross-net contact
  reporting and text/field blocking checks.
- `ksch.layout.solve_contact_layout` models symbols as rectangles with movable/fixed nodes,
  anchor links, collision cleanup, and fixed-node semantics.
- `ksch.layout_problem.LayoutProblem` now captures emitted schematic geometry as typed elements and
  reports owner-aware overlaps. The first use is symbol-body overlap detection from KiCad library
  graphics.
- The emitter can convert KiCad symbol geometry into solver nodes and relax non-interface symbol
  placements before schematic emission.
- The low/local-circuit emitter resolves real symbol-body collisions before writing the schematic,
  and a guarded post-pass also handles small-interface local power/control sheets with suffix nets
  such as `FB`, `SW`, `RT_CLK`, and `BUCK_EN`.
- Repeated two-pin passive rail-bank members get cohesion links so individual anchor gravity does
  not scatter shared rails.
- Dense anchor pin-label fields are represented as fixed contact keepouts for the solver.
- Vertical two-pin passives now derive endpoint labels from the actual lead geometry and place those
  labels beside the lead instead of above/below the component. Stacked passive tap routing now uses
  the electrical pin column rather than label anchor coincidence, so divider taps still collapse to a
  single visible side label after passive label anchors move.

Why interface sheets are gated:

- Interface sheets add hierarchy labels and future route stubs after symbol placement.
- A solver that only knows symbol bodies and anchor label keepouts can move passives into places
  where later generated labels/wires of different nets touch.
- During implementation, enabling relaxation on CM5 imported interface sheets caused real KiCad
  netlist merges:
  - `J7/J8/J9` collapsed when fixed anchors were incorrectly clamped; fixed-node semantics now
    prevent that.
  - Separate FET gate and POC-stage nets still merged when unrelated support passives moved close
    enough for generated route geometry to touch.
- With interface relaxation gated off, CM5 roundtrip parity is exact and ERC is clean.

Next compiler work:

- Feed `ksch.validation` route/text geometry back into routing and placement so detected contacts
  become avoid/repel constraints rather than a post-facto report.
- Represent movable symbol annotation and expected pin stubs as part of the moving body, not only
  the library symbol body.
- Feed placed route/text contacts back into routing candidate selection so validation failures become
  avoid/repel constraints before the gate trips.
- Once those constraints exist, remove the interface-sheet gate and apply contact layout to CM5
  sheets such as USB hub ports and GMSL2 camera.

Latest evidence:

- Broadly applying the body-collision pass to all interface sheets is still electrically unsafe:
  USB hub and CAN sheets can produce real ERC net merges because route/label geometry is generated
  after placement.
- Applying the body-collision pass only to low/local-circuit layouts and small-interface sheets
  with recognized local power/control suffixes removes the `C3` on `U10` body overlap in the CM5
  power sheet while preserving `kicad-cli sch erc` at 0 violations.
- On the current CM5 generated placed model, the placed-project validation gate passes and
  `kicad-cli sch erc` reports 0 violations. Focused synthetic tests now reject repeated single-pin
  fanout across different net names at source resolution instead of allowing KiCad to merge them.

Verified baseline:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 125 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-architecture` passes.
- `kicad-cli sch erc --output /tmp/ksch-cm5-architecture-erc.rpt /tmp/ksch-cm5-architecture/cm5hudsp.kicad_sch` reports 0 violations.
- `tests/test_importer.py` currently has one environment-coupled CM5 smoke failure because the
  local KiCad fixture references `cm5hudsp:Center_Console_USB_C_Harness_8`, which is not resolved
  by the imported schema compile path.

## 2026-05-04 Topology Routing Update

The compiler now has an explicit local topology extraction stage in `ksch.local_topology`. It
classifies placed sheet endpoints into topology nets and identifies compact anchor-to-two-pin-support
connections before generic net routing chooses labels or wires. `ksch.net_routing.route_sheet_nets`
now routes those topology-owned anchor/passive nets first and marks their endpoints consumed, so a
compact local IC/support connection is not silently downgraded to two labels when a pin-to-pin route
is available.

Additional electrical hygiene landed with this pass:

- Topology-owned local routes decouple wiring from label placement. If a clean label rectangle is not
  available, the valid pin-to-pin wire still emits and the label remains a visible annotation problem
  rather than becoming a label-only connection.
- Anchor/passive routing now has escape candidates for occupied neighboring pin lanes. This fixes
  the `U10.CL`/`R46` class of failures where an adjacent routed pin blocked the immediate left-facing
  lane.
- Vertical-anchor local route support exists for the same topology path.
- Declared no-connect pins are passed into routing as blocked coordinates. Routes no longer treat
  only named-net endpoints as obstacles, which prevents generated rails from crossing a no-connect
  pin and later tripping ERC.

This is still not the complete layout architecture. The real missing layer is an island/block
planner that groups an anchor, its support passives, shared rails, and annotations as one local
circuit before both placement and routing. Current topology routing improves connectivity and avoids
some label-only local nets, but routes can still look like large rectangles and visible labels/fields
can still collide. The next durable step is to promote `LocalTopology` from route selection into a
layout constraint graph: support islands should own placement gravity, rail collapse, route channels,
and annotation slots as a unit.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 148 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-topology5` passes.
- `kicad-cli sch erc --output /tmp/ksch-cm5-topology5-erc.rpt /tmp/ksch-cm5-topology5/cm5hudsp.kicad_sch` reports 0 violations.

## 2026-05-04 Hierarchical Placement Update

The medium-sheet placement pass now has a first topology-owned placement layer before generic
fallback slots:

- IC-local control nets such as `FB`, `COMP`, `BOOT`, `RT_CLK`, and `BUCK_EN` outrank rails when
  choosing the parent anchor for a support passive. Feedback dividers now stay owned by the IC
  feedback pin instead of splitting one resistor to the output rail and the other to the IC.
- Single anchor-side support passives can place from the parent pin geometry directly. Their
  connected lead lands on the parent pin line, and routing can emit a direct pin-to-pin segment
  instead of a dogleg through an arbitrary slot.
- Repeated capacitor rail-bank members are excluded from IC support-passive gravity. They are placed
  as rail rows before generic fallback so the rail router has a solvable horizontal geometry.
- Passive rail banks now model both top-net and bottom-net extras. This lets the bank consume nearby
  GND endpoints as part of the rail topology instead of leaving isolated GND stubs for a later pass.
- Rail-bank side candidates reject candidates whose generated top and bottom rail segments touch,
  and they also reserve future endpoint stubs from other nets before choosing a rail path. If a bank
  cannot be routed safely, generic shared-rail fallback is disabled for those bank candidate nets so
  the compiler falls back to explicit point labels rather than creating an unsafe rail.
- Pin-label stub endpoints snap to the schematic grid while preserving exact symbol pin coordinates.

Current state:

- CM5 `/power_input_5v` is materially more coherent around `U10`, `R46`, and `R47`: those support
  passives are placed from the parent IC pins and route directly instead of around large rectangles.
- CM5 output caps `C5`/`C6`/`C7` are again placed as a row, but the row is still too compressed near
  `L1` and the labels/values remain crowded. The next architectural step is rail-row anchoring and
  annotation slots, not another local route tweak.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 150 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-hierplace13` passes.
- `kicad-cli sch erc --output /tmp/ksch-cm5-hierplace13-erc.rpt /tmp/ksch-cm5-hierplace13/cm5hudsp.kicad_sch` reports 0 violations.

## 2026-05-04 Passive Continuation Update

The topology layer now recognizes a local passive-to-passive continuation when one two-pin passive
is already owned by an anchor/passive topology net and its other terminal continues into another
two-pin passive on a local non-power node. This fixes the COMP/COMP_RC pattern where the compensation
resistor was pulled next to the buck IC but the shunt capacitor stayed in generic fallback placement
with a duplicated label.

Implementation details:

- `LocalTopology` now exposes `passive_continuation_nets` with the parent anchor, source passive,
  and continuation passive.
- Placement excludes continuation target passives from generic anchor assignment, then places them
  from the source passive's real pin geometry. Candidate placement checks body overlap, pin-label
  stub collision, and rejects same-line candidates whose eventual direct route would cross another
  endpoint.
- Routing consumes passive continuation endpoints before the generic net fallback and emits one
  visible local label for the continuation node. It rejects label coordinates that coincide with an
  unrelated endpoint, preventing the first attempt's `COMP_RC` label from landing on `Q1.D`.

Current state:

- On CM5 `/power_input_5v`, `C3` is no longer floating at a distant label-only stub. It now sits near
  `R2`, `COMP_RC` appears once, and the local node is directly routed.
- The surrounding U1 annotation field density is still poor (`C1`/`C2`/`R3`/`R2` crowding is visible).
  The next durable step remains field-aware local support islands: placement should reserve annotation
  slots for a cluster before routing, rather than letting support passives and labels compete after
  symbol placement.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 152 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-continuation` passes.

## 2026-05-04 Controller Support Island Update

The medium-sheet compiler now has a more explicit controller-support island layer for the CM5 power
sheet class of problems:

- Field/readability rectangles are part of candidate placement for medium-sheet IC support passives.
  Candidates now reject visible symbol-field overlap and likely pin-stub route collisions instead of
  only avoiding raw symbol bodies.
- The field-aware allocator is scoped to controller anchors (`U*`, `IC*`, `Module*`). Connector and
  other non-controller support sheets keep the older placement path so controller-specific island
  rules do not destabilize unrelated connector support circuits.
- Divider/tap passives that share one controller pin and one local signal net are recognized as an
  intentional stack. The later anchor-pin realignment pass now preserves that stack instead of
  flattening both passives back onto the IC pin line.
- Medium-sheet compact three-point local nets are no longer gated by arbitrary interface-pin count.
  The safe local tap router can now collapse `FB` into one routed tap with one visible label on
  `/power_input_5v`.
- Repeated capacitor rail banks now choose collision-aware row slots from nearby non-bank endpoints
  instead of landing on a fixed row that could overlap the switch/output island.
- Vertical flow anchors (`L*`, `FB*`) place visible fields above the symbol instead of reserving the
  left-side space needed by nearby bootstrap/switch support parts. Placement uses the same field
  geometry as compilation, so the allocator sees the text that KiCad will render.

Current state:

- On CM5 `/power_input_5v`, the U1 area is no longer the previous label pile: `C4` sits beside the
  buck IC, `R5`/`R6` form a routed feedback divider stack, and `C5`/`C6`/`C7` are on a shared output
  rail row.
- The left input/protection subcluster around `Q1`, `C1`/`C2`, `R3`, and `R4` is still visually
  dense. The next durable step is to promote controller support islands into generic topology
  regions such as series paths, shunts, clamps, tap stacks, and rail banks with owned annotation
  slots, instead of relying on named converter-role buckets or per-part candidate ordering inside
  one island.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 152 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-flowfields` passes.
- `kicad-cli sch erc /tmp/ksch-cm5-flowfields/cm5hudsp.kicad_sch --output /tmp/ksch-cm5-flowfields/erc.rpt --format report` reports 0 violations.
- `kicad-cli sch erc /tmp/ksch-cm5-continuation/cm5hudsp.kicad_sch --output /tmp/ksch-cm5-continuation/erc.rpt --format report` reports 0 violations.

## 2026-05-05 Generic Motif Extraction Update

The compiler now has a sheet-level circuit motif extraction stage in `ksch.circuit_motifs`. This
stage works from resolved graph topology and KiCad symbol geometry rather than named converter
roles:

- Two-pin vertical symbols are classified as `series_path`, `shunt`, `clamp`, or generic `two_pin`
  by their net shape.
- Tap stacks are detected as an anchor pin plus two two-pin passives on the same tap net where one
  passive continues to a non-ground rail and the other returns to ground.
- Capacitor rail banks are detected from two or more capacitor shunts on the same non-ground/ground
  net pair. Two-cap banks are now first-class motifs instead of being left as loose labeled stubs.
- Medium-sheet support ordering now uses parent pin geometry and motif shape instead of named
  buckets such as feedback, comp, switch, or bootstrap.
- Rail-bank placement uses the non-ground rail endpoints as the primary vertical anchor. Distant
  ground endpoints no longer drag a bank toward unrelated ground structure or the title block.

Current state:

- On CM5 `/power_input_5v`, `R5`/`R6` are placed and routed through the generic tap-stack motif, and
  `C5`/`C6`/`C7` are still on a shared output rail row.
- `C1`/`C2` now stay in the schematic body rather than being pulled down by distant ground geometry.
- The left-side mixed series/clamp/support neighborhood is still not a coherent local sub-layout.
  The next compiler layer should consume the motif graph as regions with owned annotation and route
  channels, rather than letting independent motifs compete for whitespace after initial placement.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 154 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-motifs-final` passes.
- `kicad-cli sch erc /tmp/ksch-cm5-motifs-final/cm5hudsp.kicad_sch --output /tmp/ksch-cm5-motifs-final/erc.rpt --format report` reports 0 violations.

## 2026-05-05 Region Planner Update

The compiler now has a generic circuit-region stage in `ksch.circuit_regions` between motif
extraction and placement. Motifs describe local electrical shapes; regions describe ownership and
co-placement:

- Anchor-support regions are built by walking the resolved net graph through two-pin motifs from the
  best nearby anchor. This pulls passive continuations, such as an RC shunt after a controller-side
  resistor, into the same local region as the source passive.
- Rail-bank regions remain separate from anchor-support regions, so decoupling banks do not get
  pulled into IC support gravity just because they share a power rail.
- Medium-sheet continuation placement now asks the region graph whether the source and target
  passive are in the same region. Same-region continuations can use same-column stack candidates
  before falling back to a side-placement search.
- The passive-continuation layout test now accepts both compact side-by-side and compact same-column
  stacks as long as the generated route visibly connects the continuation pins.

Current state:

- On CM5 `/power_input_5v`, the `R2`/`C3` compensation branch is a single local vertical stack
  instead of `C3` floating left into the fuse/FET neighborhood.
- This is still a region ownership layer, not a complete region layout solver. The next step is to
  let each region own annotation slots and route channels explicitly, so the compiler can avoid
  long vertical helper trunks through neighboring lanes.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 156 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-regions-final` passes.
- `kicad-cli sch erc /tmp/ksch-cm5-regions-final/cm5hudsp.kicad_sch --output /tmp/ksch-cm5-regions-final/erc.rpt --format report` reports 0 violations.

## 2026-05-05 Contact Topology Update

The routing stage now has a generic contact-topology path for compact local shared nodes. The key
invariant is that electrical topology is drawn from physical pin contacts and component-boundary
escape ports first; labels are emitted after that topology exists.

- Circuit regions now treat the owning anchor as part of the region identity. This lets routing
  recognize local nodes that include an IC pin and nearby support parts as one circuit context.
- Compact same-net nodes with three or more nearby endpoints can route as a visible Manhattan tree
  even when the net name looks power-ish, as long as the node is local and not ground.
- Pin contacts first escape along each symbol's natural side, then join a shared route channel. This
  avoids the previous direct-pin tree failure mode where changing direction on a pin column would
  cut through vertical passives, diode bodies, inductors, or adjacent IC pins.
- Pin and existing-wire collisions are hard constraints. Existing text/field rectangles are scored
  as soft readability constraints for this topology path, so a compact local node still becomes
  visible when no text-perfect route exists yet.

Current state:

- On CM5 `/power_input_5v`, the `SW` node connecting `U1.SW`, `C4.2`, `D3.K`, and `L1.1` now emits
  one visible routed topology and one `SW` label instead of four isolated `SW` label stubs.
- The feedback divider tap stack gets ownership before generic contact topology. This keeps
  `R5.2`/`R6.1` as a direct midpoint tap and prevents the `FB` route from being drawn as a
  right-side trunk that visually resembles a `CM5_5V_IN` short.
- The chosen `SW` route still exposes the next architectural gap: field placement and route-channel
  reservation need to be solved together so local topology does not have to trade off against
  already-placed component value text.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 158 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-divider-fix` passes.
- `kicad-cli sch erc /tmp/ksch-cm5-divider-fix/cm5hudsp.kicad_sch --output /tmp/ksch-cm5-divider-fix/erc.rpt --format report` reports 2 root-sheet unconnected-wire warnings and no `/power_input_5v/` messages in the full report.

## 2026-05-05 Passive Chain Polarity Update

The compiler now has a symbol-orientation pass before routing/emission, and passive continuation
chains use it as an area-level polarity rule rather than a one-symbol cleanup.

- `PlacedSymbol` carries KiCad rotation and emission serializes that rotation. Pin-coordinate
  generation now accepts the same rotation, so routing and visual symbol orientation use one
  coordinate transform.
- Two-pin passive endpoints can be oriented with a GND-down invariant. For standalone shunts this
  keeps the ground endpoint physically below the local node.
- Passive continuation chains orient both members as one local electrical column. The source passive
  keeps its anchor-side pin fixed, its continuation pin points downward, the shunt target is placed
  below that continuation node, and the target's GND endpoint exits below the target.
- On CM5 `/power_input_5v`, the `R2`/`C3` compensation branch now reads top-to-bottom as
  `COMP -> R2 -> COMP_RC -> C3 -> GND` instead of placing the capacitor's GND endpoint above the
  local RC node.

Current state:

- This fixes the backwards-ground problem for vertical passive chains. The same rotation primitive
  also supports horizontal passive two-pin symbols, but larger mixed islands still need explicit
  region route channels and annotation ownership.
- The output-side catch diode/output/filter island is improved by rotation support, but it is still
  not a complete local island planner.

Verified after this update:

- `uv run pytest -q --ignore=tests/test_importer.py` passes with 160 tests.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- `uv run ksch compile /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /tmp/ksch-cm5-gnd-down` passes.
- `kicad-cli sch erc --output /tmp/ksch-cm5-gnd-down-erc.rpt /tmp/ksch-cm5-gnd-down/cm5hudsp.kicad_sch` reports 3 root-sheet unconnected-wire warnings and no `/power_input_5v/` messages in the full report.
