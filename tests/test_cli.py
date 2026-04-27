from typer.testing import CliRunner

from ksch.cli import app

runner = CliRunner()


def test_cli_validate_accepts_fixture() -> None:
    result = runner.invoke(app, ["validate", "tests/fixtures/project/project.ksch.yaml"])

    assert result.exit_code == 0
    assert "valid" in result.stdout


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
