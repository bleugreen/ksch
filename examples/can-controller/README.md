# CAN Controller

This is a self-contained `ksch` north-star example for a single-sheet CAN controller schematic organized with declared functional blocks.

Generate the KiCad project:

```sh
ksch gen
```

The source schema is `schematic/project.ksch.yaml`. The project-local symbol library in `schematic/lib/CanController.kicad_sym` keeps the example independent of globally installed KiCad libraries.
