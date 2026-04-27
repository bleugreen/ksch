import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app
from ksch.importer import import_project
from ksch.verify import connectivity_signature, parse_kicadsexpr_netlist, run_kicad_cli

runner = CliRunner()


def _export_netlist(root: Path, target: Path) -> None:
    result = run_kicad_cli(
        ["sch", "export", "netlist", "--format", "kicadsexpr", "--output", str(target), str(root)]
    )
    assert result.returncode == 0, result.stderr


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

    assert len(original - roundtrip) <= 11
    assert len(roundtrip - original) <= 3
