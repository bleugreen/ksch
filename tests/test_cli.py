from pathlib import Path

from typer.testing import CliRunner

from ksch.cli import app

runner = CliRunner()


def test_cli_validate_accepts_fixture() -> None:
    result = runner.invoke(
        app,
        [
            "validate",
            "tests/fixtures/project/project.ksch.yaml",
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 0
    assert "valid" in result.stdout


def test_cli_validate_resolves_symbol_references() -> None:
    result = runner.invoke(app, ["validate", "tests/fixtures/project/project.ksch.yaml"])

    assert result.exit_code == 1
    assert "unknown symbol library id Test:USB_C" in result.stderr


def test_cli_validate_rejects_unknown_declared_symbol_library(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "symbols:",
                "  U1:",
                "    lib: Missing:Part",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(schema)])

    assert result.exit_code == 1
    assert "unknown symbol library id Missing:Part" in result.stderr


def test_cli_validate_resolves_no_connect_endpoints(tmp_path: Path) -> None:
    schema = tmp_path / "project.ksch.yaml"
    schema.write_text(
        "\n".join(
            [
                "ksch: 1",
                "project:",
                "  name: demo",
                "symbols:",
                "  J1:",
                "    lib: Test:USB_C",
                "no_connects:",
                "  - J1.NOPE",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "validate",
            str(schema),
            "--symbol-library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 1
    assert "J1.NOPE does not match any pin on Test:USB_C" in result.stderr


def test_cli_expand_lists_sheets() -> None:
    result = runner.invoke(app, ["expand", "tests/fixtures/project/project.ksch.yaml"])

    assert result.exit_code == 0
    assert "/usb" in result.stdout


def test_cli_symbol_info_uses_fixture_library() -> None:
    result = runner.invoke(
        app,
        [
            "symbol",
            "info",
            "Test:USB_C",
            "--library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 0
    assert "D+@A6" in result.stdout
    assert "D+@B6" in result.stdout


def test_cli_skill_show_prints_bundled_skill() -> None:
    result = runner.invoke(app, ["skill", "show"])

    assert result.exit_code == 0
    assert "name: ksch" in result.stdout
    assert "ksch gen" in result.stdout
