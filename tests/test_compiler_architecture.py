from pathlib import Path

from ksch.compiler import build_placed_project
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.placed import PlacedProject, PlacedSymbol
from ksch.resolver import LibraryContext, resolve_project


def test_emit_module_is_serializer_only() -> None:
    source = Path("src/ksch/emit.py").read_text(encoding="utf-8")

    assert "ResolvedProject" not in source
    assert "SymbolInfo" not in source
    assert "EndpointKind" not in source
    assert "_layout_sheet_symbols" not in source
    assert "_net_point_lines" not in source
    assert "_symbol_pin_point" not in source


def test_symbol_geometry_is_not_defined_in_compiler() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")
    geometry_source = Path("src/ksch/geometry.py").read_text(encoding="utf-8")

    assert "def _symbol_rect" not in compiler_source
    assert "def _symbol_vertical_extent" not in compiler_source
    assert "def _symbol_graphic" not in compiler_source
    assert "def symbol_rect" in geometry_source
    assert "def symbol_vertical_extent" in geometry_source
    assert "def symbol_graphic_points" in geometry_source


def test_route_geometry_is_not_defined_in_compiler() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")
    routing_source = Path("src/ksch/routing.py").read_text(encoding="utf-8")

    assert "def _normalize_wire_segments" not in compiler_source
    assert "def _segments_touch" not in compiler_source
    assert "def _point_on_segment" not in compiler_source
    assert "def normalize_wire_segments" in routing_source
    assert "def segments_touch" in routing_source
    assert "def point_on_segment" in routing_source


def test_net_routing_stage_is_not_defined_in_compiler() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")
    net_routing_source = Path("src/ksch/net_routing.py").read_text(encoding="utf-8")

    assert "def _net_point_lines" not in compiler_source
    assert "def _passive_rail_bank_lines" not in compiler_source
    assert "def _safe_direct_net_lines" not in compiler_source
    assert "occupied_net_segments" not in compiler_source
    assert "passive_rail_banks" not in compiler_source
    assert "label_blockers" not in compiler_source
    assert "def route_sheet_nets" not in compiler_source
    assert "from ksch.net_routing import" in compiler_source
    assert "def _net_point_lines" in net_routing_source
    assert "def _passive_rail_bank_lines" in net_routing_source
    assert "def _safe_direct_net_lines" in net_routing_source
    assert "def route_sheet_nets" in net_routing_source


def test_symbol_placement_stage_is_not_defined_in_compiler() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")
    placement_source = Path("src/ksch/placement.py").read_text(encoding="utf-8")

    assert "def _layout_sheet_symbols" not in compiler_source
    assert "def _layout_low_interface_local_circuit" not in compiler_source
    assert "def _symbol_anchor_assignments" not in compiler_source
    assert "from ksch.placement import" in compiler_source
    assert "def _layout_sheet_symbols" in placement_source
    assert "def _layout_low_interface_local_circuit" in placement_source
    assert "def _symbol_anchor_assignments" in placement_source


def test_project_file_writing_is_not_defined_in_compiler() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")
    emit_source = Path("src/ksch/emit.py").read_text(encoding="utf-8")

    assert "def _write_project_file" not in compiler_source
    assert "def _write_library_table" not in compiler_source
    assert "def _write_project_file" in emit_source
    assert "def _write_library_table" in emit_source


def test_compiler_builds_a_placed_project_before_emission() -> None:
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))

    placed = build_placed_project(resolved)

    assert isinstance(placed, PlacedProject)
    assert [sheet.path for sheet in placed.sheets] == ["/", "/usb"]
    assert [sheet.filename.as_posix() for sheet in placed.sheets] == [
        "demo.kicad_sch",
        "sheets/usb.kicad_sch",
    ]
    assert all(sheet.paper in {"A4", "A3"} for sheet in placed.sheets)
    assert any(
        isinstance(item, PlacedSymbol) and item.reference == "J1"
        for sheet in placed.sheets
        for item in sheet.items
    )


def test_compiler_validates_placed_project_before_emission() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")

    assert "validate_placed_project(placed_project)" in compiler_source
    assert "validate_placed_project(" not in Path("src/ksch/emit.py").read_text(encoding="utf-8")


def test_compiler_does_not_parse_generated_schematic_text() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")
    placed_source = Path("src/ksch/placed.py").read_text(encoding="utf-8")

    assert "_schematic_text" not in compiler_source
    assert "loads(" not in compiler_source
    assert "sexpr:" not in placed_source
