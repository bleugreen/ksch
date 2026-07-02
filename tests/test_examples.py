from pathlib import Path

from typer.testing import CliRunner

from ksch.cli import app

runner = CliRunner()


def test_basic_board_example_compiles(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "compile",
            "examples/basic-board/schematic/project.ksch.yaml",
            "--out",
            str(tmp_path / "kicad"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "kicad" / "basic-board.kicad_sch").exists()


def test_can_controller_example_validates_and_compiles(tmp_path: Path) -> None:
    validate_result = runner.invoke(
        app,
        [
            "validate",
            "examples/can-controller/schematic/project.ksch.yaml",
        ],
    )
    assert validate_result.exit_code == 0, validate_result.output

    compile_result = runner.invoke(
        app,
        [
            "compile",
            "examples/can-controller/schematic/project.ksch.yaml",
            "--out",
            str(tmp_path / "kicad"),
        ],
    )

    assert compile_result.exit_code == 0, compile_result.output
    assert (tmp_path / "kicad" / "can-controller.kicad_sch").exists()
