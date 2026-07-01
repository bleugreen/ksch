import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ksch.cli as cli
from ksch.cli import app

runner = CliRunner()


def test_check_reports_clean_generated_output(tmp_path: Path) -> None:
    compile_result = runner.invoke(
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
    assert compile_result.exit_code == 0

    check_result = runner.invoke(
        app,
        [
            "check",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert check_result.exit_code == 0
    assert "generated output matches schema" in check_result.stdout


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not installed")
def test_verify_reports_clean_generated_output_without_erc(tmp_path: Path) -> None:
    compile_result = runner.invoke(
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
    assert compile_result.exit_code == 0

    verify_result = runner.invoke(
        app,
        [
            "verify",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--no-erc",
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert verify_result.exit_code == 0
    assert "netlist parity: schema matches generated schematic" in verify_result.stdout
    assert "drift: generated output matches" in verify_result.stdout
    assert "verification passed" in verify_result.stdout


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not installed")
def test_verify_reports_generated_output_drift(tmp_path: Path) -> None:
    compile_result = runner.invoke(
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
    assert compile_result.exit_code == 0
    (tmp_path / "demo.kicad_sch").write_text("manual edit\n", encoding="utf-8")

    verify_result = runner.invoke(
        app,
        [
            "verify",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--no-erc",
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert verify_result.exit_code == 1
    assert "drift: generated file differs demo.kicad_sch" in verify_result.stdout


def test_verify_fails_when_generated_netlist_merges_schema_nets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compile_result = runner.invoke(
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
    assert compile_result.exit_code == 0

    def fake_export_kicad_netlist(_schematic: Path, target: Path) -> None:
        target.write_text(
            "\n".join(
                [
                    "(export",
                    "  (nets",
                    "    (net (code \"1\") (name \"SHORT\")",
                    "      (node (ref \"J1\") (pin \"A4\"))",
                    "      (node (ref \"J1\") (pin \"B4\"))",
                    "      (node (ref \"J1\") (pin \"A6\"))",
                    "      (node (ref \"J1\") (pin \"B6\"))",
                    "      (node (ref \"U2\") (pin \"1\"))",
                    "      (node (ref \"U2\") (pin \"3\"))))",
                    ")",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(cli, "export_kicad_netlist", fake_export_kicad_netlist)

    verify_result = runner.invoke(
        app,
        [
            "verify",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(tmp_path),
            "--no-erc",
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert verify_result.exit_code == 1
    assert "netlist parity: J1.A4 net-mates differ" in verify_result.stdout
    assert "schema net +5V" in verify_result.stdout
    assert "exported net SHORT" in verify_result.stdout
