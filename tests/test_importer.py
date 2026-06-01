import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app
from ksch.importer import (
    ImportedComponent,
    ImportedNet,
    ImportedNode,
    ImportedPin,
    SheetInfo,
    _build_schema_documents,
    _canonical_power_flag_names,
    _power_flag_net_names,
    import_project,
)
from ksch.kicad.sexpr import load_sexpr_file
from ksch.verify import connectivity_signature, parse_kicadsexpr_netlist, run_kicad_cli

runner = CliRunner()


def _export_netlist(root: Path, target: Path) -> None:
    result = run_kicad_cli(
        ["sch", "export", "netlist", "--format", "kicadsexpr", "--output", str(target), str(root)]
    )
    assert result.returncode == 0, result.stderr


def _erc_violation_count(output: str) -> int:
    match = re.search(r"Found (\d+) violations", output)
    if match is None:
        raise AssertionError(f"missing ERC violation count in output: {output}")
    return int(match.group(1))


def test_import_generated_fixture_roundtrips_connectivity(tmp_path: Path) -> None:
    source_project = tmp_path / "source"
    compiled_project = tmp_path / "compiled"
    imported_schema = tmp_path / "imported"
    result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(source_project),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert result.exit_code == 0, result.output

    imported = import_project(source_project / "demo.kicad_sch", imported_schema)

    root_text = imported.root_schema.read_text(encoding="utf-8")
    child_text = (imported_schema / "sheets" / "usb.ksch.yaml").read_text(encoding="utf-8")
    assert "J1.VBUS/all" in root_text
    assert "J1.D+/all" in root_text
    assert "usb.+5V" in root_text
    assert "U2.VBUS_DET" in child_text

    result = runner.invoke(
        app,
        ["compile", str(imported.root_schema), "--out", str(compiled_project)],
    )
    assert result.exit_code == 0, result.output

    original_netlist = tmp_path / "original.net"
    compiled_netlist = tmp_path / "compiled.net"
    _export_netlist(source_project / "demo.kicad_sch", original_netlist)
    _export_netlist(compiled_project / "demo.kicad_sch", compiled_netlist)
    original = connectivity_signature(parse_kicadsexpr_netlist(original_netlist))
    compiled = connectivity_signature(parse_kicadsexpr_netlist(compiled_netlist))
    assert original == compiled


def test_import_command_reports_child_sheets(tmp_path: Path) -> None:
    source_project = tmp_path / "source"
    imported_schema = tmp_path / "imported"
    compile_result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(source_project),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert compile_result.exit_code == 0, compile_result.output

    result = runner.invoke(
        app,
        [
            "import",
            str(source_project / "demo.kicad_sch"),
            "--out",
            str(imported_schema),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "wrote 1 child sheet schema" in result.output
    assert "sheets/usb.ksch.yaml" in result.output


def test_import_maps_kicad_unconnected_nets_to_no_connects(tmp_path: Path) -> None:
    docs = _build_schema_documents(
        root_name="demo",
        project_dir=tmp_path,
        out_dir=tmp_path / "imported",
        sheets={"/": SheetInfo(sheet_path="/", source=tmp_path / "demo.kicad_sch")},
        sheet_by_file={"demo.kicad_sch": "/"},
        components={
            "J1": ImportedComponent(
                ref="J1",
                lib_id="Test:Connector",
                value="Connector",
                footprint=None,
                fields={},
                sheet_path="/",
                sheet_file="demo.kicad_sch",
            )
        },
        symbol_pins={
            "Test:Connector": {
                "A8": ImportedPin(number="A8", name="SBU1", electrical_type="passive")
            }
        },
        symbol_units={},
        nets=[
            ImportedNet(
                name="unconnected-(J1-SBU1-PadA8)",
                nodes=[ImportedNode(ref="J1", pin_number="A8", pin_name="SBU1")],
            )
        ],
    )

    assert docs["/"]["no_connects"] == ["J1.A8"]
    assert "nets" not in docs["/"]


def test_import_drops_no_connects_that_are_connected_by_netlist(tmp_path: Path) -> None:
    docs = _build_schema_documents(
        root_name="demo",
        project_dir=tmp_path,
        out_dir=tmp_path / "imported",
        sheets={"/": SheetInfo(sheet_path="/", source=tmp_path / "demo.kicad_sch")},
        sheet_by_file={"demo.kicad_sch": "/"},
        components={
            "U1": ImportedComponent(
                ref="U1",
                lib_id="Test:Device",
                value="Device",
                footprint=None,
                fields={},
                sheet_path="/",
                sheet_file="demo.kicad_sch",
            )
        },
        symbol_pins={
            "Test:Device": {
                "1": ImportedPin(number="1", name="GPIO27", electrical_type="passive")
            }
        },
        symbol_units={},
        nets=[
            ImportedNet(
                name="CM5_5V_IN",
                nodes=[ImportedNode(ref="U1", pin_number="1", pin_name="GPIO27")],
            )
        ],
        no_connects={"/": ["U1.GPIO27"]},
    )

    assert "no_connects" not in docs["/"]
    assert docs["/"]["nets"] == {"CM5_5V_IN": ["U1.GPIO27"]}


def test_import_preserves_project_footprint_libraries(tmp_path: Path) -> None:
    project = tmp_path / "project"
    out = tmp_path / "imported"
    project.mkdir()
    (project / "fp-lib-table").write_text(
        """\
(fp_lib_table
  (version 7)
  (lib
    (name "LocalFootprints")
    (type "KiCad")
    (uri "${KIPRJMOD}/lib/footprints/LocalFootprints.pretty")
    (options "")
    (descr "Local footprints")
  )
)
""",
        encoding="utf-8",
    )

    docs = _build_schema_documents(
        root_name="demo",
        project_dir=project,
        out_dir=out,
        sheets={"/": SheetInfo(sheet_path="/", source=project / "demo.kicad_sch")},
        sheet_by_file={"demo.kicad_sch": "/"},
        components={},
        symbol_pins={},
        symbol_units={},
        nets=[],
    )

    assert docs["/"]["libraries"]["footprints"]["project"]["LocalFootprints"].endswith(
        "LocalFootprints.pretty"
    )


def test_import_maps_power_flags_to_connected_label_nets(tmp_path: Path) -> None:
    schematic = tmp_path / "demo.kicad_sch"
    schematic.write_text(
        """\
(kicad_sch
  (symbol
    (lib_id "power:PWR_FLAG")
    (at 10 10 0)
    (property "Reference" "#FLG0101")
  )
  (wire
    (pts
      (xy 10 10) (xy 20 10)
    )
  )
  (label "+5V"
    (at 20 10 0)
  )
)
""",
        encoding="utf-8",
    )

    assert _power_flag_net_names(load_sexpr_file(schematic)) == ["+5V"]


def test_import_writes_power_flags_to_schema_documents(tmp_path: Path) -> None:
    docs = _build_schema_documents(
        root_name="demo",
        project_dir=tmp_path,
        out_dir=tmp_path / "imported",
        sheets={"/": SheetInfo(sheet_path="/", source=tmp_path / "demo.kicad_sch")},
        sheet_by_file={"demo.kicad_sch": "/"},
        components={},
        symbol_pins={},
        symbol_units={},
        nets=[],
        power_flags={"/": ["+5V"]},
    )

    assert docs["/"]["power_flags"] == ["+5V"]


def test_import_canonicalizes_power_flag_names_to_netlist_names() -> None:
    assert _canonical_power_flag_names(
        ["USB_ESI_VBUS"],
        {"USB Hub + Ports_USB_ESI_VBUS", "CM5_5V_IN"},
    ) == ["USB Hub + Ports_USB_ESI_VBUS"]


@pytest.mark.skipif(
    not Path("/Users/mitch/projects/cm5-hudsp/cm5hudsp/cm5hudsp.kicad_sch").exists()
    or shutil.which("kicad-cli") is None,
    reason="cm5-hudsp fixture project or kicad-cli is not available",
)
def test_import_cm5_hudsp_roundtrip_smoke(tmp_path: Path) -> None:
    source = Path("/Users/mitch/projects/cm5-hudsp/cm5hudsp/cm5hudsp.kicad_sch")
    imported = import_project(source, tmp_path / "imported")
    compiled = tmp_path / "compiled"

    result = runner.invoke(app, ["compile", str(imported.root_schema), "--out", str(compiled)])
    assert result.exit_code == 0, result.output

    original_netlist = tmp_path / "original.net"
    compiled_netlist = tmp_path / "compiled.net"
    _export_netlist(source, original_netlist)
    _export_netlist(compiled / "cm5hudsp.kicad_sch", compiled_netlist)
    original = connectivity_signature(parse_kicadsexpr_netlist(original_netlist))
    roundtrip = connectivity_signature(parse_kicadsexpr_netlist(compiled_netlist))

    assert original == roundtrip

    original_erc_report = tmp_path / "original-erc.rpt"
    original_erc = run_kicad_cli(
        [
            "sch",
            "erc",
            "--output",
            str(original_erc_report),
            str(source),
        ]
    )
    assert original_erc.returncode == 0, original_erc.stderr

    erc_report = tmp_path / "erc.rpt"
    erc = run_kicad_cli(
        [
            "sch",
            "erc",
            "--output",
            str(erc_report),
            str(compiled / "cm5hudsp.kicad_sch"),
        ]
    )
    assert erc.returncode == 0, erc.stderr
    assert _erc_violation_count(erc.stdout) <= _erc_violation_count(original_erc.stdout)
    report_text = erc_report.read_text(encoding="utf-8")
    assert "Errors 0" in report_text
    assert "[multiple_net_names]" not in report_text
    assert "[pin_not_connected]" not in report_text
    assert "[wire_dangling]" not in report_text
