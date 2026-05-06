# CM5 HUDSP Exact Round-Trip Parity

## Status

Resolved for connectivity and ERC errors.

Current verification on `/Users/mitch/projects/cm5-hudsp/cm5hudsp/cm5hudsp.kicad_sch`:

- Schematic import compiles back to KiCad.
- KiCad `kicadsexpr` connectivity signatures match exactly: 0 missing, 0 extra.
- Generated ERC reports 0 errors.
- Generated ERC still reports 4 warnings for `MountingHole:MountingHole_3.2mm_M3_DIN965_Pad`;
  the original project reports the same footprint-library warning class.

## Fixes Landed

- Import uses KiCad's own `kicadsexpr` netlist export as the source of resolved connectivity.
- Schema libraries now preserve KiCad library nicknames with `libraries.symbols.project`.
- Imported symbols preserve multi-unit references with `symbols.<ref>.units`.
- The emitter places child sheets using cumulative sheet heights to avoid overlapping sheet pins.
- The emitter places multiple symbol units for the same reference instead of collapsing every ref to unit 1.
- Generated fixture schematic round-trips with exact connectivity parity.
- KiCad inherited symbols using `(extends ...)` are flattened during library indexing so derived symbols
  keep base pins and override fields.
- Imported KiCad `power:PWR_FLAG` pseudo-symbols are preserved as schema `power_flags` and re-emitted as
  KiCad-native ERC power drivers.
- Large-sheet placement now checks existing pin and label-stub coordinates before wrapping passive columns
  back across IC lanes, preventing generated shorts such as `CM5_5V_IN` to `HUB_3V3`/GND.
- Direct local routes emit a visible label plus hidden endpoint labels so KiCad netlist export preserves
  the intended far endpoint on routed two-pin nets.
- `/all` duplicate-pin endpoints now resolve to pin-number-specific endpoint identities, preventing
  interface filtering from dropping duplicate pins such as `Module1.+3.3v_(Output)@86`.
- `test_import_cm5_hudsp_roundtrip_smoke` now asserts exact connectivity equality, no ERC errors, and
  absence of generated net-name collisions, dangling wires, and unconnected pins.

## Remaining Follow-Up

- Decide whether the compiler should infer or synthesize global footprint-library entries for stock
  footprint libraries such as `MountingHole`, or leave this as an upstream project/library-table warning.
