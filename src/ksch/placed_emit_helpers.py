from __future__ import annotations

from ksch.geometry import WireSegment
from ksch.ids import stable_uuid
from ksch.placed import PlacedItem, PlacedLabel, PlacedWire


def _wire_lines(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    key: str,
    *,
    nets: frozenset[str] | None = None,
    start_terminals: frozenset[str] = frozenset(),
    end_terminals: frozenset[str] = frozenset(),
) -> list[PlacedItem]:
    return [
        PlacedWire(
            start=(start_x, start_y),
            end=(end_x, end_y),
            uuid=stable_uuid(key),
            nets=_net_set_from_key(key) if nets is None else nets,
            start_terminals=start_terminals,
            end_terminals=end_terminals,
        )
    ]


def _label_lines(
    name: str,
    x: float,
    y: float,
    key: str,
    *,
    justify: str = "left",
    nets: frozenset[str] | None = None,
) -> list[PlacedItem]:
    return [
        PlacedLabel(
            name=name,
            at=(x, y),
            uuid=stable_uuid(key),
            justify="right" if justify == "right" else "left",
            nets=_net_set_from_key(key) if nets is None else nets,
        )
    ]


def _segment_lines(
    segments: list[WireSegment],
    key: str,
    *,
    nets: frozenset[str] | None = None,
    endpoint_text: str | None = None,
) -> list[PlacedItem]:
    items: list[PlacedItem] = []
    net_set = _net_set_from_key(key) if nets is None else nets
    for index, segment in enumerate(segments):
        start_terminals = frozenset({endpoint_text}) if endpoint_text else frozenset()
        items.extend(
            _wire_lines(
                segment[0],
                segment[1],
                segment[2],
                segment[3],
                f"{key}:{index}",
                nets=net_set,
                start_terminals=start_terminals,
            )
        )
    return items


def _net_name_from_key(key: str) -> str | None:
    parts = key.split(":")
    if len(parts) < 2:
        return None
    return parts[1]


def _net_set_from_key(key: str) -> frozenset[str]:
    net_name = _net_name_from_key(key)
    return frozenset({net_name}) if net_name else frozenset()
