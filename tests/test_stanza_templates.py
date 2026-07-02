from __future__ import annotations

from collections.abc import Iterable

from ksch.placed import PlacedJunction, PlacedLabel, PlacedSymbol, PlacedWire
from ksch.power_flags import POWER_PORT_LIB_ID

from test_block_layout import _can_controller_sheet

SENSE_GROUPS = (
    ("R10", "C7", "D6", "R12"),
    ("R11", "C8", "D5", "R9"),
    ("R7", "C6", "D4", "R6"),
)
SENSE_NETS = ("VEH_ACC_SENSE", "VEH_ILLUM_SENSE", "VEH_REV_SENSE")
INPUT_NETS = ("VEH_ACC_12V", "VEH_ILLUM_12V", "VEH_REV_12V")


def _symbols_by_ref(items: Iterable[object]) -> dict[str, PlacedSymbol]:
    return {item.reference: item for item in items if isinstance(item, PlacedSymbol)}


def _relative_symbol_signature(
    group: tuple[str, ...], symbols: dict[str, PlacedSymbol]
) -> tuple[tuple[str, str, float, float, int], ...]:
    selected = [symbols[ref] for ref in group]
    origin_x = min(symbol.at[0] for symbol in selected)
    origin_y = min(symbol.at[1] for symbol in selected)
    by_ref = {symbol.reference: symbol for symbol in selected}
    role_order = sorted(
        group,
        key=lambda ref: (
            by_ref[ref].lib_id,
            next(prop.value for prop in by_ref[ref].properties if prop.name == "Value"),
            ref[0],
        ),
    )
    return tuple(
        (
            by_ref[ref].lib_id,
            next(prop.value for prop in by_ref[ref].properties if prop.name == "Value"),
            round(by_ref[ref].at[0] - origin_x, 2),
            round(by_ref[ref].at[1] - origin_y, 2),
            by_ref[ref].rotation,
        )
        for ref in role_order
    )


def test_series_clamp_stanzas_reduce_internal_label_spam_and_emit_junctions() -> None:
    _project, sheet = _can_controller_sheet()

    labels = [item for item in sheet.items if isinstance(item, PlacedLabel)]
    wires = [item for item in sheet.items if isinstance(item, PlacedWire)]
    junctions = [item for item in sheet.items if isinstance(item, PlacedJunction)]

    assert len(labels) < 35
    for net_name in (*SENSE_NETS, *INPUT_NETS):
        assert sum(label.name == net_name for label in labels) <= 2
        assert any(net_name in wire.nets for wire in wires)
    for net_name in SENSE_NETS:
        assert any(net_name in junction.nets for junction in junctions)


def test_repeated_series_clamp_stanzas_are_translation_identical() -> None:
    _project, sheet = _can_controller_sheet()
    symbols = _symbols_by_ref(sheet.items)

    signatures = [_relative_symbol_signature(group, symbols) for group in SENSE_GROUPS]

    assert signatures[0] == signatures[1] == signatures[2]


def test_crystal_load_caps_template_matches_can_controller_oscillator() -> None:
    _project, sheet = _can_controller_sheet()
    labels = [item for item in sheet.items if isinstance(item, PlacedLabel)]
    wires = [item for item in sheet.items if isinstance(item, PlacedWire)]
    symbols = _symbols_by_ref(sheet.items)

    assert {"Y1", "C1", "C2"}.issubset(symbols)
    assert sum(label.name == "CAN_XTAL1" for label in labels) <= 1
    assert sum(label.name == "CAN_XTAL2" for label in labels) <= 1
    assert any("CAN_XTAL1" in wire.nets for wire in wires)
    assert any("CAN_XTAL2" in wire.nets for wire in wires)
    assert any("GND" in wire.nets for wire in wires)
    assert symbols["C1"].at[0] < symbols["Y1"].at[0] < symbols["C2"].at[0]

    oscillator_wires = [
        wire
        for wire in wires
        if wire.nets & {"CAN_XTAL1", "CAN_XTAL2"}
        and (
            wire.start_terminals
            and any(terminal.startswith("U1.") for terminal in wire.start_terminals)
            or wire.end_terminals
            and any(terminal.startswith("U1.") for terminal in wire.end_terminals)
        )
    ]
    assert oscillator_wires
    assert max(
        abs(wire.start[0] - wire.end[0]) + abs(wire.start[1] - wire.end[1])
        for wire in oscillator_wires
    ) <= 15.24 + 0.01


def test_power_port_value_text_effective_rotation_stays_readable() -> None:
    _project, sheet = _can_controller_sheet()
    power_ports = [
        item
        for item in sheet.items
        if isinstance(item, PlacedSymbol) and item.lib_id == POWER_PORT_LIB_ID
    ]

    assert power_ports
    for symbol in power_ports:
        value = next(prop for prop in symbol.properties if prop.name == "Value")
        effective_rotation = (
            value.rotation
            if symbol.rotation % 180 == 0
            else (symbol.rotation + value.rotation) % 360
        )
        assert effective_rotation != 180
        if effective_rotation in {90, 270}:
            assert symbol.rotation in {90, 270}
