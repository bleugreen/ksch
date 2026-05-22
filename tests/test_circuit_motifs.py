from pathlib import Path

from ksch.circuit_motifs import build_sheet_circuit_motifs
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.migrate import migrate_file_to_connects
from ksch.resolver import LibraryContext, ResolvedProject, resolve_project


def _resolved_project(tmp_path: Path, schema_text: str) -> ResolvedProject:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(schema_text, encoding="utf-8")
    migrate_file_to_connects(schema)
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    return resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))


def test_circuit_motifs_find_generic_tap_stack(tmp_path: Path) -> None:
    resolved = _resolved_project(
        tmp_path,
        """\
ksch: 1
project:
  name: motif_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 52.3k}
  R2: {lib: Test:C, value: 10.0k}
nets:
  SENSE_NODE:
    - U1.USBDP_UP
    - R1.2
    - R2.1
  VOUT:
    - R1.1
  GND:
    - R2.2
""",
    )

    motifs = build_sheet_circuit_motifs(resolved, "/")

    assert [(motif.ref, motif.kind) for motif in motifs.two_pin_refs] == [
        ("R1", "series_path"),
        ("R2", "shunt"),
    ]
    assert len(motifs.tap_stacks) == 1
    stack = motifs.tap_stacks[0]
    assert stack.anchor_ref == "U1"
    assert stack.anchor_pin_name == "USBDP_UP"
    assert stack.tap_net == "SENSE_NODE"
    assert stack.top_ref == "R1"
    assert stack.top_net == "VOUT"
    assert stack.bottom_ref == "R2"
    assert stack.bottom_net == "GND"


def test_circuit_motifs_find_two_cap_rail_bank_without_resistor_bank(
    tmp_path: Path,
) -> None:
    resolved = _resolved_project(
        tmp_path,
        """\
ksch: 1
project:
  name: motif_demo
symbols:
  C1: {lib: Test:C, value: 10uF}
  C2: {lib: Test:C, value: 100nF}
  R1: {lib: Test:C, value: 10k}
nets:
  VDD:
    - C1.1
    - C2.1
    - R1.1
  GND:
    - C1.2
    - C2.2
    - R1.2
""",
    )

    motifs = build_sheet_circuit_motifs(resolved, "/")

    assert [(motif.ref, motif.kind) for motif in motifs.two_pin_refs] == [
        ("C1", "shunt"),
        ("C2", "shunt"),
        ("R1", "shunt"),
    ]
    assert len(motifs.rail_banks) == 1
    assert motifs.rail_banks[0].top_net == "VDD"
    assert motifs.rail_banks[0].bottom_net == "GND"
    assert motifs.rail_banks[0].refs == ("C1", "C2")
