# Preserve KiCad Power Pseudo-Symbols During Import

## Context

KiCad's exported netlist omits `power:PWR_FLAG` pseudo-components, but those symbols
matter for ERC. Importing only from netlist nodes loses the explicit power drivers from
the source schematic and can create generated `power_pin_not_driven` errors.

Current CM5 verification after this was fixed:

- Generated ERC reports 0 errors.
- The remaining generated ERC messages are 4 `footprint_link_issues` warnings for stock
  mounting-hole footprints, matching a warning class also present in the original project.

Note: earlier notes in this issue recorded the original CM5 ERC as 0 violations. A fresh
KiCad CLI run against the current local project reports many original ERC messages, so
the durable invariant is generated roundtrip exact connectivity plus zero generated ERC
errors, not original zero-violation parity.

## Failed Approach

I tried compiler-side synthetic `PWR_FLAG` insertion for undriven power-input nets.
That is not safe as a blind per-sheet rule: globally shared rails then received
multiple power-output flags, producing `pin_to_pin` errors and even reintroducing a
net-name collision in the CM5 project.

## Resolution

Implemented as sheet-level `power_flags` in the schema:

- Import reads placed `power:PWR_FLAG` symbols directly from `.kicad_sch`.
- A schematic geometry pass follows wires, junctions, labels, hierarchical labels, global
  labels, and power symbols to determine the net driven by each original PWR_FLAG.
- Imported flag names are canonicalized against the KiCad netlist names for that sheet,
  which handles local labels whose exported net names include a sheet-title prefix.
- Compile emits KiCad-native `power:PWR_FLAG` symbols with hidden label stubs attached to
  the generated net name.

Verification after the fix:

- Generated CM5 ERC: 0 errors, 4 inherited footprint-library warnings.
- Added a skipped-when-unavailable CM5 regression that requires exact connectivity parity,
  no generated ERC errors, and no generated net-name collisions, dangling wires, or
  unconnected pins.

## Original Direction

Preserve KiCad power pseudo-symbols from the schematic files themselves, not from the
netlist:

- Read placed `power:*` symbols directly from each `.kicad_sch`.
- Import `power:PWR_FLAG` placements and `#PWR` power-symbol placements into schema.
- Preserve their net attachment, either as first-class schema power symbols or as an
explicit ERC-driver annotation that compiles back to KiCad-native symbols.
- Add a CM5 regression that requires generated ERC to match the original zero-violation
status for these power-driver cases.

This is done for `power:PWR_FLAG`; preserving ordinary `power:*` symbols as visible
schematic intent is still a separate readability/frontend decision.
