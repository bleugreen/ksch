import json
from pathlib import Path

import pytest
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


def test_cli_validate_reports_semantic_schema_path(tmp_path: Path) -> None:
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
                "nets:",
                "  USB_D_P:",
                "    - J1.NOPE",
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
    assert "nets.USB_D_P[0]" in result.stderr
    assert "J1.NOPE does not match any pin on Test:USB_C" in result.stderr


def test_cli_schema_show_outputs_json_schema() -> None:
    result = runner.invoke(app, ["schema", "show"])

    assert result.exit_code == 0, result.output
    schema = json.loads(result.stdout)
    assert schema["title"] == "ksch Schema v1"
    assert schema["properties"]["ksch"]["const"] == 1
    assert "symbols" in schema["properties"]


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


def test_cli_explain_library_symbol() -> None:
    result = runner.invoke(
        app,
        [
            "explain",
            "Test:USB_C",
            "--library",
            "Test=tests/fixtures/kicad/symbols/Test.kicad_sym",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "symbol Test:USB_C" in result.stdout
    assert "D+@A6 bidirectional" in result.stdout
    assert "D+@B6 bidirectional" in result.stdout


def test_cli_explain_project_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
                "symbols:",
                "  J1:",
                "    lib: Test:USB_C",
                "    value: USB_IN",
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
    result = runner.invoke(app, ["explain", "J1.D+"])

    assert result.exit_code == 0, result.output
    assert "ref J1" in result.stdout
    assert "lib: Test:USB_C" in result.stdout
    assert "value: USB_IN" in result.stdout
    assert "D+@A6 bidirectional" in result.stdout
    assert "D+@B6 bidirectional" in result.stdout


def test_cli_explain_project_endpoint_disambiguates_pin_number(
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
                "symbols:",
                "  J1:",
                "    lib: Test:USB_C",
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
    result = runner.invoke(app, ["explain", "J1.D+@A6"])

    assert result.exit_code == 0, result.output
    assert "D+@A6 bidirectional" in result.stdout
    assert "D+@B6 bidirectional" not in result.stdout


def test_cli_edit_connect_updates_configured_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    (library_dir / "Test.kicad_sym").write_text(
        Path("tests/fixtures/kicad/symbols/Test.kicad_sym").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
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
                "      Test: lib/Test.kicad_sym",
                "symbols:",
                "  J1:",
                "    lib: Test:USB_C",
                "nets:",
                "  USB_D_P:",
                "    - J1.D+@A6",
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
    result = runner.invoke(app, ["edit", "connect", "USB_D_P", "J1.D+@B6"])

    assert result.exit_code == 0, result.output
    assert "connected 1 endpoint" in result.stdout
    assert "    - J1.D+@B6\n" in schema.read_text(encoding="utf-8")


def test_cli_edit_add_symbol_updates_configured_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    (library_dir / "Test.kicad_sym").write_text(
        Path("tests/fixtures/kicad/symbols/Test.kicad_sym").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
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
    result = runner.invoke(
        app,
        [
            "edit",
            "add-symbol",
            "J1",
            "Test:USB_C",
            "--value",
            "USB_IN",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "added symbol J1" in result.stdout
    assert "  J1:\n    lib: Test:USB_C\n    value: USB_IN\n" in schema.read_text(
        encoding="utf-8"
    )


def test_cli_skill_show_prints_bundled_skill() -> None:
    result = runner.invoke(app, ["skill", "show"])

    assert result.exit_code == 0
    assert "name: ksch" in result.stdout
    assert "ksch gen" in result.stdout
