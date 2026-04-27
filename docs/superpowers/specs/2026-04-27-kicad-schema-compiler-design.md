# KiCad Schema Compiler Design

## Purpose

`kicad-schema` is a compiler-oriented KiCad workflow. Its canonical source of
truth is one strict YAML schema format, and KiCad `.kicad_sch` files are
generated artifacts.

The primary author is expected to be an agent, not a human typing every line by
hand. The design therefore prioritizes unambiguous intent, strong diagnostics,
deterministic output, and verification over terseness.

Priority order:

1. Hierarchical schematic schema and compiler.
2. Authoring tools backed by the compiler core.
3. Schema validation and verification.

The project is not an MCP server first. MCP is a thin adapter over the same core
used by the CLI.

## Core Decisions

- The schema is canonical from the start.
- There is one source schema format: strict `.ksch.yaml`.
- Hierarchical multi-sheet projects are baseline behavior, not a later feature.
- Sheet definitions own their interfaces. Parent sheet instances reference a
  child source and connect to child ports; they do not restate port directions.
- Endpoints are written by pin name by default, with optional pin-number
  disambiguation and `/all` expansion for duplicate pins.
- The compiler emits readable KiCad schematics, not only connectivity containers.
- Verification is a compiler phase and compares schema intent against KiCad's
  exported netlist and ERC output.

## Strict YAML Source

The source format is YAML, but only a small deterministic subset:

- maps, lists, strings, numbers, booleans, and nulls
- no anchors, aliases, merge keys, custom tags, or duplicate keys
- required top-level schema version, such as `ksch: 1`
- deterministic formatting through `ksch fmt`
- JSON Schema may exist for editor validation, but it is not an alternate source
  format

The canonical project flow is:

```text
project.ksch.yaml -> compiler -> generated KiCad project and .kicad_sch files
```

## Schema Shape

The top-level schema is project-oriented.

```yaml
ksch: 1

project:
  name: brain_board
  title: Brain Board
  kicad_version: "9.0"

libraries:
  symbols:
    use_global: true
    project: []
  footprints:
    use_global: true
    project: []

sheets:
  usb:
    source: sheets/usb.ksch.yaml
  power:
    source: sheets/power.ksch.yaml

symbols: {}
# Root-sheet symbols such as J1 are omitted here for brevity.

nets:
  USB_UP_DP:
    - J1.D+
    - usb.USB_UP_DP
  USB_UP_DN:
    - J1.D-
    - usb.USB_UP_DN
  +5V:
    - J1.VBUS/all
    - usb.VBUS

assertions: []
```

A child sheet is also a `.ksch.yaml` document, but defines sheet-local content.

```yaml
ksch: 1

sheet:
  id: usb
  title: USB Hub

interface:
  VBUS: power_in
  USB_UP_DP: bidirectional
  USB_UP_DN: bidirectional

symbols:
  U2:
    lib: Interface_USB:USB2514B
    value: USB2514B
    footprint: Package_DFN_QFN:QFN-36-1EP_6x6mm_P0.5mm
  J2:
    lib: Connector:USB_C_Receptacle_USB2.0_16P
    value: ESI_USB_C
    footprint: Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal

nets:
  USB_ESI_DN:
    - U2.USBDM_DN4/PRT_DIS_M4
    - J2.D-/all
  USB_ESI_DP:
    - U2.USBDP_DN4/PRT_DIS_P4
    - J2.D+/all
  VBUS:
    - J2.VBUS/all
    - U2.VBUS_DET
```

Rules:

- Sheet-local symbols, nets, labels, and no-connects live in the sheet document.
- A net name is sheet-local unless exposed through `interface`.
- Parent sheets connect to child ports with endpoints like `usb.VBUS`.
- Child `interface` entries compile to KiCad hierarchical labels in the child
  sheet and sheet pins in the parent instance.
- Separate sheet files are the normal real-project path. Inline sheets are
  permitted only for tests, examples, and compact generated fixtures.

## Endpoint Model

Endpoints are authored by pin name first.

```yaml
nets:
  USB_ESI_DN:
    - U2.USBDM_DN4/PRT_DIS_M4
    - J2.D-/all
  USB_ESI_DP:
    - U2.USBDP_DN4/PRT_DIS_P4
    - J2.D+/all
```

Endpoint forms:

- `REF.PIN_NAME`: resolves only if exactly one matching symbol pin exists.
- `REF.PIN_NAME@PIN_NUMBER`: resolves by name plus number and fails if they
  disagree.
- `REF.PIN_NAME/all`: expands to every matching pin with that name.
- `REF.PIN_NUMBER`: allowed as an escape hatch, but discouraged because it hides
  intent.
- `child_sheet.PORT`: connects a parent net to a child sheet interface port.

The resolver reports the actual KiCad pin numbers used in diagnostics and
verification output.

Example diagnostic:

```text
error: U2.GND is ambiguous in sheets/usb.ksch.yaml
matches:
  U2.GND@12
  U2.GND@25
  U2.GND@EP
use U2.GND/all if all should connect to the same net
```

## Reuse

Reusable blocks are optional. The common path should be direct schema. Blocks
exist only when repetition becomes a real problem.

Blocks are YAML fragments with simple parameters that expand into concrete
sheets, symbols, nets, and assertions before validation. After expansion, the
compiler behaves as if the expanded content was written directly.

```yaml
blocks:
  usb_downstream_port:
    params:
      index: int
      connector_ref: ref
      hub_ref: ref
    symbols:
      ${connector_ref}:
        lib: Connector:USB_C_Receptacle_USB2.0_16P
        value: USB_PORT_${index}
    nets:
      USB_DN${index}_DP:
        - ${hub_ref}.USBDP_DN${index}/PRT_DIS_P${index}
        - ${connector_ref}.D+/all
      USB_DN${index}_DN:
        - ${hub_ref}.USBDM_DN${index}/PRT_DIS_M${index}
        - ${connector_ref}.D-/all

use:
  - block: usb_downstream_port
    as: port1
    with:
      index: 1
      connector_ref: J3
      hub_ref: U2
```

Rules:

- Expansion is deterministic.
- `ksch expand` shows the expanded schema.
- Blocks cannot hide unresolved symbols, pins, nets, or assertions.
- Blocks may be local or imported from another `.ksch.yaml` file.
- Parameters should stay simple: `ref`, `net`, `string`, `int`, `bool`, and
  `enum`.
- No general-purpose programming language is part of schema v1.

## Compiler Architecture

The expanded hierarchical project graph is the compiler's truth.

```text
strict YAML
  -> parse source model
  -> expand reusable blocks/includes
  -> build explicit hierarchical project IR
  -> resolve symbols, footprints, pins, pads
  -> validate graph and sheet interfaces
  -> solve schematic placement and routing
  -> emit deterministic KiCad project files
  -> run netlist, ERC, assertion, and drift verification
```

Core packages:

- `schema`: strict YAML loader, duplicate-key detection, versioning, formatter.
- `model`: typed project, sheet, symbol, net, interface, no-connect, and
  assertion IR.
- `libraries`: KiCad global/project library-table parser plus symbol and
  footprint index.
- `resolver`: library IDs, references, pin-name endpoints, footprint filters,
  and pad compatibility.
- `layout`: sheet-aware schematic layout using real symbol pin geometry.
- `emit`: deterministic `.kicad_pro`, `.kicad_sch`, and optional project tables.
- `verify`: `kicad-cli` netlist export, ERC JSON, assertions, SVG/readability
  checks, and drift detection.
- `cli`: local terminal interface.
- `mcp`: agent-facing adapter over the same core.

## Library And Endpoint Resolution

Resolution happens after block expansion and before layout.

Resolver responsibilities:

- Load global and project KiCad symbol and footprint library tables.
- Resolve every symbol `lib` ID to an actual `.kicad_sym` entry.
- Extract symbol pins, including names, numbers, units, hidden status,
  electrical type, and graphical positions.
- Resolve every endpoint by pin name with `@number` and `/all` support.
- Resolve footprints from `.pretty/*.kicad_mod`.
- Extract footprint pads and basic pad metadata.
- Check symbol footprint filters and pin/pad compatibility where possible.
- Validate parent connections to child sheet `interface` ports.
- Reject unknown refs, nets, pins, symbols, footprints, duplicate refs in a
  sheet path, ambiguous endpoints, and mismatched disambiguators.

Authoring commands use this same resolver:

- `ksch symbols search <query>`
- `ksch symbol info <lib_id>`
- `ksch footprints search <query>`
- `ksch footprint info <footprint_id>`
- `ksch compatible-footprints <symbol_id>`
- `ksch pin-search <symbol_or_ref> <query>`
- `ksch resolve-endpoint <endpoint>`

## Layout And Emission

The compiler should emit reviewable KiCad-native schematics.

Layout behavior:

- Place sheets from the explicit sheet tree.
- In each sheet, identify anchors such as main ICs, connectors, regulators,
  oscillators, and high-pin-count devices.
- Place local support components near the pins and nets they serve.
- Prefer direct wires for short local connections.
- Use labels for sheet ports, long-range nets, crowded power rails, and
  connector exits.
- Collapse repeated power, ground, and VBUS connections into local shared rails
  when components are visually grouped.
- Preserve differential pair naming and polarity.
- Use actual symbol, pin, and text bounding boxes to avoid overlaps.
- Emit explicit no-connect markers for intentional no-connects.
- Keep placement stable from schema paths and IDs.

Emission responsibilities:

- Generate `.kicad_pro`.
- Generate one `.kicad_sch` per sheet.
- Embed the used `lib_symbols` in each schematic.
- Derive symbol, wire, sheet, label, pin, and instance UUIDs from stable schema
  paths.
- Avoid noisy timestamps.
- Mark generated files with generator metadata.
- Preserve deterministic s-expression formatting.

The schema should not contain geometry by default. A future explicit placement
escape hatch may be added, but only for cases where the layout solver cannot
produce a readable sheet.

## Verification

Verification is a normal compiler phase.

`ksch verify` should:

1. Run `kicad-cli sch export netlist --format kicadsexpr`.
2. Parse the exported netlist into a normalized graph.
3. Compare exported connectivity to resolved schema intent.
4. Run schema assertions.
5. Run `kicad-cli sch erc --format json`.
6. Optionally export SVG and run basic readability checks.

Example assertions:

```yaml
assertions:
  - net: USB_ESI_DP
    contains:
      - U2.USBDP_DN4/PRT_DIS_P4
      - J2.D+/all

  - differential_pair:
      p: USB_ESI_DP
      n: USB_ESI_DN

  - footprint:
      ref: U2
      is: Package_DFN_QFN:QFN-36-1EP_6x6mm_P0.5mm

  - no_unresolved_endpoints: true
```

Drift detection:

- Generated files must be reproducible from schema.
- `ksch check` compiles into a temporary directory and compares against current
  generated files.
- Manual edits to generated `.kicad_sch` files are reported as drift.
- PCB footprint assignments that no longer match generated schematic assignments
  are reported as stale sync risk.
- Generated files include stable metadata identifying the source schema path and
  compiler version.

Verification output should be machine-readable by default, with concise text
summaries.

```text
verify failed:
- net USB_ESI_DP missing J2.D+/all in KiCad export
- ERC warning: assigned footprint does not match symbol filter at U2
- generated sheets/usb.kicad_sch differs from schema output
```

## External Interfaces

The CLI is the primary user-facing interface:

- `ksch init`
- `ksch fmt`
- `ksch expand`
- `ksch validate`
- `ksch compile`
- `ksch verify`
- `ksch check`
- `ksch symbols search`
- `ksch symbol info`
- `ksch footprints search`
- `ksch footprint info`
- `ksch compatible-footprints`
- `ksch pin-search`
- `ksch resolve-endpoint`

The MCP server exposes the same operations as tools, without duplicating compiler
logic.

## Implementation Notes

The initial implementation should be a full compiler architecture with tests
around each boundary. Flat single-sheet fixtures are acceptable for tests, but
they must not define the architecture.

Use local KiCad installation data and project/global library tables rather than
memory. KiCad IPC should not be a core dependency for now because KiCad 9/10 IPC
is GUI-coupled and PCB-focused. `kicad-cli` is appropriate for verification
operations such as netlist export, ERC, and SVG export.

The existing `/Users/mitch/projects/kicad-mcp` repo should remain distinct. It
contains useful ideas around exported netlists, graph queries, and MCP tool
surfaces, but it is analyzer-oriented and currently dirty. Reuse concepts after
the compiler boundaries are stable rather than merging the repositories.

## Open Issues To Revisit

- Exact strict YAML parser and formatter library.
- The complete schema grammar for no-connects, fields, sheet metadata, and
  assertions.
- How much symbol-footprint compatibility can be checked from available library
  metadata.
- Layout heuristic details and whether to use an external constraint solver.
- SVG readability checks beyond basic overlap and dangling-label detection.
- Import path for an existing manually drawn KiCad project into `.ksch.yaml`.
