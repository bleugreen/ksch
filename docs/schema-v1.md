# ksch YAML Schema v1

## Canonical Source

`.ksch.yaml` is the only source schema format. Generated `.kicad_sch` and
`.kicad_pro` files are artifacts and should be checked with `ksch check` before
manual edits are trusted.

## Project Documents

Project documents define the root schematic and sheet tree:

- `ksch`: schema version, currently `1`.
- `project`: project metadata with `name`, optional `title`, and optional
  `kicad_version`.
- `libraries`: optional symbol and footprint library settings.
- `sheets`: child sheet instances by local child name.
- `symbols`: root-sheet symbols by reference.
- `nets`: root-sheet net names mapped to endpoint lists.
- `no_connects`: intentionally open endpoints.
- `assertions`: schema-level checks reserved for verification.
- `blocks` and `use`: reusable schematic fragments reserved for expansion.

## Sheet Documents

Sheet documents define one reusable or instantiated schematic sheet:

- `ksch`: schema version, currently `1`.
- `sheet`: sheet metadata with `id` and optional `title`.
- `interface`: sheet-owned ports exposed to parent sheets.
- `sheets`: nested child sheet instances.
- `symbols`: sheet-local symbols by reference.
- `nets`: sheet-local net names mapped to endpoint lists.
- `no_connects`: intentionally open endpoints.
- `assertions`: sheet-local checks reserved for verification.
- `blocks` and `use`: reusable schematic fragments reserved for expansion.

## Endpoints

Endpoints are pin-name first:

- `REF.PIN_NAME`
- `REF.PIN_NAME@PIN_NUMBER`
- `REF.PIN_NAME/all`
- `REF.PIN_NUMBER`
- `child_sheet.PORT`

Pin-name endpoints are preferred. `@PIN_NUMBER` disambiguates duplicate pin
names, and `/all` intentionally expands all matching duplicate pins. Bare pin
numbers are escape hatches for symbols whose pin names are not useful.

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
