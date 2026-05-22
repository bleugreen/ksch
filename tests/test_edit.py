from pathlib import Path

import pytest

from ksch.edit import (
    add_no_connects,
    add_symbol,
    clear_no_connects,
    connect_endpoints,
    disconnect_endpoints,
)
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
                "    connects:",
                "      D+@A6: USB_D_P",
                "      VBUS/all: VBUS",
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
    assert graph.net_for_endpoint("/", "J1.VBUS@A4") == "VBUS"
    assert graph.net_for_endpoint("/", "J1.VBUS@B4") == "VBUS"
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
    assert "      D+/all: USB_D_P\n" in text
    assert ProjectGraph.from_schema(schema).net_for_endpoint("/", "J1.D+@B6") == "USB_D_P"


def test_connect_endpoints_is_idempotent_when_existing_expression_expands(
    tmp_path: Path,
) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    result = connect_endpoints(
        schema,
        sheet_path="/",
        net_name="VBUS",
        endpoints=["J1.VBUS@A4"],
    )

    assert result.changed is False
    assert schema.read_text(encoding="utf-8") == before


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


def test_disconnect_endpoints_removes_from_net_and_formats(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)

    result = disconnect_endpoints(
        schema,
        sheet_path="/",
        net_name="USB_D_P",
        endpoints=["J1.D+@A6"],
    )

    assert result.changed is True
    assert result.removed_endpoints == ("J1.D+@A6",)
    assert result.deleted_net is True
    text = schema.read_text(encoding="utf-8")
    assert "USB_D_P" not in text
    assert "      VBUS/all: VBUS\n" in text


def test_disconnect_endpoints_splits_all_expression(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)

    result = disconnect_endpoints(
        schema,
        sheet_path="/",
        net_name="VBUS",
        endpoints=["J1.VBUS@A4"],
    )

    assert result.changed is True
    assert result.removed_endpoints == ("J1.VBUS@A4",)
    assert result.deleted_net is False
    text = schema.read_text(encoding="utf-8")
    assert "      VBUS@B4: VBUS\n" in text
    assert "J1.VBUS/all" not in text


def test_disconnect_endpoints_rejects_endpoint_on_other_net_without_writing(
    tmp_path: Path,
) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="J1.VBUS/all is connected to VBUS, not GND"):
        disconnect_endpoints(
            schema,
            sheet_path="/",
            net_name="GND",
            endpoints=["J1.VBUS/all"],
        )

    assert schema.read_text(encoding="utf-8") == before


def test_disconnect_endpoints_rejects_missing_endpoint_without_writing(
    tmp_path: Path,
) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="J1.D-@A7 is not connected in /"):
        disconnect_endpoints(
            schema,
            sheet_path="/",
            net_name="USB_D_P",
            endpoints=["J1.D-@A7"],
        )

    assert schema.read_text(encoding="utf-8") == before


def test_add_no_connects_adds_valid_unconnected_endpoint(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)

    result = add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    assert result.changed is True
    assert result.added_endpoints == ("J1.D-@A7",)
    text = schema.read_text(encoding="utf-8")
    assert "      D-@A7: nc\n" in text


def test_add_no_connects_is_idempotent(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])
    before = schema.read_text(encoding="utf-8")

    result = add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    assert result.changed is False
    assert schema.read_text(encoding="utf-8") == before


def test_add_no_connects_is_idempotent_when_existing_expression_expands(
    tmp_path: Path,
) -> None:
    schema = _write_edit_project(tmp_path)
    add_no_connects(schema, sheet_path="/", endpoints=["J1.D-/all"])
    before = schema.read_text(encoding="utf-8")

    result = add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    assert result.changed is False
    assert schema.read_text(encoding="utf-8") == before


def test_add_no_connects_collapses_duplicate_pin_group(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    result = add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@B7"])

    assert result.changed is True
    assert result.added_endpoints == ("J1.D-@B7",)
    text = schema.read_text(encoding="utf-8")
    assert "      D-/all: nc\n" in text
    assert "J1.D-@A7" not in text
    assert "J1.D-@B7" not in text


def test_add_no_connects_rejects_connected_endpoint_without_writing(
    tmp_path: Path,
) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="J1.D\\+@A6 is connected to USB_D_P"):
        add_no_connects(schema, sheet_path="/", endpoints=["J1.D+@A6"])

    assert schema.read_text(encoding="utf-8") == before


def test_clear_no_connects_removes_endpoint_and_formats(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    add_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    result = clear_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    assert result.changed is True
    assert result.removed_endpoints == ("J1.D-@A7",)
    assert "D-@A7: nc" not in schema.read_text(encoding="utf-8")


def test_clear_no_connects_splits_all_expression(tmp_path: Path) -> None:
    schema = _write_edit_project(tmp_path)
    add_no_connects(schema, sheet_path="/", endpoints=["J1.D-/all"])

    result = clear_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

    assert result.changed is True
    assert result.removed_endpoints == ("J1.D-@A7",)
    text = schema.read_text(encoding="utf-8")
    assert "      D-@B7: nc\n" in text
    assert "J1.D-/all" not in text


def test_clear_no_connects_rejects_missing_endpoint_without_writing(
    tmp_path: Path,
) -> None:
    schema = _write_edit_project(tmp_path)
    before = schema.read_text(encoding="utf-8")

    with pytest.raises(KschError, match="J1.D-@A7 is not marked no-connect in /"):
        clear_no_connects(schema, sheet_path="/", endpoints=["J1.D-@A7"])

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
