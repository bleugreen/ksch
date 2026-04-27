from pathlib import Path
from typing import Any

from ksch.emit import write_project
from ksch.expand import load_project_ir
from ksch.kicad.sexpr import atom, load_sexpr_file
from ksch.kicad.symbols import index_symbol_library
from ksch.resolver import LibraryContext, resolve_project


def _compile_schema(tmp_path: Path, text: str) -> Path:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(text, encoding="utf-8")
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    out = tmp_path / "out"
    write_project(resolved, out, {"Test": Path("tests/fixtures/kicad/symbols/Test.kicad_sym")})
    return out / "layout_demo.kicad_sch"


def _child(expr: list[Any], token: str) -> list[Any] | None:
    for item in expr[1:]:
        if isinstance(item, list) and item and atom(item[0]) == token:
            return item
    return None


def _property_value(expr: list[Any], name: str) -> str | None:
    for item in expr[1:]:
        if (
            isinstance(item, list)
            and len(item) >= 3
            and atom(item[0]) == "property"
            and atom(item[1]) == name
        ):
            return atom(item[2])
    return None


def _symbol_positions(path: Path) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    expr = load_sexpr_file(path)
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "symbol":
            continue
        ref = _property_value(item, "Reference")
        at = _child(item, "at")
        if ref and at is not None:
            positions[ref] = (float(atom(at[1])), float(atom(at[2])))
    return positions


def test_high_fanout_rail_uses_shared_rail_labels(tmp_path: Path) -> None:
    schematic = _compile_schema(
        tmp_path,
        """\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {lib: Test:USBHub}
  U2: {lib: Test:USBHub}
  U3: {lib: Test:USBHub}
nets:
  GND:
    - U1.GND/all
    - U2.GND/all
    - U3.GND/all
""",
    )

    text = schematic.read_text(encoding="utf-8")

    assert text.count('(label "GND"') <= 3


def test_passives_cluster_near_connected_anchor(tmp_path: Path) -> None:
    capacitor_symbols = "\n".join(
        f"  C{index}: {{lib: Test:C, value: 100nF}}" for index in range(1, 9)
    )
    capacitor_nets = "\n".join(
        f"  DECOUPLE_{index}:\n    - U1.VBUS_DET\n    - C{index}.1\n"
        f"  GND_C{index}:\n    - C{index}.2\n"
        for index in range(1, 9)
    )
    schematic = _compile_schema(
        tmp_path,
        f"""\
ksch: 1
project:
  name: layout_demo
symbols:
  U1: {{lib: Test:USBHub}}
{capacitor_symbols}
nets:
{capacitor_nets}
""",
    )

    positions = _symbol_positions(schematic)
    anchor_y = positions["U1"][1]

    assert max(abs(positions[f"C{index}"][1] - anchor_y) for index in range(1, 9)) <= 90


def test_root_child_sheets_use_grid_and_two_sided_pins(tmp_path: Path) -> None:
    root = tmp_path / "project.ksch.yaml"
    sheet_names = ["alpha", "beta", "gamma"]
    root.write_text(
        "ksch: 1\n"
        "project:\n"
        "  name: layout_demo\n"
        "sheets:\n"
        + "\n".join(f"  {name}: {{source: {name}.ksch.yaml}}" for name in sheet_names)
        + "\nnets:\n"
        + "\n".join(f"  NET_{name}: [\"{name}.P01\"]" for name in sheet_names)
        + "\n",
        encoding="utf-8",
    )
    for name in sheet_names:
        interface = "\n".join(f"  P{index:02d}: passive" for index in range(1, 21))
        (tmp_path / f"{name}.ksch.yaml").write_text(
            f"ksch: 1\nsheet:\n  id: {name}\ninterface:\n{interface}\n",
            encoding="utf-8",
        )
    project = load_project_ir(root)
    resolved = resolve_project(project, LibraryContext(symbols={}, footprints={}))
    out = tmp_path / "out"

    write_project(resolved, out)

    text = (out / "layout_demo.kicad_sch").read_text(encoding="utf-8")
    assert '(paper "A3")' in text
    assert text.count("(at 25.4 ") > 0
    assert text.count("(at 165.1 ") > 0
    assert text.count("(at 304.8 ") > 0
    assert " 0)\n      (uuid" in text
