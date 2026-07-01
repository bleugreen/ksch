# Layout solver: cross-net shorts (old + new) and an over-reporting contact checker

Discovered 2026-06-29 while evaluating the new layout solver against the `cm5hudsp`
project (`/Users/mitch/projects/cm5-hudsp/cm5hudsp`, per-symbol `connects:` format).

Ground truth = the source `connects:` declarations. Method: compile the project both
ways into temp dirs, export KiCad netlists (`kicad-cli sch export netlist`), and compare
each compile's net membership to source using **net-mate sets** (naming-independent — a
pin is wrong iff its set of co-net pins differs from source). Repro scorer:
`/Users/mitch/.claude/jobs/9652e9ea/tmp/netmate_score.py <compiled_dir>` →
`WRONG-NET-MATE PINS: N`. ERC (`kicad-cli sch erc`) used as a cross-check.

## Finding 1 — NEW solver: USB-DAC power-to-data short  (FIX IN PROGRESS)
On `usb_hub_ports`, the new solver places net-label stubs for `USB_DAC_VBUS`,
`USB_DAC_CC2`, and `USB_DAC_DN` on **coincident grid nodes** (~(350.5, 208.3) and
(350.5, 213.4)). KiCad merges by coordinate, so all three collapse into one net —
**5 V VBUS shorted onto USB-C CC2 and D−**. Scorer: 6 wrong pins (C80.1, R36.1/2,
R37.2, TP17.1, TP19.1). ERC: `pin_to_pin` (U10 power-output vs J5 bidir) + `multiple_net_names`.
Root cause class: emit lacks a guarantee that endpoints of *different* nets never share a
coordinate. Fix = a separation pass that nudges a coincident cross-net endpoint to a free
adjacent node (and extends its wire). Acceptance gate: scorer → 0 wrong pins, no new ones,
tests green.

## Finding 2 — OLD layout: LCD reset shorted to GND  (the live cm5 board had this)
On `lvds_display`, the OLD layout collapses the entire `LCD_GRB_RESET_N` net into GND and
mis-bridges R63:
- source: `R63 = {3V3, LCD_GRB_RESET_N}`, `LCD_GRB_RESET_N = {R63.2, C72.1, TP43.1, J13.34}`
- old output: R63.1→`LCD_DITH`, R63.2/C72.1/TP43.1/**J13.34 (panel reset pin)**→`GND`
Effect: the LCD bridge's reset line tied to ground → panel held in reset. The `GRB_RESET_N`
net does not exist in the old-layout netlist *or* the synced PCB. The NEW solver gets this
net correct. This is what made the old layout untrustworthy despite a low ERC count
(net-merges aren't ERC violations, so ERC stayed quiet). Moot once the project moves to the
(fixed) new solver, but recorded as a second concrete miscompile test case.

## Finding 3 — `cross_net_contacts` over-reports (checker, not layout)
The internal `cross_net_contacts` validation flagged 10 contacts on the new-solver output;
**only 2 were real shorts** (the USB-DAC pair above). The other 8 (e.g. CANH↔CANL @
(275.6,100.3), CAM_GPIO0↔GMSL_XRES, CFG1↔GMSL_CAPVDD) are coincident geometry that KiCad
does **not** merge (it only nets on a junction dot or a shared pin). The checker treats any
coincident different-net geometry as a contact, which is stricter than KiCad's actual
connectivity model. Consequence: it can hard-fail `ksch gen` on layouts that are
electrically fine (it blocked the cm5 2-stage PoC bias-T on a false `AP63200_FB↔GMSL_1V8`
contact). Recommend aligning the checker with KiCad semantics (merge only at junctions /
shared pins), and/or downgrading non-merging coincidences to a warning.

## Finding 4 — dangling hierarchical-label stub emitted on a fan-out net  (pre-existing)
Discovered 2026-07-01 while adding ESD arrays to `cm5hudsp`. A clean full regen emits a **dangling
`CM5_3V3_OUT` hierarchical label** at (118.11, 152.40) on `lvds_display` — ERC `label_dangling`,
"Label not connected to anything". Net-mate score is 0 (connectivity is correct), so it is a
redundant/orphaned label the placer dropped without routing a wire to it, not an electrical fault.
Independent of the ESD parts (A/B regen with/without the 2 new touch symbols → identical). Occurs on
a high-fan-out net (CM5_3V3_OUT has ~14 label instances on the sheet). Likely the same emit path that
places net-label stubs (cf. Finding 1) failing to attach a wire on one instance when the tap count is
high. Low severity (ERC-only), but it means a clean regen never reaches 0 ERC errors. Acceptance gate
if fixed: full-project regen of cm5hudsp → no `label_dangling` on driven fan-out nets, net-mate still 0.

## Repro quickref
- compile with a given solver: `PYTHONPATH=<src> <tool-python> -c "from pathlib import Path; from ksch.cli import _compile_project; _compile_project(Path('ksch/project.ksch.yaml'), Path('<out>'), [])"` (cwd = cm5 project)
- score: `python3 netmate_score.py <out_dir>` → wrong-pin count + affected nets
- ERC cross-check: `kicad-cli sch erc --severity-all <out>/cm5hudsp.kicad_sch`
- net-merges are invisible to ERC — use the net-mate scorer, not ERC count, as the gate.
