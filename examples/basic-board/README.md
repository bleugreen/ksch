# Basic Board

This is a minimal external-project example: the text schema under `schematic/`
generates a KiCad schematic project under `kicad/`.

Generate the schematic:

```sh
ksch gen
```

Open `kicad/basic-board.kicad_pro` in KiCad after generation.

The local `schematic/lib/Starter.kicad_sym` library keeps the example
self-contained. Real projects can point `libraries.symbols.project` at their own
`.kicad_sym` files or use installed KiCad libraries through `--symbol-library`.
