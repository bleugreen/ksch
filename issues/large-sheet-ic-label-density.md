# Large Sheet IC Label Density

Generated large-sheet schematics now keep support passives out of IC pin-label keepouts and avoid
showing generated helper labels directly on dense controller pins when the same net has a clearer
endpoint elsewhere. The CM5 USB hub sheet is the current repro.

Completed compiler work:

- Sheet-local display labels strip a dominant imported prefix such as `USB Hub + Ports_`, while
  preserving canonical net identity internally.
- Multi-endpoint local labels prefer readable endpoints over dense controller/module pins when
  there is an equally valid visible endpoint. This is based on actual symbol pin density, not the
  `U*` reference prefix alone.
- Small `U*` parts such as the MIC2026 load switches keep visible pin-field labels, including on
  shared rails, so they do not become unlabeled boxes while dense USB controller labels stay hidden.
- Hidden duplicate labels are still emitted for electrical connectivity, but use tiny hidden text so
  KiCad SVG export does not visually pollute screenshots.
- Regression coverage checks prefix stripping, dense-controller label avoidance, and small `U*`
  visible-label preservation.

Remaining visual work:

- The USB2514 symbol itself still has long internal pin names such as `USBDM_DN1/PRT_DIS_M1`, so
  the controller body can remain dense even after generated net labels are moved away.
- Larger USB and connector sheets still need stronger block-level placement/routing so passives,
  crystal circuits, pulls, and rail groups read as local circuits instead of a loose right-side
  component field.

Current verified baseline after the label-density work:

- `uv run pytest -q` passes.
- `uv run ruff check .` passes.
- `uv run mypy src tests` passes.
- CM5 compiled netlist parity is exact: `missing=0 extra=0`.
- CM5 ERC has `Errors 0`, with only the inherited MountingHole footprint warnings.
