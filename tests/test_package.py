import tomllib
from pathlib import Path

from typer.testing import CliRunner

import ksch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_package_exports_version() -> None:
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert ksch.__version__ == metadata["project"]["version"]


def test_cli_reports_version() -> None:
    from ksch.cli import app

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"ksch {ksch.__version__}"


def test_cli_help_is_available() -> None:
    from ksch.cli import app

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Usage" in result.output
