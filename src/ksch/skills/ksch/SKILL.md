---
name: ksch
description: Use when authoring, importing, validating, compiling, or debugging ksch text schematics and generated KiCad schematic projects.
---

# ksch

`ksch` is a text-first schematic compiler for KiCad. Treat the `.ksch.yaml`
schema as source of truth once a project is initialized; generated `.kicad_sch`
files are outputs unless the task is explicitly importing an existing KiCad
project as the starting point.

## Project Workflow

Check for `ksch.toml` first. It defines:

```toml
schema = "ksch/project.ksch.yaml"
out = "."
# Optional extra libraries in the same format as --symbol-library.
symbol_library = ["Test=lib/Test.kicad_sym"]
```

Common commands:

- `ksch init`: initialize in the current directory; imports if one KiCad root is detected.
- `ksch gen`: compile the project described by `ksch.toml`.
- `ksch verify`: compile, run KiCad ERC, and compare generated output.
- `ksch doctor`: report project config, KiCad CLI, and library path readiness.
- `ksch check`: compile to a temporary directory and compare against configured output.
- `ksch validate <schema>`: validate schema, symbols, endpoints, and sheet ports.
- `ksch fmt <schema>`: normalize schema formatting.
- `ksch schema show`: print the JSON Schema for `.ksch.yaml`.
- `ksch explain <target>`: explain a library symbol, project ref, or endpoint.
- `ksch skill show`: print this skill for installation in another agent environment.

For an imported KiCad project, `out = "."` means `ksch gen` updates the actual
KiCad project directory. Do not copy from a generated scratch directory by hand.

Use `ksch verify` as the normal dogfooding gate when `kicad-cli` is available:

```bash
ksch gen
ksch verify
```

When comparing against an existing KiCad schematic during import or roundtrip
work, keep artifacts for inspection:

```bash
ksch verify --against path/to/root.kicad_sch --artifacts .ksch-verify
```

## Schema Rules

Use one schema format: YAML `.ksch.yaml`.

Core top-level keys:

- `project` on the root document, or `sheet` on child sheets.
- `libraries.symbols.project` and `libraries.footprints.project` for project-local KiCad libs.
- `interface` for sheet ports.
- `sheets` for hierarchy.
- `symbols` for placed electrical parts.
- `nets` for electrical connectivity.
- `power_flags` for intentional powered nets.
- `no_connects` for intentional NC pins.

Pin endpoints use symbol references plus pin names:

```yaml
nets:
  USB_D_P:
    - J1.D+@A6
    - J1.D+@B6
  GND:
    - U1.GND/all
```

Use `@pin_number` when duplicate pin names need one physical pin. Use `/all`
when every duplicate pin with that name is connected.

## Authoring Checks

Before writing endpoints for unfamiliar symbols:

```bash
ksch symbol info Device:R
ksch pin-search Connector:USB_C_Receptacle_USB2.0 D+
ksch explain U1.D+
```

Inside a configured project, authoring lookup uses project context
automatically: schema-declared libraries, `ksch.toml` extra symbol libraries,
and generated KiCad `sym-lib-table` entries. Use `--library NICK=PATH` only for
one-off extra libraries.

If a symbol is project-local, prefer declaring it in the schema or project
config rather than passing `--symbol-library` every time.

If validation reports a schema path such as `nets.USB_D_P[1]`, use
`ksch explain` on the referenced symbol or endpoint to inspect the actual KiCad
pins.

Edit `.ksch.yaml` directly for normal authoring, then validate and verify:

```bash
ksch validate ksch/project.ksch.yaml
ksch gen
ksch verify
```

Low-level structured edit APIs exist for tools and future semantic refactors.
They resolve aggregate endpoint expressions such as `/all` before rewriting, but
they are not the primary authoring workflow.

## Common Fixes

- `unknown symbol library id`: add the KiCad symbol library under
  `libraries.symbols.project`, or pass `--symbol-library NICK=PATH`.
- Ambiguous endpoint: use `@pin_number` or `/all`.
- Drift after manual KiCad edits: import again if KiCad is the desired source,
  otherwise edit schema and rerun `ksch gen`.
- Generated schematic readability issues belong in placement/routing/compiler
  stages, not serialization-only emission code.
