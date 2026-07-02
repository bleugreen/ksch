from pathlib import Path

from ksch.compiler import write_project
from ksch.emit import write_project as write_placed_project
from ksch.expand import load_project_ir
from ksch.ids import stable_uuid
from ksch.kicad.symbols import index_symbol_library
from ksch.placed import PlacedGraphicRectangle, PlacedProject, PlacedSheet, PlacedText
from ksch.resolver import LibraryContext, ResolvedProject, resolve_project


def _resolved() -> ResolvedProject:
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
    assert '(generator "kicad-schema")' in (tmp_path / "demo.kicad_sch").read_text()


def test_write_project_emits_kicad_required_symbol_sections(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    schematic = (tmp_path / "demo.kicad_sch").read_text()

    assert '(lib_symbols\n    (symbol "Test:USB_C"' in schematic
    assert '(lib_id "Test:USB_C")' in schematic
    assert '(pin "A6"' in schematic
    assert "(instances" in schematic
    assert '(reference "J1")' in schematic


def test_write_project_emits_sheet_pins_and_sheet_instances(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    schematic = (tmp_path / "demo.kicad_sch").read_text()

    assert '(pin "VBUS" passive' in schematic
    assert '(pin "USB_UP_DP" bidirectional' in schematic
    assert "(sheet_instances" in schematic


def test_write_project_emits_visible_net_labels_and_wires(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    root = (tmp_path / "demo.kicad_sch").read_text()
    child = (tmp_path / "sheets" / "usb.kicad_sch").read_text()

    assert "(wire" in root
    assert '(label "+5V"' in root
    assert '(label "USB_UP_DP"' in root
    assert '(hierarchical_label "VBUS"' in child


def test_write_project_emits_no_connect_markers(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    root = (tmp_path / "demo.kicad_sch").read_text()
    child = (tmp_path / "sheets" / "usb.kicad_sch").read_text()

    assert root.count("(no_connect") == 2
    assert child.count("(no_connect") == 3


def test_write_placed_project_emits_graphical_text(tmp_path: Path) -> None:
    project = PlacedProject(
        name="demo",
        sheets=(
            PlacedSheet(
                path="/",
                filename=Path("demo.kicad_sch"),
                uuid="sheet",
                paper="A4",
                lib_symbols=(),
                items=(PlacedText(text="LOCAL_PWR", at=(10.0, 20.0), uuid="text-uuid"),),
                instance_path="/",
                page="1",
            ),
        ),
    )

    write_placed_project(project, tmp_path)
    schematic = (tmp_path / "demo.kicad_sch").read_text()

    assert '(text "LOCAL_PWR"' in schematic


def test_write_placed_project_emits_graphical_rectangle_and_sized_text(tmp_path: Path) -> None:
    project = PlacedProject(
        name="demo",
        sheets=(
            PlacedSheet(
                path="/",
                filename=Path("demo.kicad_sch"),
                uuid="sheet",
                paper="A4",
                lib_symbols=(),
                items=(
                    PlacedGraphicRectangle(
                        at=(10.0, 20.0),
                        size=(40.0, 30.0),
                        uuid="frame-uuid",
                        stroke_width=0.254,
                    ),
                    PlacedText(
                        text="CAN Controller",
                        at=(12.54, 22.54),
                        uuid="title-uuid",
                        size=(2.54, 2.54),
                    ),
                ),
                instance_path="/",
                page="1",
            ),
        ),
    )

    write_placed_project(project, tmp_path)
    schematic = (tmp_path / "demo.kicad_sch").read_text()

    assert "(rectangle\n    (start 10.0 20.0)\n    (end 50.0 50.0)" in schematic
    assert "(stroke\n      (width 0.254)\n      (type solid)\n    )" in schematic
    assert "(fill\n      (type none)\n    )" in schematic
    assert '(uuid "frame-uuid")' in schematic
    assert '(text "CAN Controller"' in schematic
    assert "(size 2.54 2.54)" in schematic
    assert '(uuid "title-uuid")' in schematic
