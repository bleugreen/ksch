from pathlib import Path

from ksch.compiler import build_placed_project, write_project
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.placed import PlacedHierarchicalLabel, PlacedLabel, PlacedWire
from ksch.resolver import LibraryContext, resolve_project
from ksch.validation import placed_layout_report


def _resolved_fixture_project():
    project = load_project_ir(Path("tests/fixtures/project/project.ksch.yaml"))
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    return resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))


def test_build_placed_project_returns_reportable_project() -> None:
    placed = build_placed_project(_resolved_fixture_project())

    report = placed_layout_report(placed)

    assert report.to_dict()["counts"] is not None
    assert [sheet.path for sheet in placed.sheets] == ["/", "/usb"]
    assert {sheet.paper for sheet in placed.sheets} == {"A3"}
    assert all(sheet.items for sheet in placed.sheets)


def test_write_project_emits_root_and_child_sheets(tmp_path: Path) -> None:
    write_project(
        _resolved_fixture_project(),
        tmp_path,
        {"Test": Path("tests/fixtures/kicad/symbols/Test.kicad_sym")},
    )

    assert (tmp_path / "demo.kicad_sch").exists()
    assert (tmp_path / "sheets" / "usb.kicad_sch").exists()


def test_build_placed_project_emits_endpoint_labels_and_stubs() -> None:
    placed = build_placed_project(_resolved_fixture_project())

    labels = [
        item.name
        for sheet in placed.sheets
        for item in sheet.items
        if isinstance(item, PlacedLabel | PlacedHierarchicalLabel)
    ]
    wires = [
        item
        for sheet in placed.sheets
        for item in sheet.items
        if isinstance(item, PlacedWire)
    ]

    assert {"+5V", "USB_UP_DP", "VBUS"} <= set(labels)
    assert wires
