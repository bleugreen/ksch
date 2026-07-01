# Open-source KiCad roundtrip witnesses

Status: discovered from external project smoke tests on 2026-05-27.

The CM5 project is not enough coverage for import -> YAML -> regenerated KiCad
roundtrips. A small external corpus already shows that the current pipeline is
not ready for general KiCad projects.

## Witness Corpus

Artifacts live under `snapshots/open-source-roundtrip/`.

| Project | Commit | Root schematic | Result |
| --- | --- | --- | --- |
| `devnithw/stm32-devboard` | `109de2ac2feef33dad70bef926d5ac915fef9d0b` | `stm32_board.kicad_sch` | imports and compiles from layout-free YAML; generated layout is still visibly weaker than the original |
| `hlord2000/Ohmbedded-RP2040-PCB-Template` | `39e37888d1f57189a24e1febc16bbf3486ba5bf6` | `PCB.kicad_sch` | imports, then compile fails on RP2040 pin mismatch |
| `hatlabs/SH-RPi-hardware` | `e82e3c9823bbcf94091b22927dd2e0da9cdfac8c` | `SH-RPi.kicad_sch` | imports, then compile fails on stock symbol lookup |
| `Neotron-Compute/Neotron-Pico` | `f078d9977104968c83bb603bdcde0fd8731324a7` | `Kicad/neotron-pico.kicad_sch` | imports, then compile fails on missing submodule symbol library |
| `will127534/PCIe3_Hub` | `267218109236a03b6e4b5e1a542b499a629a2cb4` | `ASM2806_Breakout.kicad_sch` | imports, then compile fails on embedded custom symbol |
| `jackw01/scanlight` | `c8bf780c413ab7ce6cdb1c28d4e70728e5174cca` | `pcb/sl_v1/backlight.kicad_sch` | imports, then compile fails on embedded custom symbol |
| `apfaudio/soldiercrab` | `34bb02968687c139312497a80d2f55b4b4acc8dc` | `soldiercrab.kicad_sch` | imports, then compile fails on embedded/local symbol mismatch |

## Reproduction

The comparison oracle used here is:

```sh
uv run ksch import <original>.kicad_sch --out snapshots/open-source-roundtrip/results/<name>/imported
uv run ksch compile snapshots/open-source-roundtrip/results/<name>/imported/project.ksch.yaml \
  --out snapshots/open-source-roundtrip/results/<name>/regenerated \
  --symbol-library <stock and project library args>
uv run ksch verify snapshots/open-source-roundtrip/results/<name>/imported/project.ksch.yaml \
  --out snapshots/open-source-roundtrip/results/<name>/regenerated \
  --against <original>.kicad_sch \
  --artifacts snapshots/open-source-roundtrip/results/<name>/verify \
  --no-drift \
  --symbol-library <stock and project library args>
```

These are text-only roundtrips. Imported YAML must not contain source placement,
wire geometry, labels, copied text, or any other schematic layout hints.

## Findings

### Imported YAML does not carry enough library context

Both `stm32-devboard` and `Ohmbedded-RP2040-PCB-Template` imported to YAML that
referenced stock KiCad libraries such as `Device:C` and `Device:C_Small`, but
the compiled schema cannot resolve those libraries unless a caller manually
passes KiCad stock library paths.

Imported projects should either declare the stock symbol libraries they use or
the compiler should have a KiCad-library discovery path that is explicit,
deterministic, and visible in verification artifacts.

### Embedded schematic symbols must be preserved

Several projects rely on `lib_symbols` embedded in the `.kicad_sch`, either for
custom parts or for symbol definitions that differ from the installed KiCad 9
stock library:

- `Ohmbedded-RP2040-PCB-Template`: imported schema references
  `MCU_RaspberryPi:RP2040`, but compiling against the local KiCad 9 library
  fails because `U1.VREG_IN` does not match the installed symbol pins.
- `PCIe3_Hub`: `ASM2806:ASM2806` exists in the source schematic's embedded
  `lib_symbols`, not as a separate `.kicad_sym` library in the repository.
- `scanlight`: `LED_1` is an embedded schematic symbol with no normal
  `nickname:name` library id.
- `soldiercrab`: `support_hardware:DSC60xx` appears in the source schematic's
  embedded symbols even though the checked-in `support_hardware.kicad_sym`
  library does not contain that symbol.

Importer now emits project-local symbol libraries from embedded `lib_symbols`
and references those libraries from imported YAML. That preserves symbol
semantics without preserving schematic layout. The remaining external witnesses
should be rerun against the embedded-library path before diagnosing their next
failure.

### Symbol-library indexing misclassifies valid symbol names

`SH-RPi-hardware` fails on `Connector:Raspberry_Pi_2_3`. KiCad 9's stock
`Connector.kicad_sym` contains a top-level symbol with that exact name, but
`index_symbol_library()` drops it because `_is_nested_unit_symbol_name()`
treats any name ending in `_<digit>_<digit>` as a nested unit symbol.

That heuristic is false for symbols whose actual top-level names contain a
numeric model/revision suffix. Nested unit detection must be based on the
symbol tree context, not name shape alone.

### Text-only layout is the real witness

The earlier retained-layout experiment was invalid as a layout benchmark because
it copied existing `.kicad_sch` placement into YAML. That path must stay deleted.
For `stm32-devboard`, a layout-free imported YAML compile currently produces:

```json
{
  "layout_errors": 0,
  "out_of_bounds": 0,
  "visible_overlaps": 32,
  "route_blockers": 0,
  "cross_net_contacts": 0
}
```

This is the current text-only baseline, not a success condition. The useful
comparison is against the original schematic's organization: local functional
clusters, short direct wires, shared rails for repeated decoupling, and
intentional page use.

## Text-only Rerun Results

After preserving embedded symbol libraries while rejecting all source layout:

| Project | Import | Compile | Verify without ERC | Current failure |
| --- | --- | --- | --- | --- |
| `stm32-devboard` | ok | ok | not run | generated layout remains bucket-of-parts compared to the original |
| `Ohmbedded-RP2040-PCB-Template` | ok | ok | fail | switch-matrix nets import as disjoint buses; rows/columns lack local junction topology |
| `SH-RPi-hardware` | ok | fail | not run | `Connector:Raspberry_Pi_2_3` still dropped by nested-unit name heuristic |
| `Neotron-Pico` | ok | fail | not run | imported root nets reference child sheet `bmc`, but the sheet instance was not imported into the root schema |
| `PCIe3_Hub` | ok | ok | fail | several oscillator/regulator/control nets lose connectivity |
| `scanlight` | ok | fail | not run | embedded symbol id `LED_1` has no `nickname:name` library id path yet |
| `soldiercrab` | ok | fail | not run | no-connect import emits refs for sheet-local repeated units not present in that sheet schema |

## Next Work

1. Replace name-shape nested-unit detection with parser-context-aware top-level
   symbol indexing.
2. Keep roundtrip witnesses layout-free and compare generated organization
   against original screenshots only as a human readability oracle.
3. Preserve/import non-`nickname:name` embedded symbols such as `LED_1`.
4. Fix hierarchical sheet instance import before interpreting cross-sheet net
   failures as layout failures.
5. Promote at least three external witnesses into an opt-in integration test
   harness once the import layer can compile them without manual library
   surgery.
