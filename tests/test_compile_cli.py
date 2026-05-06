from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app

runner = CliRunner()


def test_compile_writes_project(tmp_path: Path) -> None:
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
    assert (tmp_path / "demo.kicad_pro").exists()
    assert (tmp_path / "demo.kicad_sch").exists()
    assert (tmp_path / "sym-lib-table").exists()


def test_compile_uses_schema_project_symbol_libraries(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "libraries:",
                "  symbols:",
                "    project:",
                "      - Test.kicad_sym",
                "symbols:",
                "  J1:",
                "    lib: Test:USB_C",
                "nets:",
                "  +5V:",
                "    - J1.VBUS/all",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "Test.kicad_sym").write_text(
        Path("tests/fixtures/kicad/symbols/Test.kicad_sym").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["compile", str(schema), "--out", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "sym-lib-table").read_text(encoding="utf-8").count("Test") >= 1


def test_compile_uses_schema_project_footprint_libraries(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "libraries:",
                "  footprints:",
                "    project:",
                "      TestFootprints: TestFootprints.pretty",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "TestFootprints.pretty").mkdir()

    result = runner.invoke(app, ["compile", str(schema), "--out", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert "TestFootprints" in (tmp_path / "out" / "fp-lib-table").read_text(encoding="utf-8")


def test_compile_emits_power_flags(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "power_flags:",
                "  - +5V",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["compile", str(schema), "--out", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    schematic = (tmp_path / "out" / "demo.kicad_sch").read_text(encoding="utf-8")
    assert '(symbol "power:PWR_FLAG"' in schematic
    assert '(lib_id "power:PWR_FLAG")' in schematic
    assert '(label "+5V"' in schematic
    assert "(hide yes)" in schematic


def test_gen_uses_project_config_from_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "starter-board"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    monkeypatch.chdir(project_dir)
    result = runner.invoke(app, ["gen"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "kicad" / "starter-board.kicad_sch").exists()


def test_gen_uses_config_symbol_library_relative_to_config_path(tmp_path: Path) -> None:
    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    (library_dir / "Test.kicad_sym").write_text(
        Path("tests/fixtures/kicad/symbols/Test.kicad_sym").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "project.ksch.yaml").write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "symbols:",
                "  J1:",
                "    lib: Test:USB_C",
                "nets:",
                "  +5V:",
                "    - J1.VBUS/all",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "ksch.toml").write_text(
        "\n".join(
            [
                'schema = "project.ksch.yaml"',
                'out = "out"',
                'symbol_library = ["Test=lib/Test.kicad_sym"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["gen", "--config", str(tmp_path / "ksch.toml")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "demo.kicad_sch").exists()


def test_check_uses_project_config_from_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "starter-board"
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    monkeypatch.chdir(project_dir)
    gen_result = runner.invoke(app, ["gen"])
    assert gen_result.exit_code == 0, gen_result.output

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0, result.output
    assert "matches schema" in result.output
