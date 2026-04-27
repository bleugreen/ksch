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
