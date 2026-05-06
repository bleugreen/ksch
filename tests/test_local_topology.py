from pathlib import Path

from ksch.expand import load_project_ir
from ksch.geometry import PinPoint
from ksch.kicad.symbols import index_symbol_library
from ksch.local_topology import build_local_topology
from ksch.resolver import LibraryContext, resolve_project


def test_local_topology_classifies_anchor_passive_nets(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: topology_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 10k}
nets:
  SIG_A:
    - U1.USBDP_UP
    - R1.2
  SENSE_A:
    - R1.1
""",
        encoding="utf-8",
    )
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    topology = build_local_topology(
        resolved,
        "/",
        {
            "SIG_A": [
                ("U1.USBDP_UP", PinPoint(100.0, 50.0, 95.0, 50.0)),
                ("R1.2", PinPoint(80.0, 55.0, 80.0, 60.0)),
            ],
            "SENSE_A": [("R1.1", PinPoint(80.0, 45.0, 80.0, 40.0))],
        },
    )

    assert [route.net_name for route in topology.anchor_passive_nets] == ["SIG_A"]
    route = topology.anchor_passive_nets[0]
    assert route.anchor.ref == "U1"
    assert route.passive.ref == "R1"
    assert route.endpoints == (
        ("U1.USBDP_UP", PinPoint(100.0, 50.0, 95.0, 50.0)),
        ("R1.2", PinPoint(80.0, 55.0, 80.0, 60.0)),
    )


def test_local_topology_classifies_passive_continuation_nets(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        """\
ksch: 1
project:
  name: topology_demo
symbols:
  U1: {lib: Test:USBHub}
  R1: {lib: Test:C, value: 100k}
  C1: {lib: Test:C, value: 10nF}
nets:
  COMP:
    - U1.USBDP_UP
    - R1.2
  COMP_RC:
    - R1.1
    - C1.2
  GND:
    - C1.1
""",
        encoding="utf-8",
    )
    project = load_project_ir(schema)
    symbols = index_symbol_library("Test", Path("tests/fixtures/kicad/symbols/Test.kicad_sym"))
    resolved = resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))
    topology = build_local_topology(
        resolved,
        "/",
        {
            "COMP": [
                ("U1.USBDP_UP", PinPoint(100.0, 50.0, 95.0, 50.0)),
                ("R1.2", PinPoint(80.0, 55.0, 80.0, 60.0)),
            ],
            "COMP_RC": [
                ("R1.1", PinPoint(80.0, 45.0, 75.0, 45.0)),
                ("C1.2", PinPoint(60.0, 45.0, 55.0, 45.0)),
            ],
            "GND": [("C1.1", PinPoint(60.0, 35.0, 55.0, 35.0))],
        },
    )

    assert [route.net_name for route in topology.passive_continuation_nets] == ["COMP_RC"]
    route = topology.passive_continuation_nets[0]
    assert route.anchor.ref == "U1"
    assert route.source.ref == "R1"
    assert route.passive.ref == "C1"
    assert route.endpoints == (
        ("R1.1", PinPoint(80.0, 45.0, 75.0, 45.0)),
        ("C1.2", PinPoint(60.0, 45.0, 55.0, 45.0)),
    )
