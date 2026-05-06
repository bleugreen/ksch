# Layout Problem Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move schematic layout toward a generic graph-and-geometry compiler by introducing a first-class layout problem model and using it to prevent symbol-body overlaps before KiCad emission.

**Architecture:** `emit.py` should stop being the long-term layout engine. This first slice adds a reusable `LayoutProblem` model that represents visible geometry as elements with rectangles, owners, nets, and mobility, then wires the current emitter through that model for a concrete invariant: symbol bodies must not overlap. Later slices will add fields, labels, stubs, rails, hierarchy labels, and cross-net geometry checks to the same model.

**Tech Stack:** Python dataclasses, existing `ksch.layout.Point/Rect/solve_contact_layout`, pytest, ruff, mypy, KiCad CLI for generated schematic verification.

---

### Task 1: Geometry Problem Model

**Files:**
- Create: `src/ksch/layout_problem.py`
- Test: `tests/test_layout_problem.py`

- [ ] **Step 1: Write failing tests**

Add tests that construct generic layout elements and assert overlap detection is owner-aware:

```python
from ksch.layout import Rect
from ksch.layout_problem import LayoutElement, LayoutProblem


def test_layout_problem_reports_overlapping_symbol_bodies() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1", owner="U1", kind="symbol", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="C1", owner="C1", kind="symbol", rect=Rect(10, 10, 14, 14)),
        )
    )

    overlaps = problem.overlaps()

    assert [(hit.first.id, hit.second.id) for hit in overlaps] == [("U1", "C1")]


def test_layout_problem_ignores_same_owner_overlaps() -> None:
    problem = LayoutProblem(
        elements=(
            LayoutElement(id="U1:body", owner="U1", kind="symbol", rect=Rect(0, 0, 20, 20)),
            LayoutElement(id="U1:value", owner="U1", kind="field", rect=Rect(5, 5, 10, 8)),
        )
    )

    assert problem.overlaps() == ()
```

Run: `uv run pytest tests/test_layout_problem.py -q`
Expected: import failure for `ksch.layout_problem`.

- [ ] **Step 2: Implement model**

Create immutable dataclasses:

```python
@dataclass(frozen=True)
class LayoutElement:
    id: str
    owner: str
    kind: str
    rect: Rect
    nets: frozenset[str] = frozenset()
    movable: bool = True

@dataclass(frozen=True)
class LayoutOverlap:
    first: LayoutElement
    second: LayoutElement

@dataclass(frozen=True)
class LayoutProblem:
    elements: tuple[LayoutElement, ...]

    def overlaps(self) -> tuple[LayoutOverlap, ...]:
        ...
```

Run: `uv run pytest tests/test_layout_problem.py -q`
Expected: 2 passed.

### Task 2: Emitter Symbol Geometry Adapter

**Files:**
- Modify: `src/ksch/emit.py`
- Test: `tests/test_emit_layout.py`

- [ ] **Step 1: Write failing integration test**

Add a test that compiles a low-interface local circuit with enough local passives to reproduce slot collision risk, extracts symbol rectangles, and asserts no symbol-body overlaps. The assertion must use source symbol geometry, not screenshot pixels.

Run the specific test and verify it fails on the current low-interface branch.

- [ ] **Step 2: Build symbol-body layout problem**

Add an emitter helper that converts placed symbols into `LayoutElement(kind="symbol")` using existing `_symbol_horizontal_extent` and `_symbol_vertical_extent`.

- [ ] **Step 3: Resolve symbol-body overlaps generically**

Add a helper that converts those elements to `LayoutNode`s and calls `solve_contact_layout` with no attraction links, only collision cleanup. Fixed anchors remain fixed; movable support parts move just enough to clear symbol bodies.

- [ ] **Step 4: Use it in low-interface placement**

Before `_layout_low_interface_local_circuit` returns positions, run the body-overlap resolver. This is not a special case for caps or regulators; it is a generic invariant for all symbols in that branch.

Run: `uv run pytest tests/test_emit_layout.py::<new_test> -q`
Expected: pass.

### Task 3: Verification

**Files:**
- Modify only if tests reveal real regressions.

- [ ] Run focused layout tests:

```bash
uv run pytest tests/test_layout.py tests/test_layout_problem.py tests/test_emit_layout.py -q
```

- [ ] Run full suite and static checks:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src tests
```

- [ ] Regenerate CM5 output and verify:

```bash
KSCH_BIN=/Users/mitch/.config/superpowers/worktrees/kicad-schema/codex/schema-compiler/.venv/bin/ksch /Users/mitch/projects/cm5-hudsp/scripts/gen-ksch-schematic.sh
/Users/mitch/.config/superpowers/worktrees/kicad-schema/codex/schema-compiler/.venv/bin/ksch check /Users/mitch/projects/cm5-hudsp/ksch/project.ksch.yaml --out /Users/mitch/projects/cm5-hudsp/ksch-out
kicad-cli sch erc --output /tmp/cm5-hudsp-layout-problem-erc.rpt /Users/mitch/projects/cm5-hudsp/ksch-out/cm5hudsp.kicad_sch
```

Expected: tests pass, static checks pass, schema output matches, ERC reports 0 violations.
