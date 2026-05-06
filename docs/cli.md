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

## Validate

```bash
ksch validate project.ksch.yaml \
  --symbol-library Test=path/to/Test.kicad_sym
```

Validates the root project document, referenced sheet documents, symbol library
ids, endpoint references, duplicate pin-name disambiguation, and sheet ports.
Project-local libraries declared under `libraries.symbols.project` are loaded
automatically.

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
```

Lookup commands read actual KiCad symbol libraries. They are intended for
authoring and agent use before endpoints are written.
