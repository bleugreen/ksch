# ksch CLI

## Initialize A Project

```bash
ksch init my-board
cd my-board
ksch gen
```

Creates a starter external project:

- `schematic/project.ksch.yaml`: canonical text schema
- `schematic/lib/Starter.kicad_sym`: small project-local symbol library
- `scripts/gen-schematic.sh`: one-command KiCad generation wrapper
- `ksch.toml`: project-local tool config
- `kicad/`: generated KiCad project directory

Initialize schema files inside an existing KiCad project:

```bash
cd existing-board
ksch init
```

When `ksch init` finds one existing KiCad root schematic in the project
directory or an immediate child directory, it asks whether to import it. The
command imports the existing schematic tree into `ksch/`, writes `ksch.toml`,
and creates `scripts/gen-ksch-schematic.sh`. The generated output target is the
existing KiCad project directory, so future generation updates the real KiCad
schematics rather than a copy.

`ksch.toml` is the project-root contract:

```toml
schema = "ksch/project.ksch.yaml"
out = "."
# Optional extra libraries in the same format as --symbol-library.
symbol_library = ["Test=lib/Test.kicad_sym"]
```

Run `ksch gen` from the project root to compile the configured schema to the
configured KiCad output.

## Diagnose Project Context

```bash
ksch doctor
ksch doctor --config path/to/ksch.toml
```

Reports whether `kicad-cli` is available, whether `ksch.toml` and the schema
load correctly, and whether discovered symbol and footprint library paths exist.
Discovery includes schema-declared project libraries, `ksch.toml`
`symbol_library` entries, and generated KiCad `sym-lib-table` / `fp-lib-table`
entries from the configured output directory.

## Verify Generated KiCad

```bash
ksch verify
ksch verify --against path/to/original.kicad_sch --artifacts .ksch-verify
```

Reads `ksch.toml`, compiles the configured schema, runs KiCad ERC on the
generated root schematic, and compares generated files against the configured
output. `--against` exports KiCad netlists for the original schematic and the
generated schematic, then compares connectivity. `--artifacts` keeps the
generated verification project, ERC report, and netlists.

Use this as the normal dogfooding gate after editing schema:

```bash
ksch gen
ksch verify
```

## Validate

```bash
ksch validate project.ksch.yaml \
  --symbol-library Test=path/to/Test.kicad_sym
```

Validates the root project document, referenced sheet documents, symbol library
ids, endpoint references, duplicate pin-name disambiguation, and sheet ports.
Project-local libraries declared under `libraries.symbols.project` are loaded
automatically. Semantic validation errors include schema paths such as
`symbols.J1.connects.D+@A6` or `symbols.U1.lib`.

## JSON Schema

```bash
ksch schema show
```

Prints the canonical JSON Schema for `.ksch.yaml` documents. Use it for editor
configuration, external validators, and agent context.

## Format

```bash
ksch fmt project.ksch.yaml
ksch fmt project.ksch.yaml --check
```

Formats schema YAML with deterministic top-level key ordering.

## Expand

```bash
ksch expand project.ksch.yaml
```

Prints the expanded sheet paths.

## Compile

```bash
ksch compile project.ksch.yaml \
  --out generated \
  --symbol-library Test=path/to/Test.kicad_sym
```

Generates deterministic KiCad project and schematic files.

## Generate From Config

```bash
ksch gen
ksch gen --config path/to/ksch.toml
```

Reads `ksch.toml` and compiles `schema` into `out`. Init-generated shell scripts
delegate to this command.

Project-local libraries can be declared in the schema:

```yaml
libraries:
  symbols:
    project:
      MyParts: lib/MyParts.kicad_sym
```

Then symbols can reference `MyParts:PartName` without passing
`--symbol-library`.

## Check Drift

```bash
ksch check
ksch check project.ksch.yaml \
  --out generated \
  --symbol-library Test=path/to/Test.kicad_sym
```

Regenerates into a temporary directory and reports differences from the current
generated output. With no arguments it reads `ksch.toml`.

`ksch check` is the fast generated-file comparison. Prefer `ksch verify` when
`kicad-cli` is available, because it also runs ERC and can compare netlist
connectivity.

## Skill Material

```bash
ksch skill show
```

Prints the bundled Codex skill covering ksch workflow and schema conventions.

## Authoring Lookup

```bash
ksch symbols search USB --library Test=path/to/Test.kicad_sym
ksch symbol info Test:USB_C --library Test=path/to/Test.kicad_sym
ksch pin-search Test:USB_C D+ --library Test=path/to/Test.kicad_sym
ksch explain Test:USB_C --library Test=path/to/Test.kicad_sym
ksch explain U1.USBDP_UP
```

Lookup commands read actual KiCad symbol libraries. Inside a configured project
they use project context automatically: schema-declared libraries, `ksch.toml`
extra symbol libraries, and generated KiCad `sym-lib-table` entries. Use
`--library NICK=PATH` for one-off extra libraries.

`ksch explain` accepts a library symbol id, a project ref, or a project endpoint
and prints the concrete symbol/pin information used by validation.

## Net Audit

```bash
ksch net +3V3
```

Prints the resolved endpoints on one net as compact YAML.

## Migration

```bash
ksch migrate-connects project.ksch.yaml
```

Rewrites legacy top-level `nets` and `no_connects` into symbol-local and
sheet-instance-local `connects`.

## Low-Level Structured Edits

```bash
ksch edit add-symbol R1 Device:R --value 10k
ksch edit add-symbol C1 Device:C --value 100nF --sheet /power
ksch edit connect RESET R1.1 U1.RESET
ksch edit connect +3V3 C1.1 --sheet /power
```

For human authoring, prefer editing `.ksch.yaml` directly, then running
`ksch validate`, `ksch gen`, and `ksch verify`. The low-level edit commands are
mainly for tools and agents that need narrow validated mutations instead of
open-ended YAML writes.

Structured edits load the configured project graph, validate symbol libraries
and endpoints, reject cross-net conflicts, then rewrite the affected schema
sheet with deterministic formatting. The internal edit core resolves aggregate
endpoint expressions such as `/all` to physical pin keys before rewriting. With
no `--schema`, edit commands read the root schema from `ksch.toml`.
