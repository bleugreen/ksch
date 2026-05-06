import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app

runner = CliRunner()


def test_init_writes_compilable_project(tmp_path: Path) -> None:
    target = tmp_path / "starter-board"

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code == 0, result.output
    assert (target / "schematic" / "project.ksch.yaml").exists()
    assert (target / "schematic" / "lib" / "Starter.kicad_sym").exists()
    assert (target / "scripts" / "gen-schematic.sh").exists()
    assert (target / "ksch.toml").exists()
    assert (target / "kicad").is_dir()

    compile_result = runner.invoke(
        app,
        [
            "compile",
            str(target / "schematic" / "project.ksch.yaml"),
            "--out",
            str(target / "kicad"),
        ],
    )

    assert compile_result.exit_code == 0, compile_result.output
    assert (target / "kicad" / "starter-board.kicad_sch").exists()


def test_init_generator_script_runs(tmp_path: Path) -> None:
    target = tmp_path / "starter-board"
    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 0, result.output

    ksch = shutil.which("ksch")
    if ksch is None:
        pytest.skip("ksch console script is not available")

    script_result = subprocess.run(
        [str(target / "scripts" / "gen-schematic.sh")],
        check=False,
        env={**os.environ, "KSCH_BIN": ksch},
        capture_output=True,
        text=True,
    )

    assert script_result.returncode == 0, script_result.stderr
    assert (target / "kicad" / "starter-board.kicad_sch").exists()


def test_init_refuses_non_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "starter-board"
    target.mkdir()
    (target / "README.md").write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code == 1
    stderr = result.stderr.replace("\n", " ")
    assert "already exists" in stderr
    assert "not" in stderr
    assert "empty" in stderr


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not available")
def test_init_from_existing_kicad_project_imports_schema(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    compile_result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(existing),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert compile_result.exit_code == 0, compile_result.output

    result = runner.invoke(app, ["init", str(existing)], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Found KiCad schematic" in result.output
    assert "wrote 1 child sheet schema" in result.output
    assert "ksch/sheets/usb.ksch.yaml" in result.output
    assert (existing / "ksch" / "project.ksch.yaml").exists()
    assert (existing / "ksch" / "sheets" / "usb.ksch.yaml").exists()
    assert (existing / "ksch.toml").read_text(encoding="utf-8") == (
        'schema = "ksch/project.ksch.yaml"\n'
        'out = "."\n'
    )
    assert (existing / "scripts" / "gen-ksch-schematic.sh").exists()
    assert (existing / "demo.kicad_sch").exists()

    roundtrip = runner.invoke(
        app,
        [
            "compile",
            str(existing / "ksch" / "project.ksch.yaml"),
            "--out",
            str(existing),
        ],
    )
    assert roundtrip.exit_code == 0, roundtrip.output
    assert (existing / "demo.kicad_sch").exists()


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not available")
def test_init_defaults_to_current_directory_for_existing_kicad_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / "existing"
    compile_result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(existing),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert compile_result.exit_code == 0, compile_result.output

    monkeypatch.chdir(existing)
    result = runner.invoke(app, ["init", "--yes"])

    assert result.exit_code == 0, result.output
    assert (existing / "ksch" / "project.ksch.yaml").exists()
    assert (existing / "ksch.toml").read_text(encoding="utf-8") == (
        'schema = "ksch/project.ksch.yaml"\n'
        'out = "."\n'
    )


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli is not available")
def test_init_detects_child_kicad_project_and_targets_child_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project_dir = repo / "board"
    compile_result = runner.invoke(
        app,
        [
            "compile",
            "tests/fixtures/project/project.ksch.yaml",
            "--out",
            str(project_dir),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )
    assert compile_result.exit_code == 0, compile_result.output

    result = runner.invoke(app, ["init", str(repo), "--yes"])

    assert result.exit_code == 0, result.output
    assert (repo / "ksch" / "project.ksch.yaml").exists()
    assert (repo / "ksch.toml").read_text(encoding="utf-8") == (
        'schema = "ksch/project.ksch.yaml"\n'
        'out = "board"\n'
    )
