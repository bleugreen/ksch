from pathlib import Path

from ksch.model.endpoint import EndpointKind
from ksch.model.ir import ProjectIR, SheetIR
from ksch.model.source import PinDirection
from ksch.resolver import ResolvedEndpoint, ResolvedProject, ResolvedSheet
from ksch.verify import (
    NetlistNet,
    compare_dirs,
    compare_netlist_to_schema,
    connectivity_signature,
    schema_net_mates,
)


def _pin(ref: str, pin_number: str, *, sheet_path: str = "/") -> ResolvedEndpoint:
    return ResolvedEndpoint(
        text=f"{ref}.{pin_number}",
        kind=EndpointKind.SYMBOL_PIN,
        sheet_path=sheet_path,
        ref=ref,
        pin_name=pin_number,
        pin_number=pin_number,
    )


def _project(*, root_nets: dict[str, list[ResolvedEndpoint]]) -> ResolvedProject:
    return ResolvedProject(
        name="demo",
        source=ProjectIR(
            name="demo",
            root_path=Path("project.ksch.yaml"),
            sheets={"/": SheetIR(path="/", source_path=Path("project.ksch.yaml"))},
        ),
        sheets={"/": ResolvedSheet(path="/", nets=root_nets)},
    )


def test_compare_netlist_to_schema_reports_merged_net_mates() -> None:
    project = _project(
        root_nets={
            "USB_DP": [_pin("J1", "A6"), _pin("U1", "1")],
            "GND": [_pin("J1", "A1"), _pin("U1", "2")],
        }
    )
    exported = {
        "GND": NetlistNet(
            name="GND",
            connections={("J1", "A6"), ("U1", "1"), ("J1", "A1"), ("U1", "2")},
        )
    }

    findings = compare_netlist_to_schema(project, exported)

    assert findings == [
        "J1.A1 net-mates differ; schema net GND mates [U1.2]; "
        "exported net GND mates [J1.A6, U1.1, U1.2]",
        "J1.A6 net-mates differ; schema net USB_DP mates [U1.1]; "
        "exported net GND mates [J1.A1, U1.1, U1.2]",
        "U1.1 net-mates differ; schema net USB_DP mates [J1.A6]; "
        "exported net GND mates [J1.A1, J1.A6, U1.2]",
        "U1.2 net-mates differ; schema net GND mates [J1.A1]; "
        "exported net GND mates [J1.A1, J1.A6, U1.1]",
    ]


def test_compare_netlist_to_schema_accepts_name_swap_with_same_topology() -> None:
    project = _project(
        root_nets={
            "USB_DP": [_pin("J1", "A6"), _pin("U1", "1")],
            "GND": [_pin("J1", "A1"), _pin("U1", "2")],
        }
    )
    exported = {
        "RENAMED_A": NetlistNet(name="RENAMED_A", connections={("J1", "A6"), ("U1", "1")}),
        "RENAMED_B": NetlistNet(name="RENAMED_B", connections={("J1", "A1"), ("U1", "2")}),
    }

    assert compare_netlist_to_schema(project, exported) == []


def test_schema_net_mates_flattens_sheet_interfaces() -> None:
    project = ResolvedProject(
        name="demo",
        source=ProjectIR(
            name="demo",
            root_path=Path("project.ksch.yaml"),
            sheets={
                "/": SheetIR(path="/", source_path=Path("project.ksch.yaml")),
                "/usb": SheetIR(
                    path="/usb",
                    source_path=Path("usb.ksch.yaml"),
                    interface={"VBUS": PinDirection.POWER_IN},
                ),
            },
        ),
        sheets={
            "/": ResolvedSheet(
                path="/",
                nets={
                    "+5V": [
                        _pin("J1", "A4"),
                        ResolvedEndpoint(
                            text="usb.VBUS",
                            kind=EndpointKind.SHEET_PORT,
                            sheet_path="/",
                            child_sheet="usb",
                            port="VBUS",
                        ),
                    ]
                },
            ),
            "/usb": ResolvedSheet(path="/usb", nets={"VBUS": [_pin("U2", "3", sheet_path="/usb")]}),
        },
    )

    mates, _nets = schema_net_mates(project)

    assert mates[("J1", "A4")] == frozenset({("U2", "3")})
    assert mates[("U2", "3")] == frozenset({("J1", "A4")})


def test_connectivity_signature_ignores_single_pin_nets() -> None:
    assert connectivity_signature(
        {
            "unconnected-(J1-Pad1)": NetlistNet(
                name="unconnected-(J1-Pad1)",
                connections={("J1", "1")},
            ),
            "REAL": NetlistNet(name="REAL", connections={("J1", "2"), ("U1", "1")}),
        }
    ) == {frozenset({("J1", "2"), ("U1", "1")})}


def test_compare_dirs_reports_missing_and_different_generated_files(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    (expected / "sheets").mkdir(parents=True)
    (actual / "sheets").mkdir(parents=True)
    (expected / "same.txt").write_text("same", encoding="utf-8")
    (actual / "same.txt").write_text("same", encoding="utf-8")
    (expected / "missing.txt").write_text("missing", encoding="utf-8")
    (actual / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    (expected / "sheets" / "changed.txt").write_text("before", encoding="utf-8")
    (actual / "sheets" / "changed.txt").write_text("after", encoding="utf-8")

    assert compare_dirs(expected, actual) == [
        "missing generated file missing.txt",
        "generated file differs sheets/changed.txt",
    ]
