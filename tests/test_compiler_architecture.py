from pathlib import Path
import ast

from ksch.compiler import build_placed_project
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.placed import PlacedProject, PlacedSymbol
from ksch.resolver import LibraryContext, resolve_project


DELETED_GEOMETRY_MODULES = (
    "src/ksch/placement.py",
    "src/ksch/routing.py",
    "src/ksch/net_routing.py",
    "src/ksch/local_topology.py",
    "src/ksch/circuit_regions.py",
    "src/ksch/circuit_motifs.py",
    "src/ksch/layout_problem.py",
)


def test_deleted_partial_geometry_modules_stay_deleted() -> None:
    for path in DELETED_GEOMETRY_MODULES:
        assert not Path(path).exists(), path


def test_compiler_uses_layout_solver_without_deleted_placement() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")

    assert "from ksch.placement import" not in compiler_source
    assert "from ksch.layout_solver import" in compiler_source
    assert "from ksch.net_routing import" not in compiler_source
    assert "from ksch.routing import" not in compiler_source
    assert "from ksch.layout_problem import" not in compiler_source
    assert "def _clear_power_flag_position" not in compiler_source
    assert "def _no_connect_pin_points" not in compiler_source


def test_canonical_geometry_owns_layout_problem_types() -> None:
    geometry_source = Path("src/ksch/schematic_geometry.py").read_text(encoding="utf-8")

    assert "class LayoutProblem" in geometry_source
    assert "class LayoutElement" in geometry_source
    assert "def placed_items_geometry" in geometry_source
    assert "def legalize_sheet_geometry" in geometry_source
    assert "def label_geometry_elements" in geometry_source


def test_deleted_placement_solver_names_stay_deleted() -> None:
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/ksch").glob("*.py")
    )

    for forbidden in (
        "layout_sheet_symbol_groups",
        "_assemble_group",
        "_group_owned_labels",
        "_solve_owned_labels",
        "layout_sheet_boundary_labels",
        "layout_sheet_power_flags",
        "_dense_symbol_labels",
        "_coalesced_rail_labels",
    ):
        assert forbidden not in production_source


def test_no_late_whole_sheet_label_rescue_names() -> None:
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/ksch").glob("*.py")
    )

    for forbidden in (
        "component_label_requests",
        "solved_component_placements",
        "relocate_overlapping_labels",
        "legalize_label_stub_paths",
        "_repair_omitted_label",
        "solve_point_label_placements",
        "legalize_dense_interface_labels",
    ):
        assert forbidden not in production_source


def test_compiler_cannot_construct_endpoint_labels_after_layout() -> None:
    compiler_path = Path("src/ksch/compiler.py")
    compiler_source = compiler_path.read_text(encoding="utf-8")

    for forbidden in (
        "EndpointLabelRequest",
        "_net_label_items",
        "_first_free_label_point",
        "_label_point_candidates",
        "_label_x_steps",
        "_label_lane_steps",
        "_label_lane_candidate_is_clear",
        "_cluster_label_requests",
        "_symbol_endpoint_point",
        "point_stub_segments",
        "sheet_symbol_pin_point",
        "text_rect",
    ):
        assert forbidden not in compiler_source

    compiler_ast = ast.parse(compiler_source, filename=str(compiler_path))
    for node in ast.walk(compiler_ast):
        if not isinstance(node, ast.For):
            continue
        iter_source = ast.unparse(node.iter)
        assert ".nets.items()" not in iter_source


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
    assert any(
        isinstance(item, PlacedSymbol) and item.reference == "J1"
        for sheet in placed.sheets
        for item in sheet.items
    )


def test_compiler_build_is_emit_always_not_validation_gated() -> None:
    compiler_source = Path("src/ksch/compiler.py").read_text(encoding="utf-8")

    assert "validate_placed_project(" not in compiler_source
    assert "legalize_sheet_geometry(" not in compiler_source
    assert "return placed_project" in compiler_source
    assert "validate_placed_project(" not in Path("src/ksch/emit.py").read_text(encoding="utf-8")
