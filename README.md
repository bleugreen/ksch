# kicad-schema

Canonical, text-first schematic authoring for KiCad.

This project is a compiler-oriented KiCad workflow. The goal is to make the schematic's durable source of truth a structured text schema that defines symbols, pins, nets, fields, footprints, no-connects, interfaces, and sheet hierarchy. KiCad `.kicad_sch` files are generated artifacts.

## Core Decision

The schema is canonical from the start.

That means:

- `.kicad_sch` files are generated output.
- Manual schematic edits in KiCad are drift unless explicitly imported.
- The schema owns components, refs, symbols, pins, nets, fields, footprints, no-connects, and sheet grouping.
- The compiler emits deterministic KiCad files from the same schema and same libraries.
- Stable IDs/UUIDs should be derived from canonical schema IDs so generated KiCad diffs do not churn.
- Verification compares schema intent against generated KiCad output through netlist export, ERC, and assertions.

The project is not intended to be just an MCP server. It should have a core compiler and CLI that work without an agent session. MCP is an adapter for agent-assisted authoring.

## Current Docs

- [Schema v1](docs/schema-v1.md)
- [CLI](docs/cli.md)
- [Compiler design spec](docs/superpowers/specs/2026-04-27-kicad-schema-compiler-design.md)
- [Implementation plan](docs/superpowers/plans/2026-04-27-kicad-schema-compiler.md)

## What The Schema Should Be

Keep the schema close to EDA primitives:

- Symbols
- References
- Values and fields
- Footprints
- Pins
- Nets
- No-connects
- Sheets/groups
- Optional assertions

Do not make the schema depend on a large typed archetype registry such as `usb2_host_ports`, `esi_audio`, or `ordered_interface_group`. Those abstractions create parsing and maintenance burden, and they force the user to teach the schema things that are already implied by symbols, pins, nets, and repeated graph structure.

Example direction:

```yaml
symbols:
  U2:
    lib: Interface_USB:USB2514B
    value: USB2514B
    footprint: Package_QFN:QFN-36-1EP_6x6mm_P0.5mm
  J2:
    lib: Connector:USB_C_Receptacle_USB2.0_16P
    value: ESI_USB_C
    footprint: Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal

nets:
  USB_ESI_DN: [U2.8, J2.A7, J2.B7]
  USB_ESI_DP: [U2.9, J2.A6, J2.B6]
  USB_ESI_VBUS: [U4.10, J2.A4, J2.A9, J2.B4, J2.B9]
```

Pin numbers are allowed only because they are resolvable against a real KiCad symbol. The authoring tools should also support pin-name lookup so the writer does not guess magic numbers.

## Authoring Oracle

The missing piece for agent use is not just validation after writing. The agent needs library lookup while writing.

Required authoring operations:

- `find-symbol <query>`
- `symbol-info <lib_id>`
- `find-footprint <query>`
- `footprint-info <footprint_id>`
- `compatible-footprints <symbol_id>`
- `pin-search <symbol_or_ref> <query>`
- `resolve-endpoint <ref.pin_name_or_number>`

The tool should answer from actual KiCad project/global libraries, not memory. For example:

```text
symbol-info Interface_USB:USB2514B
```

Should return:

```text
pins:
  8  USBDM_DN4/PRT_DIS_M4  bidirectional
  9  USBDP_DN4/PRT_DIS_P4  bidirectional
footprints:
  suggested: Package_QFN:QFN-36-1EP_6x6mm_P0.5mm
```

Only after that should an agent write endpoints such as `U2.8`.

The compiler should still validate every endpoint and fail before generating KiCad output if anything cannot be resolved.

## Layout Solver

The layout problem should be handled by the compiler/rendering layer, not by adding prose-like layout instructions to the schema.

The renderer should infer good schematic layout from:

- symbol geometry from KiCad libraries
- pin positions and pin electrical types
- net graph structure
- component category and reference prefix
- repeated connections to the same rails
- local two-pin support components around IC pins
- sheet/group membership

Expected renderer behavior:

- Place major ICs/connectors as anchors.
- Place local passives near the pins/nets they support.
- Use direct wires for short local connections.
- Use labels for long-range nets, sheet boundaries, and connectors.
- Collapse repeated VBUS/GND/power labels into shared rails where visually local.
- Avoid overlaps using actual symbol and text bounding boxes.
- Preserve differential pair polarity and names.
- Produce valid KiCad schematic files that pass ERC and match schema assertions.

The schema may have light grouping to control sheet organization, but it should not encode geometry like "right of U2" or "below connector stack" unless a real escape hatch is needed.

## Architecture

Recommended shape:

```text
kicad-schema/
  core/
    schema parser
    KiCad symbol/footprint library indexer
    endpoint resolver
    net validator
    layout solver
    .kicad_sch emitter
    verification runner

  cli/
    ksch init
    ksch symbols search USB2514
    ksch symbol info Interface_USB:USB2514B
    ksch footprints search USB_C
    ksch footprint info Connector_USB:...
    ksch validate design.yml
    ksch compile design.yml --out path/to/project
    ksch verify design.yml

  mcp/
    exposes the same core operations as agent tools

  schemas/
    JSON Schema for canonical YAML
```

The MCP server should be a thin adapter over the same core library used by the CLI.

## Verification Model

Compilation should have a tight feedback loop:

1. Parse schema.
2. Resolve libraries, symbols, footprints, and endpoints.
3. Validate symbol pin and footprint pad compatibility where possible.
4. Generate deterministic KiCad schematic files.
5. Export KiCad netlist with `kicad-cli`.
6. Compare exported netlist to schema intent.
7. Run schema assertions.
8. Run ERC.
9. Optionally export SVG and run readability checks.

If generated KiCad files already exist, the tool should detect drift:

- generated output differs from current files
- current files contain manual edits not represented in schema
- PCB is stale relative to schematic-generated footprint assignments

## Relationship To Existing `kicad-mcp`

There is an existing project at `/Users/mitch/projects/kicad-mcp`.

Its current shape is mostly an MCP server for analyzing existing KiCad schematics:

- exports schematic netlists through `kicad-cli`
- parses netlists with `kinparse`
- builds a NetworkX graph of components and nets
- provides component, net, pin, and path queries
- supports multi-board signal tracing
- has a config/cache layer for named boards and systems
- includes datasheet lookup support

Useful pieces for `kicad-schema`:

- The idea of a normalized `Component`, `Net`, and `Netlist` model.
- The use of `kicad-cli sch export netlist` as an authoritative connectivity source.
- Graph queries over components/nets.
- Netlist diff concepts in `config.py`.
- MCP tool surface patterns.
- Multi-board tracing may become useful later for harnesses or multi-board products.

What it does not yet provide:

- KiCad symbol library indexing.
- KiCad footprint library indexing.
- Symbol pin lookup before authoring.
- Footprint pad lookup and symbol-footprint compatibility checks.
- Canonical schema parsing.
- Deterministic KiCad schematic generation.
- Layout solving.
- Generated-file drift detection.

Recommendation: keep them distinct for now.

`kicad-mcp` is currently an analyzer for existing KiCad files. `kicad-schema` should start as a compiler and authoring system. The overlap should be handled by extracting or copying specific reusable ideas only after the compiler shape is clearer.

A likely future split:

- `kicad-schema-core`: schema, library index, resolver, compiler, verifier
- `kicad-schema-cli`: local terminal interface
- `kicad-schema-mcp`: agent-facing adapter
- optional import from `kicad-mcp`: graph/netlist analysis code, after cleanup

Do not merge the repos immediately. Merging now would mix an analyzer-oriented MCP project with a compiler-oriented source-of-truth project before the core boundaries are stable.

## Notes From Inspecting `kicad-mcp`

The repo currently has uncommitted changes. It should be treated as dirty and not modified casually.

Observed files:

- `src/kicad_mcp/circuit_graph.py`: netlist export/parsing and NetworkX graph model.
- `src/kicad_mcp/server.py`: MCP tool definitions and request handling.
- `src/kicad_mcp/config.py`: config, cache, and netlist diff logic.
- `src/kicad_mcp/multi_board_graph.py`: cross-board signal graph.
- `src/kicad_mcp/datasheet_lookup.py`: DuckDuckGo-backed datasheet search/cache.
- `tests/test_server.py`: a small async MCP handler test tied to a local KiCad project path.

One caution: the current working tree appears mid-edit, and `server.py` contains an invalid split assignment around datasheet field extraction. That should be fixed in `kicad-mcp` before reusing it directly.

## Current Implementation Plan

The project design is captured in
[`docs/superpowers/specs/2026-04-27-kicad-schema-compiler-design.md`](docs/superpowers/specs/2026-04-27-kicad-schema-compiler-design.md).

Implementation is tracked in
[`docs/superpowers/plans/2026-04-27-kicad-schema-compiler.md`](docs/superpowers/plans/2026-04-27-kicad-schema-compiler.md).
