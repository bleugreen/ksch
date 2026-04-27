# CM5 HUDSP exact round-trip parity

The KiCad schematic importer can import `/Users/mitch/projects/cm5-hudsp/cm5hudsp/cm5hudsp.kicad_sch`,
compile the generated schema back to KiCad, and export a valid KiCad netlist. The current parity smoke
test holds the residual at or below:

- 11 original connectivity signatures missing from the round trip
- 3 extra generated connectivity signatures

The remaining gaps are concentrated in a small set of local/power nets, for example:

- `C1.1/C2.1/D12.2/Q1.2/Q2.2/R3.1/R36.1/U1.2`
- `C1.2/C10.2/C11.2/.../C21.2/...` ground-like aggregate
- `J2.A6/J2.B6/U2.4` merged with `Module1.103/U2.30`

Work already done:

- Import uses KiCad's own `kicadsexpr` netlist export as the source of resolved connectivity.
- Schema libraries now preserve KiCad library nicknames with `libraries.symbols.project`.
- Imported symbols preserve multi-unit references with `symbols.<ref>.units`.
- The emitter places child sheets using cumulative sheet heights to avoid overlapping sheet pins.
- The emitter places multiple symbol units for the same reference instead of collapsing every ref to unit 1.
- Generated fixture schematic round-trips with exact connectivity parity.

Likely next work:

- Preserve original symbol instance positions when importing, at least as optional placement metadata.
- Preserve original no-connect markers and power symbols from schematic geometry, not just netlist nodes.
- Extend parity diagnostics to report net names beside connectivity signatures.
- Replace the residual threshold in `test_import_cm5_hudsp_roundtrip_smoke` with exact equality once the
  remaining power/local net merges are removed.
