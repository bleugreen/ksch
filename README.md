# ksch

Text-first schematic compiler for KiCad projects.

`ksch` turns canonical YAML schematic schemas into deterministic KiCad
projects. The schema owns symbols, refs, values, fields, footprints, pins, nets,
no-connects, power flags, sheet interfaces, and hierarchy. KiCad `.kicad_sch`
files are generated output.

The package includes:

- a CLI for local authoring and generation
- a schema importer for existing KiCad projects
- KiCad symbol and footprint library indexing
- project-aware library discovery and diagnostics
- endpoint resolution and schema validation
- generated-file drift checks, KiCad ERC checks, and netlist parity checks
- a layout/compiler pipeline that emits KiCad schematic files
- a thin MCP adapter for agent-assisted workflows
- a bundled Codex skill for using the tool in other projects

## Install

From a checkout:

```bash
uv sync
uv run ksch --help
```

As a local CLI tool:

```bash
uv tool install .
ksch --help
```

Some operations, including KiCad import and netlist-based roundtrip checks,
require `kicad-cli` on `PATH`.

## Quick Start

Create a new schema-owned KiCad project:

```bash
ksch init my-board
cd my-board
ksch gen
```

This creates:

```text
my-board/
  ksch.toml
  schematic/project.ksch.yaml
  schematic/lib/Starter.kicad_sym
  scripts/gen-schematic.sh
  kicad/
```

`ksch.toml` records the project contract:

```toml
schema = "schematic/project.ksch.yaml"
out = "kicad"
```

Generate from the project root with `ksch gen`. Verify the generated KiCad
project with `ksch verify`.

## Import Existing KiCad

Run `ksch init` inside an existing KiCad project:

```bash
cd existing-board
ksch init
ksch gen
```

If the directory contains one KiCad root schematic, `ksch init` offers to import
it. The imported schema is written to `ksch/project.ksch.yaml`; generation
targets the existing KiCad project directory:

```toml
schema = "ksch/project.ksch.yaml"
out = "."
```

That means `ksch gen` updates the actual schematic project. There is no separate
generated copy to hand-copy back into KiCad.

`ksch init` also detects the common repo shape where the current directory has
one immediate child KiCad project. In that case `out` points at that child
project directory.

## CLI

Common commands:

```bash
ksch init
ksch gen
ksch verify
ksch doctor
ksch check
ksch validate schematic/project.ksch.yaml
ksch fmt schematic/project.ksch.yaml
ksch schema show
ksch explain U1.USBDP_UP
ksch compile schematic/project.ksch.yaml --out kicad
ksch import board.kicad_sch --out ksch
ksch symbols search USB
ksch symbol info Device:R
ksch pin-search Connector:USB_C_Receptacle_USB2.0 D+
ksch skill show
```

`ksch gen`, bare `ksch verify`, bare `ksch check`, `ksch doctor`, and the
authoring lookup commands read `ksch.toml`. Explicit `compile` and `import`
commands are still available for scripts and one-off conversions.

See [docs/cli.md](docs/cli.md) for command details.

## Schema

The schema is YAML. A root document has `project`; child sheets have `sheet`.

```yaml
ksch: 1
project:
  name: usb-demo

libraries:
  symbols:
    project:
      Local: lib/Local.kicad_sym

symbols:
  J1:
    lib: Connector:USB_C_Receptacle_USB2.0_16P
    value: USB_IN
  U1:
    lib: Interface_USB:USB2514B
    value: USB2514B
    footprint: Package_QFN:QFN-36-1EP_6x6mm_P0.5mm

nets:
  USB_D_P:
    - J1.D+@A6
    - J1.D+@B6
    - U1.USBDP_UP
  GND:
    - J1.GND/all
    - U1.GND/all
```

Endpoint syntax uses symbol references plus pin names. Use `@pin_number` when a
symbol has duplicate pin names and only one physical pin is connected. Use
`/all` when all pins with that name are connected.

Project-local KiCad libraries can be declared in the schema:

```yaml
libraries:
  symbols:
    project:
      MyParts: lib/MyParts.kicad_sym
  footprints:
    project:
      MyFootprints: lib/MyFootprints.pretty
```

See [docs/schema-v1.md](docs/schema-v1.md) for the schema reference.

## Authoring Workflow

Use the library lookup commands before writing endpoints for unfamiliar parts:

```bash
ksch symbols search USB2514
ksch symbol info Interface_USB:USB2514B
ksch pin-search Interface_USB:USB2514B USBDP
ksch explain U1.USBDP_UP
```

Inside a configured project, lookup commands use schema-declared libraries,
`ksch.toml` extra libraries, and generated KiCad library tables automatically.
Use `--library NICK=PATH` only for one-off extra libraries.

Use `ksch schema show` to print the canonical JSON Schema for editor
configuration and external tooling.

The compiler validates symbol library ids, endpoint references, duplicate pin
disambiguation, sheet ports, and no-connect endpoints before writing KiCad
output.

Edit `.ksch.yaml` directly as the normal authoring workflow, then validate and
verify:

```bash
ksch validate schematic/project.ksch.yaml
ksch gen
ksch verify
```

The package also contains a structured edit core for tool, agent, and future
semantic refactor operations. That layer validates candidate changes before
writing, resolves aggregate endpoint expressions such as `/all` to physical pin
keys, supports symbol/net renames across coupled schema locations, and rejects
connected no-connect endpoints. It is not the primary human authoring interface.

For agent environments, print the bundled skill:

```bash
ksch skill show
```

That output is a compact `SKILL.md` covering the project workflow, schema
conventions, and common fixes.

## Generated Layout

`ksch` emits complete KiCad schematic files, including symbols, labels, wires,
junctions, hierarchical sheets, power flags, no-connects, library tables, and
project files.

The layout pipeline uses KiCad symbol geometry, pin positions, net graph
structure, component refs, local two-pin topology, and sheet structure. It keeps
local support passives near the pins they support, routes compact local nets
directly, uses labels for long-range connectivity, and checks for avoidable
symbol/text/wire overlaps before emission.

The generated schematic is meant to be usable and deterministic. Manual cleanup
in KiCad becomes drift unless it is imported back into schema.

## Verification

Useful verification commands:

```bash
ksch doctor
ksch verify
ksch verify --against path/to/original.kicad_sch --artifacts .ksch-verify
uv run pre-commit install
uv run pre-commit run --all-files
./scripts/test.sh
./scripts/check.sh
```

`ksch verify` compiles the configured schema, runs KiCad ERC on the generated
root schematic, and compares generated files against the configured output. Use
`--against` to compare generated netlist connectivity against an existing KiCad
root schematic. Use `--artifacts` to keep the generated verification project,
ERC report, and netlists for inspection.

The pre-commit hooks run `ruff`, `mypy`, and a fast CLI/package test slice.
`./scripts/test.sh` runs the full test suite. `./scripts/check.sh` runs the
full local gate: lint, typecheck, tests, and package build.

The test suite includes KiCad CLI integration tests where `kicad-cli` is
available, importer roundtrip smoke tests, layout tests, and package build
coverage. Run `ksch validate`, `ksch gen`, and `ksch verify` inside generated
projects while dogfooding schemas.

## Examples And Docs

- [Basic board example](examples/basic-board)
- [CLI reference](docs/cli.md)
- [Schema v1 reference](docs/schema-v1.md)
- [Compiler design spec](docs/superpowers/specs/2026-04-27-kicad-schema-compiler-design.md)

## Architecture

The implementation is split into compiler stages:

```text
schema loader -> source model -> project IR -> resolver
  -> placement/routing/layout validation -> KiCad emitter
```

Important modules:

- `ksch.schema`: YAML loading and formatting
- `ksch.model`: source and IR models
- `ksch.kicad`: KiCad library, symbol, footprint, and S-expression helpers
- `ksch.resolver`: endpoint and net resolution
- `ksch.graph`, `ksch.edit`: project graph indexing and structured schema edits
- `ksch.placement`, `ksch.net_routing`, `ksch.layout_problem`: schematic layout
- `ksch.compiler`: placed-project construction and generation orchestration
- `ksch.emit`: serialization of placed objects to KiCad files
- `ksch.importer`: KiCad schematic to schema conversion
- `ksch.verify`: generated output and netlist comparison
- `ksch.mcp`: optional agent-facing adapter

The MCP server is an adapter over the same package used by the CLI. The compiler
and CLI work without an agent session.
