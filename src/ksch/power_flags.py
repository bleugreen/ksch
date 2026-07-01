from __future__ import annotations

from typing import Any

from sexpdata import Symbol  # type: ignore[import-untyped]

from ksch.ids import stable_uuid
from ksch.layout import Point, snap_grid
from ksch.placed import PlacedProperty, PlacedSymbol, PlacedSymbolPin

POWER_FLAG_LIB_ID = "power:PWR_FLAG"
POWER_FLAG_VALUE = "PWR_FLAG"
POWER_PORT_LIB_ID = "power:KSCH_POWER_PORT"
POWER_DRIVER_LIB_ID = "power:KSCH_POWER_DRIVER"


def _a(value: str) -> Symbol:
    return Symbol(value)


def _symbol_definition_effects(*, hidden: bool = False) -> list[Any]:
    expr: list[Any] = [_a("effects"), [_a("font"), [_a("size"), 1.27, 1.27]]]
    if hidden:
        expr.append([_a("hide"), _a("yes")])
    return expr


def _symbol_definition_property(
    name: str,
    value: str,
    *,
    at: tuple[float, float] = (0.0, 0.0),
    hidden: bool = False,
) -> list[Any]:
    return [
        _a("property"),
        name,
        value,
        [_a("at"), at[0], at[1], 0],
        _symbol_definition_effects(hidden=hidden),
    ]


def power_flag_symbol_definition() -> list[Any]:
    return [
        _a("symbol"),
        POWER_FLAG_LIB_ID,
        [_a("power")],
        [_a("pin_numbers"), [_a("hide"), _a("yes")]],
        [_a("pin_names"), [_a("offset"), 0], [_a("hide"), _a("yes")]],
        [_a("exclude_from_sim"), _a("no")],
        [_a("in_bom"), _a("yes")],
        [_a("on_board"), _a("yes")],
        _symbol_definition_property("Reference", "#FLG", at=(0.0, 1.905), hidden=True),
        _symbol_definition_property("Value", POWER_FLAG_VALUE, at=(0.0, 3.81), hidden=True),
        _symbol_definition_property("Footprint", "", hidden=True),
        _symbol_definition_property("Datasheet", "~", hidden=True),
        _symbol_definition_property(
            "Description",
            "Special symbol for telling ERC where power comes from",
            hidden=True,
        ),
        _symbol_definition_property("ki_keywords", "flag power", hidden=True),
        [
            _a("symbol"),
            "PWR_FLAG_0_0",
            [
                _a("pin"),
                _a("power_out"),
                _a("line"),
                [_a("at"), 0, 0, 90],
                [_a("length"), 0],
                [_a("name"), "~", _symbol_definition_effects()],
                [_a("number"), "1", _symbol_definition_effects()],
            ],
        ],
        [
            _a("symbol"),
            "PWR_FLAG_0_1",
            [
                _a("polyline"),
                [
                    _a("pts"),
                    [_a("xy"), 0, 0],
                    [_a("xy"), 0, 1.27],
                    [_a("xy"), -1.016, 1.905],
                    [_a("xy"), 0, 2.54],
                    [_a("xy"), 1.016, 1.905],
                    [_a("xy"), 0, 1.27],
                ],
                [_a("stroke"), [_a("width"), 0], [_a("type"), _a("default")]],
                [_a("fill"), [_a("type"), _a("none")]],
            ],
        ],
        [_a("embedded_fonts"), _a("no")],
    ]


def power_port_symbol_definition() -> list[Any]:
    return [
        _a("symbol"),
        POWER_PORT_LIB_ID,
        [_a("power")],
        [_a("pin_numbers"), [_a("hide"), _a("yes")]],
        [_a("pin_names"), [_a("offset"), 0], [_a("hide"), _a("yes")]],
        [_a("exclude_from_sim"), _a("no")],
        [_a("in_bom"), _a("no")],
        [_a("on_board"), _a("no")],
        _symbol_definition_property("Reference", "#PWR", hidden=True),
        _symbol_definition_property("Value", "PWR", at=(0.0, -2.54)),
        _symbol_definition_property("Footprint", "", hidden=True),
        _symbol_definition_property("Datasheet", "~", hidden=True),
        [
            _a("symbol"),
            "KSCH_POWER_PORT_0_1",
            [
                _a("polyline"),
                [
                    _a("pts"),
                    [_a("xy"), 0, 0],
                    [_a("xy"), 0, -1.27],
                    [_a("xy"), -1.016, -1.905],
                    [_a("xy"), 0, -1.27],
                    [_a("xy"), 1.016, -1.905],
                ],
                [_a("stroke"), [_a("width"), 0], [_a("type"), _a("default")]],
                [_a("fill"), [_a("type"), _a("none")]],
            ],
            [
                _a("pin"),
                _a("power_in"),
                _a("line"),
                [_a("at"), 0, 0, 90],
                [_a("length"), 0],
                [_a("name"), "~", _symbol_definition_effects(hidden=True)],
                [_a("number"), "1", _symbol_definition_effects(hidden=True)],
            ],
        ],
        [_a("embedded_fonts"), _a("no")],
    ]


def power_driver_symbol_definition() -> list[Any]:
    return [
        _a("symbol"),
        POWER_DRIVER_LIB_ID,
        [_a("power")],
        [_a("pin_numbers"), [_a("hide"), _a("yes")]],
        [_a("pin_names"), [_a("offset"), 0], [_a("hide"), _a("yes")]],
        [_a("exclude_from_sim"), _a("no")],
        [_a("in_bom"), _a("no")],
        [_a("on_board"), _a("no")],
        _symbol_definition_property("Reference", "#FLG", hidden=True),
        _symbol_definition_property("Value", "PWR_DRIVER", hidden=True),
        _symbol_definition_property("Footprint", "", hidden=True),
        _symbol_definition_property("Datasheet", "~", hidden=True),
        [
            _a("symbol"),
            "KSCH_POWER_DRIVER_0_1",
            [
                _a("pin"),
                _a("power_out"),
                _a("line"),
                [_a("at"), 0, 0, 90],
                [_a("length"), 0],
                [_a("name"), "~", _symbol_definition_effects(hidden=True)],
                [_a("number"), "1", _symbol_definition_effects(hidden=True)],
            ],
        ],
        [_a("embedded_fonts"), _a("no")],
    ]


def power_flag_reference(sheet_path: str, net_name: str, index: int) -> str:
    source = stable_uuid(f"{sheet_path}:{net_name}:power-flag:{index}")
    number = int(source.replace("-", "")[:8], 16)
    return f"#FLG{number % 1_000_000:06d}"


def power_flag_symbol(
    sheet_path: str,
    net_name: str,
    index: int,
    position: Point,
    *,
    project_name: str = "",
    sheet_instance_path: str = "/",
) -> PlacedSymbol:
    ref = power_flag_reference(sheet_path, net_name, index)
    key = f"{sheet_path}:{net_name}:power-flag:{index}"
    return PlacedSymbol(
        lib_id=POWER_FLAG_LIB_ID,
        at=(position.x, position.y),
        unit=1,
        uuid=stable_uuid(key),
        project_name=project_name,
        sheet_instance_path=sheet_instance_path,
        reference=ref,
        properties=(
            _symbol_property(
                "Reference",
                ref,
                Point(x=position.x, y=_snap_grid(position.y - 6.35)),
                hidden=True,
            ),
            _symbol_property(
                "Value",
                POWER_FLAG_VALUE,
                Point(x=position.x, y=_snap_grid(position.y - 3.81)),
                hidden=True,
            ),
            _symbol_property(
                "Footprint",
                "",
                Point(x=position.x, y=position.y),
                hidden=True,
            ),
        ),
        pins=(PlacedSymbolPin(number="1", uuid=stable_uuid(key + ":pin:1")),),
        in_bom=False,
        on_board=False,
    )


def power_port_reference(sheet_path: str, net_name: str, endpoint_key: str) -> str:
    source = stable_uuid(f"{sheet_path}:{net_name}:{endpoint_key}:power-port")
    number = int(source.replace("-", "")[:8], 16)
    return f"#PWR{number % 1_000_000:06d}"


def power_driver_reference(sheet_path: str, net_name: str, endpoint_key: str) -> str:
    source = stable_uuid(f"{sheet_path}:{net_name}:{endpoint_key}:power-driver")
    number = int(source.replace("-", "")[:8], 16)
    return f"#FLG{number % 1_000_000:06d}"


def power_driver_symbol(
    sheet_path: str,
    net_name: str,
    endpoint_key: str,
    position: Point,
    *,
    project_name: str = "",
    sheet_instance_path: str = "/",
) -> PlacedSymbol:
    ref = power_driver_reference(sheet_path, net_name, endpoint_key)
    key = f"{sheet_path}:{net_name}:{endpoint_key}:power-driver"
    return PlacedSymbol(
        lib_id=POWER_DRIVER_LIB_ID,
        at=(position.x, position.y),
        unit=1,
        uuid=stable_uuid(key),
        project_name=project_name,
        sheet_instance_path=sheet_instance_path,
        reference=ref,
        properties=(
            _symbol_property("Reference", ref, Point(x=position.x, y=position.y), hidden=True),
            _symbol_property("Value", "PWR_DRIVER", Point(x=position.x, y=position.y), hidden=True),
            _symbol_property("Footprint", "", Point(x=position.x, y=position.y), hidden=True),
        ),
        pins=(PlacedSymbolPin(number="1", uuid=stable_uuid(key + ":pin:1")),),
        in_bom=False,
        on_board=False,
    )


def power_port_symbol(
    sheet_path: str,
    net_name: str,
    endpoint_key: str,
    position: Point,
    value_at: Point,
    *,
    value: str | None = None,
    justify: str = "left",
    rotation: int = 0,
    symbol_rotation: int = 0,
    hidden_value: bool = False,
    project_name: str = "",
    sheet_instance_path: str = "/",
) -> PlacedSymbol:
    ref = power_port_reference(sheet_path, net_name, endpoint_key)
    key = f"{sheet_path}:{net_name}:{endpoint_key}:power-port"
    return PlacedSymbol(
        lib_id=POWER_PORT_LIB_ID,
        at=(position.x, position.y),
        unit=1,
        uuid=stable_uuid(key),
        project_name=project_name,
        sheet_instance_path=sheet_instance_path,
        reference=ref,
        properties=(
            _symbol_property(
                "Reference",
                ref,
                Point(x=position.x, y=position.y),
                hidden=True,
            ),
            _symbol_property(
                "Value",
                value or net_name,
                value_at,
                justify=justify,
                rotation=rotation,
                hidden=hidden_value,
            ),
            _symbol_property(
                "Footprint",
                "",
                Point(x=position.x, y=position.y),
                hidden=True,
            ),
        ),
        pins=(PlacedSymbolPin(number="1", uuid=stable_uuid(key + ":pin:1")),),
        in_bom=False,
        on_board=False,
        rotation=symbol_rotation,
    )


def _symbol_property(
    name: str,
    value: str,
    point: Point,
    *,
    justify: str = "left",
    hidden: bool = False,
    rotation: int = 0,
) -> PlacedProperty:
    return PlacedProperty(
        name=name,
        value=value,
        at=(point.x, point.y),
        justify="right" if justify == "right" else "left",
        hidden=hidden,
        rotation=rotation,
    )


def _snap_grid(value: float) -> float:
    return snap_grid(value)
