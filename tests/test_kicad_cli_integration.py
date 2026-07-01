import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app
from ksch.verify import run_kicad_cli

runner = CliRunner()


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not installed")
def test_generated_project_is_seen_by_kicad_cli(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 0
    schematic = tmp_path / "demo.kicad_sch"
    netlist = tmp_path / "demo.net"
    cli_result = run_kicad_cli(
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "--output",
            str(netlist),
            str(schematic),
        ]
    )
    assert cli_result.returncode == 0, cli_result.stderr
    assert netlist.exists()
    netlist_text = netlist.read_text(encoding="utf-8")
    assert '(ref "J1")' in netlist_text
    assert '(ref "U2")' in netlist_text
    assert '(name "+5V")' in netlist_text or '(name "/+5V")' in netlist_text
    assert '(node (ref "J1") (pin "A4")' in netlist_text
    assert '(node (ref "J1") (pin "B4")' in netlist_text
    assert '(node (ref "U2") (pin "3")' in netlist_text
    assert '(name "/USB_UP_DP")' in netlist_text
    assert '(node (ref "J1") (pin "A6")' in netlist_text
    assert '(node (ref "J1") (pin "B6")' in netlist_text
    assert '(node (ref "U2") (pin "1")' in netlist_text

    erc_report = tmp_path / "erc.rpt"
    erc_result = run_kicad_cli(
        ["sch", "erc", "--output", str(erc_report), str(tmp_path / "demo.kicad_sch")]
    )
    assert erc_result.returncode == 0, erc_result.stderr
    assert "Found 0 violations" in erc_result.stdout


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not installed")
def test_generated_project_exports_visible_svg(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert result.exit_code == 0

    svg_dir = tmp_path / "svg"
    svg_result = run_kicad_cli(
        [
            "sch",
            "export",
            "svg",
            "--output",
            str(svg_dir),
            "--exclude-drawing-sheet",
            str(tmp_path / "demo.kicad_sch"),
        ]
    )
    assert svg_result.returncode == 0, svg_result.stderr

    root_svg = (svg_dir / "demo.svg").read_text(encoding="utf-8")
    child_svg = (svg_dir / "demo-usb.svg").read_text(encoding="utf-8")
    assert "USB_IN" in root_svg
    assert "+5V" in root_svg
    assert "USB2514B" in child_svg
    assert "VBUS" in child_svg


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not installed")
def test_verify_runs_erc_and_netlist_parity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(project),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert result.exit_code == 0, result.output

    artifacts = tmp_path / "verify-artifacts"
    verify_result = runner.invoke(
        app,
        [
            "verify",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(project),
            "--against",
            str(project / "demo.kicad_sch"),
            "--artifacts",
            str(artifacts),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert verify_result.exit_code == 0, verify_result.output
    assert "erc: 0 violation(s)" in verify_result.stdout
    assert "netlist: matches" in verify_result.stdout
    assert "drift: generated output matches" in verify_result.stdout
    assert "verification passed" in verify_result.stdout
    assert (artifacts / "erc.rpt").exists()
    assert (artifacts / "reference.net").exists()
    assert (artifacts / "generated.net").exists()
