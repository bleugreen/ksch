# ksch YAML Schema v1

## Canonical Source

`.ksch.yaml` is the only source schema format. Generated `.kicad_sch` and
`.kicad_pro` files are artifacts and should be verified with `ksch verify`
before manual edits are trusted.

## Project Documents

Project documents define the root schematic and sheet tree:

- `ksch`: schema version, currently `1`.
- `project`: project metadata with `name`, optional `title`, and optional
  `kicad_version`.
- `libraries`: optional symbol and footprint library settings.
- `sheets`: child sheet instances by local child name, with optional port `connects`.
- `symbols`: root-sheet symbols by reference, with optional pin `connects`.
- `assertions`: schema-level checks reserved for verification.
- `blocks` and `use`: reusable schematic fragments reserved for expansion.

Example project-local library declaration:

```yaml
libraries:
  symbols:
    project:
      MyParts: lib/MyParts.kicad_sym
  footprints:
    project:
      MyFootprints: footprints/MyFootprints.pretty
```

Symbol ids use the declared nickname, such as `MyParts:PowerSwitch`.

## Sheet Documents

Sheet documents define one reusable or instantiated schematic sheet:

- `ksch`: schema version, currently `1`.
- `sheet`: sheet metadata with `id` and optional `title`.
- `interface`: sheet-owned ports exposed to parent sheets.
- `sheets`: nested child sheet instances, with optional port `connects`.
- `symbols`: sheet-local symbols by reference, with optional pin `connects`.
- `assertions`: sheet-local checks reserved for verification.
- `blocks` and `use`: reusable schematic fragments reserved for expansion.

## Connections

Symbols declare pin connections locally:

```yaml
symbols:
  U1:
    lib: Device:R
    connects:
      '1': +3V3
      '2': GND
```

Connection keys are pin-name first:

- `PIN_NAME`
- `PIN_NAME@PIN_NUMBER`
- `PIN_NAME/all`
- `PIN_NUMBER`

Pin-name keys are preferred. `@PIN_NUMBER` disambiguates duplicate pin names,
and `/all` intentionally expands all matching duplicate pins. Bare pin numbers
are escape hatches for symbols whose pin names are not useful.

Values are net names. The reserved value `nc` marks an intentional no-connect
and cannot be used as a net name.

Child sheet instances declare port connections locally:

```yaml
sheets:
  usb:
    source: sheets/usb.ksch.yaml
    connects:
      VBUS: +5V
```

## Interfaces

Each sheet owns its `interface`. Parent sheets connect to a child sheet with
`child_sheet.PORT`; they do not define the child interface inline. Interface
directions use the same vocabulary as schema pin directions:

- `input`
- `output`
- `bidirectional`
- `tri_state`
- `passive`
- `power_in`
- `power_out`

During KiCad emission, `power_in` and `power_out` sheet pins are emitted as
KiCad `passive` sheet pins because KiCad sheet-pin shapes do not model power
direction separately.
