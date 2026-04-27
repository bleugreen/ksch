from pathlib import Path

from ksch.emit import stable_uuid, write_project
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
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
    assert "(generator \"kicad-schema\")" in (tmp_path / "demo.kicad_sch").read_text()


def test_write_project_emits_kicad_required_symbol_sections(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    schematic = (tmp_path / "demo.kicad_sch").read_text()

    assert "(lib_symbols\n    (symbol \"Test:USB_C\"" in schematic
    assert "(lib_id \"Test:USB_C\")" in schematic
    assert "(pin \"A6\"" in schematic
    assert "(instances" in schematic
    assert "(reference \"J1\")" in schematic


def test_write_project_emits_sheet_pins_and_sheet_instances(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    schematic = (tmp_path / "demo.kicad_sch").read_text()

    assert "(pin \"VBUS\" passive" in schematic
    assert "(pin \"USB_UP_DP\" bidirectional" in schematic
    assert "(sheet_instances" in schematic


def test_write_project_emits_visible_net_labels_and_wires(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    root = (tmp_path / "demo.kicad_sch").read_text()
    child = (tmp_path / "sheets" / "usb.kicad_sch").read_text()

    assert "(wire" in root
    assert "(label \"+5V\"" in root
    assert "(label \"USB_UP_DP\"" in root
    assert "(hierarchical_label \"VBUS\"" in child
    assert "(label \"VBUS\"" in child


def test_write_project_emits_no_connect_markers(tmp_path: Path) -> None:
    write_project(_resolved(), tmp_path)
    root = (tmp_path / "demo.kicad_sch").read_text()
    child = (tmp_path / "sheets" / "usb.kicad_sch").read_text()

    assert root.count("(no_connect") == 2
    assert child.count("(no_connect") == 3
