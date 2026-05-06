from pathlib import Path

import pytest

from ksch.edit import add_symbol, connect_endpoints
from ksch.errors import KschError
from ksch.graph import ProjectGraph


def _write_edit_project(tmp_path: Path) -> Path:
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
                "  name: edit-demo",
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
                "  VBUS:",
                "    - J1.VBUS/all",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return schema


def test_project_graph_indexes_endpoint_nets(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)

    graph = ProjectGraph.from_schema(schema)

    assert graph.net_for_endpoint("/", "J1.D+@A6") == "USB_D_P"
    assert graph.net_for_endpoint("/", "J1.VBUS/all") == "VBUS"
    assert graph.net_for_endpoint("/", "J1.D+@B6") is None


def test_connect_endpoints_adds_to_existing_net_and_formats(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)

    result = connect_endpoints(
        schema,
        sheet_path="/",
        net_name="USB_D_P",
        endpoints=["J1.D+@B6"],
    )

    assert result.changed is True
    assert result.schema_path == schema
    text = schema.read_text(encoding="utf-8")
    assert "  USB_D_P:\n    - J1.D+@A6\n    - J1.D+@B6\n" in text
    assert ProjectGraph.from_schema(schema).net_for_endpoint("/", "J1.D+@B6") == "USB_D_P"


def test_connect_endpoints_is_idempotent(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    result = connect_endpoints(
        schema,
        sheet_path="/",
        net_name="USB_D_P",
        endpoints=["J1.D+@A6"],
    )

    assert result.changed is False
    assert schema.read_text(encoding="utf-8") == before


def test_connect_endpoints_rejects_cross_net_conflict(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="already connected to VBUS"):
        connect_endpoints(
            schema,
            sheet_path="/",
            net_name="GND",
            endpoints=["J1.VBUS/all"],
        )

    assert schema.read_text(encoding="utf-8") == before


def test_add_symbol_writes_symbol_decl_and_validates_library(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)

    result = add_symbol(
        schema,
        sheet_path="/",
        ref="U1",
        lib_id="Test:USB_C",
        value="USB_OUT",
        footprint="Connector_USB:USB_C_Receptacle",
    )

    assert result.changed is True
    text = schema.read_text(encoding="utf-8")
    assert (
        "  U1:\n"
        "    lib: Test:USB_C\n"
        "    value: USB_OUT\n"
        "    footprint: Connector_USB:USB_C_Receptacle\n"
    ) in text


def test_add_symbol_rejects_duplicate_ref_without_writing(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="symbol J1 already exists"):
        add_symbol(schema, sheet_path="/", ref="J1", lib_id="Test:USB_C")

    assert schema.read_text(encoding="utf-8") == before


def test_add_symbol_rejects_unknown_library_without_writing(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="unknown symbol library id Missing:Part"):
        add_symbol(schema, sheet_path="/", ref="U1", lib_id="Missing:Part")

    assert schema.read_text(encoding="utf-8") == before
