from pathlib import Path

from typer.testing import CliRunner

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
