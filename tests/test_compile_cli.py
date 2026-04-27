from pathlib import Path

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
