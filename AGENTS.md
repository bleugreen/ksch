# ksch

`ksch` is a text-first schematic compiler: canonical `.ksch.yaml` schemas are the
source of truth, and KiCad `.kicad_sch` / `.kicad_pro` files are generated output.
See `README.md` for the architecture map and `docs/` for the CLI and schema references.

## Who this is for

The primary author of `.ksch.yaml` schemas is an AI agent, not a human. The intended
workflow is: a user wants a circuit or a change, an agent writes or edits the schema
and generates, and a human then routes the PCB from the generated schematic. Optimize
the authoring surface for agent usability — machine-checkable gates, actionable error
text, and the JSON Schema plus the bundled `ksch` skill as the real UI. Optimize the
generated schematic itself for the human who reads and routes it.

The reference for the target output style is `examples/can-controller`: sheets are
organized as titled functional-block frames (declared via schema `blocks:`), repeated
sub-circuits are stamped from identical stanza templates with direct local wiring,
inter-block connectivity is carried by net labels flush at pins, power runs GND-down /
rails-up, and frames tile in reading order. The `ksch-internals` skill documents this
north-star in full and how the layout solver realizes it.

## Development gate

- `./scripts/test.sh` runs the full test suite and is the project gate.
- `./scripts/check.sh` runs the full local gate in order: `ruff check`, `mypy`
  (`strict = true`), `pytest -q`, then `uv build`. Ruff must be clean before strict
  mypy runs, and strict mypy will block the gate even when the first complaint was only
  lint.
- Raw `uv run ruff check .` and `uv run mypy --strict src tests` surface a large
  pre-existing repo-wide backlog (long lines, `B905` zip findings, strict-typing gaps)
  outside any tight change. Scope lint and type expectations to your own diff, and
  report unrelated pre-existing findings separately rather than folding them into a
  feature change.
