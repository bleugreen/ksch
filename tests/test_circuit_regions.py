from pathlib import Path

from ksch.circuit_regions import build_sheet_circuit_regions
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.resolver import LibraryContext, ResolvedProject, resolve_project


def _resolved_project(tmp_path: Path, schema_text: str) -> ResolvedProject:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(schema_text, encoding="utf-8")
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    return resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))


def test_circuit_regions_group_passive_continuation_with_anchor_support(
    tmp_path: Path,
) -> None:
    resolved = _resolved_project(
        tmp_path,
        """\
ksch: 1
project:
  name: region_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 10k}
  C1: {lib: Test:C, value: 10nF}
nets:
  LOCAL_CTRL:
    - U1.USBDP_UP
    - R1.2
  LOCAL_RC:
    - R1.1
    - C1.2
  GND:
    - C1.1
""",
    )

    regions = build_sheet_circuit_regions(resolved, "/")

    assert regions.same_region("U1", "R1")
    assert regions.refs_for_anchor("U1") == ("C1", "R1")
    assert regions.same_region("R1", "C1")


def test_circuit_regions_keep_rail_banks_out_of_anchor_support_region(
    tmp_path: Path,
) -> None:
    resolved = _resolved_project(
        tmp_path,
        """\
ksch: 1
project:
  name: region_demo
symbols:
  U1: {lib: Test:USBHub}
  C1: {lib: Test:C, value: 10uF}
  C2: {lib: Test:C, value: 100nF}
  R1: {lib: Test:C, value: 10k}
nets:
  VDD:
    - U1.VBUS_DET
    - C1.1
    - C2.1
    - R1.1
  GND:
    - U1.GND/all
    - C1.2
    - C2.2
    - R1.2
""",
    )

    regions = build_sheet_circuit_regions(resolved, "/")

    assert regions.refs_for_anchor("U1") == ("R1",)
    rail_region = regions.region_for_ref("C1")
    assert rail_region is not None
    assert rail_region.kind == "rail_bank"
    assert rail_region.refs == ("C1", "C2")
