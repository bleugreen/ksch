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
