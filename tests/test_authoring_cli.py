from pathlib import Path

import pytest
from typer.testing import CliRunner

from ksch.cli import app

runner = CliRunner()


def test_symbols_search() -> None:
    result = runner.invoke(
        app,
        [
            "symbols",
            "search",
            "usb",
            "--library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 0
    assert "Test:USB_C" in result.stdout


def test_pin_search() -> None:
    result = runner.invoke(
        app,
        [
            "pin-search",
            "Test:USB_C",
            "D+",
            "--library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 0
    assert "D+@A6" in result.stdout
    assert "D+@B6" in result.stdout


def test_symbols_search_uses_schema_project_libraries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                "libraries:",
                "  symbols:",
                "    project:",
                "      Test: lib/Test.kicad_sym",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "ksch.toml").write_text(
        'schema = "project.ksch.yaml"\nout = "generated"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["symbols", "search", "usb"])

    assert result.exit_code == 0, result.output
    assert "Test:USB_C" in result.stdout


def test_pin_search_uses_generated_kicad_library_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "board"
    symbols_dir = out_dir / "symbols"
    symbols_dir.mkdir(parents=True)
    (symbols_dir / "Test.kicad_sym").write_text(
        Path("tests/fixtures/kicad/symbols/Test.kicad_sym").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (out_dir / "sym-lib-table").write_text(
        """\
(sym_lib_table
  (lib
    (name "Test")
    (type "KiCad")
    (uri "${KIPRJMOD}/symbols/Test.kicad_sym")
    (options "")
    (descr "Test symbols"))
)
""",
        encoding="utf-8",
    )
    (tmp_path / "project.ksch.yaml").write_text(
        "ksch: 1\nproject:\n  name: demo\n",
        encoding="utf-8",
    )
    (tmp_path / "ksch.toml").write_text(
        'schema = "project.ksch.yaml"\nout = "board"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["pin-search", "Test:USB_C", "D+"])

    assert result.exit_code == 0, result.output
    assert "D+@A6" in result.stdout
    assert "D+@B6" in result.stdout


def test_doctor_reports_missing_project_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "project.ksch.yaml").write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "libraries:",
                "  symbols:",
                "    project:",
                "      Missing: lib/Missing.kicad_sym",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "ksch.toml").write_text(
        'schema = "project.ksch.yaml"\nout = "generated"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "missing symbol library Missing" in result.stdout
