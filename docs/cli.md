# ksch CLI

## Validate

```bash
ksch validate project.ksch.yaml
```

Validates the root project document and referenced sheet documents.

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

## Check Drift

```bash
ksch check project.ksch.yaml \
  --out generated \
  --symbol-library Test=path/to/Test.kicad_sym
```

Regenerates into a temporary directory and reports differences from the current
generated output.

## Authoring Lookup

```bash
ksch symbols search USB --library Test=path/to/Test.kicad_sym
ksch symbol info Test:USB_C --library Test=path/to/Test.kicad_sym
ksch pin-search Test:USB_C D+ --library Test=path/to/Test.kicad_sym
```

Lookup commands read actual KiCad symbol libraries. They are intended for
authoring and agent use before endpoints are written.
