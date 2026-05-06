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
