# KiCad Schema Compiler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the hierarchical `.ksch.yaml` schema/compiler that generates deterministic, reviewable KiCad schematics and verifies generated output against schema intent.

**Architecture:** Implement a Python core package first, then expose it through CLI and MCP adapters. The compiler parses strict YAML, expands reusable schema fragments, builds a hierarchical IR, resolves symbols/footprints/pins from real KiCad libraries, lays out sheets, emits deterministic KiCad files, and verifies with `kicad-cli`.

**Tech Stack:** Python 3.12, `pydantic` v2, `ruamel.yaml`, `typer`, `rich`, `sexpdata`, `pytest`, optional `mcp`, local `kicad-cli`.

---

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, CLI entry point, test tooling.
- Create `src/ksch/__init__.py`: version export.
- Create `src/ksch/errors.py`: typed diagnostic errors with file/path context.
- Create `src/ksch/schema/loader.py`: strict YAML loader with duplicate-key and forbidden-feature rejection.
- Create `src/ksch/schema/formatter.py`: deterministic schema formatter for `ksch fmt`.
- Create `src/ksch/model/source.py`: pydantic source schema models matching `.ksch.yaml`.
- Create `src/ksch/model/endpoint.py`: endpoint parser and normalized endpoint forms.
- Create `src/ksch/model/ir.py`: expanded hierarchical project IR.
- Create `src/ksch/expand.py`: reusable block expansion and source-to-IR assembly.
- Create `src/ksch/kicad/sexpr.py`: s-expression parse/write helpers.
- Create `src/ksch/kicad/libraries.py`: KiCad library-table parsing and path resolution.
- Create `src/ksch/kicad/symbols.py`: `.kicad_sym` indexing and symbol pin metadata.
- Create `src/ksch/kicad/footprints.py`: `.pretty/*.kicad_mod` indexing and pad metadata.
- Create `src/ksch/resolver.py`: library, footprint, sheet-port, and endpoint resolution.
- Create `src/ksch/layout.py`: deterministic schematic placement and local wire/label decisions.
- Create `src/ksch/emit.py`: deterministic `.kicad_pro` and `.kicad_sch` emission.
- Create `src/ksch/verify.py`: netlist export parsing, assertion checks, ERC runner, drift detection.
- Create `src/ksch/cli.py`: `ksch` command tree.
- Create `src/ksch/mcp/server.py`: thin MCP adapter over core operations.
- Create `tests/fixtures/`: strict YAML, KiCad symbol, footprint, and hierarchy fixtures.
- Create focused `tests/test_*.py` files beside each major behavior boundary.

## Task 1: Scaffold Python Package And Test Harness

**Files:**
- Create: `pyproject.toml`
- Create: `src/ksch/__init__.py`
- Create: `tests/test_package.py`

- [ ] **Step 1: Write failing package smoke test**

Create `tests/test_package.py`:

```python
import ksch


def test_package_exports_version() -> None:
    assert isinstance(ksch.__version__, str)
    assert ksch.__version__
```

- [ ] **Step 2: Run smoke test to verify it fails**

Run: `uv run pytest tests/test_package.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ksch'`.

- [ ] **Step 3: Create package metadata and version export**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kicad-schema"
version = "0.1.0"
description = "Canonical text-first schematic compiler for KiCad"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
  "mcp>=1.0.0",
  "pydantic>=2.7",
  "rich>=13.7",
  "ruamel.yaml>=0.18",
  "sexpdata>=1.0.0",
  "typer>=0.12",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-cov>=5.0",
  "ruff>=0.6",
  "mypy>=1.10",
]

[dependency-groups]
dev = [
  "pytest>=8.0",
  "pytest-cov>=5.0",
  "ruff>=0.6",
  "mypy>=1.10",
]

[project.scripts]
ksch = "ksch.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/ksch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.mypy]
python_version = "3.12"
strict = true
```

Create `src/ksch/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Run smoke test to verify it passes**

Run: `uv run pytest tests/test_package.py -v`

Expected: PASS.

- [ ] **Step 5: Commit scaffold**

```bash
git add pyproject.toml src/ksch/__init__.py tests/test_package.py
git commit -m "feat: scaffold kicad-schema package"
```

## Task 2: Strict YAML Loader And Formatter

**Files:**
- Create: `src/ksch/errors.py`
- Create: `src/ksch/schema/__init__.py`
- Create: `src/ksch/schema/loader.py`
- Create: `src/ksch/schema/formatter.py`
- Create: `tests/test_schema_loader.py`

- [ ] **Step 1: Write failing strict-loader tests**

Create `tests/test_schema_loader.py`:

```python
from pathlib import Path

import pytest

from ksch.errors import KschError
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_text


def test_loads_plain_yaml_mapping() -> None:
    data = load_yaml_text("ksch: 1\nproject:\n  name: demo\n", Path("demo.ksch.yaml"))
    assert data == {"ksch": 1, "project": {"name": "demo"}}


def test_rejects_duplicate_keys() -> None:
    text = "ksch: 1\nproject: {}\nproject: {}\n"
    with pytest.raises(KschError, match="duplicate key 'project'"):
        load_yaml_text(text, Path("bad.ksch.yaml"))


def test_rejects_yaml_aliases() -> None:
    text = "ksch: 1\nshared: &x {name: demo}\nproject: *x\n"
    with pytest.raises(KschError, match="anchors and aliases are not allowed"):
        load_yaml_text(text, Path("bad.ksch.yaml"))


def test_formatter_orders_top_level_keys() -> None:
    text = "nets: {}\nksch: 1\nproject:\n  name: demo\n"
    assert format_schema_text(text) == "ksch: 1\nproject:\n  name: demo\nnets: {}\n"
```

- [ ] **Step 2: Run loader tests to verify failure**

Run: `uv run pytest tests/test_schema_loader.py -v`

Expected: FAIL with imports for `ksch.errors` or `ksch.schema.loader` missing.

- [ ] **Step 3: Implement diagnostics, strict loading, and formatting**

Create `src/ksch/errors.py`:

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    message: str
    path: Path | None = None
    location: str | None = None

    def render(self) -> str:
        prefix = ""
        if self.path is not None:
            prefix += str(self.path)
        if self.location:
            prefix += f":{self.location}"
        if prefix:
            return f"{prefix}: {self.message}"
        return self.message


class KschError(Exception):
    def __init__(self, message: str, path: Path | None = None, location: str | None = None):
        self.diagnostic = Diagnostic(message=message, path=path, location=location)
        super().__init__(self.diagnostic.render())
```

Create `src/ksch/schema/__init__.py`:

```python
from .loader import load_yaml_file, load_yaml_text

__all__ = ["load_yaml_file", "load_yaml_text"]
```

Create `src/ksch/schema/loader.py`:

```python
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

from ksch.errors import KschError


class StrictYaml:
    def __init__(self) -> None:
        self.yaml = YAML(typ="rt")
        self.yaml.allow_duplicate_keys = False

    def load_text(self, text: str, path: Path) -> Any:
        root = self.yaml.compose(text)
        if root is not None:
            self._reject_forbidden_nodes(root, path)
        try:
            data = self.yaml.load(text)
        except DuplicateKeyError as exc:
            key = str(exc.context_mark).splitlines()[0] if exc.context_mark else "unknown"
            duplicate = str(exc.problem).split('"')[1] if '"' in str(exc.problem) else "unknown"
            raise KschError(f"duplicate key '{duplicate}'", path, key) from exc
        except Exception as exc:
            raise KschError(str(exc), path) from exc
        return self._plain(data)

    def _reject_forbidden_nodes(self, node: object, path: Path) -> None:
        anchor = getattr(node, "anchor", None)
        if anchor:
            raise KschError("anchors and aliases are not allowed", path)
        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                self._reject_forbidden_nodes(key_node, path)
                self._reject_forbidden_nodes(value_node, path)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                self._reject_forbidden_nodes(item, path)
        elif isinstance(node, ScalarNode):
            if node.tag and not node.tag.startswith("tag:yaml.org,2002:"):
                raise KschError("custom YAML tags are not allowed", path)

    def _plain(self, value: Any) -> Any:
        if isinstance(value, CommentedMap):
            return {self._plain(k): self._plain(v) for k, v in value.items()}
        if isinstance(value, CommentedSeq):
            return [self._plain(v) for v in value]
        return value


def load_yaml_text(text: str, path: Path) -> Any:
    return StrictYaml().load_text(text, path)


def load_yaml_file(path: Path) -> Any:
    return load_yaml_text(path.read_text(encoding="utf-8"), path)
```

Create `src/ksch/schema/formatter.py`:

```python
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.schema.loader import load_yaml_text

TOP_LEVEL_ORDER = [
    "ksch",
    "project",
    "sheet",
    "libraries",
    "interface",
    "sheets",
    "symbols",
    "nets",
    "no_connects",
    "assertions",
    "blocks",
    "use",
]


def _order_mapping(value: Any, top_level: bool = False) -> Any:
    if isinstance(value, dict):
        keys = list(value.keys())
        if top_level:
            rank = {key: index for index, key in enumerate(TOP_LEVEL_ORDER)}
            keys.sort(key=lambda key: (rank.get(str(key), len(rank)), str(key)))
        return {key: _order_mapping(value[key]) for key in keys}
    if isinstance(value, list):
        return [_order_mapping(item) for item in value]
    return value


def format_schema_text(text: str, path: Path | None = None) -> str:
    source_path = path or Path("<memory>")
    data = load_yaml_text(text, source_path)
    ordered = _order_mapping(data, top_level=True)
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 100
    output = StringIO()
    yaml.dump(ordered, output)
    return output.getvalue()
```

- [ ] **Step 4: Run loader tests**

Run: `uv run pytest tests/test_schema_loader.py -v`

Expected: PASS.

- [ ] **Step 5: Commit strict YAML support**

```bash
git add src/ksch/errors.py src/ksch/schema tests/test_schema_loader.py
git commit -m "feat: add strict schema yaml loader"
```

## Task 3: Source Models And Endpoint Parser

**Files:**
- Create: `src/ksch/model/__init__.py`
- Create: `src/ksch/model/endpoint.py`
- Create: `src/ksch/model/source.py`
- Create: `tests/test_endpoint_model.py`
- Create: `tests/test_source_model.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_endpoint_model.py`:

```python
from ksch.model.endpoint import Endpoint, EndpointKind, parse_endpoint


def test_parse_named_pin_endpoint() -> None:
    endpoint = parse_endpoint("U2.USBDP_DN4/PRT_DIS_P4")
    assert endpoint == Endpoint(
        kind=EndpointKind.SYMBOL_PIN,
        ref="U2",
        pin_name="USBDP_DN4/PRT_DIS_P4",
        pin_number=None,
        all_matching=False,
        sheet=None,
        port=None,
    )


def test_parse_named_pin_with_number() -> None:
    endpoint = parse_endpoint("U1.GND@42")
    assert endpoint.ref == "U1"
    assert endpoint.pin_name == "GND"
    assert endpoint.pin_number == "42"


def test_parse_all_matching_pin_name() -> None:
    endpoint = parse_endpoint("J2.VBUS/all")
    assert endpoint.ref == "J2"
    assert endpoint.pin_name == "VBUS"
    assert endpoint.all_matching is True


def test_parse_child_sheet_port() -> None:
    endpoint = parse_endpoint("usb.VBUS")
    assert endpoint.kind is EndpointKind.SHEET_PORT
    assert endpoint.sheet == "usb"
    assert endpoint.port == "VBUS"
```

- [ ] **Step 2: Write failing source-model tests**

Create `tests/test_source_model.py`:

```python
from pathlib import Path

from ksch.model.source import SourceDocument
from ksch.schema.loader import load_yaml_text


def test_project_document_accepts_hierarchy() -> None:
    data = load_yaml_text(
        """
ksch: 1
project:
  name: demo
sheets:
  usb:
    source: sheets/usb.ksch.yaml
symbols:
  J1:
    lib: Test:USB_C
nets:
  +5V:
    - J1.VBUS/all
    - usb.VBUS
""",
        Path("project.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.ksch == 1
    assert document.project is not None
    assert document.sheets["usb"].source == Path("sheets/usb.ksch.yaml")


def test_sheet_document_accepts_interface() -> None:
    data = load_yaml_text(
        """
ksch: 1
sheet:
  id: usb
interface:
  VBUS: power_in
symbols:
  U2:
    lib: Test:USBHub
nets:
  VBUS:
    - U2.VBUS_DET
""",
        Path("usb.ksch.yaml"),
    )
    document = SourceDocument.model_validate(data)
    assert document.sheet is not None
    assert document.interface["VBUS"] == "power_in"
```

- [ ] **Step 3: Run model tests to verify failure**

Run: `uv run pytest tests/test_endpoint_model.py tests/test_source_model.py -v`

Expected: FAIL with missing `ksch.model` modules.

- [ ] **Step 4: Implement endpoint and source models**

Create `src/ksch/model/__init__.py`:

```python
from .endpoint import Endpoint, EndpointKind, parse_endpoint
from .source import SourceDocument

__all__ = ["Endpoint", "EndpointKind", "SourceDocument", "parse_endpoint"]
```

Create `src/ksch/model/endpoint.py`:

```python
from enum import Enum

from pydantic import BaseModel


class EndpointKind(str, Enum):
    SYMBOL_PIN = "symbol_pin"
    SHEET_PORT = "sheet_port"


class Endpoint(BaseModel, frozen=True):
    kind: EndpointKind
    ref: str | None = None
    pin_name: str | None = None
    pin_number: str | None = None
    all_matching: bool = False
    sheet: str | None = None
    port: str | None = None


def parse_endpoint(text: str) -> Endpoint:
    head, sep, tail = text.partition(".")
    if not sep or not head or not tail:
        raise ValueError(f"invalid endpoint '{text}'")

    all_matching = False
    if tail.endswith("/all"):
        tail = tail[:-4]
        all_matching = True

    pin_name = tail
    pin_number = None
    if "@" in tail:
        pin_name, pin_number = tail.rsplit("@", 1)
        if not pin_name or not pin_number:
            raise ValueError(f"invalid endpoint '{text}'")

    if head[:1].isupper():
        return Endpoint(
            kind=EndpointKind.SYMBOL_PIN,
            ref=head,
            pin_name=pin_name,
            pin_number=pin_number,
            all_matching=all_matching,
        )

    if "@" in tail or all_matching:
        raise ValueError(f"sheet port endpoint cannot use pin disambiguation: '{text}'")
    return Endpoint(kind=EndpointKind.SHEET_PORT, sheet=head, port=tail)
```

Create `src/ksch/model/source.py`:

```python
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PinDirection = Literal["input", "output", "bidirectional", "tri_state", "passive", "power_in", "power_out"]


class ProjectMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    title: str | None = None
    kicad_version: str | None = None


class SheetMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str | None = None


class LibrarySet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_global: bool = True
    project: list[Path] = Field(default_factory=list)


class Libraries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: LibrarySet = Field(default_factory=LibrarySet)
    footprints: LibrarySet = Field(default_factory=LibrarySet)


class SheetInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Path
    title: str | None = None


class SymbolDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lib: str
    value: str | None = None
    footprint: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)


class BlockDecl(BaseModel):
    model_config = ConfigDict(extra="allow")
    params: dict[str, str] = Field(default_factory=dict)


class UseDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block: str
    as_: str = Field(alias="as")
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ksch: int
    project: ProjectMeta | None = None
    sheet: SheetMeta | None = None
    libraries: Libraries = Field(default_factory=Libraries)
    interface: dict[str, PinDirection] = Field(default_factory=dict)
    sheets: dict[str, SheetInstance] = Field(default_factory=dict)
    symbols: dict[str, SymbolDecl] = Field(default_factory=dict)
    nets: dict[str, list[str]] = Field(default_factory=dict)
    no_connects: list[str] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    blocks: dict[str, BlockDecl] = Field(default_factory=dict)
    use: list[UseDecl] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_document_kind(self) -> "SourceDocument":
        if self.ksch != 1:
            raise ValueError("unsupported schema version")
        if self.project is None and self.sheet is None:
            raise ValueError("document must define either project or sheet")
        if self.project is not None and self.sheet is not None:
            raise ValueError("document cannot define both project and sheet")
        return self
```

- [ ] **Step 5: Run model tests**

Run: `uv run pytest tests/test_endpoint_model.py tests/test_source_model.py -v`

Expected: PASS.

- [ ] **Step 6: Commit source models**

```bash
git add src/ksch/model tests/test_endpoint_model.py tests/test_source_model.py
git commit -m "feat: add schema source models"
```

## Task 4: Expanded Hierarchical IR And Block Expansion

**Files:**
- Create: `src/ksch/model/ir.py`
- Create: `src/ksch/expand.py`
- Create: `tests/fixtures/project/project.ksch.yaml`
- Create: `tests/fixtures/project/sheets/usb.ksch.yaml`
- Create: `tests/test_expand.py`

- [ ] **Step 1: Create hierarchy fixtures**

Create `tests/fixtures/project/project.ksch.yaml`:

```yaml
ksch: 1
project:
  name: demo
sheets:
  usb:
    source: sheets/usb.ksch.yaml
symbols:
  J1:
    lib: Test:USB_C
    value: USB_IN
nets:
  +5V:
    - J1.VBUS/all
    - usb.VBUS
  USB_UP_DP:
    - J1.D+/all
    - usb.USB_UP_DP
```

Create `tests/fixtures/project/sheets/usb.ksch.yaml`:

```yaml
ksch: 1
sheet:
  id: usb
  title: USB
interface:
  VBUS: power_in
  USB_UP_DP: bidirectional
symbols:
  U2:
    lib: Test:USBHub
    value: USB2514B
nets:
  VBUS:
    - U2.VBUS_DET
  USB_UP_DP:
    - U2.USBDP_UP
```

- [ ] **Step 2: Write failing expansion tests**

Create `tests/test_expand.py`:

```python
from pathlib import Path

from ksch.expand import load_project_ir


FIXTURE = Path("tests/fixtures/project/project.ksch.yaml")


def test_load_project_ir_with_child_sheet() -> None:
    project = load_project_ir(FIXTURE)
    assert project.name == "demo"
    assert "/" in project.sheets
    assert "/usb" in project.sheets
    assert project.sheets["/usb"].interface["VBUS"] == "power_in"
    assert project.sheets["/"].child_instances["usb"].target_path == "/usb"


def test_root_net_can_target_child_port() -> None:
    project = load_project_ir(FIXTURE)
    root = project.sheets["/"]
    assert root.nets["+5V"] == ["J1.VBUS/all", "usb.VBUS"]
```

- [ ] **Step 3: Run expansion tests to verify failure**

Run: `uv run pytest tests/test_expand.py -v`

Expected: FAIL with missing `ksch.expand`.

- [ ] **Step 4: Implement IR and project loading**

Create `src/ksch/model/ir.py`:

```python
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ksch.model.source import PinDirection, SymbolDecl


class ChildInstanceIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    source: Path
    target_path: str


class SheetIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    source_path: Path
    title: str | None = None
    interface: dict[str, PinDirection] = Field(default_factory=dict)
    symbols: dict[str, SymbolDecl] = Field(default_factory=dict)
    nets: dict[str, list[str]] = Field(default_factory=dict)
    no_connects: list[str] = Field(default_factory=list)
    assertions: list[dict[str, object]] = Field(default_factory=list)
    child_instances: dict[str, ChildInstanceIR] = Field(default_factory=dict)


class ProjectIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    root_path: Path
    sheets: dict[str, SheetIR]
```

Create `src/ksch/expand.py`:

```python
from pathlib import Path

from ksch.model.ir import ChildInstanceIR, ProjectIR, SheetIR
from ksch.model.source import SourceDocument
from ksch.schema.loader import load_yaml_file


def _load_source(path: Path) -> SourceDocument:
    return SourceDocument.model_validate(load_yaml_file(path))


def _join_sheet_path(parent_path: str, child_name: str) -> str:
    if parent_path == "/":
        return f"/{child_name}"
    return f"{parent_path}/{child_name}"


def _sheet_ir(path: str, source_path: Path, source: SourceDocument) -> SheetIR:
    title = source.sheet.title if source.sheet else source.project.title if source.project else None
    return SheetIR(
        path=path,
        source_path=source_path,
        title=title,
        interface=source.interface,
        symbols=source.symbols,
        nets=source.nets,
        no_connects=source.no_connects,
        assertions=source.assertions,
    )


def _load_sheet_tree(path: Path, sheet_path: str, sheets: dict[str, SheetIR]) -> None:
    source = _load_source(path)
    sheet = _sheet_ir(sheet_path, path, source)
    sheets[sheet_path] = sheet

    for child_name, child in source.sheets.items():
        child_path = _join_sheet_path(sheet_path, child_name)
        child_source = (path.parent / child.source).resolve()
        sheet.child_instances[child_name] = ChildInstanceIR(
            name=child_name,
            source=child_source,
            target_path=child_path,
        )
        _load_sheet_tree(child_source, child_path, sheets)


def load_project_ir(path: Path) -> ProjectIR:
    root_path = path.resolve()
    source = _load_source(root_path)
    if source.project is None:
        raise ValueError("root document must define project")
    sheets: dict[str, SheetIR] = {}
    _load_sheet_tree(root_path, "/", sheets)
    return ProjectIR(name=source.project.name, root_path=root_path, sheets=sheets)
```

- [ ] **Step 5: Run expansion tests**

Run: `uv run pytest tests/test_expand.py -v`

Expected: PASS.

- [ ] **Step 6: Commit IR expansion**

```bash
git add src/ksch/model/ir.py src/ksch/expand.py tests/fixtures/project tests/test_expand.py
git commit -m "feat: load hierarchical project ir"
```

## Task 5: KiCad S-Expression And Library Table Parser

**Files:**
- Create: `src/ksch/kicad/__init__.py`
- Create: `src/ksch/kicad/sexpr.py`
- Create: `src/ksch/kicad/libraries.py`
- Create: `tests/fixtures/kicad/sym-lib-table`
- Create: `tests/fixtures/kicad/fp-lib-table`
- Create: `tests/test_kicad_libraries.py`

- [ ] **Step 1: Create library-table fixtures**

Create `tests/fixtures/kicad/sym-lib-table`:

```scheme
(sym_lib_table
  (version 7)
  (lib (name "Test")(type "KiCad")(uri "${KIPRJMOD}/symbols/Test.kicad_sym")(options "")(descr "Test symbols"))
)
```

Create `tests/fixtures/kicad/fp-lib-table`:

```scheme
(fp_lib_table
  (version 7)
  (lib (name "TestFootprints")(type "KiCad")(uri "${KIPRJMOD}/footprints/TestFootprints.pretty")(options "")(descr "Test footprints"))
)
```

- [ ] **Step 2: Write failing library parser tests**

Create `tests/test_kicad_libraries.py`:

```python
from pathlib import Path

from ksch.kicad.libraries import parse_library_table


def test_parse_symbol_library_table_with_project_variable() -> None:
    table = parse_library_table(
        Path("tests/fixtures/kicad/sym-lib-table"),
        variables={"KIPRJMOD": str(Path("tests/fixtures/kicad").resolve())},
    )
    assert table.kind == "sym_lib_table"
    assert table.entries["Test"].path.name == "Test.kicad_sym"


def test_parse_footprint_library_table_with_project_variable() -> None:
    table = parse_library_table(
        Path("tests/fixtures/kicad/fp-lib-table"),
        variables={"KIPRJMOD": str(Path("tests/fixtures/kicad").resolve())},
    )
    assert table.kind == "fp_lib_table"
    assert table.entries["TestFootprints"].path.name == "TestFootprints.pretty"
```

- [ ] **Step 3: Run parser tests to verify failure**

Run: `uv run pytest tests/test_kicad_libraries.py -v`

Expected: FAIL with missing `ksch.kicad`.

- [ ] **Step 4: Implement s-expression helpers and table parsing**

Create `src/ksch/kicad/__init__.py`:

```python
__all__: list[str] = []
```

Create `src/ksch/kicad/sexpr.py`:

```python
from pathlib import Path
from typing import Any

from sexpdata import Symbol, loads


Sexpr = list[Any] | str | int | float | Symbol


def load_sexpr_file(path: Path) -> list[Any]:
    data = loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} did not contain a top-level s-expression list")
    return data


def atom(value: Any) -> str:
    if isinstance(value, Symbol):
        return value.value()
    return str(value)
```

Create `src/ksch/kicad/libraries.py`:

```python
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class LibraryEntry:
    name: str
    type: str
    uri: str
    path: Path
    description: str


@dataclass(frozen=True)
class LibraryTable:
    kind: str
    entries: dict[str, LibraryEntry]


VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_uri(uri: str, variables: dict[str, str]) -> Path:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return variables.get(name, os.environ.get(name, match.group(0)))

    return Path(VAR_PATTERN.sub(replace, uri)).expanduser()


def parse_library_table(path: Path, variables: dict[str, str] | None = None) -> LibraryTable:
    variables = variables or {}
    expr = load_sexpr_file(path)
    kind = atom(expr[0])
    entries: dict[str, LibraryEntry] = {}
    for item in expr[1:]:
        if not isinstance(item, list) or atom(item[0]) != "lib":
            continue
        fields: dict[str, str] = {}
        for child in item[1:]:
            if isinstance(child, list) and len(child) >= 2:
                fields[atom(child[0])] = atom(child[1])
        name = fields["name"]
        uri = fields["uri"]
        entries[name] = LibraryEntry(
            name=name,
            type=fields.get("type", ""),
            uri=uri,
            path=_expand_uri(uri, variables),
            description=fields.get("descr", ""),
        )
    return LibraryTable(kind=kind, entries=entries)
```

- [ ] **Step 5: Run parser tests**

Run: `uv run pytest tests/test_kicad_libraries.py -v`

Expected: PASS.

- [ ] **Step 6: Commit KiCad library parser**

```bash
git add src/ksch/kicad tests/fixtures/kicad tests/test_kicad_libraries.py
git commit -m "feat: parse kicad library tables"
```

## Task 6: Symbol And Footprint Indexers

**Files:**
- Create: `src/ksch/kicad/symbols.py`
- Create: `src/ksch/kicad/footprints.py`
- Create: `tests/fixtures/kicad/symbols/Test.kicad_sym`
- Create: `tests/fixtures/kicad/footprints/TestFootprints.pretty/USB_Test.kicad_mod`
- Create: `tests/test_kicad_indexers.py`

- [ ] **Step 1: Create compact KiCad library fixtures**

Create `tests/fixtures/kicad/symbols/Test.kicad_sym`:

```scheme
(kicad_symbol_lib
  (version 20240101)
  (generator "ksch-test")
  (symbol "USBHub"
    (property "Reference" "U" (at 0 0 0))
    (property "Value" "USBHub" (at 0 -2.54 0))
    (property "Footprint" "TestFootprints:USB_Test" (at 0 -5.08 0))
    (symbol "USBHub_1_1"
      (pin bidirectional line (at -5.08 0 0) (length 2.54) (name "USBDP_UP") (number "1"))
      (pin bidirectional line (at -5.08 -2.54 0) (length 2.54) (name "USBDM_UP") (number "2"))
      (pin input line (at -5.08 -5.08 0) (length 2.54) (name "VBUS_DET") (number "3"))
      (pin power_in line (at 5.08 -7.62 180) (length 2.54) (name "GND") (number "4"))
      (pin power_in line (at 5.08 -10.16 180) (length 2.54) (name "GND") (number "EP"))
    )
  )
  (symbol "USB_C"
    (property "Reference" "J" (at 0 0 0))
    (property "Value" "USB_C" (at 0 -2.54 0))
    (property "Footprint" "TestFootprints:USB_Test" (at 0 -5.08 0))
    (symbol "USB_C_1_1"
      (pin bidirectional line (at -5.08 0 0) (length 2.54) (name "D+") (number "A6"))
      (pin bidirectional line (at -5.08 -2.54 0) (length 2.54) (name "D+") (number "B6"))
      (pin bidirectional line (at -5.08 -5.08 0) (length 2.54) (name "D-") (number "A7"))
      (pin bidirectional line (at -5.08 -7.62 0) (length 2.54) (name "D-") (number "B7"))
      (pin power_in line (at 5.08 0 180) (length 2.54) (name "VBUS") (number "A4"))
      (pin power_in line (at 5.08 -2.54 180) (length 2.54) (name "VBUS") (number "B4"))
    )
  )
)
```

Create `tests/fixtures/kicad/footprints/TestFootprints.pretty/USB_Test.kicad_mod`:

```scheme
(footprint "USB_Test"
  (version 20240101)
  (generator "ksch-test")
  (descr "USB test footprint")
  (tags "usb test")
  (pad "A4" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "B4" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "A6" smd rect (at 2 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "B6" smd rect (at 3 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "A7" smd rect (at 4 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "B7" smd rect (at 5 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
)
```

- [ ] **Step 2: Write failing indexer tests**

Create `tests/test_kicad_indexers.py`:

```python
from pathlib import Path

from ksch.kicad.footprints import index_footprint_library
from ksch.kicad.symbols import index_symbol_library


def test_symbol_index_extracts_duplicate_pin_names() -> None:
    index = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    symbol = index.symbols["Test:USB_C"]
    d_plus = [pin.number for pin in symbol.pins if pin.name == "D+"]
    assert d_plus == ["A6", "B6"]


def test_symbol_index_extracts_default_footprint() -> None:
    index = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    assert index.symbols["Test:USBHub"].footprint == "TestFootprints:USB_Test"


def test_footprint_index_extracts_pads() -> None:
    index = index_footprint_library(
        "TestFootprints",
        Path("tests/fixtures/kicad/footprints/TestFootprints.pretty"),
    )
    footprint = index.footprints["TestFootprints:USB_Test"]
    assert sorted(footprint.pads) == ["A4", "A6", "A7", "B4", "B6", "B7"]
```

- [ ] **Step 3: Run indexer tests to verify failure**

Run: `uv run pytest tests/test_kicad_indexers.py -v`

Expected: FAIL with missing `ksch.kicad.symbols`.

- [ ] **Step 4: Implement symbol and footprint indexing**

Create `src/ksch/kicad/symbols.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class SymbolPin:
    name: str
    number: str
    electrical_type: str
    at: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SymbolInfo:
    lib_id: str
    name: str
    footprint: str | None
    pins: list[SymbolPin] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolLibraryIndex:
    nickname: str
    path: Path
    symbols: dict[str, SymbolInfo]


def _property_value(symbol: list[Any], key: str) -> str | None:
    for item in symbol:
        if isinstance(item, list) and len(item) >= 3 and atom(item[0]) == "property" and atom(item[1]) == key:
            return atom(item[2])
    return None


def _find_pin_fields(pin_expr: list[Any]) -> tuple[str, str, tuple[float, float, float] | None]:
    name = ""
    number = ""
    at = None
    for item in pin_expr:
        if isinstance(item, list) and item:
            token = atom(item[0])
            if token == "name":
                name = atom(item[1])
            elif token == "number":
                number = atom(item[1])
            elif token == "at":
                at = (float(atom(item[1])), float(atom(item[2])), float(atom(item[3])) if len(item) > 3 else 0.0)
    return name, number, at


def _collect_pins(expr: list[Any]) -> list[SymbolPin]:
    pins: list[SymbolPin] = []
    for item in expr:
        if isinstance(item, list) and item:
            token = atom(item[0])
            if token == "pin":
                name, number, at = _find_pin_fields(item)
                pins.append(SymbolPin(name=name, number=number, electrical_type=atom(item[1]), at=at))
            elif token == "symbol":
                pins.extend(_collect_pins(item[1:]))
    return pins


def index_symbol_library(nickname: str, path: Path) -> SymbolLibraryIndex:
    expr = load_sexpr_file(path)
    symbols: dict[str, SymbolInfo] = {}
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "symbol":
            continue
        name = atom(item[1])
        if "_" in name and name.rsplit("_", 2)[-1].isdigit():
            continue
        lib_id = f"{nickname}:{name}"
        symbols[lib_id] = SymbolInfo(
            lib_id=lib_id,
            name=name,
            footprint=_property_value(item, "Footprint"),
            pins=_collect_pins(item),
        )
    return SymbolLibraryIndex(nickname=nickname, path=path, symbols=symbols)
```

Create `src/ksch/kicad/footprints.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class FootprintInfo:
    footprint_id: str
    name: str
    path: Path
    pads: set[str] = field(default_factory=set)
    description: str | None = None
    tags: str | None = None


@dataclass(frozen=True)
class FootprintLibraryIndex:
    nickname: str
    path: Path
    footprints: dict[str, FootprintInfo]


def _field(expr: list[Any], token: str) -> str | None:
    for item in expr:
        if isinstance(item, list) and item and atom(item[0]) == token and len(item) >= 2:
            return atom(item[1])
    return None


def _pads(expr: list[Any]) -> set[str]:
    pads: set[str] = set()
    for item in expr:
        if isinstance(item, list) and item and atom(item[0]) == "pad":
            pad = atom(item[1])
            if pad:
                pads.add(pad)
    return pads


def index_footprint_library(nickname: str, path: Path) -> FootprintLibraryIndex:
    footprints: dict[str, FootprintInfo] = {}
    for mod_path in sorted(path.glob("*.kicad_mod")):
        expr = load_sexpr_file(mod_path)
        name = atom(expr[1])
        footprint_id = f"{nickname}:{name}"
        footprints[footprint_id] = FootprintInfo(
            footprint_id=footprint_id,
            name=name,
            path=mod_path,
            pads=_pads(expr),
            description=_field(expr, "descr"),
            tags=_field(expr, "tags"),
        )
    return FootprintLibraryIndex(nickname=nickname, path=path, footprints=footprints)
```

- [ ] **Step 5: Run indexer tests**

Run: `uv run pytest tests/test_kicad_indexers.py -v`

Expected: PASS.

- [ ] **Step 6: Commit KiCad indexers**

```bash
git add src/ksch/kicad/symbols.py src/ksch/kicad/footprints.py tests/fixtures/kicad tests/test_kicad_indexers.py
git commit -m "feat: index kicad symbols and footprints"
```

## Task 7: Endpoint Resolver And Sheet Interface Validation

**Files:**
- Create: `src/ksch/resolver.py`
- Create: `tests/test_resolver.py`

- [ ] **Step 1: Write failing resolver tests**

Create `tests/test_resolver.py`:

```python
from pathlib import Path

import pytest

from ksch.errors import KschError
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.resolver import LibraryContext, resolve_project


def _context() -> LibraryContext:
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    return LibraryContext(symbols=symbols.symbols, footprints={})


def test_resolves_pin_name_all_to_duplicate_numbers() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    resolved = resolve_project(project, _context())
    endpoints = resolved.sheets["/"].nets["+5V"]
    assert [endpoint.pin_number for endpoint in endpoints if endpoint.ref == "J1"] == ["A4", "B4"]


def test_rejects_ambiguous_pin_without_all_or_number() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].nets["BAD"] = ["J1.D+"]
    with pytest.raises(KschError, match="J1.D\\+ is ambiguous"):
        resolve_project(project, _context())


def test_rejects_unknown_child_port() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    project.sheets["/"].nets["BAD"] = ["usb.NO_SUCH_PORT"]
    with pytest.raises(KschError, match="unknown sheet port usb.NO_SUCH_PORT"):
        resolve_project(project, _context())
```

- [ ] **Step 2: Run resolver tests to verify failure**

Run: `uv run pytest tests/test_resolver.py -v`

Expected: FAIL with missing `ksch.resolver`.

- [ ] **Step 3: Implement resolver**

Create `src/ksch/resolver.py`:

```python
from dataclasses import dataclass, field

from ksch.errors import KschError
from ksch.kicad.footprints import FootprintInfo
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.model.ir import ProjectIR


@dataclass(frozen=True)
class LibraryContext:
    symbols: dict[str, SymbolInfo]
    footprints: dict[str, FootprintInfo]


@dataclass(frozen=True)
class ResolvedEndpoint:
    text: str
    kind: EndpointKind
    sheet_path: str
    ref: str | None = None
    pin_name: str | None = None
    pin_number: str | None = None
    child_sheet: str | None = None
    port: str | None = None


@dataclass
class ResolvedSheet:
    path: str
    nets: dict[str, list[ResolvedEndpoint]] = field(default_factory=dict)


@dataclass
class ResolvedProject:
    name: str
    source: ProjectIR
    sheets: dict[str, ResolvedSheet] = field(default_factory=dict)


def _matching_pins(symbol: SymbolInfo, pin_name: str) -> list[SymbolPin]:
    return [pin for pin in symbol.pins if pin.name == pin_name or pin.number == pin_name]


def _resolve_symbol_pin(
    sheet_path: str,
    ref: str,
    endpoint_text: str,
    symbol: SymbolInfo,
    pin_name: str,
    pin_number: str | None,
    all_matching: bool,
) -> list[ResolvedEndpoint]:
    matches = _matching_pins(symbol, pin_name)
    if pin_number is not None:
        matches = [pin for pin in matches if pin.number == pin_number]
        if not matches:
            raise KschError(f"{endpoint_text} does not match any pin on {symbol.lib_id}")
    elif all_matching:
        if not matches:
            raise KschError(f"{endpoint_text} does not match any pin on {symbol.lib_id}")
    elif len(matches) != 1:
        if matches:
            rendered = ", ".join(f"{ref}.{pin.name}@{pin.number}" for pin in matches)
            raise KschError(f"{endpoint_text} is ambiguous; matches: {rendered}")
        raise KschError(f"{endpoint_text} does not match any pin on {symbol.lib_id}")

    return [
        ResolvedEndpoint(
            text=endpoint_text,
            kind=EndpointKind.SYMBOL_PIN,
            sheet_path=sheet_path,
            ref=ref,
            pin_name=pin.name,
            pin_number=pin.number,
        )
        for pin in matches
    ]


def resolve_project(project: ProjectIR, libraries: LibraryContext) -> ResolvedProject:
    resolved = ResolvedProject(name=project.name, source=project)
    for sheet_path, sheet in project.sheets.items():
        resolved_sheet = ResolvedSheet(path=sheet_path)
        for net_name, endpoint_texts in sheet.nets.items():
            resolved_endpoints: list[ResolvedEndpoint] = []
            for endpoint_text in endpoint_texts:
                endpoint = parse_endpoint(endpoint_text)
                if endpoint.kind is EndpointKind.SHEET_PORT:
                    child = sheet.child_instances.get(endpoint.sheet or "")
                    if child is None:
                        raise KschError(f"unknown child sheet {endpoint.sheet}")
                    child_sheet = project.sheets[child.target_path]
                    if endpoint.port not in child_sheet.interface:
                        raise KschError(f"unknown sheet port {endpoint_text}")
                    resolved_endpoints.append(
                        ResolvedEndpoint(
                            text=endpoint_text,
                            kind=EndpointKind.SHEET_PORT,
                            sheet_path=sheet_path,
                            child_sheet=endpoint.sheet,
                            port=endpoint.port,
                        )
                    )
                    continue

                ref = endpoint.ref or ""
                symbol_decl = sheet.symbols.get(ref)
                if symbol_decl is None:
                    raise KschError(f"unknown symbol reference {ref} in {sheet_path}")
                symbol = libraries.symbols.get(symbol_decl.lib)
                if symbol is None:
                    raise KschError(f"unknown symbol library id {symbol_decl.lib}")
                resolved_endpoints.extend(
                    _resolve_symbol_pin(
                        sheet_path,
                        ref,
                        endpoint_text,
                        symbol,
                        endpoint.pin_name or "",
                        endpoint.pin_number,
                        endpoint.all_matching,
                    )
                )
            resolved_sheet.nets[net_name] = resolved_endpoints
        resolved.sheets[sheet_path] = resolved_sheet
    return resolved
```

- [ ] **Step 4: Run resolver tests**

Run: `uv run pytest tests/test_resolver.py -v`

Expected: PASS.

- [ ] **Step 5: Commit resolver**

```bash
git add src/ksch/resolver.py tests/test_resolver.py
git commit -m "feat: resolve schematic endpoints"
```

## Task 8: Deterministic UUIDs And KiCad Schematic Emission

**Files:**
- Create: `src/ksch/emit.py`
- Create: `tests/test_emit.py`

- [ ] **Step 1: Write failing emitter tests**

Create `tests/test_emit.py`:

```python
from pathlib import Path

from ksch.emit import stable_uuid, write_project
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.resolver import LibraryContext, resolve_project


def _resolved():
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    return resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))


def test_stable_uuid_is_deterministic() -> None:
    assert stable_uuid("/usb/U2") == stable_uuid("/usb/U2")
    assert stable_uuid("/usb/U2") != stable_uuid("/usb/U3")


def test_write_project_creates_schematic_files(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    assert (tmp_path / "demo.kicad_pro").exists()
    assert (tmp_path / "demo.kicad_sch").exists()
    assert (tmp_path / "sheets" / "usb.kicad_sch").exists()
    assert "(generator \"kicad-schema\")" in (tmp_path / "demo.kicad_sch").read_text()
```

- [ ] **Step 2: Run emitter tests to verify failure**

Run: `uv run pytest tests/test_emit.py -v`

Expected: FAIL with missing `ksch.emit`.

- [ ] **Step 3: Implement deterministic emission skeleton**

Create `src/ksch/emit.py`:

```python
import json
import uuid
from pathlib import Path

from ksch.resolver import ResolvedProject

UUID_NAMESPACE = uuid.UUID("7d91d76e-4e61-4c8c-a1b7-4a5f2d7d6f4b")


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, key))


def _sheet_filename(project_name: str, sheet_path: str) -> Path:
    if sheet_path == "/":
        return Path(f"{project_name}.kicad_sch")
    parts = [part for part in sheet_path.split("/") if part]
    return Path("sheets").joinpath(*parts).with_suffix(".kicad_sch")


def _write_project_file(project: ResolvedProject, output_dir: Path) -> None:
    data = {
        "board": {"design_settings": {"defaults": {}}},
        "meta": {"filename": f"{project.name}.kicad_pro", "version": 1},
        "schematic": {},
    }
    (output_dir / f"{project.name}.kicad_pro").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _schematic_text(project: ResolvedProject, sheet_path: str) -> str:
    sheet = project.source.sheets[sheet_path]
    lines = [
        "(kicad_sch",
        "  (version 20240101)",
        "  (generator \"kicad-schema\")",
        f"  (uuid {stable_uuid(sheet_path)})",
        "  (paper \"A4\")",
        "  (lib_symbols)",
    ]
    for ref, symbol in sorted(sheet.symbols.items()):
        lines.extend(
            [
                f"  (symbol \"{symbol.lib}\"",
                "    (at 50 50 0)",
                "    (unit 1)",
                "    (in_bom yes)",
                "    (on_board yes)",
                f"    (uuid {stable_uuid(sheet_path + '/' + ref)})",
                f"    (property \"Reference\" \"{ref}\" (at 50 47.46 0))",
                f"    (property \"Value\" \"{symbol.value or ref}\" (at 50 52.54 0))",
                f"    (property \"Footprint\" \"{symbol.footprint or ''}\" (at 50 55.08 0))",
                "  )",
            ]
        )
    for child_name, child in sorted(sheet.child_instances.items()):
        lines.extend(
            [
                "  (sheet",
                "    (at 100 50)",
                "    (size 40 30)",
                f"    (uuid {stable_uuid(sheet_path + '/' + child_name + ':sheet')})",
                f"    (property \"Sheetname\" \"{child_name}\" (at 100 48 0))",
                f"    (property \"Sheetfile\" \"{_sheet_filename(project.name, child.target_path).as_posix()}\" (at 100 82 0))",
                "  )",
            ]
        )
    lines.append("  (path \"/\" (page \"1\"))")
    lines.append(")")
    return "\n".join(lines) + "\n"


def write_project(project: ResolvedProject, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_project_file(project, output_dir)
    for sheet_path in sorted(project.source.sheets):
        target = output_dir / _sheet_filename(project.name, sheet_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_schematic_text(project, sheet_path), encoding="utf-8")
```

- [ ] **Step 4: Run emitter tests**

Run: `uv run pytest tests/test_emit.py -v`

Expected: PASS.

- [ ] **Step 5: Commit deterministic emission skeleton**

```bash
git add src/ksch/emit.py tests/test_emit.py
git commit -m "feat: emit deterministic kicad project files"
```

## Task 9: Layout Heuristics With Stable Positions

**Files:**
- Create: `src/ksch/layout.py`
- Modify: `src/ksch/emit.py`
- Create: `tests/test_layout.py`

- [ ] **Step 1: Write failing layout tests**

Create `tests/test_layout.py`:

```python
from ksch.layout import Point, layout_sheet_symbols


def test_layout_places_connectors_left_and_ics_center() -> None:
    positions = layout_sheet_symbols(["J1", "U2", "C1", "R1"])
    assert positions["J1"].x < positions["U2"].x
    assert positions["C1"].y > positions["U2"].y
    assert positions["R1"].y > positions["U2"].y


def test_layout_is_stable_for_same_refs() -> None:
    first = layout_sheet_symbols(["U2", "J1", "C1"])
    second = layout_sheet_symbols(["C1", "J1", "U2"])
    assert first == second
    assert isinstance(first["U2"], Point)
```

- [ ] **Step 2: Run layout tests to verify failure**

Run: `uv run pytest tests/test_layout.py -v`

Expected: FAIL with missing `ksch.layout`.

- [ ] **Step 3: Implement deterministic placement helper**

Create `src/ksch/layout.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Point:
    x: float
    y: float


def _prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _lane(prefix: str) -> tuple[float, int]:
    if prefix in {"J", "P", "CN"}:
        return (30.0, 0)
    if prefix in {"U", "IC"}:
        return (90.0, 1)
    if prefix in {"R", "C", "L", "FB", "D", "TP"}:
        return (90.0, 2)
    return (150.0, 3)


def layout_sheet_symbols(refs: list[str]) -> dict[str, Point]:
    ordered = sorted(refs, key=lambda ref: (_lane(_prefix(ref))[1], ref))
    lane_counts: dict[int, int] = {}
    positions: dict[str, Point] = {}
    for ref in ordered:
        x, lane = _lane(_prefix(ref))
        index = lane_counts.get(lane, 0)
        lane_counts[lane] = index + 1
        y = 40.0 + index * 25.0
        if lane == 2:
            y += 50.0
        positions[ref] = Point(x=x, y=y)
    return positions
```

- [ ] **Step 4: Use layout positions in emitter**

Modify `src/ksch/emit.py` so `_schematic_text` computes positions:

```python
from ksch.layout import layout_sheet_symbols
```

Inside `_schematic_text`, before iterating symbols:

```python
    positions = layout_sheet_symbols(list(sheet.symbols))
```

Replace fixed symbol coordinates with:

```python
        position = positions[ref]
        x = position.x
        y = position.y
```

Use `x` and `y` in the emitted symbol and property `(at ...)` lines.

- [ ] **Step 5: Run layout and emitter tests**

Run: `uv run pytest tests/test_layout.py tests/test_emit.py -v`

Expected: PASS.

- [ ] **Step 6: Commit layout baseline**

```bash
git add src/ksch/layout.py src/ksch/emit.py tests/test_layout.py
git commit -m "feat: add deterministic schematic layout baseline"
```

## Task 10: Verification Runner And Netlist Comparison

**Files:**
- Create: `src/ksch/verify.py`
- Create: `tests/test_verify.py`

- [ ] **Step 1: Write failing verification tests**

Create `tests/test_verify.py`:

```python
from ksch.resolver import ResolvedEndpoint, ResolvedProject, ResolvedSheet
from ksch.model.endpoint import EndpointKind
from ksch.verify import NetlistNet, compare_connectivity


def test_compare_connectivity_reports_missing_pin() -> None:
    project = ResolvedProject(name="demo", source=None)  # type: ignore[arg-type]
    project.sheets["/"] = ResolvedSheet(
        path="/",
        nets={
            "USB_DP": [
                ResolvedEndpoint(
                    text="J1.D+@A6",
                    kind=EndpointKind.SYMBOL_PIN,
                    sheet_path="/",
                    ref="J1",
                    pin_name="D+",
                    pin_number="A6",
                )
            ]
        },
    )
    exported = {"USB_DP": NetlistNet(name="USB_DP", connections=set())}
    findings = compare_connectivity(project, exported)
    assert findings == ["USB_DP missing J1.A6"]


def test_compare_connectivity_accepts_matching_pin() -> None:
    project = ResolvedProject(name="demo", source=None)  # type: ignore[arg-type]
    project.sheets["/"] = ResolvedSheet(
        path="/",
        nets={
            "USB_DP": [
                ResolvedEndpoint(
                    text="J1.D+@A6",
                    kind=EndpointKind.SYMBOL_PIN,
                    sheet_path="/",
                    ref="J1",
                    pin_name="D+",
                    pin_number="A6",
                )
            ]
        },
    )
    exported = {"USB_DP": NetlistNet(name="USB_DP", connections={("J1", "A6")})}
    assert compare_connectivity(project, exported) == []
```

- [ ] **Step 2: Run verification tests to verify failure**

Run: `uv run pytest tests/test_verify.py -v`

Expected: FAIL with missing `ksch.verify`.

- [ ] **Step 3: Implement connectivity comparison and CLI-safe runner shell**

Create `src/ksch/verify.py`:

```python
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedProject


@dataclass(frozen=True)
class NetlistNet:
    name: str
    connections: set[tuple[str, str]]


def compare_connectivity(project: ResolvedProject, exported: dict[str, NetlistNet]) -> list[str]:
    findings: list[str] = []
    for sheet in project.sheets.values():
        for net_name, endpoints in sheet.nets.items():
            exported_net = exported.get(net_name)
            exported_connections = exported_net.connections if exported_net else set()
            for endpoint in endpoints:
                if endpoint.kind is not EndpointKind.SYMBOL_PIN:
                    continue
                expected = (endpoint.ref or "", endpoint.pin_number or "")
                if expected not in exported_connections:
                    findings.append(f"{net_name} missing {expected[0]}.{expected[1]}")
    return findings


def run_kicad_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kicad-cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
```

- [ ] **Step 4: Run verification tests**

Run: `uv run pytest tests/test_verify.py -v`

Expected: PASS.

- [ ] **Step 5: Commit verification core**

```bash
git add src/ksch/verify.py tests/test_verify.py
git commit -m "feat: compare schema intent to netlist"
```

## Task 11: CLI Commands

**Files:**
- Create: `src/ksch/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from ksch.cli import app


runner = CliRunner()


def test_cli_validate_accepts_fixture() -> None:
    result = runner.invoke(app, ["validate", "tests/fixtures/project/project.ksch.yaml"])
    assert result.exit_code == 0
    assert "valid" in result.stdout


def test_cli_expand_lists_sheets() -> None:
    result = runner.invoke(app, ["expand", "tests/fixtures/project/project.ksch.yaml"])
    assert result.exit_code == 0
    assert "/usb" in result.stdout


def test_cli_symbol_info_uses_fixture_library() -> None:
    result = runner.invoke(
        app,
        [
            "symbol",
            "info",
            "Test:USB_C",
            "--library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert result.exit_code == 0
    assert "D+@A6" in result.stdout
    assert "D+@B6" in result.stdout
```

- [ ] **Step 2: Run CLI tests to verify failure**

Run: `uv run pytest tests/test_cli.py -v`

Expected: FAIL with missing `ksch.cli`.

- [ ] **Step 3: Implement CLI surface**

Create `src/ksch/cli.py`:

```python
from pathlib import Path

import typer
from rich.console import Console

from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_file

app = typer.Typer(no_args_is_help=True)
symbol_app = typer.Typer(no_args_is_help=True)
app.add_typer(symbol_app, name="symbol")
console = Console()


@app.command()
def validate(path: Path) -> None:
    load_yaml_file(path)
    load_project_ir(path)
    console.print(f"{path} valid")


@app.command()
def fmt(path: Path, check: bool = typer.Option(False, "--check")) -> None:
    formatted = format_schema_text(path.read_text(encoding="utf-8"), path)
    if check:
        raise typer.Exit(0 if formatted == path.read_text(encoding="utf-8") else 1)
    path.write_text(formatted, encoding="utf-8")


@app.command()
def expand(path: Path) -> None:
    project = load_project_ir(path)
    for sheet_path in sorted(project.sheets):
        console.print(sheet_path)


@symbol_app.command("info")
def symbol_info(
    lib_id: str,
    library: list[str] = typer.Option([], "--library", help="NICKNAME=PATH_TO_KICAD_SYM"),
) -> None:
    symbols = {}
    for item in library:
        nickname, raw_path = item.split("=", 1)
        symbols.update(index_symbol_library(nickname, Path(raw_path)).symbols)
    symbol = symbols[lib_id]
    console.print(lib_id)
    if symbol.footprint:
        console.print(f"footprint: {symbol.footprint}")
    for pin in symbol.pins:
        console.print(f"{pin.name}@{pin.number} {pin.electrical_type}")
```

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit CLI baseline**

```bash
git add src/ksch/cli.py tests/test_cli.py
git commit -m "feat: add ksch cli commands"
```

## Task 12: Compile Command End-To-End

**Files:**
- Modify: `src/ksch/cli.py`
- Create: `tests/test_compile_cli.py`

- [ ] **Step 1: Write failing compile command test**

Create `tests/test_compile_cli.py`:

```python
from typer.testing import CliRunner

from ksch.cli import app


runner = CliRunner()


def test_compile_writes_project(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / "demo.kicad_pro").exists()
    assert (tmp_path / "demo.kicad_sch").exists()
```

- [ ] **Step 2: Run compile test to verify failure**

Run: `uv run pytest tests/test_compile_cli.py -v`

Expected: FAIL because `compile` command is not registered.

- [ ] **Step 3: Add compile command**

Modify `src/ksch/cli.py` to import:

```python
from ksch.emit import write_project
from ksch.resolver import LibraryContext, resolve_project
```

Add helper:

```python
def _load_symbol_libraries(items: list[str]):
    symbols = {}
    for item in items:
        nickname, raw_path = item.split("=", 1)
        symbols.update(index_symbol_library(nickname, Path(raw_path)).symbols)
    return symbols
```

Add command:

```python
@app.command()
def compile(
    path: Path,
    out: Path = typer.Option(..., "--out"),
    symbol_library: list[str] = typer.Option([], "--symbol-library"),
) -> None:
    project = load_project_ir(path)
    symbols = _load_symbol_libraries(symbol_library)
    resolved = resolve_project(project, LibraryContext(symbols=symbols, footprints={}))
    write_project(resolved, out)
    console.print(f"wrote {out}")
```

- [ ] **Step 4: Run compile command test**

Run: `uv run pytest tests/test_compile_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit compile command**

```bash
git add src/ksch/cli.py tests/test_compile_cli.py
git commit -m "feat: add compile command"
```

## Task 13: Authoring Lookup Commands

**Files:**
- Modify: `src/ksch/cli.py`
- Create: `tests/test_authoring_cli.py`

- [ ] **Step 1: Write failing authoring command tests**

Create `tests/test_authoring_cli.py`:

```python
from typer.testing import CliRunner

from ksch.cli import app


runner = CliRunner()


def test_symbols_search() -> None:
    result = runner.invoke(
        app,
        ["symbols", "search", "usb", "--library", "Test=tests/fixtures/kicad/symbols/Test.kicad_sym"],
    )
    assert result.exit_code == 0
    assert "Test:USB_C" in result.stdout


def test_pin_search() -> None:
    result = runner.invoke(
        app,
        ["pin-search", "Test:USB_C", "D+", "--library", "Test=tests/fixtures/kicad/symbols/Test.kicad_sym"],
    )
    assert result.exit_code == 0
    assert "D+@A6" in result.stdout
    assert "D+@B6" in result.stdout
```

- [ ] **Step 2: Run authoring CLI tests to verify failure**

Run: `uv run pytest tests/test_authoring_cli.py -v`

Expected: FAIL because command groups are missing.

- [ ] **Step 3: Add symbol search and pin search commands**

Modify `src/ksch/cli.py`:

```python
symbols_app = typer.Typer(no_args_is_help=True)
app.add_typer(symbols_app, name="symbols")


@symbols_app.command("search")
def symbols_search(
    query: str,
    library: list[str] = typer.Option([], "--library"),
) -> None:
    symbols = _load_symbol_libraries(library)
    query_lower = query.lower()
    for lib_id in sorted(symbols):
        if query_lower in lib_id.lower():
            console.print(lib_id)


@app.command("pin-search")
def pin_search(
    symbol_id: str,
    query: str,
    library: list[str] = typer.Option([], "--library"),
) -> None:
    symbols = _load_symbol_libraries(library)
    symbol = symbols[symbol_id]
    query_lower = query.lower()
    for pin in symbol.pins:
        if query_lower in pin.name.lower() or query_lower in pin.number.lower():
            console.print(f"{pin.name}@{pin.number} {pin.electrical_type}")
```

- [ ] **Step 4: Run authoring CLI tests**

Run: `uv run pytest tests/test_authoring_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit authoring commands**

```bash
git add src/ksch/cli.py tests/test_authoring_cli.py
git commit -m "feat: add authoring lookup commands"
```

## Task 14: MCP Adapter

**Files:**
- Create: `src/ksch/mcp/__init__.py`
- Create: `src/ksch/mcp/server.py`
- Create: `tests/test_mcp_adapter.py`

- [ ] **Step 1: Write failing MCP adapter tests**

Create `tests/test_mcp_adapter.py`:

```python
import pytest

from ksch.mcp.server import symbol_info_text


def test_symbol_info_text_returns_pin_details() -> None:
    text = symbol_info_text(
        "Test:USB_C",
        ["Test=tests/fixtures/kicad/symbols/Test.kicad_sym"],
    )
    assert "Test:USB_C" in text
    assert "D+@A6" in text


@pytest.mark.asyncio
async def test_mcp_module_imports() -> None:
    from ksch.mcp.server import create_server

    server = create_server()
    assert server.name == "kicad-schema"
```

- [ ] **Step 2: Run MCP adapter tests to verify failure**

Run: `uv run pytest tests/test_mcp_adapter.py -v`

Expected: FAIL with missing `ksch.mcp`.

- [ ] **Step 3: Implement thin MCP adapter**

Create `src/ksch/mcp/__init__.py`:

```python
__all__: list[str] = []
```

Create `src/ksch/mcp/server.py`:

```python
from pathlib import Path

from mcp.server import Server

from ksch.kicad.symbols import index_symbol_library


def _load_symbols(libraries: list[str]):
    symbols = {}
    for item in libraries:
        nickname, raw_path = item.split("=", 1)
        symbols.update(index_symbol_library(nickname, Path(raw_path)).symbols)
    return symbols


def symbol_info_text(lib_id: str, libraries: list[str]) -> str:
    symbol = _load_symbols(libraries)[lib_id]
    lines = [lib_id]
    if symbol.footprint:
        lines.append(f"footprint: {symbol.footprint}")
    for pin in symbol.pins:
        lines.append(f"{pin.name}@{pin.number} {pin.electrical_type}")
    return "\n".join(lines)


def create_server() -> Server:
    return Server("kicad-schema")
```

- [ ] **Step 4: Run MCP adapter tests**

Run: `uv run pytest tests/test_mcp_adapter.py -v`

Expected: PASS.

- [ ] **Step 5: Commit MCP adapter**

```bash
git add src/ksch/mcp tests/test_mcp_adapter.py
git commit -m "feat: add mcp adapter shell"
```

## Task 15: KiCad CLI Integration Test Gate

**Files:**
- Create: `tests/test_kicad_cli_integration.py`

- [ ] **Step 1: Write KiCad integration test that skips cleanly without KiCad**

Create `tests/test_kicad_cli_integration.py`:

```python
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app
from ksch.verify import run_kicad_cli


runner = CliRunner()


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not installed")
def test_generated_project_is_seen_by_kicad_cli(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert result.exit_code == 0
    schematic = tmp_path / "demo.kicad_sch"
    cli_result = run_kicad_cli(["sch", "export", "netlist", "--format", "kicadsexpr", str(schematic)])
    assert cli_result.returncode == 0, cli_result.stderr
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/test_kicad_cli_integration.py -v`

Expected when KiCad is installed: FAIL until the emitter writes all KiCad-required schematic sections. Expected without KiCad: SKIPPED.

- [ ] **Step 3: Add KiCad-required schematic sections**

Modify `src/ksch/emit.py` so generated schematics include these sections for every placed symbol and sheet instance:

```scheme
(pin "PIN_NUMBER" (uuid PIN_UUID))
(instances
  (project "PROJECT_NAME"
    (path "/"
      (reference "REF")
      (unit 1))))
```

```scheme
(stroke (width 0.1524) (type solid) (color 0 0 0 0))
(fill (color 0 0 0 0))
(instances
  (project "PROJECT_NAME"
    (path "/"
      (page "1"))))
```

For child sheet instances, emit one sheet `pin` entry for each child `interface` port. Map schema-only `power_in` and `power_out` interface directions to KiCad's `passive` sheet-pin shape during emission.

After the emitter update, rerun:

```bash
uv run pytest tests/test_emit.py tests/test_kicad_cli_integration.py -v
```

Expected: PASS when KiCad is installed, SKIPPED when KiCad is unavailable.

- [ ] **Step 4: Commit integration gate**

```bash
git add src/ksch/emit.py tests/test_kicad_cli_integration.py
git commit -m "test: add kicad cli integration gate"
```

## Task 16: Verification Command And Drift Check

**Files:**
- Modify: `src/ksch/cli.py`
- Modify: `src/ksch/verify.py`
- Create: `tests/test_check_cli.py`

- [ ] **Step 1: Write failing check command test**

Create `tests/test_check_cli.py`:

```python
from typer.testing import CliRunner

from ksch.cli import app


runner = CliRunner()


def test_check_reports_clean_generated_output(tmp_path) -> None:
    compile_result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert compile_result.exit_code == 0
    check_result = runner.invoke(
        app,
        [
            "check",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert check_result.exit_code == 0
    assert "generated output matches schema" in check_result.stdout
```

- [ ] **Step 2: Run check command test to verify failure**

Run: `uv run pytest tests/test_check_cli.py -v`

Expected: FAIL because `check` command is missing.

- [ ] **Step 3: Add directory comparison helper**

Modify `src/ksch/verify.py`:

```python
from filecmp import dircmp


def compare_dirs(expected: Path, actual: Path) -> list[str]:
    comparison = dircmp(expected, actual)
    findings: list[str] = []

    def walk(cmp: dircmp, prefix: Path) -> None:
        for name in cmp.left_only:
            findings.append(f"missing generated file {(prefix / name).as_posix()}")
        for name in cmp.right_only:
            findings.append(f"unexpected generated file {(prefix / name).as_posix()}")
        for name in cmp.diff_files:
            findings.append(f"generated file differs {(prefix / name).as_posix()}")
        for name, child in cmp.subdirs.items():
            walk(child, prefix / name)

    walk(comparison, Path("."))
    return findings
```

- [ ] **Step 4: Add check command**

Modify `src/ksch/cli.py`:

```python
import tempfile
from ksch.verify import compare_dirs
```

Add command:

```python
@app.command()
def check(
    path: Path,
    out: Path = typer.Option(..., "--out"),
    symbol_library: list[str] = typer.Option([], "--symbol-library"),
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        compile(path, tmp_path, symbol_library)
        findings = compare_dirs(tmp_path, out)
    if findings:
        for finding in findings:
            console.print(finding)
        raise typer.Exit(1)
    console.print("generated output matches schema")
```

- [ ] **Step 5: Run check command tests**

Run: `uv run pytest tests/test_check_cli.py -v`

Expected: PASS.

- [ ] **Step 6: Commit drift check**

```bash
git add src/ksch/cli.py src/ksch/verify.py tests/test_check_cli.py
git commit -m "feat: add generated output drift check"
```

## Task 17: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Create: `docs/schema-v1.md`
- Create: `docs/cli.md`

- [ ] **Step 1: Write schema documentation**

Create `docs/schema-v1.md` with these sections:

```markdown
# ksch YAML Schema v1

## Canonical Source

`.ksch.yaml` is the only source schema format. Generated `.kicad_sch` files are artifacts.

## Project Documents

Project documents define `ksch`, `project`, optional `libraries`, child `sheets`, root-sheet `symbols`, root-sheet `nets`, and `assertions`.

## Sheet Documents

Sheet documents define `ksch`, `sheet`, optional `interface`, sheet-local `symbols`, sheet-local `nets`, `no_connects`, and `assertions`.

## Endpoints

- `REF.PIN_NAME`
- `REF.PIN_NAME@PIN_NUMBER`
- `REF.PIN_NAME/all`
- `REF.PIN_NUMBER`
- `child_sheet.PORT`

Pin-name endpoints are preferred. Bare pin numbers are escape hatches.
```

- [ ] **Step 2: Write CLI documentation**

Create `docs/cli.md`:

````markdown
# ksch CLI

## Validate

```bash
ksch validate project.ksch.yaml
```

## Compile

```bash
ksch compile project.ksch.yaml --out generated
```

## Check Drift

```bash
ksch check project.ksch.yaml --out generated
```

## Authoring Lookup

```bash
ksch symbols search USB2514
ksch symbol info Interface_USB:USB2514B
ksch pin-search Interface_USB:USB2514B GND
```
````

- [ ] **Step 3: Update README**

Replace the README implementation-target wording with links to the design and docs:

```markdown
## Current Implementation Plan

The project design is captured in `docs/superpowers/specs/2026-04-27-kicad-schema-compiler-design.md`.

Implementation is tracked in `docs/superpowers/plans/2026-04-27-kicad-schema-compiler.md`.
```

- [ ] **Step 4: Run full local verification**

Run:

```bash
uv run pytest -v
uv run ruff check .
```

Expected: all tests pass and ruff reports no violations.

- [ ] **Step 5: Commit docs and verification**

```bash
git add README.md docs/schema-v1.md docs/cli.md
git commit -m "docs: document schema and cli"
```

## Self-Review Checklist

- Spec coverage:
  - Strict one-format YAML source: Tasks 2, 3, 11, 17.
  - Hierarchical project and sheet tree: Tasks 4, 12.
  - Sheet-owned interfaces: Tasks 3, 4, 7.
  - Pin-name endpoint model with `@number` and `/all`: Tasks 3, 7, 13, 17.
  - KiCad library indexing: Tasks 5, 6, 13.
  - Resolver and authoring tools share core logic: Tasks 7, 11, 13, 14.
  - Deterministic emission and UUIDs: Task 8.
  - Layout baseline: Task 9.
  - Verification and drift checks: Tasks 10, 15, 16.
  - CLI and MCP adapters: Tasks 11, 12, 13, 14.
- Placeholder scan: no placeholder markers or underspecified test steps are allowed in this plan.
- Type consistency:
  - `ProjectIR`, `SheetIR`, and `SourceDocument` are introduced before resolver and emitter tasks use them.
  - `ResolvedProject`, `ResolvedSheet`, and `ResolvedEndpoint` are introduced before verification tasks use them.
  - CLI helper `_load_symbol_libraries` is introduced before authoring commands reuse it.
