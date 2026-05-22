from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ksch.circuit_regions import SheetCircuitRegions, build_sheet_circuit_regions
from ksch.geometry import (
    Coordinate,
    PinPoint,
    WireSegment,
    symbol_graphic_extent,
    symbol_horizontal_extent,
    symbol_vertical_extent,
)
from ksch.ids import stable_uuid
from ksch.kicad.symbols import SymbolInfo
from ksch.layout import Point
from ksch.layout import Rect as LayoutRect
from ksch.layout_problem import text_rect
from ksch.local_topology import build_local_topology
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.placed import PlacedItem, PlacedJunction, PlacedLabel, PlacedSymbol, PlacedWire
from ksch.placement import (
    DENSE_CONTROLLER_PIN_COUNT,
    PIN_LABEL_STUB,
    SYMBOL_MARGIN_X,
    _clamp,
    _is_anchor_ref,
    _is_groundish_net,
    _is_powerish_net,
    _local_signal_suffix,
    _snap_grid,
    _symbol_layout_bounds,
    _symbol_prefix,
)
from ksch.resolver import ResolvedProject
from ksch.routing import (
    coordinate as _coordinate,
)
from ksch.routing import (
    normalize_wire_segments as _normalize_wire_segments,
)
from ksch.routing import (
    pin_point_coordinates as _pin_point_coordinates,
)
from ksch.routing import (
    point_on_segment as _point_on_segment,
)
from ksch.routing import (
    segment_endpoint_coordinates as _segment_endpoint_coordinates,
)
from ksch.routing import (
    segments_clear_existing as _segments_clear_existing,
)
from ksch.routing import (
    segments_clear_obstacles as _segments_clear_obstacles,
)
from ksch.routing import (
    segments_clear_rects as _segments_clear_rects,
)
from ksch.routing import (
    segments_touch as _segments_touch,
)
from ksch.routing import (
    split_segments_at_coordinates as _split_segments_at_coordinates,
)
from ksch.routing import (
    without_zero_segments as _without_zero_segments,
)
from ksch.validation import placed_items_layout_problem

LABEL_EDGE_MARGIN = 12.7
LOCAL_PARENT_LABEL_RADIUS = 76.2
TEXT_CLEARANCE = 1.27
ROUTING_GRID = 1.27
CONTACT_TREE_LABEL_CANDIDATE_LIMIT = 12
EndpointStubSegment = tuple[str, str, WireSegment]


@dataclass(frozen=True)
class PassiveRailBankMember:
    ref: str
    top_endpoint: str
    bottom_endpoint: str
    top_point: PinPoint
    bottom_point: PinPoint


@dataclass(frozen=True)
class PassiveRailBank:
    top_net: str
    bottom_net: str
    members: tuple[PassiveRailBankMember, ...]
    top_extras: tuple[tuple[str, PinPoint], ...]
    bottom_extras: tuple[tuple[str, PinPoint], ...] = ()


@dataclass(frozen=True)
class SheetNetRoutingConfig:
    sheet_path: str
    page_width: float | None
    uses_low_interface_local_layout: bool
    local_label_prefix: str | None
    blocked_coordinates: frozenset[Coordinate] = frozenset()


@dataclass(frozen=True)
class SheetNetRoutingResult:
    items: tuple[PlacedItem, ...]
    interface_label_points: dict[str, PinPoint]


def _net_name_from_key(key: str) -> str | None:
    parts = key.split(":")
    if len(parts) < 2 or parts[1] in {"passive-bank"}:
        return None
    return parts[1]


def _net_set_from_key(key: str) -> frozenset[str]:
    net_name = _net_name_from_key(key)
    return frozenset({net_name}) if net_name else frozenset()


def _terminal_set(terminal: str | None) -> frozenset[str]:
    return frozenset({terminal}) if terminal else frozenset()


def _segments_clear_blockers(
    segments: list[WireSegment],
    blocked_rects: tuple[LayoutRect, ...],
) -> bool:
    return _segments_clear_rects(segments, blocked_rects)


def _segments_blocker_count(
    segments: list[WireSegment],
    blocked_rects: tuple[LayoutRect, ...],
) -> int:
    return sum(
        1
        for rect in blocked_rects
        if not _segments_clear_rects(segments, (rect,))
    )


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


def _junction_lines(x: float, y: float, key: str) -> list[PlacedItem]:
    return [PlacedJunction(at=(x, y), uuid=stable_uuid(key), nets=_net_set_from_key(key))]


def _label_lines(
    name: str,
    x: float,
    y: float,
    key: str,
    *,
    justify: str = "left",
    hidden: bool = False,
) -> list[PlacedItem]:
    if justify not in {"left", "right"}:
        raise ValueError(f"unsupported label justification: {justify}")
    label_justify: Literal["left", "right"] = "left" if justify == "left" else "right"
    return [
        PlacedLabel(
            name=name,
            at=(x, y),
            uuid=stable_uuid(key),
            justify=label_justify,
            hidden=False,
            nets=_net_set_from_key(key),
        )
    ]


def _visible_label_blockers(items: Iterable[PlacedItem]) -> tuple[LayoutRect, ...]:
    return tuple(
        text_rect(Point(item.at[0], item.at[1]), item.name, justify=item.justify)
        for item in items
        if isinstance(item, PlacedLabel) and not item.hidden
    )


def _symbol_body_label_blockers(
    project: ResolvedProject,
    items: Iterable[PlacedItem],
) -> tuple[LayoutRect, ...]:
    return tuple(
        _symbol_body_label_blocker(
            project.symbol_library.get(item.lib_id),
            item.at[0],
            item.at[1],
            item.rotation,
        )
        for item in items
        if isinstance(item, PlacedSymbol)
    )


def _symbol_body_label_blocker(
    symbol_info: SymbolInfo | None,
    symbol_x: float,
    symbol_y: float,
    rotation: int,
) -> LayoutRect:
    graphic_extent = symbol_graphic_extent(symbol_info)
    if graphic_extent is None:
        local_min_x, local_max_x = symbol_horizontal_extent(symbol_info)
        local_min_y, local_max_y = symbol_vertical_extent(symbol_info)
    else:
        local_min_x, local_max_x, local_min_y, local_max_y = graphic_extent

    rotation = rotation % 360
    transformed_points: list[tuple[float, float]] = []
    for local_x, local_y in (
        (local_min_x, local_min_y),
        (local_min_x, local_max_y),
        (local_max_x, local_min_y),
        (local_max_x, local_max_y),
    ):
        if rotation == 90:
            local_x, local_y = -local_y, local_x
        elif rotation == 180:
            local_x, local_y = -local_x, -local_y
        elif rotation == 270:
            local_x, local_y = local_y, -local_x
        transformed_points.append((symbol_x + local_x, symbol_y - local_y))

    xs = [point[0] for point in transformed_points]
    ys = [point[1] for point in transformed_points]
    margin = 2.54
    return LayoutRect(
        left=min(xs) - margin,
        top=min(ys) - margin,
        right=max(xs) + margin,
        bottom=max(ys) + margin,
    )


def _rect_with_margin(rect: LayoutRect, margin: float) -> LayoutRect:
    return LayoutRect(
        left=rect.left - margin,
        top=rect.top - margin,
        right=rect.right + margin,
        bottom=rect.bottom + margin,
    )


def _text_rect_clears_blockers(
    rect: LayoutRect,
    blocked_rects: tuple[LayoutRect, ...],
) -> bool:
    return not any(
        rect.overlaps(_rect_with_margin(blocker, TEXT_CLEARANCE))
        for blocker in blocked_rects
    )


def _label_justify_for_point(point: PinPoint, page_width: float | None) -> str:
    if page_width is not None and point.label_x <= LABEL_EDGE_MARGIN:
        return "left"
    if page_width is not None and point.label_x >= page_width - LABEL_EDGE_MARGIN:
        return "right"
    if point.label_x < point.x:
        return "right"
    return "left"


def _label_justify_away_from_x(
    label_x: float,
    anchor_x: float,
    *,
    page_width: float | None,
) -> str:
    if page_width is not None and label_x <= LABEL_EDGE_MARGIN:
        return "left"
    if page_width is not None and label_x >= page_width - LABEL_EDGE_MARGIN:
        return "right"
    return "right" if label_x < anchor_x else "left"


def _point_label_lines(
    name: str,
    point: PinPoint,
    key: str,
    *,
    page_width: float | None = None,
    hidden: bool = False,
    away_from_x: float | None = None,
) -> list[PlacedItem]:
    justify = (
        _label_justify_away_from_x(point.label_x, away_from_x, page_width=page_width)
        if away_from_x is not None
        else _label_justify_for_point(point, page_width)
    )
    return _label_lines(
        name,
        point.label_x,
        point.label_y,
        key,
        justify=justify,
        hidden=hidden,
    )


def _label_clears_blockers(
    name: str,
    point: PinPoint,
    *,
    page_width: float | None,
    blocked_rects: tuple[LayoutRect, ...],
    stub_blocked_rects: tuple[LayoutRect, ...] | None = None,
    obstacles: set[Coordinate] | None = None,
    occupied_segments: list[WireSegment] | None = None,
    away_from_x: float | None = None,
    allowed_obstacles: frozenset[Coordinate] = frozenset(),
) -> bool:
    justify = (
        _label_justify_away_from_x(point.label_x, away_from_x, page_width=page_width)
        if away_from_x is not None
        else _label_justify_for_point(point, page_width)
    )
    label_rect = text_rect(Point(point.label_x, point.label_y), name, justify=justify)
    if not _text_rect_clears_blockers(label_rect, blocked_rects):
        return False
    stub = _point_stub_segment(point)
    if stub is None:
        return True
    stub_blocked_rects = blocked_rects if stub_blocked_rects is None else stub_blocked_rects
    if not _segments_clear_rects([stub], stub_blocked_rects):
        return False
    if not _segments_clear_existing([stub], occupied_segments or []):
        return False
    if obstacles is None:
        return True
    allowed = {
        _coordinate(point.x, point.y),
        *allowed_obstacles,
    }
    return _segments_clear_obstacles([stub], obstacles=obstacles, allowed=allowed)


def _label_rect_clears_blockers(
    name: str,
    x: float,
    y: float,
    *,
    justify: str,
    blocked_rects: tuple[LayoutRect, ...],
) -> bool:
    rect = text_rect(Point(x, y), name, justify=justify)
    return _text_rect_clears_blockers(rect, blocked_rects)


def _point_with_clear_label(
    name: str,
    point: PinPoint,
    *,
    page_width: float | None,
    blocked_rects: tuple[LayoutRect, ...],
    stub_blocked_rects: tuple[LayoutRect, ...] | None = None,
    obstacles: set[Coordinate] | None = None,
    occupied_segments: list[WireSegment] | None = None,
    away_from_x: float | None = None,
    allow_text_overlap_fallback: bool = True,
) -> PinPoint:
    if not blocked_rects and not obstacles and not occupied_segments:
        return point

    delta_x = point.label_x - point.x
    delta_y = point.label_y - point.y
    if abs(delta_x) < 0.001 and abs(delta_y) < 0.001:
        return point
    original_label_coordinate = _coordinate(point.label_x, point.label_y)

    same_axis_candidates: list[PinPoint] = []
    alternate_axis_candidates: list[PinPoint] = []
    opposite_axis_candidates: list[PinPoint] = []
    offsets = (0.0, 5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
    extended_offsets = (*offsets, 35.56, 40.64, 45.72, 53.34)
    if abs(delta_x) >= abs(delta_y):
        sign = 1 if delta_x >= 0 else -1
        same_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=_snap_grid(point.label_x + sign * offset),
                label_y=point.label_y,
            )
            for offset in offsets
        )
        opposite_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=_snap_grid(point.x - sign * offset),
                label_y=point.y,
            )
            for offset in offsets[1:]
        )
        alternate_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=point.x,
                label_y=_snap_grid(point.y + offset),
            )
            for offset in offsets[1:]
        )
        alternate_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=point.x,
                label_y=_snap_grid(point.y - offset),
            )
            for offset in offsets[1:]
        )
    else:
        sign = 1 if delta_y >= 0 else -1
        same_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=point.label_x,
                label_y=_snap_grid(point.label_y + sign * offset),
            )
            for offset in offsets
        )
        opposite_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=point.x,
                label_y=_snap_grid(point.y - sign * offset),
            )
            for offset in offsets[1:]
        )
        alternate_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=_snap_grid(point.x + offset),
                label_y=point.y,
            )
            for offset in offsets[1:]
        )
        alternate_axis_candidates.extend(
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=_snap_grid(point.x - offset),
                label_y=point.y,
            )
            for offset in offsets[1:]
        )

    candidate_groups: tuple[tuple[tuple[PinPoint, ...], bool], ...]
    def away_compatible(candidate: PinPoint) -> bool:
        if away_from_x is None:
            return True
        if abs(candidate.label_x - candidate.x) < abs(candidate.label_y - candidate.y):
            return True
        if candidate.x < away_from_x:
            return candidate.label_x <= candidate.x + 0.001
        if candidate.x > away_from_x:
            return candidate.label_x >= candidate.x - 0.001
        return True

    opposite_axis_candidates = [
        candidate for candidate in opposite_axis_candidates if away_compatible(candidate)
    ]

    same_axis = tuple(dict.fromkeys(same_axis_candidates))
    opposite_axis = tuple(dict.fromkeys(opposite_axis_candidates))
    alternate_axis = tuple(dict.fromkeys(alternate_axis_candidates))
    if abs(delta_x) >= abs(delta_y):
        secondary_axis = opposite_axis
        sign = 1 if delta_x >= 0 else -1
        extended_same_axis_candidates = [
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=_snap_grid(point.label_x + sign * offset),
                label_y=point.label_y,
            )
            for offset in extended_offsets
        ]
        all_axis_candidates = [
            *extended_same_axis_candidates,
            *opposite_axis_candidates,
            *alternate_axis_candidates,
        ]
    else:
        secondary_axis = alternate_axis
        sign = 1 if delta_y >= 0 else -1
        extended_same_axis_candidates = [
            PinPoint(
                x=point.x,
                y=point.y,
                label_x=point.label_x,
                label_y=_snap_grid(point.label_y + sign * offset),
            )
            for offset in extended_offsets
        ]
        all_axis_candidates = [
            *extended_same_axis_candidates,
            *alternate_axis_candidates,
            *opposite_axis_candidates,
        ]
    all_axes = tuple(dict.fromkeys(all_axis_candidates))
    if allow_text_overlap_fallback:
        candidate_groups = (
            (same_axis, True),
            (secondary_axis, True),
            (all_axes, True),
            (same_axis, False),
            (secondary_axis, False),
            (all_axes, False),
        )
    else:
        candidate_groups = (
            (same_axis, True),
            (secondary_axis, True),
            (all_axes, True),
        )
    for unique_candidates, require_text_clearance in candidate_groups:
        for candidate in unique_candidates:
            if _label_clears_blockers(
                name,
                candidate,
                page_width=page_width,
                blocked_rects=blocked_rects if require_text_clearance else (),
                stub_blocked_rects=stub_blocked_rects if require_text_clearance else (),
                obstacles=obstacles,
                occupied_segments=occupied_segments,
                away_from_x=away_from_x,
                allowed_obstacles=frozenset({original_label_coordinate}),
            ):
                return candidate
    if not allow_text_overlap_fallback:
        return PinPoint(x=point.x, y=point.y, label_x=point.x, label_y=point.y)
    return point


def _point_stub_lines(
    point: PinPoint,
    key: str,
    *,
    start_terminal: str | None = None,
) -> list[PlacedItem]:
    if point.x == point.label_x and point.y == point.label_y:
        return []
    return _wire_lines(
        point.x,
        point.y,
        point.label_x,
        point.label_y,
        key,
        start_terminals=_terminal_set(start_terminal),
    )


def _point_stub_segment(point: PinPoint) -> WireSegment | None:
    if point.x == point.label_x and point.y == point.label_y:
        return None
    return (point.x, point.y, point.label_x, point.label_y)


def _terminal_coordinates(
    points: list[tuple[str, PinPoint]],
) -> dict[Coordinate, frozenset[str]]:
    terminals: dict[Coordinate, set[str]] = {}
    for endpoint_text, point in points:
        terminals.setdefault(_coordinate(point.x, point.y), set()).add(endpoint_text)
    return {
        coordinate: frozenset(endpoint_texts)
        for coordinate, endpoint_texts in terminals.items()
    }


def _point_avoiding_obstacle_stub(
    point: PinPoint,
    obstacles: set[Coordinate],
    *,
    occupied_segments: list[WireSegment] | None = None,
) -> PinPoint:
    def clear(candidate: PinPoint) -> bool:
        segment = _point_stub_segment(candidate)
        if segment is None:
            return True
        allowed = {
            _coordinate(candidate.x, candidate.y),
            _coordinate(candidate.label_x, candidate.label_y),
        }
        return _segments_clear_obstacles(
            [segment],
            obstacles=obstacles,
            allowed=allowed,
        ) and _segments_clear_existing([segment], occupied_segments or [])

    if clear(point):
        return point

    candidates: list[PinPoint] = []
    offsets = (PIN_LABEL_STUB, PIN_LABEL_STUB * 2, PIN_LABEL_STUB * 3)
    if abs(point.x - point.label_x) < 0.001:
        direction = 1 if point.label_y >= point.y else -1
        for offset in offsets:
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=_snap_grid(point.x + offset),
                    label_y=point.y,
                )
            )
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=_snap_grid(point.x - offset),
                    label_y=point.y,
                )
            )
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=point.x,
                    label_y=_snap_grid(point.y + direction * offset),
                )
            )
    else:
        direction = 1 if point.label_x >= point.x else -1
        for offset in offsets:
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=_snap_grid(point.x - direction * offset),
                    label_y=point.y,
                )
            )
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=_snap_grid(point.x + direction * offset),
                    label_y=point.y,
                )
            )
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=point.x,
                    label_y=_snap_grid(point.y - offset),
                )
            )
            candidates.append(
                PinPoint(
                    x=point.x,
                    y=point.y,
                    label_x=point.x,
                    label_y=_snap_grid(point.y + offset),
                )
            )

    for candidate in dict.fromkeys(candidates):
        if clear(candidate):
            return candidate
    return point


def _record_point_stub_segment(
    point: PinPoint,
    occupied_segments: list[WireSegment],
) -> None:
    segment = _point_stub_segment(point)
    if segment is not None:
        occupied_segments.append(segment)

def _direct_route_segments(start: PinPoint, end: PinPoint) -> list[WireSegment]:
    if abs(start.x - end.x) >= abs(start.y - end.y):
        mid_x = _snap_grid((start.x + end.x) / 2)
        return [
            (start.x, start.y, mid_x, start.y),
            (mid_x, start.y, mid_x, end.y),
            (mid_x, end.y, end.x, end.y),
        ]

    mid_y = _snap_grid((start.y + end.y) / 2)
    return [
        (start.x, start.y, start.x, mid_y),
        (start.x, mid_y, end.x, mid_y),
        (end.x, mid_y, end.x, end.y),
    ]


def _direct_route_candidates(start: PinPoint, end: PinPoint) -> list[list[WireSegment]]:
    candidates = [_direct_route_segments(start, end)]
    for offset in (5.08, 10.16, 15.24):
        top_y = _snap_grid(min(start.y, end.y) - offset)
        bottom_y = _snap_grid(max(start.y, end.y) + offset)
        left_x = _snap_grid(min(start.x, end.x) - offset)
        right_x = _snap_grid(max(start.x, end.x) + offset)
        candidates.extend(
            [
                _without_zero_segments(
                    [
                        (start.x, start.y, start.x, top_y),
                        (start.x, top_y, end.x, top_y),
                        (end.x, top_y, end.x, end.y),
                    ]
                ),
                _without_zero_segments(
                    [
                        (start.x, start.y, start.x, bottom_y),
                        (start.x, bottom_y, end.x, bottom_y),
                        (end.x, bottom_y, end.x, end.y),
                    ]
                ),
                _without_zero_segments(
                    [
                        (start.x, start.y, left_x, start.y),
                        (left_x, start.y, left_x, end.y),
                        (left_x, end.y, end.x, end.y),
                    ]
                ),
                _without_zero_segments(
                    [
                        (start.x, start.y, right_x, start.y),
                        (right_x, start.y, right_x, end.y),
                        (right_x, end.y, end.x, end.y),
                    ]
                ),
            ]
        )
    return candidates


def _segment_lines(
    segments: list[WireSegment],
    key: str,
    *,
    nets: frozenset[str] | None = None,
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
) -> list[PlacedItem]:
    lines: list[PlacedItem] = []
    terminal_coordinates = terminal_coordinates or {}
    for index, (start_x, start_y, end_x, end_y) in enumerate(segments):
        lines.extend(
            _wire_lines(
                start_x,
                start_y,
                end_x,
                end_y,
                f"{key}:{index}",
                nets=nets,
                start_terminals=terminal_coordinates.get(
                    _coordinate(start_x, start_y),
                    frozenset(),
                ),
                end_terminals=terminal_coordinates.get(
                    _coordinate(end_x, end_y),
                    frozenset(),
                ),
            )
        )
    return lines


def _route_junction_coordinates(segments: list[WireSegment]) -> set[Coordinate]:
    endpoints = {
        endpoint
        for segment in segments
        for endpoint in _segment_endpoint_coordinates(segment)
    }
    return {
        endpoint
        for endpoint in endpoints
        if sum(1 for segment in segments if _point_on_segment(endpoint, segment)) > 1
    }


def _junctions_for_segments(segments: list[WireSegment], key: str) -> list[PlacedItem]:
    lines: list[PlacedItem] = []
    for index, (x, y) in enumerate(sorted(_route_junction_coordinates(segments))):
        lines.extend(_junction_lines(x, y, f"{key}:junction:{index}"))
    return lines


def _compact_local_route_candidates(
    points: list[PinPoint],
) -> list[tuple[list[WireSegment], tuple[float, float]]]:
    label_xs = [point.label_x for point in points]
    label_ys = [point.label_y for point in points]
    min_x = min(label_xs)
    max_x = max(label_xs)
    min_y = min(label_ys)
    max_y = max(label_ys)
    stub_segments = [
        segment
        for point in points
        for segment in [_point_stub_segment(point)]
        if segment is not None
    ]
    candidates: list[tuple[list[WireSegment], tuple[float, float]]] = []

    if max_x - min_x >= max_y - min_y:
        rail_ys = [
            _snap_grid(sorted(label_ys)[len(label_ys) // 2]),
            _snap_grid(min_y - 5.08),
            _snap_grid(max_y + 5.08),
            _snap_grid(min_y - 10.16),
            _snap_grid(max_y + 10.16),
            _snap_grid(min_y - 15.24),
            _snap_grid(max_y + 15.24),
        ]
        for rail_y in dict.fromkeys(rail_ys):
            route_segments = [(min_x, rail_y, max_x, rail_y)]
            for point in points:
                if abs(point.label_y - rail_y) > 0.001:
                    route_segments.append((point.label_x, point.label_y, point.label_x, rail_y))
            label_point = (_snap_grid(min_x + 5.08), rail_y)
            candidates.append(
                (_without_zero_segments([*stub_segments, *route_segments]), label_point)
            )
        return candidates

    rail_xs = [
        _snap_grid(sorted(label_xs)[len(label_xs) // 2]),
        _snap_grid(min_x - 5.08),
        _snap_grid(max_x + 5.08),
        _snap_grid(min_x - 10.16),
        _snap_grid(max_x + 10.16),
        _snap_grid(min_x - 15.24),
        _snap_grid(max_x + 15.24),
    ]
    for rail_x in dict.fromkeys(rail_xs):
        route_segments = [(rail_x, min_y, rail_x, max_y)]
        for point in points:
            if abs(point.label_x - rail_x) > 0.001:
                route_segments.append((point.label_x, point.label_y, rail_x, point.label_y))
        label_point = (rail_x, min_y)
        candidates.append((_without_zero_segments([*stub_segments, *route_segments]), label_point))
    return candidates


def _safe_compact_local_net_lines(
    name: str,
    points: list[PinPoint],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    label_away_from_x: float | None = None,
) -> list[PlacedItem] | None:
    allowed = _pin_point_coordinates(points)
    for segments, (label_x, label_y) in _compact_local_route_candidates(points):
        segments = _normalize_wire_segments(segments)
        if not _segments_clear_obstacles(segments, obstacles=obstacles, allowed=allowed):
            continue
        if not _segments_clear_blockers(segments, blocked_rects):
            continue
        if not _segments_clear_existing(segments, occupied_segments):
            continue

        justify = (
            _label_justify_away_from_points(
                label_x,
                points,
                page_width=page_width,
            )
            if label_away_from_x is None
            else _label_justify_away_from_x(
                label_x,
                label_away_from_x,
                page_width=page_width,
            )
        )
        if not _label_rect_clears_blockers(
            name,
            label_x,
            label_y,
            justify=justify,
            blocked_rects=label_blocked_rects,
        ):
            continue

        lines = _segment_lines(
            segments,
            key + ":route",
            terminal_coordinates=terminal_coordinates,
        )
        lines.extend(_junctions_for_segments(segments, key + ":route"))
        lines.extend(_label_lines(name, label_x, label_y, key + ":label", justify=justify))
        occupied_segments.extend(segments)
        return lines
    return None


def _same_row_contact_rail_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    anchor_label_xs: tuple[float, ...] = (),
) -> list[PlacedItem] | None:
    if len(points) < 3:
        return None

    pin_points = [point for _endpoint_text, point in points]
    rail_y = pin_points[0].y
    if any(abs(point.y - rail_y) > 0.001 for point in pin_points):
        return None

    rail_xs = sorted({point.x for point in pin_points})
    if len(rail_xs) < 3:
        return None

    min_x = rail_xs[0]
    max_x = rail_xs[-1]
    center_x = sum(point.x for point in pin_points) / len(pin_points)
    anchor_x = (
        min(anchor_label_xs, key=lambda candidate: abs(candidate - center_x))
        if anchor_label_xs
        else None
    )
    preferred_sign = -1 if anchor_x is not None and anchor_x >= center_x else 1
    if page_width is not None:
        if min_x - PIN_LABEL_STUB < LABEL_EDGE_MARGIN:
            preferred_sign = 1
        elif max_x + PIN_LABEL_STUB > page_width - LABEL_EDGE_MARGIN:
            preferred_sign = -1

    base_segments = _normalize_wire_segments(
        [
            (start_x, rail_y, end_x, rail_y)
            for start_x, end_x in zip(rail_xs, rail_xs[1:], strict=False)
        ]
    )
    allowed_pin_coordinates = _pin_point_coordinates(pin_points)
    split_coordinates = {
        _coordinate(point.x, point.y)
        for point in pin_points
    }

    connection_options: tuple[tuple[int, int, float], ...]
    if preferred_sign < 0:
        connection_options = (
            (0, -1, max_x),
            (1, -1, min_x),
            (2, 1, max_x),
            (3, 1, min_x),
        )
    else:
        connection_options = (
            (0, 1, min_x),
            (1, 1, max_x),
            (2, -1, min_x),
            (3, -1, max_x),
        )

    candidates: list[
        tuple[int, int, int, float, list[WireSegment], tuple[float, float], str]
    ] = []
    for preference_rank, sign, connection_x in connection_options:
        for label_y_offset in (0.0, -5.08, 5.08, -10.16, 10.16):
            label_y = _snap_grid(rail_y + label_y_offset)
            for label_x_offset in (PIN_LABEL_STUB, 10.16, 15.24, 20.32, 25.4, 30.48):
                label_x = _snap_grid(connection_x + sign * label_x_offset)
                if page_width is not None and label_x > page_width - LABEL_EDGE_MARGIN:
                    continue
                if label_x < LABEL_EDGE_MARGIN:
                    continue
                label_segments = (
                    [(connection_x, rail_y, label_x, rail_y)]
                    if abs(label_y - rail_y) < 0.001
                    else [
                        (connection_x, rail_y, label_x, rail_y),
                        (label_x, rail_y, label_x, label_y),
                    ]
                )
                segments = _normalize_wire_segments([*base_segments, *label_segments])
                candidate_split_coordinates = {
                    *split_coordinates,
                    _coordinate(connection_x, rail_y),
                    _coordinate(label_x, rail_y),
                    _coordinate(label_x, label_y),
                }
                allowed = allowed_pin_coordinates | candidate_split_coordinates
                if not _segments_clear_obstacles(
                    segments,
                    obstacles=obstacles,
                    allowed=allowed,
                ):
                    continue
                if not _segments_clear_existing(segments, occupied_segments):
                    continue
                justify = _label_justify_away_from_x(
                    label_x,
                    center_x,
                    page_width=page_width,
                )
                if not _label_rect_clears_blockers(
                    name,
                    label_x,
                    label_y,
                    justify=justify,
                    blocked_rects=label_blocked_rects,
                ):
                    continue
                blocker_count = _segments_blocker_count(segments, blocked_rects)
                total_length = sum(
                    abs(start_x - end_x) + abs(start_y - end_y)
                    for start_x, start_y, end_x, end_y in segments
                )
                on_rail_label_rank = int(
                    abs(label_y - rail_y) < 0.001
                    and min_x + 0.001 < label_x < max_x - 0.001
                )
                candidates.append(
                    (
                        blocker_count,
                        preference_rank,
                        on_rail_label_rank,
                        total_length,
                        segments,
                        (label_x, label_y),
                        justify,
                    )
                )

    if not candidates:
        return None

    (
        _blocker_count,
        _rank,
        _on_rail_rank,
        _total_length,
        segments,
        (label_x, label_y),
        justify,
    ) = min(
        candidates,
        key=lambda item: (
            item[0],
            item[1],
            item[2],
            item[3],
            abs(item[5][1] - rail_y),
            item[5][0],
        ),
    )
    segments = _split_segments_at_coordinates(segments, split_coordinates)
    lines = _segment_lines(
        segments,
        key + ":same-row-contact:route",
        terminal_coordinates=terminal_coordinates,
    )
    lines.extend(_junctions_for_segments(segments, key + ":same-row-contact:route"))
    lines.extend(
        _label_lines(
            name,
            label_x,
            label_y,
            key + ":same-row-contact:label",
            justify=justify,
        )
    )
    occupied_segments.extend(segments)
    return lines


def _snap_routing_grid(value: float) -> float:
    return round(round(value / ROUTING_GRID) * ROUTING_GRID, 2)


def _contact_tree_label_candidates(
    name: str,
    points: list[PinPoint],
    *,
    page_width: float | None,
    label_blocked_rects: tuple[LayoutRect, ...],
    anchor_label_xs: tuple[float, ...],
) -> list[tuple[int, float, float, str]]:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)
    anchor_x = (
        min(anchor_label_xs, key=lambda candidate: abs(candidate - center_x))
        if anchor_label_xs
        else None
    )
    away_sign = -1 if anchor_x is not None and anchor_x > center_x else 1
    if page_width is not None:
        if max_x + PIN_LABEL_STUB > page_width - LABEL_EDGE_MARGIN:
            away_sign = -1
        elif min_x - PIN_LABEL_STUB < LABEL_EDGE_MARGIN:
            away_sign = 1

    x_options = [
        (max_x, 1, 0 if away_sign > 0 else 1),
        (min_x, -1, 0 if away_sign < 0 else 1),
    ]
    offsets = (
        PIN_LABEL_STUB,
        10.16,
        15.24,
        20.32,
        25.4,
        30.48,
        38.1,
        50.8,
        63.5,
        76.2,
        101.6,
    )
    y_options = tuple(
        dict.fromkeys(
            [
                _snap_routing_grid(center_y),
                *(_snap_routing_grid(y) for y in ys),
                _snap_routing_grid(min_y - 5.08),
                _snap_routing_grid(max_y + 5.08),
                _snap_routing_grid(min_y - 10.16),
                _snap_routing_grid(max_y + 10.16),
                _snap_routing_grid(min_y - 20.32),
                _snap_routing_grid(max_y + 20.32),
            ]
        )
    )

    candidates: list[tuple[int, float, float, str]] = []
    for base_x, sign, side_rank in x_options:
        for offset_rank, offset in enumerate(offsets):
            label_x = _snap_routing_grid(base_x + sign * offset)
            if page_width is not None and label_x > page_width - LABEL_EDGE_MARGIN:
                continue
            if label_x < LABEL_EDGE_MARGIN:
                continue
            for y_rank, label_y in enumerate(y_options):
                justify = _label_justify_away_from_x(
                    label_x,
                    center_x,
                    page_width=page_width,
                )
                if not _label_rect_clears_blockers(
                    name,
                    label_x,
                    label_y,
                    justify=justify,
                    blocked_rects=label_blocked_rects,
                ):
                    continue
                distance_rank = int(abs(label_y - center_y) / ROUTING_GRID)
                candidates.append(
                    (
                        side_rank * 10_000 + offset_rank * 100 + y_rank + distance_rank,
                        label_x,
                        label_y,
                        justify,
                    )
                )
    return sorted(candidates)


def _rectilinear_path_to_targets(
    start: Coordinate,
    targets: set[Coordinate],
    *,
    obstacles: set[Coordinate],
    allowed_coordinates: set[Coordinate],
    occupied_segments: list[WireSegment],
) -> list[WireSegment] | None:
    if start in targets:
        return []
    start_point = PinPoint(x=start[0], y=start[1], label_x=start[0], label_y=start[1])
    candidates: list[tuple[float, list[WireSegment]]] = []
    extra_xs: set[float] = set()
    extra_ys: set[float] = set()
    for start_x, start_y, end_x, end_y in occupied_segments:
        if abs(start_y - end_y) < 0.001:
            extra_ys.update(_snap_routing_grid(start_y + offset) for offset in (-5.08, 5.08))
            extra_xs.update(
                _snap_routing_grid(value + offset)
                for value in (start_x, end_x)
                for offset in (-5.08, 5.08)
            )
        if abs(start_x - end_x) < 0.001:
            extra_xs.update(_snap_routing_grid(start_x + offset) for offset in (-5.08, 5.08))
            extra_ys.update(
                _snap_routing_grid(value + offset)
                for value in (start_y, end_y)
                for offset in (-5.08, 5.08)
            )
    for target in targets:
        target_point = PinPoint(x=target[0], y=target[1], label_x=target[0], label_y=target[1])
        route_candidates = [
            [
                (start[0], start[1], target[0], start[1]),
                (target[0], start[1], target[0], target[1]),
            ],
            [
                (start[0], start[1], start[0], target[1]),
                (start[0], target[1], target[0], target[1]),
            ],
            *_direct_route_candidates(start_point, target_point),
        ]
        for offset in (20.32, 25.4, 30.48, 38.1, 50.8, 63.5):
            top_y = _snap_routing_grid(min(start[1], target[1]) - offset)
            bottom_y = _snap_routing_grid(max(start[1], target[1]) + offset)
            left_x = _snap_routing_grid(min(start[0], target[0]) - offset)
            right_x = _snap_routing_grid(max(start[0], target[0]) + offset)
            route_candidates.extend(
                [
                    [
                        (start[0], start[1], start[0], top_y),
                        (start[0], top_y, target[0], top_y),
                        (target[0], top_y, target[0], target[1]),
                    ],
                    [
                        (start[0], start[1], start[0], bottom_y),
                        (start[0], bottom_y, target[0], bottom_y),
                        (target[0], bottom_y, target[0], target[1]),
                    ],
                    [
                        (start[0], start[1], left_x, start[1]),
                        (left_x, start[1], left_x, target[1]),
                        (left_x, target[1], target[0], target[1]),
                    ],
                    [
                        (start[0], start[1], right_x, start[1]),
                        (right_x, start[1], right_x, target[1]),
                        (right_x, target[1], target[0], target[1]),
                    ],
                ]
            )
        for route_x in sorted(extra_xs):
            route_candidates.append(
                [
                    (start[0], start[1], route_x, start[1]),
                    (route_x, start[1], route_x, target[1]),
                    (route_x, target[1], target[0], target[1]),
                ]
            )
        for route_y in sorted(extra_ys):
            route_candidates.append(
                [
                    (start[0], start[1], start[0], route_y),
                    (start[0], route_y, target[0], route_y),
                    (target[0], route_y, target[0], target[1]),
                ]
            )
        for segments in route_candidates:
            segments = _normalize_wire_segments(_without_zero_segments(segments))
            if not _segments_clear_obstacles(
                segments,
                obstacles=obstacles,
                allowed=allowed_coordinates,
            ):
                continue
            if not _segments_clear_existing(segments, occupied_segments):
                continue
            total_length = sum(
                abs(start_x - end_x) + abs(start_y - end_y)
                for start_x, start_y, end_x, end_y in segments
            )
            candidates.append((total_length + len(segments) * ROUTING_GRID, segments))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _pathfinder_contact_tree_net_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    anchor_label_xs: tuple[float, ...] = (),
) -> list[PlacedItem] | None:
    pin_points = [point for _endpoint_text, point in points]
    allowed_pin_coordinates = _pin_point_coordinates(pin_points)
    ordered_points = sorted(
        pin_points,
        key=lambda point: (
            abs(point.x - sum(pin.x for pin in pin_points) / len(pin_points))
            + abs(point.y - sum(pin.y for pin in pin_points) / len(pin_points)),
            point.y,
            point.x,
        ),
    )

    for _rank, label_x, label_y, justify in _contact_tree_label_candidates(
        name,
        pin_points,
        page_width=page_width,
        label_blocked_rects=label_blocked_rects,
        anchor_label_xs=anchor_label_xs,
    )[:CONTACT_TREE_LABEL_CANDIDATE_LIMIT]:
        label_coordinate = _coordinate(label_x, label_y)
        allowed_coordinates = {*allowed_pin_coordinates, label_coordinate}
        tree_nodes = {label_coordinate}
        segments: list[WireSegment] = []
        success = True
        for point in sorted(
            ordered_points,
            key=lambda pin: abs(pin.x - label_x) + abs(pin.y - label_y),
        ):
            start = _coordinate(point.x, point.y)
            path = _rectilinear_path_to_targets(
                start,
                tree_nodes,
                obstacles=obstacles,
                allowed_coordinates=allowed_coordinates,
                occupied_segments=occupied_segments,
            )
            if path is None:
                success = False
                break
            segments.extend(path)
            for segment in path:
                tree_nodes.update(_segment_endpoint_coordinates(segment))
            tree_nodes.add(start)
        if not success:
            continue
        split_points = {*allowed_coordinates, *tree_nodes}
        segments = _normalize_wire_segments(_split_segments_at_coordinates(segments, split_points))
        if not _segments_clear_existing(segments, occupied_segments):
            continue
        if not _segments_clear_obstacles(
            segments,
            obstacles=obstacles,
            allowed=allowed_coordinates | tree_nodes,
        ):
            continue
        lines = _segment_lines(
            segments,
            key + ":pathfinder-contact-tree:route",
            terminal_coordinates=terminal_coordinates,
        )
        lines.extend(
            _junctions_for_segments(segments, key + ":pathfinder-contact-tree:route")
        )
        lines.extend(
            _label_lines(
                name,
                label_x,
                label_y,
                key + ":pathfinder-contact-tree:label",
                justify=justify,
            )
        )
        occupied_segments.extend(segments)
        return lines
    return None


def _local_contact_tree_net_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    anchor_label_xs: tuple[float, ...] = (),
) -> list[PlacedItem] | None:
    if len(points) < 3:
        return None

    pin_points = [point for _endpoint_text, point in points]
    if not _is_local_route_span(pin_points):
        return None

    xs = [point.x for point in pin_points]
    ys = [point.y for point in pin_points]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)
    anchor_points = [
        point for endpoint_text, point in points if _is_anchor_endpoint(endpoint_text)
    ]
    anchor_x = (
        min(anchor_label_xs, key=lambda candidate: abs(candidate - center_x))
        if anchor_label_xs
        else (
            sum(point.x for point in anchor_points) / len(anchor_points)
            if anchor_points
            else None
        )
    )
    away_sign = -1 if anchor_x is not None and anchor_x > center_x else 1
    if page_width is not None:
        if max_x + PIN_LABEL_STUB > page_width - LABEL_EDGE_MARGIN:
            away_sign = -1
        elif min_x - PIN_LABEL_STUB < LABEL_EDGE_MARGIN:
            away_sign = 1

    horizontal_rail_ys = tuple(
        dict.fromkeys(
            [
                *(point.y for point in anchor_points),
                _snap_grid(sorted(ys)[len(ys) // 2]),
                _snap_grid(center_y),
                *ys,
                _snap_grid(min_y - 5.08),
                _snap_grid(max_y + 5.08),
                _snap_grid(min_y - 10.16),
                _snap_grid(max_y + 10.16),
            ]
        )
    )
    vertical_rail_xs = tuple(
        dict.fromkeys(
            [
                *(point.x for point in anchor_points),
                _snap_grid(sorted(xs)[len(xs) // 2]),
                _snap_grid(center_x),
                *xs,
                _snap_grid(min_x - 5.08),
                _snap_grid(max_x + 5.08),
                _snap_grid(min_x - 10.16),
                _snap_grid(max_x + 10.16),
            ]
        )
    )

    allowed_pin_coordinates = _pin_point_coordinates(pin_points)
    candidates: list[
        tuple[int, int, float, list[WireSegment], tuple[float, float], str, set[Coordinate]]
    ] = []

    def add_candidate(
        *,
        orientation_rank: int,
        segments: list[WireSegment],
        label_x: float,
        label_y: float,
        justify: str,
        split_points: set[Coordinate],
    ) -> None:
        if page_width is not None and label_x > page_width - LABEL_EDGE_MARGIN:
            return
        if label_x < LABEL_EDGE_MARGIN:
            return
        if not _label_rect_clears_blockers(
            name,
            label_x,
            label_y,
            justify=justify,
            blocked_rects=label_blocked_rects,
        ):
            return
        segments = _normalize_wire_segments(
            _split_segments_at_coordinates(
                _without_zero_segments(segments),
                split_points,
            )
        )
        allowed = allowed_pin_coordinates | split_points
        if split_points & (obstacles - allowed_pin_coordinates):
            return
        if not _segments_clear_obstacles(segments, obstacles=obstacles, allowed=allowed):
            return
        if not _segments_clear_existing(segments, occupied_segments):
            return
        blocker_count = _segments_blocker_count(segments, blocked_rects)
        total_length = sum(
            abs(start_x - end_x) + abs(start_y - end_y)
            for start_x, start_y, end_x, end_y in segments
        )
        candidates.append(
            (
                blocker_count,
                orientation_rank,
                total_length,
                segments,
                (label_x, label_y),
                justify,
                split_points,
            )
        )

    horizontal_rank = 0 if max_x - min_x >= max_y - min_y else 1
    vertical_rank = 0 if max_y - min_y > max_x - min_x else 1
    for rail_y in horizontal_rail_ys:
        bus_min_x = min_x
        bus_max_x = max_x
        base_segments = [
            (bus_min_x, rail_y, bus_max_x, rail_y),
            *(
                (point.x, point.y, point.x, rail_y)
                for point in pin_points
                if abs(point.y - rail_y) >= 0.001
            ),
        ]
        base_split_points = {
            *(_coordinate(point.x, point.y) for point in pin_points),
            *(_coordinate(point.x, rail_y) for point in pin_points),
            _coordinate(bus_min_x, rail_y),
            _coordinate(bus_max_x, rail_y),
        }
        edge_options = (
            (bus_max_x, 1, 0 if away_sign > 0 else 1),
            (bus_min_x, -1, 0 if away_sign < 0 else 1),
        )
        for edge_x, sign, edge_rank in edge_options:
            for offset in (PIN_LABEL_STUB, 10.16, 15.24, 20.32, 25.4, 30.48):
                label_x = _snap_grid(edge_x + sign * offset)
                label_y = rail_y
                split_points = {
                    *base_split_points,
                    _coordinate(label_x, label_y),
                    _coordinate(edge_x, rail_y),
                }
                add_candidate(
                    orientation_rank=horizontal_rank + edge_rank,
                    segments=[*base_segments, (edge_x, rail_y, label_x, label_y)],
                    label_x=label_x,
                    label_y=label_y,
                    justify=_label_justify_away_from_x(
                        label_x,
                        center_x,
                        page_width=page_width,
                    ),
                    split_points=split_points,
                )
                for label_y_offset in (-5.08, 5.08, -10.16, 10.16):
                    label_y = _snap_grid(rail_y + label_y_offset)
                    split_points = {
                        *base_split_points,
                        _coordinate(edge_x, rail_y),
                        _coordinate(label_x, rail_y),
                        _coordinate(label_x, label_y),
                    }
                    add_candidate(
                        orientation_rank=horizontal_rank + edge_rank + 1,
                        segments=[
                            *base_segments,
                            (edge_x, rail_y, label_x, rail_y),
                            (label_x, rail_y, label_x, label_y),
                        ],
                        label_x=label_x,
                        label_y=label_y,
                        justify=_label_justify_away_from_x(
                            label_x,
                            center_x,
                            page_width=page_width,
                        ),
                        split_points=split_points,
                    )

    for rail_x in vertical_rail_xs:
        bus_min_y = min_y
        bus_max_y = max_y
        base_segments = [
            (rail_x, bus_min_y, rail_x, bus_max_y),
            *(
                (point.x, point.y, rail_x, point.y)
                for point in pin_points
                if abs(point.x - rail_x) >= 0.001
            ),
        ]
        base_split_points = {
            *(_coordinate(point.x, point.y) for point in pin_points),
            *(_coordinate(rail_x, point.y) for point in pin_points),
            _coordinate(rail_x, bus_min_y),
            _coordinate(rail_x, bus_max_y),
        }
        for sign, edge_rank in ((away_sign, 0), (-away_sign, 1)):
            for offset in (PIN_LABEL_STUB, 10.16, 15.24, 20.32, 25.4, 30.48):
                label_x = _snap_grid(rail_x + sign * offset)
                label_y = _snap_grid(center_y)
                label_start_y = _clamp(label_y, bus_min_y, bus_max_y)
                split_points = {
                    *base_split_points,
                    _coordinate(rail_x, label_start_y),
                    _coordinate(label_x, label_start_y),
                    _coordinate(label_x, label_y),
                }
                add_candidate(
                    orientation_rank=vertical_rank + edge_rank,
                    segments=[
                        *base_segments,
                        (rail_x, label_start_y, label_x, label_start_y),
                        (label_x, label_start_y, label_x, label_y),
                    ],
                    label_x=label_x,
                    label_y=label_y,
                    justify=_label_justify_away_from_x(
                        label_x,
                        center_x,
                        page_width=page_width,
                    ),
                    split_points=split_points,
                )

    if not candidates:
        return _pathfinder_contact_tree_net_lines(
            name,
            points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            anchor_label_xs=anchor_label_xs,
        )

    _blocker_count, _rank, _total_length, segments, (label_x, label_y), justify, _splits = min(
        candidates,
        key=lambda item: (
            item[0],
            item[1],
            item[2],
            abs(item[4][1] - center_y),
            item[4][0],
        ),
    )
    lines = _segment_lines(
        segments,
        key + ":local-contact-tree:route",
        terminal_coordinates=terminal_coordinates,
    )
    lines.extend(_junctions_for_segments(segments, key + ":local-contact-tree:route"))
    lines.extend(
        _label_lines(
            name,
            label_x,
            label_y,
            key + ":local-contact-tree:label",
            justify=justify,
        )
    )
    occupied_segments.extend(segments)
    return lines


def _contact_topology_route_candidates(
    points: list[tuple[str, PinPoint]],
    *,
    page_width: float | None,
    anchor_label_xs: tuple[float, ...],
) -> list[tuple[list[WireSegment], tuple[float, float], str, set[Coordinate]]]:
    pin_points = [point for _endpoint_text, point in points]
    port_points = [
        PinPoint(
            x=point.label_x,
            y=point.label_y,
            label_x=point.label_x,
            label_y=point.label_y,
        )
        for point in pin_points
    ]
    pin_escape_segments = [
        segment
        for point in pin_points
        for segment in [_point_stub_segment(point)]
        if segment is not None
    ]
    xs = [point.x for point in port_points]
    ys = [point.y for point in port_points]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)
    anchor_points = [
        port
        for (endpoint_text, _point), port in zip(points, port_points, strict=True)
        if _is_anchor_endpoint(endpoint_text)
    ]
    anchor_x = (
        min(anchor_label_xs, key=lambda candidate: abs(candidate - center_x))
        if anchor_label_xs
        else (
            sum(point.x for point in anchor_points) / len(anchor_points)
            if anchor_points
            else None
        )
    )
    away_sign = -1 if anchor_x is not None and anchor_x > center_x else 1
    label_anchor_x = center_x if anchor_x is None else anchor_x
    if page_width is not None:
        if max_x + PIN_LABEL_STUB > page_width - LABEL_EDGE_MARGIN:
            away_sign = -1
        elif min_x - PIN_LABEL_STUB < LABEL_EDGE_MARGIN:
            away_sign = 1

    anchor_ys = tuple(dict.fromkeys(_snap_grid(point.y) for point in anchor_points))
    median_y = _snap_grid(sorted(ys)[len(ys) // 2])
    horizontal_rail_ys = tuple(
        dict.fromkeys(
            [
                *anchor_ys,
                median_y,
                _snap_grid(center_y),
                min_y,
                max_y,
                _snap_grid(min_y - 5.08),
                _snap_grid(max_y + 5.08),
                _snap_grid(min_y - 10.16),
                _snap_grid(max_y + 10.16),
            ]
        )
    )
    median_x = _snap_grid(sorted(xs)[len(xs) // 2])
    vertical_rail_xs = tuple(
        dict.fromkeys(
            [
                median_x,
                _snap_grid(center_x),
                min_x,
                max_x,
                _snap_grid(min_x - 5.08),
                _snap_grid(max_x + 5.08),
                _snap_grid(min_x - 10.16),
                _snap_grid(max_x + 10.16),
            ]
        )
    )

    def horizontal_escape_x(pin: PinPoint, port: PinPoint, offset: float) -> float:
        if abs(offset) < 0.001:
            return port.x
        if port.x > pin.x:
            sign = 1
        elif port.x < pin.x:
            sign = -1
        else:
            sign = away_sign
        return _snap_grid(port.x + sign * offset)

    candidates: list[tuple[list[WireSegment], tuple[float, float], str, set[Coordinate]]] = []

    for rail_y in horizontal_rail_ys:
        for branch_offset in (0.0, 2.54, 5.08, 10.16, 15.24, 20.32):
            branch_points = [
                (
                    port,
                    horizontal_escape_x(pin, port, branch_offset)
                    if abs(port.y - rail_y) >= 0.001
                    else port.x,
                )
                for pin, port in zip(pin_points, port_points, strict=True)
            ]
            branch_xs = [branch_x for _port, branch_x in branch_points]
            bus_min_x = min(min_x, *branch_xs)
            bus_max_x = max(max_x, *branch_xs)
            label_bus_x = bus_max_x if away_sign > 0 else bus_min_x
            for label_offset in (
                -PIN_LABEL_STUB,
                PIN_LABEL_STUB,
                -PIN_LABEL_STUB * 2,
                PIN_LABEL_STUB * 2,
            ):
                label_x = label_bus_x
                label_y = _snap_grid(rail_y + label_offset)
                if page_width is not None and label_x > page_width - LABEL_EDGE_MARGIN:
                    continue
                if label_x < LABEL_EDGE_MARGIN:
                    continue
                branch_segments = [
                    segment
                    for port, branch_x in branch_points
                    for segment in (
                        (port.x, port.y, branch_x, port.y),
                        (branch_x, port.y, branch_x, rail_y),
                    )
                    if abs(segment[0] - segment[2]) >= 0.001
                    or abs(segment[1] - segment[3]) >= 0.001
                ]
                segments = [
                    (bus_min_x, rail_y, bus_max_x, rail_y),
                    (label_bus_x, rail_y, label_x, label_y),
                    *branch_segments,
                ]
                split_points = {
                    _coordinate(point.x, point.y)
                    for point in pin_points
                } | {
                    _coordinate(point.x, point.y)
                    for point in port_points
                } | {
                    _coordinate(branch_x, port.y)
                    for port, branch_x in branch_points
                } | {
                    _coordinate(branch_x, rail_y)
                    for _port, branch_x in branch_points
                } | {
                    _coordinate(label_bus_x, rail_y),
                    _coordinate(label_x, label_y),
                }
                candidates.append(
                    (
                        _without_zero_segments([*pin_escape_segments, *segments]),
                        (label_x, label_y),
                        _label_justify_away_from_x(
                            label_x,
                            label_anchor_x,
                            page_width=page_width,
                        ),
                        split_points,
                    )
                )

    for rail_x in vertical_rail_xs:
        bus_min_y = min_y
        bus_max_y = max_y
        label_y = _snap_grid(center_y)
        label_start_y = _clamp(label_y, bus_min_y, bus_max_y)
        for label_offset in (
            -PIN_LABEL_STUB,
            PIN_LABEL_STUB,
            -PIN_LABEL_STUB * 2,
            PIN_LABEL_STUB * 2,
        ):
            label_x = rail_x
            label_y = _snap_grid(label_start_y + label_offset)
            if page_width is not None and label_x > page_width - LABEL_EDGE_MARGIN:
                continue
            if label_x < LABEL_EDGE_MARGIN:
                continue
            segments = [
                (rail_x, bus_min_y, rail_x, bus_max_y),
                (rail_x, label_start_y, label_x, label_y),
                *(
                    (point.x, point.y, rail_x, point.y)
                    for point in port_points
                    if abs(point.x - rail_x) >= 0.001
                ),
            ]
            split_points = {
                _coordinate(point.x, point.y)
                for point in pin_points
            } | {
                _coordinate(point.x, point.y)
                for point in port_points
            } | {
                _coordinate(rail_x, point.y)
                for point in port_points
            } | {
                _coordinate(rail_x, label_start_y),
                _coordinate(label_x, label_y),
            }
            candidates.append(
                (
                    _without_zero_segments([*pin_escape_segments, *segments]),
                    (label_x, label_y),
                    _label_justify_away_from_x(
                        label_x,
                        label_anchor_x,
                        page_width=page_width,
                    ),
                    split_points,
                )
            )

    if max_x - min_x >= max_y - min_y:
        return candidates
    horizontal_count = len(horizontal_rail_ys) * 6 * 3
    return [*candidates[horizontal_count:], *candidates[:horizontal_count]]


def _refs_share_circuit_region(
    points: list[tuple[str, PinPoint]],
    regions: SheetCircuitRegions | None,
) -> bool:
    if regions is None:
        return False
    refs = [
        ref
        for endpoint_text, _point in points
        for ref in [_endpoint_ref(endpoint_text)]
        if ref is not None
    ]
    for region in regions.regions:
        if sum(1 for ref in refs if region.contains_ref(ref)) >= 2:
            return True
    return False


def _is_contact_topology_candidate(
    name: str,
    points: list[tuple[str, PinPoint]],
    *,
    regions: SheetCircuitRegions | None,
) -> bool:
    if _is_groundish_net(name):
        return False
    if not 3 <= len(points) <= 10:
        return False
    pin_points = [point for _endpoint_text, point in points]
    if not _is_local_route_span(pin_points):
        return False
    return _refs_share_circuit_region(points, regions) or _is_compact_net(pin_points)


def _safe_contact_topology_net_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    anchor_label_xs: tuple[float, ...] = (),
) -> list[PlacedItem] | None:
    allowed_pin_coordinates = _pin_point_coordinates(
        [point for _endpoint_text, point in points]
    )
    soft_candidates: list[
        tuple[int, float, list[WireSegment], tuple[float, float], str]
    ] = []
    for segments, (label_x, label_y), justify, split_points in _contact_topology_route_candidates(
        points,
        page_width=page_width,
        anchor_label_xs=anchor_label_xs,
    ):
        if not _label_rect_clears_blockers(
            name,
            label_x,
            label_y,
            justify=justify,
            blocked_rects=label_blocked_rects,
        ):
            continue
        segments = _normalize_wire_segments(
            _split_segments_at_coordinates(segments, split_points)
        )
        if split_points & (obstacles - allowed_pin_coordinates):
            continue
        allowed = allowed_pin_coordinates | split_points
        if not _segments_clear_obstacles(segments, obstacles=obstacles, allowed=allowed):
            continue
        if not _segments_clear_existing(segments, occupied_segments):
            continue
        blocker_count = _segments_blocker_count(segments, blocked_rects)
        if blocker_count:
            total_length = sum(
                abs(segment[0] - segment[2]) + abs(segment[1] - segment[3])
                for segment in segments
            )
            soft_candidates.append(
                (blocker_count, total_length, segments, (label_x, label_y), justify)
            )
            continue

        lines = _segment_lines(
            segments,
            key + ":contact-topology:route",
            terminal_coordinates=terminal_coordinates,
        )
        lines.extend(_junctions_for_segments(segments, key + ":contact-topology:route"))
        lines.extend(
            _label_lines(
                name,
                label_x,
                label_y,
                key + ":contact-topology:label",
                justify=justify,
            )
        )
        occupied_segments.extend(segments)
        return lines
    if soft_candidates:
        _blocker_count, _total_length, segments, (label_x, label_y), justify = min(
            soft_candidates,
            key=lambda item: (item[0], item[1], item[3][1], item[3][0]),
        )
        lines = _segment_lines(
            segments,
            key + ":contact-topology:route",
            terminal_coordinates=terminal_coordinates,
        )
        lines.extend(_junctions_for_segments(segments, key + ":contact-topology:route"))
        lines.extend(
            _label_lines(
                name,
                label_x,
                label_y,
                key + ":contact-topology:label",
                justify=justify,
            )
        )
        occupied_segments.extend(segments)
        return lines
    return None


def _stacked_tap_route_lines(
    name: str,
    points: list[PinPoint],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    label_away_from_x: float | None = None,
) -> list[PlacedItem] | None:
    if len(points) != 3:
        return None

    by_pin_x: dict[float, list[PinPoint]] = {}
    for point in points:
        by_pin_x.setdefault(point.x, []).append(point)

    tap_points = next(
        (
            sorted(items, key=lambda point: point.y)
            for items in by_pin_x.values()
            if len(items) == 2 and max(point.y for point in items) - min(
                point.y for point in items
            )
            <= 35.56
        ),
        None,
    )
    if tap_points is None:
        return None

    remaining = [point for point in points if point not in tap_points]
    if len(remaining) != 1:
        return None

    tap_x = tap_points[0].x
    if any(abs(point.x - tap_x) > 0.001 for point in tap_points):
        return None

    top_tap, bottom_tap = sorted(tap_points, key=lambda point: point.y)
    tap_y = _snap_grid((top_tap.y + bottom_tap.y) / 2)
    source = remaining[0]
    base_label_x = _snap_grid(tap_x + PIN_LABEL_STUB)
    if page_width is not None and base_label_x > page_width - SYMBOL_MARGIN_X:
        base_label_x = _snap_grid(tap_x - PIN_LABEL_STUB)
    label_sign = 1 if base_label_x >= tap_x else -1
    label_x_candidates = tuple(
        dict.fromkeys(
            _snap_grid(base_label_x + label_sign * offset)
            for offset in (0.0, 5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
        )
    )

    allowed = _pin_point_coordinates(points) | {
        _coordinate(tap_x, tap_y),
        *(_coordinate(label_x, tap_y) for label_x in label_x_candidates),
    }
    for route_segments in _stacked_tap_route_candidates(source, tap_x, tap_y):
        for label_x in label_x_candidates:
            fixed_segments = [
                (tap_x, top_tap.y, tap_x, bottom_tap.y),
                (tap_x, tap_y, label_x, tap_y),
            ]
            segments = _without_zero_segments([*fixed_segments, *route_segments])
            if not _segments_clear_obstacles(segments, obstacles=obstacles, allowed=allowed):
                continue
            if not _segments_clear_blockers(segments, blocked_rects):
                continue
            if not _segments_clear_existing(segments, occupied_segments):
                continue
            justify = (
                _label_justify_away_from_x(
                    label_x,
                    label_away_from_x,
                    page_width=page_width,
                )
                if label_away_from_x is not None
                else _label_justify_for_point(
                    PinPoint(x=tap_x, y=tap_y, label_x=label_x, label_y=tap_y),
                    page_width,
                )
            )
            if not _label_rect_clears_blockers(
                name,
                label_x,
                tap_y,
                justify=justify,
                blocked_rects=label_blocked_rects,
            ):
                continue
            segments = _normalize_wire_segments(
                _split_segments_at_coordinates(
                    segments,
                    {_coordinate(tap_x, tap_y)},
                )
            )

            lines = _segment_lines(
                segments,
                key + ":route",
                terminal_coordinates=terminal_coordinates,
            )
            lines.extend(_junction_lines(tap_x, tap_y, key + ":route:junction:tap"))
            lines.extend(_label_lines(name, label_x, tap_y, key + ":label", justify=justify))
            occupied_segments.extend(segments)
            return lines
    return None


def _stacked_tap_route_candidates(
    source: PinPoint,
    tap_x: float,
    tap_y: float,
) -> list[list[WireSegment]]:
    if source.x <= tap_x:
        elbow_xs = [
            _snap_grid(tap_x - offset)
            for offset in (7.62, 10.16, 12.7, 15.24, 20.32)
            if tap_x - offset > source.x + 2.54
        ]
        if not elbow_xs:
            elbow_xs = [_snap_grid((source.x + tap_x) / 2)]
    else:
        elbow_xs = [
            _snap_grid(tap_x + offset)
            for offset in (7.62, 10.16, 12.7, 15.24, 20.32)
            if tap_x + offset < source.x - 2.54
        ]
        if not elbow_xs:
            elbow_xs = [_snap_grid((source.x + tap_x) / 2)]

    candidates: list[list[WireSegment]] = []
    for elbow_x in dict.fromkeys(elbow_xs):
        candidates.append(
            _without_zero_segments(
                [
                    (source.x, source.y, elbow_x, source.y),
                    (elbow_x, source.y, elbow_x, tap_y),
                    (elbow_x, tap_y, tap_x, tap_y),
                ]
            )
        )

    direct_source = PinPoint(
        x=source.x,
        y=source.y,
        label_x=source.x,
        label_y=source.y,
    )
    direct_tap = PinPoint(x=tap_x, y=tap_y, label_x=tap_x, label_y=tap_y)
    candidates.extend(_direct_route_candidates(direct_source, direct_tap))
    return candidates


def _straight_direct_net_lines(
    name: str,
    start: PinPoint,
    end: PinPoint,
    key: str,
    *,
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    label_away_from_x: float | None = None,
) -> list[PlacedItem]:
    left = start if start.label_x <= end.label_x else end
    right = end if left is start else start
    segments = _normalize_wire_segments(
        [
            *(
                segment
                for point in (left, right)
                for segment in [_point_stub_segment(point)]
                if segment is not None
            ),
            (left.label_x, left.label_y, right.label_x, right.label_y),
        ]
    )
    lines = _segment_lines(
        segments,
        key + ":route",
        terminal_coordinates=terminal_coordinates,
    )
    lines.extend(_junctions_for_segments(segments, key + ":route"))
    label_x = _snap_grid(min(left.label_x, right.label_x) + 5.08)
    lines.extend(
        _label_lines(
            name,
            label_x,
            left.label_y,
            key + ":label",
            justify=(
                _label_justify_away_from_x(label_x, label_away_from_x, page_width=None)
                if label_away_from_x is not None
                else _label_justify_away_from_points(
                    label_x,
                    [left, right],
                    page_width=None,
                )
            ),
        )
    )
    return lines


def _safe_direct_net_lines(
    name: str,
    start: PinPoint,
    end: PinPoint,
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    label_away_from_x: float | None = None,
) -> list[PlacedItem] | None:
    route_start = PinPoint(
        x=start.label_x,
        y=start.label_y,
        label_x=start.label_x,
        label_y=start.label_y,
    )
    route_end = PinPoint(x=end.x, y=end.y, label_x=end.x, label_y=end.y)
    segments = [
        segment
        for segment in [_point_stub_segment(start)]
        if segment is not None
    ]
    allowed = _pin_point_coordinates([start, end])
    for route_segments in _direct_route_candidates(route_start, route_end):
        candidate_segments = _normalize_wire_segments([*segments, *route_segments])
        if not _segments_clear_obstacles(
            candidate_segments,
            obstacles=obstacles,
            allowed=allowed,
        ):
            continue
        if not _segments_clear_blockers(candidate_segments, blocked_rects):
            continue
        if _segments_clear_existing(candidate_segments, occupied_segments):
            justify = (
                _label_justify_away_from_x(
                    start.label_x,
                    label_away_from_x,
                    page_width=page_width,
                )
                if label_away_from_x is not None
                else _label_justify_for_point(start, page_width)
            )
            if not _label_rect_clears_blockers(
                name,
                start.label_x,
                start.label_y,
                justify=justify,
                blocked_rects=label_blocked_rects,
            ):
                continue
            lines = _segment_lines(
                candidate_segments,
                key + ":route",
                terminal_coordinates=terminal_coordinates,
            )
            lines.extend(_junctions_for_segments(candidate_segments, key + ":route"))
            lines.extend(
                _point_label_lines(
                    name,
                    start,
                    key + ":label",
                    page_width=page_width,
                    away_from_x=label_away_from_x,
                )
            )
            occupied_segments.extend(candidate_segments)
            return lines

    return None


def _safe_passive_continuation_net_lines(
    name: str,
    source: PinPoint,
    passive: PinPoint,
    key: str,
    *,
    parent_anchor_x: float,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    require_clear_label: bool = True,
) -> list[PlacedItem] | None:
    label_origin = (
        passive
        if abs(passive.x - parent_anchor_x) >= abs(source.x - parent_anchor_x)
        else source
    )
    label_sign = -1 if label_origin.x <= parent_anchor_x else 1
    label_candidates = [
        (
            _snap_grid(label_origin.x + label_sign * offset),
            label_origin.y,
            _label_justify_away_from_x(
                _snap_grid(label_origin.x + label_sign * offset),
                parent_anchor_x,
                page_width=page_width,
            ),
        )
        for offset in (5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
    ]
    route_candidates: list[list[WireSegment]]
    if abs(source.y - passive.y) < 0.001:
        route_candidates = [[(source.x, source.y, passive.x, passive.y)]]
    else:
        route_candidates = _direct_route_candidates(
            PinPoint(source.x, source.y, source.x, source.y),
            PinPoint(passive.x, passive.y, passive.x, passive.y),
        )
    allowed_points = _pin_point_coordinates([source, passive])
    for require_blocker_clearance in (True, False):
        for route_segments in route_candidates:
            for label_x, label_y, justify in dict.fromkeys(label_candidates):
                label_coordinate = _coordinate(label_x, label_y)
                if label_coordinate in obstacles - allowed_points:
                    continue
                if require_clear_label and not _label_rect_clears_blockers(
                    name,
                    label_x,
                    label_y,
                    justify=justify,
                    blocked_rects=label_blocked_rects,
                ):
                    continue
                segments = _normalize_wire_segments(
                    _without_zero_segments(
                        [
                            *route_segments,
                            (label_origin.x, label_origin.y, label_x, label_y),
                        ]
                    )
                )
                if not _segments_clear_obstacles(
                    segments,
                    obstacles=obstacles,
                    allowed=allowed_points | {label_coordinate},
                ):
                    continue
                if require_blocker_clearance and not _segments_clear_blockers(
                    segments,
                    blocked_rects,
                ):
                    continue
                if not _segments_clear_existing(segments, occupied_segments):
                    continue

                lines = _segment_lines(
                    segments,
                    key + ":route",
                    terminal_coordinates=terminal_coordinates,
                )
                lines.extend(_junctions_for_segments(segments, key + ":route"))
                lines.extend(_label_lines(name, label_x, label_y, key + ":label", justify=justify))
                occupied_segments.extend(segments)
                return lines
    return None


def _safe_anchor_passive_direct_net_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    require_clear_label: bool = True,
) -> list[PlacedItem] | None:
    if not _is_safe_anchor_passive_pair(points):
        return None

    anchor_items = [
        (endpoint, point) for endpoint, point in points if _is_anchor_endpoint(endpoint)
    ]
    passive_items = [
        (endpoint, point) for endpoint, point in points if not _is_anchor_endpoint(endpoint)
    ]
    if len(anchor_items) != 1 or len(passive_items) != 1:
        return None

    _anchor_endpoint, anchor = anchor_items[0]
    _passive_endpoint, passive = passive_items[0]
    aligned_lines = _safe_collinear_anchor_passive_direct_net_lines(
        name,
        anchor,
        passive,
        key,
        page_width=page_width,
        obstacles=obstacles,
        occupied_segments=occupied_segments,
        terminal_coordinates=terminal_coordinates,
        blocked_rects=blocked_rects,
        label_blocked_rects=label_blocked_rects,
        require_clear_label=require_clear_label,
    )
    if aligned_lines is not None:
        return aligned_lines
    if abs(anchor.label_x - anchor.x) < 0.001:
        return _safe_vertical_anchor_passive_direct_net_lines(
            name,
            anchor,
            passive,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            require_clear_label=require_clear_label,
        )

    label_sign = -1 if passive.label_x < anchor.x else 1
    pin_label_candidates = [
        (
            _snap_grid(passive.label_x + label_sign * offset),
            passive.label_y,
            _label_justify_away_from_x(
                _snap_grid(passive.label_x + label_sign * offset),
                anchor.x,
                page_width=page_width,
            ),
            (passive.x, passive.y),
        )
        for offset in (0.0, 5.08, 10.16, 15.24, 20.32, 25.4)
    ]

    y_sign = 1 if passive.y >= anchor.y else -1
    if anchor.label_x < anchor.x:
        anchor_route_xs = [
            _snap_grid(anchor.label_x - offset)
            for offset in (2.54, 5.08, 7.62, 10.16, 15.24, 20.32, 25.4, 30.48)
        ]
    elif anchor.label_x > anchor.x:
        anchor_route_xs = [
            _snap_grid(anchor.label_x + offset)
            for offset in (2.54, 5.08, 7.62, 10.16, 15.24, 20.32, 25.4, 30.48)
        ]
    else:
        anchor_route_xs = [anchor.x]
    allowed = _pin_point_coordinates([anchor, passive])
    for require_blocker_clearance in (True, False):
        for anchor_route_x in dict.fromkeys(anchor_route_xs):
            for offset in (5.08, 10.16, 15.24, 20.32):
                trunk_y = _snap_grid(passive.y + y_sign * offset)
                trunk_label_candidates = [
                    (
                        _snap_grid(passive.x + label_sign * label_offset),
                        trunk_y,
                        _label_justify_away_from_x(
                            _snap_grid(passive.x + label_sign * label_offset),
                            anchor.x,
                            page_width=page_width,
                        ),
                        (passive.x, trunk_y),
                    )
                    for label_offset in (5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
                ]
                label_candidates = [
                    *pin_label_candidates,
                    *trunk_label_candidates,
                ]
                for label_candidate in dict.fromkeys(label_candidates):
                    if require_clear_label and not _label_rect_clears_blockers(
                        name,
                        label_candidate[0],
                        label_candidate[1],
                        justify=label_candidate[2],
                        blocked_rects=label_blocked_rects,
                    ):
                        continue
                    label_connection_x, label_connection_y = label_candidate[3]
                    label_segment = (
                        label_connection_x,
                        label_connection_y,
                        label_candidate[0],
                        label_candidate[1],
                    )
                    segments = _without_zero_segments(
                        [
                            label_segment,
                            (anchor.x, anchor.y, anchor_route_x, anchor.y),
                            (anchor_route_x, anchor.y, anchor_route_x, trunk_y),
                            (anchor_route_x, trunk_y, passive.x, trunk_y),
                            (passive.x, trunk_y, passive.x, passive.y),
                        ]
                    )
                    if not _segments_clear_obstacles(
                        segments,
                        obstacles=obstacles,
                        allowed=allowed
                        | {
                            _coordinate(label_candidate[0], label_candidate[1]),
                            _coordinate(label_connection_x, label_connection_y),
                            _coordinate(anchor_route_x, anchor.y),
                            _coordinate(anchor_route_x, trunk_y),
                            _coordinate(passive.x, trunk_y),
                        },
                    ):
                        continue
                    if require_blocker_clearance and not _segments_clear_blockers(
                        segments,
                        blocked_rects,
                    ):
                        continue
                    if not _segments_clear_existing(segments, occupied_segments):
                        continue

                    segments = _normalize_wire_segments(segments)
                    lines = _segment_lines(
                        segments,
                        key + ":route",
                        terminal_coordinates=terminal_coordinates,
                    )
                    lines.extend(_junctions_for_segments(segments, key + ":route"))
                    lines.extend(
                        _label_lines(
                            name,
                            label_candidate[0],
                            label_candidate[1],
                            key + ":label",
                            justify=label_candidate[2],
                        )
                    )
                    occupied_segments.extend(segments)
                    return lines
    return _safe_horizontal_anchor_escape_passive_direct_net_lines(
        name,
        anchor,
        passive,
        key,
        page_width=page_width,
        obstacles=obstacles,
        occupied_segments=occupied_segments,
        terminal_coordinates=terminal_coordinates,
        blocked_rects=blocked_rects,
        label_blocked_rects=label_blocked_rects,
        require_clear_label=require_clear_label,
    )


def _safe_collinear_anchor_passive_direct_net_lines(
    name: str,
    anchor: PinPoint,
    passive: PinPoint,
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    require_clear_label: bool = True,
) -> list[PlacedItem] | None:
    if abs(anchor.y - passive.y) >= 0.001:
        return None

    label_sign = -1 if passive.x < anchor.x else 1
    label_candidates = [
        (
            _snap_grid(passive.x + label_sign * offset),
            passive.y,
            _label_justify_away_from_x(
                _snap_grid(passive.x + label_sign * offset),
                anchor.x,
                page_width=page_width,
            ),
        )
        for offset in (5.08, 10.16, 15.24, 20.32, 25.4)
    ]
    for label_x, label_y, justify in label_candidates:
        if require_clear_label and not _label_rect_clears_blockers(
            name,
            label_x,
            label_y,
            justify=justify,
            blocked_rects=label_blocked_rects,
        ):
            continue
        segments = _normalize_wire_segments(
            _without_zero_segments(
                [
                    (anchor.x, anchor.y, passive.x, passive.y),
                    (passive.x, passive.y, label_x, label_y),
                ]
            )
        )
        if not _segments_clear_obstacles(
            segments,
            obstacles=obstacles,
            allowed=_pin_point_coordinates([anchor, passive]) | {_coordinate(label_x, label_y)},
        ):
            continue
        if not _segments_clear_blockers(segments, blocked_rects):
            continue
        if not _segments_clear_existing(segments, occupied_segments):
            continue

        lines = _segment_lines(
            segments,
            key + ":route",
            terminal_coordinates=terminal_coordinates,
        )
        lines.extend(_junctions_for_segments(segments, key + ":route"))
        lines.extend(_label_lines(name, label_x, label_y, key + ":label", justify=justify))
        occupied_segments.extend(segments)
        return lines
    return None


def _safe_horizontal_anchor_escape_passive_direct_net_lines(
    name: str,
    anchor: PinPoint,
    passive: PinPoint,
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    require_clear_label: bool = True,
) -> list[PlacedItem] | None:
    if abs(anchor.label_x - anchor.x) < 0.001:
        return None

    label_sign = -1 if passive.x < anchor.x else 1
    pin_label_candidates = [
        (
            _snap_grid(passive.label_x + label_sign * offset),
            passive.label_y,
            _label_justify_away_from_x(
                _snap_grid(passive.label_x + label_sign * offset),
                anchor.x,
                page_width=page_width,
            ),
            (passive.x, passive.y),
        )
        for offset in (0.0, 5.08, 10.16, 15.24, 20.32, 25.4)
    ]

    passive_route_sign = _passive_pin_route_y_sign(passive, anchor)
    route_x_sign = -1 if passive.x < anchor.x else 1
    route_xs = [
        _snap_grid(passive.x + route_x_sign * offset)
        for offset in (5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
    ]
    escape_y_candidates = [
        _snap_grid(anchor.y + sign * offset)
        for sign in (1, -1)
        for offset in (5.08, 7.62, 10.16, 15.24, 20.32, 25.4)
    ]
    allowed = _pin_point_coordinates([anchor, passive])
    for require_blocker_clearance in (True, False):
        for anchor_escape_y in dict.fromkeys(escape_y_candidates):
            for route_x in dict.fromkeys(route_xs):
                for offset in (5.08, 10.16, 15.24, 20.32):
                    trunk_y = _snap_grid(passive.y + passive_route_sign * offset)
                    trunk_label_candidates = [
                        (
                            _snap_grid(passive.x + label_sign * label_offset),
                            trunk_y,
                            _label_justify_away_from_x(
                                _snap_grid(passive.x + label_sign * label_offset),
                                anchor.x,
                                page_width=page_width,
                            ),
                            (passive.x, trunk_y),
                        )
                        for label_offset in (5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
                    ]
                    for label_candidate in dict.fromkeys(
                        [*pin_label_candidates, *trunk_label_candidates]
                    ):
                        if require_clear_label and not _label_rect_clears_blockers(
                            name,
                            label_candidate[0],
                            label_candidate[1],
                            justify=label_candidate[2],
                            blocked_rects=label_blocked_rects,
                        ):
                            continue
                        label_connection_x, label_connection_y = label_candidate[3]
                        label_segment = (
                            label_connection_x,
                            label_connection_y,
                            label_candidate[0],
                            label_candidate[1],
                        )
                        segments = _without_zero_segments(
                            [
                                label_segment,
                                (anchor.x, anchor.y, anchor.label_x, anchor.y),
                                (
                                    anchor.label_x,
                                    anchor.y,
                                    anchor.label_x,
                                    anchor_escape_y,
                                ),
                                (anchor.label_x, anchor_escape_y, route_x, anchor_escape_y),
                                (route_x, anchor_escape_y, route_x, trunk_y),
                                (route_x, trunk_y, passive.x, trunk_y),
                                (passive.x, trunk_y, passive.x, passive.y),
                            ]
                        )
                        if not _segments_clear_obstacles(
                            segments,
                            obstacles=obstacles,
                            allowed=allowed
                            | {
                                _coordinate(label_candidate[0], label_candidate[1]),
                                _coordinate(label_connection_x, label_connection_y),
                                _coordinate(anchor.label_x, anchor.y),
                                _coordinate(anchor.label_x, anchor_escape_y),
                                _coordinate(route_x, anchor_escape_y),
                                _coordinate(route_x, trunk_y),
                                _coordinate(passive.x, trunk_y),
                            },
                        ):
                            continue
                        if require_blocker_clearance and not _segments_clear_blockers(
                            segments,
                            blocked_rects,
                        ):
                            continue
                        if not _segments_clear_existing(segments, occupied_segments):
                            continue

                        segments = _normalize_wire_segments(segments)
                        lines = _segment_lines(
                            segments,
                            key + ":route",
                            terminal_coordinates=terminal_coordinates,
                        )
                        lines.extend(_junctions_for_segments(segments, key + ":route"))
                        lines.extend(
                            _label_lines(
                                name,
                                label_candidate[0],
                                label_candidate[1],
                                key + ":label",
                                justify=label_candidate[2],
                            )
                        )
                        occupied_segments.extend(segments)
                        return lines
    return None


def _safe_vertical_anchor_passive_direct_net_lines(
    name: str,
    anchor: PinPoint,
    passive: PinPoint,
    key: str,
    *,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    terminal_coordinates: dict[Coordinate, frozenset[str]] | None = None,
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] = (),
    require_clear_label: bool = True,
) -> list[PlacedItem] | None:
    if abs(anchor.label_y - anchor.y) < 0.001:
        return None

    label_sign = -1 if passive.x < anchor.x else 1
    pin_label_candidates = [
        (
            _snap_grid(passive.label_x + label_sign * offset),
            passive.label_y,
            _label_justify_away_from_x(
                _snap_grid(passive.label_x + label_sign * offset),
                anchor.x,
                page_width=page_width,
            ),
            (passive.x, passive.y),
        )
        for offset in (0.0, 5.08, 10.16, 15.24, 20.32, 25.4)
    ]

    anchor_escape_sign = -1 if anchor.label_y < anchor.y else 1
    passive_route_sign = _passive_pin_route_y_sign(passive, anchor)
    route_x_sign = -1 if passive.x < anchor.x else 1
    anchor_escape_ys = [
        _snap_grid(anchor.label_y + anchor_escape_sign * offset)
        for offset in (0.0, 2.54, 5.08, 7.62, 10.16, 15.24, 20.32)
    ]
    route_xs = [
        _snap_grid(passive.x + route_x_sign * offset)
        for offset in (5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
    ]
    allowed = _pin_point_coordinates([anchor, passive])
    for require_blocker_clearance in (True, False):
        for anchor_escape_y in dict.fromkeys(anchor_escape_ys):
            for route_x in dict.fromkeys(route_xs):
                for offset in (5.08, 10.16, 15.24, 20.32):
                    trunk_y = _snap_grid(passive.y + passive_route_sign * offset)
                    trunk_label_candidates = [
                        (
                            _snap_grid(passive.x + label_sign * label_offset),
                            trunk_y,
                            _label_justify_away_from_x(
                                _snap_grid(passive.x + label_sign * label_offset),
                                anchor.x,
                                page_width=page_width,
                            ),
                            (passive.x, trunk_y),
                        )
                        for label_offset in (5.08, 10.16, 15.24, 20.32, 25.4, 30.48)
                    ]
                    for label_candidate in dict.fromkeys(
                        [*pin_label_candidates, *trunk_label_candidates]
                    ):
                        if require_clear_label and not _label_rect_clears_blockers(
                            name,
                            label_candidate[0],
                            label_candidate[1],
                            justify=label_candidate[2],
                            blocked_rects=label_blocked_rects,
                        ):
                            continue
                        label_connection_x, label_connection_y = label_candidate[3]
                        label_segment = (
                            label_connection_x,
                            label_connection_y,
                            label_candidate[0],
                            label_candidate[1],
                        )
                        segments = _without_zero_segments(
                            [
                                label_segment,
                                (anchor.x, anchor.y, anchor.x, anchor_escape_y),
                                (anchor.x, anchor_escape_y, route_x, anchor_escape_y),
                                (route_x, anchor_escape_y, route_x, trunk_y),
                                (route_x, trunk_y, passive.x, trunk_y),
                                (passive.x, trunk_y, passive.x, passive.y),
                            ]
                        )
                        if not _segments_clear_obstacles(
                            segments,
                            obstacles=obstacles,
                            allowed=allowed
                            | {
                                _coordinate(label_candidate[0], label_candidate[1]),
                                _coordinate(label_connection_x, label_connection_y),
                                _coordinate(anchor.x, anchor_escape_y),
                                _coordinate(route_x, anchor_escape_y),
                                _coordinate(route_x, trunk_y),
                                _coordinate(passive.x, trunk_y),
                            },
                        ):
                            continue
                        if require_blocker_clearance and not _segments_clear_blockers(
                            segments,
                            blocked_rects,
                        ):
                            continue
                        if not _segments_clear_existing(segments, occupied_segments):
                            continue

                        segments = _normalize_wire_segments(segments)
                        lines = _segment_lines(
                            segments,
                            key + ":route",
                            terminal_coordinates=terminal_coordinates,
                        )
                        lines.extend(_junctions_for_segments(segments, key + ":route"))
                        lines.extend(
                            _label_lines(
                                name,
                                label_candidate[0],
                                label_candidate[1],
                                key + ":label",
                                justify=label_candidate[2],
                            )
                        )
                        occupied_segments.extend(segments)
                        return lines
    return None


def _passive_pin_route_y_sign(passive: PinPoint, anchor: PinPoint) -> int:
    if passive.label_y < passive.y:
        return -1
    if passive.label_y > passive.y:
        return 1
    return 1 if passive.y >= anchor.y else -1


def _is_compact_net(points: list[PinPoint]) -> bool:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return max(xs) - min(xs) <= 110 and max(ys) - min(ys) <= 110


def _is_local_route_span(points: list[PinPoint]) -> bool:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return max(xs) - min(xs) <= 190 and max(ys) - min(ys) <= 150


def _endpoint_ref(endpoint_text: str) -> str | None:
    ref, separator, _pin = endpoint_text.partition(".")
    return ref if separator else None


def _is_anchor_endpoint(endpoint_text: str) -> bool:
    ref = _endpoint_ref(endpoint_text)
    return ref is not None and _is_anchor_ref(ref)


def _symbol_pin_count(symbol_info: SymbolInfo | None) -> int:
    if symbol_info is None:
        return 0
    return len({pin.number for pin in symbol_info.pins})


def _dense_controller_refs(project: ResolvedProject, sheet_path: str) -> set[str]:
    sheet = project.source.sheets[sheet_path]
    refs: set[str] = set()
    for ref, symbol_decl in sheet.symbols.items():
        if ref.startswith("Module"):
            refs.add(ref)
            continue
        prefix = _symbol_prefix(ref)
        if prefix not in {"U", "IC"}:
            continue
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        if _symbol_pin_count(symbol_info) >= DENSE_CONTROLLER_PIN_COUNT:
            refs.add(ref)
    return refs


def _small_anchor_refs(project: ResolvedProject, sheet_path: str) -> set[str]:
    sheet = project.source.sheets[sheet_path]
    refs: set[str] = set()
    for ref, symbol_decl in sheet.symbols.items():
        if ref.startswith("Module"):
            continue
        if _symbol_prefix(ref) not in {"U", "IC"}:
            continue
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        if 0 < _symbol_pin_count(symbol_info) <= 3:
            refs.add(ref)
    return refs


def _visible_label_endpoint_rank(endpoint_text: str, dense_controller_refs: set[str]) -> int:
    ref = _endpoint_ref(endpoint_text)
    if ref is None:
        return 1
    if ref in dense_controller_refs:
        return 2
    if _is_anchor_ref(ref):
        return 0
    return 1


def _prefer_visible_label_points(
    points: list[tuple[str, PinPoint]],
    *,
    dense_controller_refs: set[str],
) -> list[tuple[str, PinPoint]]:
    return sorted(
        points,
        key=lambda item: (
            _visible_label_endpoint_rank(item[0], dense_controller_refs),
            item[1].x,
            item[1].y,
            item[0],
        ),
    )


def _anchor_label_xs(
    points: list[tuple[str, PinPoint]],
    *,
    dense_controller_refs: set[str],
) -> tuple[float, ...]:
    dense_points = tuple(
        point.x
        for endpoint_text, point in points
        for ref in [_endpoint_ref(endpoint_text)]
        if ref in dense_controller_refs
    )
    if dense_points:
        return dense_points
    return tuple(
        point.x
        for endpoint_text, point in points
        for ref in [_endpoint_ref(endpoint_text)]
        if ref is not None and _is_anchor_ref(ref)
    )


def _nearest_anchor_label_x(
    point: PinPoint,
    anchor_xs: tuple[float, ...],
) -> float | None:
    if not anchor_xs:
        return None
    anchor_x = min(anchor_xs, key=lambda candidate: abs(point.x - candidate))
    if abs(point.x - anchor_x) > LOCAL_PARENT_LABEL_RADIUS:
        return None
    return anchor_x


def _small_anchor_supplemental_label_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    dense_controller_refs: set[str],
    small_anchor_refs: set[str],
    connect_stubs: bool,
    existing_label_points: set[Coordinate] | None = None,
) -> list[PlacedItem]:
    if _is_groundish_net(name):
        return []

    seen_label_points = set(existing_label_points or set())
    lines: list[PlacedItem] = []
    for index, (endpoint_text, point) in enumerate(points):
        label_point = _coordinate(point.label_x, point.label_y)
        if label_point in seen_label_points:
            continue
        seen_label_points.add(label_point)
        ref = _endpoint_ref(endpoint_text)
        if ref not in small_anchor_refs or ref in dense_controller_refs:
            continue
        lines.extend(
            _point_stub_lines(
                point,
                f"{key}:small-anchor-visible-label:{index}:stub",
                start_terminal=endpoint_text,
            )
        )
        lines.extend(
            _point_label_lines(
                name,
                point,
                f"{key}:small-anchor-visible-label:{index}",
                page_width=page_width,
            )
        )
    return lines


def _is_safe_anchor_span(points: list[tuple[str, PinPoint]]) -> bool:
    if len(points) != 2 or not all(_is_anchor_endpoint(endpoint) for endpoint, _point in points):
        return False
    start = points[0][1]
    end = points[1][1]
    start_faces_right = start.label_x > start.x
    end_faces_right = end.label_x > end.x
    return (
        start_faces_right != end_faces_right
        and abs(start.label_y - end.label_y) < 0.001
        and abs(start.label_x - end.label_x) <= 130
    )


def _is_safe_anchor_passive_pair(points: list[tuple[str, PinPoint]]) -> bool:
    if len(points) != 2:
        return False
    anchor_count = sum(1 for endpoint, _point in points if _is_anchor_endpoint(endpoint))
    if anchor_count != 1:
        return False
    plain_points = [point for _endpoint, point in points]
    return _is_compact_net(plain_points)


def _anchor_passive_route_y(net_points: list[tuple[str, PinPoint]]) -> float | None:
    if len(net_points) != 2:
        return None
    anchor_ys: list[float] = []
    for endpoint_text, point in net_points:
        endpoint = parse_endpoint(endpoint_text)
        if endpoint.kind is not EndpointKind.SYMBOL_PIN or endpoint.ref is None:
            continue
        if _is_anchor_ref(endpoint.ref):
            anchor_ys.append(point.y)
    return anchor_ys[0] if len(anchor_ys) == 1 else None


def _net_route_order(item: tuple[str, list[tuple[str, PinPoint]]]) -> tuple[int, float, str]:
    net_name, net_points = item
    route_y = _anchor_passive_route_y(net_points)
    if route_y is not None:
        return (0, route_y, net_name)
    return (1, 0.0, net_name)


def _sheet_local_label_prefix(net_names: list[str]) -> str | None:
    candidates: dict[str, int] = {}
    for name in net_names:
        separator = name.find("_")
        if separator <= 0 or separator == len(name) - 1:
            continue
        prefix = name[: separator + 1]
        prefix_stem = prefix[:-1]
        if not any(ch.isspace() for ch in prefix_stem) and "+" not in prefix_stem:
            continue
        candidates[prefix] = candidates.get(prefix, 0) + 1

    if not candidates:
        return None
    prefix, count = max(candidates.items(), key=lambda item: (item[1], len(item[0])))
    return prefix if count >= 3 else None


def _display_net_label(
    name: str,
    *,
    compact_local_labels: bool,
    local_label_prefix: str | None = None,
) -> str:
    suffix = _local_signal_suffix(name)
    if compact_local_labels and suffix is not None:
        return suffix
    if local_label_prefix is not None and name.startswith(local_label_prefix):
        local_name = name[len(local_label_prefix) :]
        if local_name:
            return local_name
    return name


def _power_flag_positions(
    project: ResolvedProject,
    sheet_path: str,
    net_points_by_name: dict[str, list[tuple[str, PinPoint]]],
) -> dict[str, Point]:
    sheet = project.source.sheets[sheet_path]
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    positions: dict[str, Point] = {}
    used: set[Coordinate] = set()
    for index, net_name in enumerate(sorted(set(sheet.power_flags))):
        points = [point for _endpoint, point in net_points_by_name.get(net_name, [])]
        if points:
            anchor = sorted(points, key=lambda point: (point.y, point.x))[0]
            x = _snap_grid(_clamp(anchor.x - 12.7, min_x, max_x))
            y = _snap_grid(_clamp(anchor.y - 12.7, min_y, max_y))
        else:
            x = _snap_grid(min_x + (index % 4) * 25.4)
            y = _snap_grid(min_y + (index // 4) * 12.7)
        while (x, y) in used and y < max_y:
            y = _snap_grid(y + 7.62)
        if (x, y) in used:
            x = _snap_grid(_clamp(x + 10.16, min_x, max_x))
        used.add((x, y))
        positions[net_name] = Point(x=x, y=y)
    return positions


def _endpoint_pin_number(endpoint_text: str) -> str | None:
    endpoint = parse_endpoint(endpoint_text)
    if endpoint.kind is EndpointKind.SYMBOL_PIN:
        return endpoint.pin_number or endpoint.pin_name
    return None


def _passive_rail_banks(
    net_points_by_name: dict[str, list[tuple[str, PinPoint]]],
) -> list[PassiveRailBank]:
    endpoints_by_ref: dict[str, list[tuple[str, str, PinPoint]]] = {}
    for net_name, net_points in net_points_by_name.items():
        for endpoint_text, point in net_points:
            ref = _endpoint_ref(endpoint_text)
            if ref is None or _symbol_prefix(ref) != "C":
                continue
            pin_number = _endpoint_pin_number(endpoint_text)
            if pin_number is None:
                continue
            endpoints_by_ref.setdefault(ref, []).append((net_name, endpoint_text, point))

    grouped: dict[tuple[str, str], list[PassiveRailBankMember]] = {}
    for ref, endpoints in endpoints_by_ref.items():
        if len(endpoints) != 2:
            continue
        first, second = sorted(endpoints, key=lambda item: item[2].y)
        top_net, top_endpoint, top_point = first
        bottom_net, bottom_endpoint, bottom_point = second
        if _is_groundish_net(top_net) and not _is_groundish_net(bottom_net):
            top_net, bottom_net = bottom_net, top_net
            top_endpoint, bottom_endpoint = bottom_endpoint, top_endpoint
            top_point, bottom_point = bottom_point, top_point
        if _is_groundish_net(top_net) or not _is_groundish_net(bottom_net):
            continue
        grouped.setdefault((top_net, bottom_net), []).append(
            PassiveRailBankMember(
                ref=ref,
                top_endpoint=top_endpoint,
                bottom_endpoint=bottom_endpoint,
                top_point=top_point,
                bottom_point=bottom_point,
            )
        )

    banks: list[PassiveRailBank] = []
    for (top_net, bottom_net), members in sorted(grouped.items()):
        if len(members) < 2:
            continue
        ordered_members = tuple(sorted(members, key=lambda member: member.top_point.x))
        member_endpoints = {
            endpoint
            for member in ordered_members
            for endpoint in (member.top_endpoint, member.bottom_endpoint)
        }
        top_points = [member.top_point for member in ordered_members]
        bottom_points = [member.bottom_point for member in ordered_members]
        top_extras = tuple(
            (endpoint, point)
            for endpoint, point in net_points_by_name.get(top_net, [])
            if endpoint not in member_endpoints and _is_compact_net([*top_points, point])
            and _is_anchor_endpoint(endpoint)
        )
        bottom_extras = tuple(
            (endpoint, point)
            for endpoint, point in net_points_by_name.get(bottom_net, [])
            if endpoint not in member_endpoints and _is_compact_net([*bottom_points, point])
            and _is_anchor_endpoint(endpoint)
        )
        banks.append(
            PassiveRailBank(
                top_net=top_net,
                bottom_net=bottom_net,
                members=ordered_members,
                top_extras=top_extras,
                bottom_extras=bottom_extras,
            )
        )
    return banks


def _route_point_to_horizontal_rail_segments(
    point: PinPoint,
    rail_y: float,
    rail_min_x: float,
    rail_max_x: float,
) -> list[WireSegment]:
    target_x = _snap_grid(_clamp(point.x, rail_min_x, rail_max_x))
    return _without_zero_segments(
        [
            (point.x, point.y, point.x, rail_y),
            (point.x, rail_y, target_x, rail_y),
        ]
    )


def _passive_rail_bank_allowed_coordinates(bank: PassiveRailBank) -> set[Coordinate]:
    points = [
        point
        for member in bank.members
        for point in (member.top_point, member.bottom_point)
    ]
    points.extend(point for _endpoint, point in bank.top_extras)
    points.extend(point for _endpoint, point in bank.bottom_extras)
    return _pin_point_coordinates(points)


def _passive_rail_bank_endpoint_texts_for_bank(bank: PassiveRailBank) -> set[str]:
    return {
        *(
            endpoint
            for member in bank.members
            for endpoint in (member.top_endpoint, member.bottom_endpoint)
        ),
        *(endpoint for endpoint, _point in bank.top_extras),
        *(endpoint for endpoint, _point in bank.bottom_extras),
    }


def _passive_rail_bank_endpoint_texts_for_net(
    bank: PassiveRailBank,
    net_name: str,
) -> set[str]:
    if net_name == bank.top_net:
        return {
            *(member.top_endpoint for member in bank.members),
            *(endpoint for endpoint, _point in bank.top_extras),
        }
    if net_name == bank.bottom_net:
        return {
            *(member.bottom_endpoint for member in bank.members),
            *(endpoint for endpoint, _point in bank.bottom_extras),
        }
    return set()


def _endpoint_stub_segments_by_net(
    net_points_by_name: dict[str, list[tuple[str, PinPoint]]],
) -> tuple[EndpointStubSegment, ...]:
    stubs: list[EndpointStubSegment] = []
    for net_name, net_points in net_points_by_name.items():
        for endpoint, point in net_points:
            segment = _point_stub_segment(point)
            if segment is not None:
                stubs.append((net_name, endpoint, segment))
    return tuple(stubs)


def _segments_touch_reserved_endpoint_stubs(
    segments: list[WireSegment],
    reserved_endpoint_stubs: tuple[EndpointStubSegment, ...],
    *,
    net_name: str,
    allowed_endpoints: set[str],
) -> bool:
    return any(
        endpoint not in allowed_endpoints
        and reserved_net != net_name
        and _segments_touch(segment, reserved_segment)
        for segment in segments
        for reserved_net, endpoint, reserved_segment in reserved_endpoint_stubs
    )


def _passive_rail_bank_horizontal_lines(
    bank: PassiveRailBank,
    key: str,
    *,
    page_width: float | None,
    compact_local_labels: bool,
    local_label_prefix: str | None,
    obstacles: set[Coordinate],
    blocked_rects: tuple[LayoutRect, ...],
) -> list[PlacedItem] | None:
    top_points = [member.top_point for member in bank.members]
    bottom_points = [member.bottom_point for member in bank.members]
    if (
        len({round(point.y, 2) for point in top_points}) != 1
        or len({round(point.y, 2) for point in bottom_points}) != 1
    ):
        return None

    top_rail_y = round(top_points[0].y, 2)
    bottom_rail_y = round(bottom_points[0].y, 2)
    rail_min_x = _snap_grid(min(point.x for point in [*top_points, *bottom_points]))
    rail_max_x = _snap_grid(max(point.x for point in [*top_points, *bottom_points]))
    top_label_x = _snap_grid(rail_max_x + 15.24)
    if page_width is not None:
        top_label_x = min(top_label_x, _snap_grid(page_width - 25.4))
    top_rail_end_x = max(rail_max_x, top_label_x)
    ground_drop_x = _snap_grid(sorted(point.x for point in bottom_points)[len(bottom_points) // 2])
    ground_label_y = _snap_grid(bottom_rail_y + 12.7)

    segments = _without_zero_segments(
        [
            (rail_min_x, top_rail_y, top_rail_end_x, top_rail_y),
            (rail_min_x, bottom_rail_y, rail_max_x, bottom_rail_y),
            (ground_drop_x, bottom_rail_y, ground_drop_x, ground_label_y),
        ]
    )
    for _endpoint, point in bank.top_extras:
        segments.extend(
            _route_point_to_horizontal_rail_segments(
                point,
                top_rail_y,
                rail_min_x,
                top_rail_end_x,
            )
        )
    for _endpoint, point in bank.bottom_extras:
        segments.extend(
            _route_point_to_horizontal_rail_segments(
                point,
                bottom_rail_y,
                rail_min_x,
                rail_max_x,
            )
        )

    split_points = {
        _coordinate(point.x, top_rail_y) for point in top_points
    } | {
        _coordinate(point.x, bottom_rail_y) for point in bottom_points
    } | {
        _coordinate(ground_drop_x, bottom_rail_y),
        _coordinate(ground_drop_x, ground_label_y),
        _coordinate(top_label_x, top_rail_y),
        *(_coordinate(point.x, top_rail_y) for _endpoint, point in bank.top_extras),
        *(_coordinate(point.x, bottom_rail_y) for _endpoint, point in bank.bottom_extras),
    }
    allowed = _passive_rail_bank_allowed_coordinates(bank) | split_points
    if not _segments_clear_obstacles(segments, obstacles=obstacles, allowed=allowed):
        return None
    if not _segments_clear_blockers(segments, blocked_rects):
        return None
    segments = _split_segments_at_coordinates(segments, split_points)

    return _passive_rail_bank_render_lines(
        bank,
        key,
        segments,
        split_points,
        top_label=(
            _display_net_label(
                bank.top_net,
                compact_local_labels=compact_local_labels,
                local_label_prefix=local_label_prefix,
            ),
            top_label_x,
            top_rail_y,
        ),
        bottom_label=(
            _display_net_label(
                bank.bottom_net,
                compact_local_labels=compact_local_labels,
                local_label_prefix=local_label_prefix,
            ),
            ground_drop_x,
            ground_label_y,
        ),
        page_width=page_width,
    )


def _side_rail_x_candidates(
    points: list[PinPoint],
    *,
    page_width: float | None,
) -> tuple[list[float], list[float]]:
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_allowed = SYMBOL_MARGIN_X / 2
    max_allowed = page_width - SYMBOL_MARGIN_X / 2 if page_width is not None else None
    left = [_snap_grid(min_x - offset) for offset in (10.16, 15.24, 20.32, 25.4)]
    right = [_snap_grid(max_x + offset) for offset in (10.16, 15.24, 20.32, 25.4)]

    def in_bounds(values: list[float]) -> list[float]:
        bounded = []
        for value in values:
            if value < min_allowed:
                continue
            if max_allowed is not None and value > max_allowed:
                continue
            bounded.append(value)
        return list(dict.fromkeys(bounded))

    return in_bounds(left), in_bounds(right)


def _passive_rail_bank_side_segments(
    bank: PassiveRailBank,
    *,
    top_rail_x: float,
    bottom_rail_x: float,
) -> tuple[
    list[WireSegment],
    list[WireSegment],
    list[WireSegment],
    set[Coordinate],
    tuple[float, float],
    tuple[float, float],
]:
    top_route_points = [member.top_point for member in bank.members]
    top_route_points.extend(point for _endpoint, point in bank.top_extras)
    bottom_route_points = [member.bottom_point for member in bank.members]
    bottom_route_points.extend(point for _endpoint, point in bank.bottom_extras)

    top_label_y = _snap_grid(min(point.y for point in top_route_points) - 5.08)
    top_min_y = min(top_label_y, *(point.y for point in top_route_points))
    top_max_y = max(point.y for point in top_route_points)
    bottom_label_y = _snap_grid(max(point.y for point in bottom_route_points) + 5.08)
    bottom_min_y = min(point.y for point in bottom_route_points)
    bottom_max_y = max(bottom_label_y, *(point.y for point in bottom_route_points))

    top_segments = _without_zero_segments(
        [
            (top_rail_x, top_min_y, top_rail_x, top_max_y),
            *(
                (point.x, point.y, top_rail_x, point.y)
                for point in top_route_points
            ),
        ]
    )
    bottom_segments = _without_zero_segments(
        [
            (bottom_rail_x, bottom_min_y, bottom_rail_x, bottom_max_y),
            *(
                (point.x, point.y, bottom_rail_x, point.y)
                for point in bottom_route_points
            ),
        ]
    )
    split_points = {
        _coordinate(top_rail_x, top_label_y),
        *(_coordinate(top_rail_x, point.y) for point in top_route_points),
        _coordinate(bottom_rail_x, bottom_label_y),
        *(_coordinate(bottom_rail_x, point.y) for point in bottom_route_points),
    }
    return (
        [*top_segments, *bottom_segments],
        top_segments,
        bottom_segments,
        split_points,
        (top_rail_x, top_label_y),
        (bottom_rail_x, bottom_label_y),
    )


def _segments_touch_between_nets(
    first_segments: list[WireSegment],
    second_segments: list[WireSegment],
) -> bool:
    return any(
        _segments_touch(first, second)
        for first in first_segments
        for second in second_segments
    )


def _passive_rail_bank_side_lines(
    bank: PassiveRailBank,
    key: str,
    *,
    page_width: float | None,
    compact_local_labels: bool,
    local_label_prefix: str | None,
    obstacles: set[Coordinate],
    blocked_rects: tuple[LayoutRect, ...],
    reserved_endpoint_stubs: tuple[EndpointStubSegment, ...],
) -> list[PlacedItem] | None:
    top_route_points = [member.top_point for member in bank.members]
    top_route_points.extend(point for _endpoint, point in bank.top_extras)
    bottom_route_points = [member.bottom_point for member in bank.members]
    bottom_route_points.extend(point for _endpoint, point in bank.bottom_extras)
    top_left, top_right = _side_rail_x_candidates(top_route_points, page_width=page_width)
    bottom_left, bottom_right = _side_rail_x_candidates(
        bottom_route_points,
        page_width=page_width,
    )
    rail_pairs = [
        *( (top_x, bottom_x) for top_x in top_right for bottom_x in bottom_left ),
        *( (top_x, bottom_x) for top_x in top_right for bottom_x in bottom_right ),
        *( (top_x, bottom_x) for top_x in top_left for bottom_x in bottom_left ),
        *( (top_x, bottom_x) for top_x in top_left for bottom_x in bottom_right ),
    ]
    top_allowed_endpoints = _passive_rail_bank_endpoint_texts_for_net(bank, bank.top_net)
    bottom_allowed_endpoints = _passive_rail_bank_endpoint_texts_for_net(bank, bank.bottom_net)

    for top_rail_x, bottom_rail_x in list(dict.fromkeys(rail_pairs)):
        if abs(top_rail_x - bottom_rail_x) < 0.001:
            continue
        (
            segments,
            top_segments,
            bottom_segments,
            split_points,
            top_label_point,
            bottom_label_point,
        ) = _passive_rail_bank_side_segments(
            bank,
            top_rail_x=top_rail_x,
            bottom_rail_x=bottom_rail_x,
        )
        if _segments_touch_between_nets(top_segments, bottom_segments):
            continue
        if _segments_touch_reserved_endpoint_stubs(
            top_segments,
            reserved_endpoint_stubs,
            net_name=bank.top_net,
            allowed_endpoints=top_allowed_endpoints,
        ):
            continue
        if _segments_touch_reserved_endpoint_stubs(
            bottom_segments,
            reserved_endpoint_stubs,
            net_name=bank.bottom_net,
            allowed_endpoints=bottom_allowed_endpoints,
        ):
            continue
        allowed = _passive_rail_bank_allowed_coordinates(bank) | split_points
        if not _segments_clear_obstacles(segments, obstacles=obstacles, allowed=allowed):
            continue
        if not _segments_clear_blockers(segments, blocked_rects):
            continue
        segments = _split_segments_at_coordinates(segments, split_points)
        return _passive_rail_bank_render_lines(
            bank,
            key,
            segments,
            split_points,
            top_label=(
                _display_net_label(
                    bank.top_net,
                    compact_local_labels=compact_local_labels,
                    local_label_prefix=local_label_prefix,
                ),
                top_label_point[0],
                top_label_point[1],
            ),
            bottom_label=(
                _display_net_label(
                    bank.bottom_net,
                    compact_local_labels=compact_local_labels,
                    local_label_prefix=local_label_prefix,
                ),
                bottom_label_point[0],
                bottom_label_point[1],
            ),
            page_width=page_width,
        )
    return None


def _passive_rail_bank_render_lines(
    bank: PassiveRailBank,
    key: str,
    segments: list[WireSegment],
    split_points: set[Coordinate],
    *,
    top_label: tuple[str, float, float],
    bottom_label: tuple[str, float, float],
    page_width: float | None,
) -> list[PlacedItem]:
    top_label_name, top_label_x, top_label_y = top_label
    bottom_label_name, bottom_label_x, bottom_label_y = bottom_label
    top_points = [member.top_point for member in bank.members]
    top_points.extend(point for _endpoint, point in bank.top_extras)
    bottom_points = [member.bottom_point for member in bank.members]
    terminal_coordinates = _terminal_coordinates(
        [
            *(
                (member.top_endpoint, member.top_point)
                for member in bank.members
            ),
            *(
                (member.bottom_endpoint, member.bottom_point)
                for member in bank.members
            ),
            *bank.top_extras,
            *bank.bottom_extras,
        ]
    )
    lines = _segment_lines(
        segments,
        key + ":route",
        terminal_coordinates=terminal_coordinates,
    )
    for index, coordinate in enumerate(sorted(split_points)):
        lines.extend(_junction_lines(coordinate[0], coordinate[1], f"{key}:junction:{index}"))
    lines.extend(
        _label_lines(
            top_label_name,
            top_label_x,
            top_label_y,
            key + ":top-label",
            justify=_label_justify_away_from_points(
                top_label_x,
                top_points,
                page_width=page_width,
            ),
        )
    )
    bottom_points.extend(point for _endpoint, point in bank.bottom_extras)
    lines.extend(
        _label_lines(
            bottom_label_name,
            bottom_label_x,
            bottom_label_y,
            key + ":bottom-label",
            justify=_label_justify_away_from_points(
                bottom_label_x,
                bottom_points,
                page_width=page_width,
            ),
        )
    )
    return lines


def _passive_rail_bank_lines(
    bank: PassiveRailBank,
    key: str,
    *,
    page_width: float | None,
    compact_local_labels: bool,
    local_label_prefix: str | None = None,
    obstacles: set[Coordinate],
    blocked_rects: tuple[LayoutRect, ...] = (),
    reserved_endpoint_stubs: tuple[EndpointStubSegment, ...] = (),
) -> list[PlacedItem] | None:
    horizontal_lines = _passive_rail_bank_horizontal_lines(
        bank,
        key,
        page_width=page_width,
        compact_local_labels=compact_local_labels,
        local_label_prefix=local_label_prefix,
        obstacles=obstacles,
        blocked_rects=blocked_rects,
    )
    if horizontal_lines is not None:
        return horizontal_lines
    return _passive_rail_bank_side_lines(
        bank,
        key,
        page_width=page_width,
        compact_local_labels=compact_local_labels,
        local_label_prefix=local_label_prefix,
        obstacles=obstacles,
        blocked_rects=blocked_rects,
        reserved_endpoint_stubs=reserved_endpoint_stubs,
    )


def _passive_rail_bank_endpoint_texts(banks: list[PassiveRailBank]) -> set[str]:
    return {
        endpoint
        for bank in banks
        for endpoint in (
            *(
                endpoint
                for member in bank.members
                for endpoint in (member.top_endpoint, member.bottom_endpoint)
            ),
            *(endpoint for endpoint, _point in bank.top_extras),
            *(endpoint for endpoint, _point in bank.bottom_extras),
        )
    }


def _passive_rail_bank_member_subbanks(bank: PassiveRailBank) -> list[PassiveRailBank]:
    members = sorted(bank.members, key=lambda member: (member.top_point.y, member.top_point.x))
    subbanks: list[PassiveRailBank] = []
    for size in range(len(members) - 1, 2, -1):
        for start in range(0, len(members) - size + 1):
            submembers = tuple(members[start : start + size])
            top_points = [member.top_point for member in submembers]
            top_extras = tuple(
                (endpoint, point)
                for endpoint, point in bank.top_extras
                if _is_compact_net([*top_points, point])
            )
            bottom_points = [member.bottom_point for member in submembers]
            bottom_extras = tuple(
                (endpoint, point)
                for endpoint, point in bank.bottom_extras
                if _is_compact_net([*bottom_points, point])
            )
            subbanks.append(
                PassiveRailBank(
                    top_net=bank.top_net,
                    bottom_net=bank.bottom_net,
                    members=submembers,
                    top_extras=top_extras,
                    bottom_extras=bottom_extras,
                )
            )
    return subbanks


def _should_emit_rail(name: str, points: list[PinPoint]) -> bool:
    return len(points) >= 3 and _is_powerish_net(name)


def _rail_side(point: PinPoint) -> str:
    if point.label_y < point.y:
        return "top"
    if point.label_y > point.y:
        return "bottom"
    return "left" if point.label_x <= point.x else "right"


def _rail_label_justify(rail_x: float, page_width: float | None) -> str:
    if page_width is not None and rail_x >= page_width - LABEL_EDGE_MARGIN:
        return "right"
    return "left"


def _label_justify_away_from_points(
    label_x: float,
    points: list[PinPoint],
    *,
    page_width: float | None,
) -> str:
    if page_width is not None and label_x <= LABEL_EDGE_MARGIN:
        return "left"
    if page_width is not None and label_x >= page_width - LABEL_EDGE_MARGIN:
        return "right"
    center_x = sum(point.x for point in points) / len(points)
    return "right" if label_x < center_x else "left"


def _rail_spine_lines(
    fixed: float,
    offsets: list[float],
    key: str,
    *,
    vertical: bool,
) -> list[PlacedItem]:
    unique_offsets = sorted(set(offsets))
    lines: list[PlacedItem] = []
    for index, (start, end) in enumerate(zip(unique_offsets, unique_offsets[1:], strict=False)):
        if start == end:
            continue
        if vertical:
            lines.extend(_wire_lines(fixed, start, fixed, end, f"{key}:segment:{index}"))
        else:
            lines.extend(_wire_lines(start, fixed, end, fixed, f"{key}:segment:{index}"))
    return lines


def _points_are_on_one_symbol(points: list[tuple[str, PinPoint]]) -> bool:
    refs: set[str] = set()
    for endpoint_text, _point in points:
        endpoint = parse_endpoint(endpoint_text)
        if endpoint.kind is not EndpointKind.SYMBOL_PIN or endpoint.ref is None:
            return False
        refs.add(endpoint.ref)
    return len(refs) == 1


def _rail_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    hide_duplicate_labels: bool = False,
) -> list[PlacedItem]:
    grouped: dict[str, list[tuple[str, PinPoint]]] = {
        "left": [],
        "right": [],
        "top": [],
        "bottom": [],
    }
    for endpoint_text, point in points:
        grouped[_rail_side(point)].append((endpoint_text, point))

    lines: list[PlacedItem] = []
    visible_label_emitted = False

    def emit_discrete_points(side: str, side_points: list[tuple[str, PinPoint]]) -> None:
        nonlocal visible_label_emitted
        for index, (endpoint_text, point) in enumerate(side_points):
            point_key = f"{key}:{side}:{index}"
            lines.extend(
                _point_stub_lines(
                    point,
                    point_key + ":stub",
                    start_terminal=endpoint_text,
                )
            )
            lines.extend(
                _point_label_lines(
                    name,
                    point,
                    point_key + ":label",
                    page_width=page_width,
                    hidden=hide_duplicate_labels and visible_label_emitted,
                )
            )
            visible_label_emitted = True

    for side, side_points in grouped.items():
        if not side_points:
            continue
        if len(side_points) < 3:
            emit_discrete_points(side, side_points)
            continue
        side_pin_points = [point for _endpoint_text, point in side_points]

        if side == "left":
            rail_x = min(min(point.x, point.label_x) for point in side_pin_points) - 5.08
            rail_min_y = min(point.label_y for point in side_pin_points)
            rail_max_y = max(point.label_y for point in side_pin_points)
            lines.extend(
                _rail_spine_lines(
                    rail_x,
                    [rail_min_y, rail_max_y, *(point.y for point in side_pin_points)],
                    key + ":left:rail",
                    vertical=True,
                )
            )
            for index, (endpoint_text, point) in enumerate(side_points):
                point_key = f"{key}:left:{index}"
                lines.extend(
                    _wire_lines(
                        point.x,
                        point.y,
                        rail_x,
                        point.y,
                        point_key + ":rail-join",
                        start_terminals=_terminal_set(endpoint_text),
                    )
                )
                lines.extend(_junction_lines(rail_x, point.y, point_key + ":junction"))
            lines.extend(
                _label_lines(
                    name,
                    rail_x,
                    rail_min_y,
                    key + ":left:label",
                    justify=_label_justify_away_from_points(
                        rail_x,
                        side_pin_points,
                        page_width=page_width,
                    ),
                    hidden=hide_duplicate_labels and visible_label_emitted,
                )
            )
            visible_label_emitted = True
        elif side == "right":
            rail_x = max(max(point.x, point.label_x) for point in side_pin_points) + 5.08
            rail_min_y = min(point.label_y for point in side_pin_points)
            rail_max_y = max(point.label_y for point in side_pin_points)
            lines.extend(
                _rail_spine_lines(
                    rail_x,
                    [rail_min_y, rail_max_y, *(point.y for point in side_pin_points)],
                    key + ":right:rail",
                    vertical=True,
                )
            )
            for index, (endpoint_text, point) in enumerate(side_points):
                point_key = f"{key}:right:{index}"
                lines.extend(
                    _wire_lines(
                        point.x,
                        point.y,
                        rail_x,
                        point.y,
                        point_key + ":rail-join",
                        start_terminals=_terminal_set(endpoint_text),
                    )
                )
                lines.extend(_junction_lines(rail_x, point.y, point_key + ":junction"))
            lines.extend(
                _label_lines(
                    name,
                    rail_x,
                    rail_min_y,
                    key + ":right:label",
                    justify=_label_justify_away_from_points(
                        rail_x,
                        side_pin_points,
                        page_width=page_width,
                    ),
                    hidden=hide_duplicate_labels and visible_label_emitted,
                )
            )
            visible_label_emitted = True
        else:
            rail_min_x = min(point.label_x for point in side_pin_points)
            rail_max_x = max(point.label_x for point in side_pin_points)
            if abs(rail_max_x - rail_min_x) < 0.001:
                emit_discrete_points(side, side_points)
                continue

            rail_y = (
                min(min(point.y, point.label_y) for point in side_pin_points) - 5.08
                if side == "top"
                else max(max(point.y, point.label_y) for point in side_pin_points) + 5.08
            )
            lines.extend(
                _rail_spine_lines(
                    rail_y,
                    [rail_min_x, rail_max_x, *(point.x for point in side_pin_points)],
                    key + f":{side}:rail",
                    vertical=False,
                )
            )
            for index, (endpoint_text, point) in enumerate(side_points):
                point_key = f"{key}:{side}:{index}"
                lines.extend(
                    _wire_lines(
                        point.x,
                        point.y,
                        point.x,
                        rail_y,
                        point_key + ":rail-join",
                        start_terminals=_terminal_set(endpoint_text),
                    )
                )
                lines.extend(_junction_lines(point.x, rail_y, point_key + ":junction"))
            lines.extend(
                _label_lines(
                    name,
                    rail_min_x,
                    rail_y,
                    key + f":{side}:label",
                    justify=_label_justify_away_from_points(
                        rail_min_x,
                        side_pin_points,
                        page_width=page_width,
                    ),
                    hidden=hide_duplicate_labels and visible_label_emitted,
                )
            )
            visible_label_emitted = True
    return lines


def _rail_lines_if_clear_existing(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    page_width: float | None,
    occupied_segments: list[WireSegment],
    hide_duplicate_labels: bool = False,
) -> list[PlacedItem] | None:
    lines = _rail_lines(
        name,
        points,
        key,
        page_width=page_width,
        hide_duplicate_labels=hide_duplicate_labels,
    )
    segments = [
        (item.start[0], item.start[1], item.end[0], item.end[1])
        for item in lines
        if isinstance(item, PlacedWire)
    ]
    if _segments_clear_existing(segments, occupied_segments):
        return lines
    return None


def _net_point_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    allow_shared_rails: bool,
    allow_direct_nets: bool,
    allow_safe_direct_nets: bool,
    allow_safe_local_rails: bool,
    allow_anchor_direct_nets: bool,
    allow_anchor_passive_direct_nets: bool,
    allow_medium_signal_rails: bool,
    allow_contact_topology_nets: bool = False,
    compact_local_labels: bool,
    local_label_prefix: str | None,
    dense_controller_refs: set[str],
    small_anchor_refs: set[str] | None = None,
    hide_duplicate_labels: bool,
    force_hidden_labels: bool = False,
    page_width: float | None,
    obstacles: set[Coordinate],
    occupied_segments: list[WireSegment],
    blocked_rects: tuple[LayoutRect, ...] = (),
    label_blocked_rects: tuple[LayoutRect, ...] | None = None,
    circuit_regions: SheetCircuitRegions | None = None,
) -> list[PlacedItem]:
    label_name = _display_net_label(
        name,
        compact_local_labels=compact_local_labels,
        local_label_prefix=local_label_prefix,
    )
    small_anchor_refs = set() if small_anchor_refs is None else small_anchor_refs
    anchor_label_xs = _anchor_label_xs(points, dense_controller_refs=dense_controller_refs)
    label_blocked_rects = blocked_rects if label_blocked_rects is None else label_blocked_rects
    points = [
        (
            endpoint_text,
            _point_with_clear_label(
                label_name,
                point,
                page_width=page_width,
                blocked_rects=label_blocked_rects,
                stub_blocked_rects=blocked_rects,
                obstacles=obstacles,
                occupied_segments=occupied_segments,
                away_from_x=_nearest_anchor_label_x(point, anchor_label_xs),
            ),
        )
        for endpoint_text, point in points
    ]
    if force_hidden_labels:
        hidden_lines: list[PlacedItem] = []
        for index, (endpoint_text, point) in enumerate(points):
            point_key = f"{key}:{endpoint_text}:{index}"
            hidden_lines.extend(
                _point_stub_lines(
                    point,
                    point_key + ":wire",
                    start_terminal=endpoint_text,
                )
            )
            _record_point_stub_segment(point, occupied_segments)
            hidden_lines.extend(
                _point_label_lines(
                    label_name,
                    point,
                    point_key + ":label",
                    page_width=page_width,
                    hidden=True,
                    away_from_x=_nearest_anchor_label_x(point, anchor_label_xs),
                )
            )
        return hidden_lines
    if len(points) > 1 and _points_are_on_one_symbol(points):
        discrete_lines: list[PlacedItem] = []
        for index, (endpoint_text, point) in enumerate(points):
            point_key = f"{key}:{endpoint_text}:{index}"
            discrete_lines.extend(
                _point_stub_lines(
                    point,
                    point_key + ":wire",
                    start_terminal=endpoint_text,
                )
            )
            _record_point_stub_segment(point, occupied_segments)
            discrete_lines.extend(
                _point_label_lines(
                    label_name,
                    point,
                    point_key + ":label",
                    page_width=page_width,
                    hidden=hide_duplicate_labels and index > 0,
                    away_from_x=_nearest_anchor_label_x(point, anchor_label_xs),
                )
            )
        return discrete_lines
    if len(points) > 1:
        points = _prefer_visible_label_points(
            points,
            dense_controller_refs=dense_controller_refs,
        )
    plain_points = [point for _endpoint_text, point in points]
    terminal_coordinates = _terminal_coordinates(points)

    def label_away_from_x(point: PinPoint) -> float | None:
        return _nearest_anchor_label_x(point, anchor_label_xs)

    def with_small_anchor_supplemental_labels(
        lines: list[PlacedItem],
        *,
        connect_stubs: bool = False,
    ) -> list[PlacedItem]:
        if not hide_duplicate_labels or len(points) <= 1:
            return hide_blocked_visible_labels(lines)
        existing_label_points = {
            _coordinate(item.at[0], item.at[1])
            for item in lines
            if isinstance(item, PlacedLabel)
        }
        return hide_blocked_visible_labels(
            [
            *lines,
            *_small_anchor_supplemental_label_lines(
                label_name,
                points,
                key,
                page_width=page_width,
                dense_controller_refs=dense_controller_refs,
                small_anchor_refs=small_anchor_refs,
                connect_stubs=connect_stubs,
                existing_label_points=existing_label_points,
            ),
            ]
        )

    def hide_blocked_visible_labels(lines: list[PlacedItem]) -> list[PlacedItem]:
        if len(points) <= 1:
            return lines
        hidden_lines: list[PlacedItem] = []
        for item in lines:
            if not isinstance(item, PlacedLabel) or item.hidden:
                hidden_lines.append(item)
                continue
            if _label_rect_clears_blockers(
                item.name,
                item.at[0],
                item.at[1],
                justify=item.justify,
                blocked_rects=label_blocked_rects,
            ):
                hidden_lines.append(item)
                continue
            hidden_lines.append(
                PlacedLabel(
                    name=item.name,
                    at=item.at,
                    uuid=item.uuid,
                    justify=item.justify,
                    hidden=True,
                    nets=item.nets,
                )
            )
        return hidden_lines

    if (
        (allow_safe_local_rails or allow_medium_signal_rails or allow_contact_topology_nets)
        and not _is_groundish_net(name)
        and not _is_powerish_net(name)
        and 3 <= len(points) <= 8
        and _is_local_route_span(plain_points)
    ):
        same_row_lines = _same_row_contact_rail_lines(
            label_name,
            points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            anchor_label_xs=anchor_label_xs,
        )
        if same_row_lines is not None:
            return same_row_lines
        if len(points) >= 4:
            contact_tree_lines = _local_contact_tree_net_lines(
                label_name,
                points,
                key,
                page_width=page_width,
                obstacles=obstacles,
                occupied_segments=occupied_segments,
                terminal_coordinates=terminal_coordinates,
                blocked_rects=blocked_rects,
                label_blocked_rects=label_blocked_rects,
                anchor_label_xs=anchor_label_xs,
            )
            if contact_tree_lines is not None:
                return contact_tree_lines

    if allow_anchor_direct_nets and _is_safe_anchor_span(points):
        return with_small_anchor_supplemental_labels(
            _straight_direct_net_lines(
                label_name,
                plain_points[0],
                plain_points[1],
                key,
                terminal_coordinates=terminal_coordinates,
                label_away_from_x=label_away_from_x(plain_points[0]),
            )
        )
    if (
        allow_safe_direct_nets
        and not _is_groundish_net(name)
        and not _is_powerish_net(name)
        and len(points) == 2
        and _is_local_route_span(plain_points)
    ):
        safe_lines = _safe_direct_net_lines(
            label_name,
            plain_points[0],
            plain_points[1],
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            label_away_from_x=label_away_from_x(plain_points[0]),
        )
        if safe_lines is not None:
            return with_small_anchor_supplemental_labels(safe_lines, connect_stubs=True)
    if (
        allow_safe_local_rails
        and not _is_groundish_net(name)
        and not _is_powerish_net(name)
        and 3 <= len(points) <= 8
        and _is_local_route_span(plain_points)
    ):
        stacked_tap_lines = _stacked_tap_route_lines(
            label_name,
            plain_points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            label_away_from_x=label_away_from_x(plain_points[0]),
        )
        if stacked_tap_lines is not None:
            return with_small_anchor_supplemental_labels(
                stacked_tap_lines,
                connect_stubs=True,
            )
        contact_tree_lines = _local_contact_tree_net_lines(
            label_name,
            points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            anchor_label_xs=anchor_label_xs,
        )
        if contact_tree_lines is not None:
            return contact_tree_lines
        safe_lines = _safe_compact_local_net_lines(
            label_name,
            plain_points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            label_away_from_x=label_away_from_x(plain_points[0]),
        )
        if safe_lines is not None:
            return with_small_anchor_supplemental_labels(safe_lines)
    if (
        allow_anchor_passive_direct_nets
        and not _is_powerish_net(name)
        and _is_safe_anchor_passive_pair(points)
    ):
        safe_lines = _safe_anchor_passive_direct_net_lines(
            label_name,
            points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
        )
        if safe_lines is None:
            safe_lines = _safe_direct_net_lines(
                label_name,
                plain_points[0],
                plain_points[1],
                key,
                page_width=page_width,
                obstacles=obstacles,
                occupied_segments=occupied_segments,
                terminal_coordinates=terminal_coordinates,
                blocked_rects=blocked_rects,
                label_blocked_rects=label_blocked_rects,
                label_away_from_x=label_away_from_x(plain_points[0]),
            )
        if safe_lines is not None:
            return with_small_anchor_supplemental_labels(safe_lines, connect_stubs=True)
    if allow_direct_nets and len(points) == 2 and _is_compact_net(plain_points):
        safe_lines = _safe_direct_net_lines(
            label_name,
            plain_points[0],
            plain_points[1],
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            label_away_from_x=label_away_from_x(plain_points[0]),
        )
        if safe_lines is not None:
            return with_small_anchor_supplemental_labels(safe_lines, connect_stubs=True)
    if (
        allow_medium_signal_rails
        and not _is_powerish_net(name)
        and len(points) == 3
        and _is_compact_net(plain_points)
    ):
        stacked_tap_lines = _stacked_tap_route_lines(
            label_name,
            plain_points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            label_away_from_x=label_away_from_x(plain_points[0]),
        )
        if stacked_tap_lines is not None:
            return with_small_anchor_supplemental_labels(
                stacked_tap_lines,
                connect_stubs=True,
            )
        contact_tree_lines = _local_contact_tree_net_lines(
            label_name,
            points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            anchor_label_xs=anchor_label_xs,
        )
        if contact_tree_lines is not None:
            return contact_tree_lines
        safe_lines = _safe_compact_local_net_lines(
            label_name,
            plain_points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
        )
        if safe_lines is not None:
            return with_small_anchor_supplemental_labels(safe_lines)
        rail_lines = _rail_lines_if_clear_existing(
            label_name,
            points,
            key,
            page_width=page_width,
            occupied_segments=occupied_segments,
            hide_duplicate_labels=hide_duplicate_labels,
        )
        if rail_lines is not None:
            return with_small_anchor_supplemental_labels(rail_lines)
    if (
        allow_shared_rails
        and _should_emit_rail(name, plain_points)
        and _is_compact_net(plain_points)
    ):
        rail_lines = _rail_lines_if_clear_existing(
            label_name,
            points,
            key,
            page_width=page_width,
            occupied_segments=occupied_segments,
            hide_duplicate_labels=hide_duplicate_labels,
        )
        if rail_lines is not None:
            return with_small_anchor_supplemental_labels(rail_lines)
    if allow_shared_rails and len(points) >= 3 and _is_compact_net(plain_points):
        rail_lines = _rail_lines_if_clear_existing(
            label_name,
            points,
            key,
            page_width=page_width,
            occupied_segments=occupied_segments,
            hide_duplicate_labels=hide_duplicate_labels,
        )
        if rail_lines is not None:
            return with_small_anchor_supplemental_labels(rail_lines)
    if allow_contact_topology_nets and _is_contact_topology_candidate(
        name,
        points,
        regions=circuit_regions,
    ):
        safe_lines = _safe_contact_topology_net_lines(
            label_name,
            points,
            key,
            page_width=page_width,
            obstacles=obstacles,
            occupied_segments=occupied_segments,
            terminal_coordinates=terminal_coordinates,
            blocked_rects=blocked_rects,
            label_blocked_rects=label_blocked_rects,
            anchor_label_xs=anchor_label_xs,
        )
        if safe_lines is not None:
            return with_small_anchor_supplemental_labels(safe_lines, connect_stubs=True)

    lines: list[PlacedItem] = []
    for index, (endpoint_text, point) in enumerate(points):
        away_from_x = label_away_from_x(point)
        label_is_clear = True
        if len(points) == 1 and point.label_x < point.x:
            pass
        else:
            point = _point_with_clear_label(
                label_name,
                point,
                page_width=page_width,
                blocked_rects=label_blocked_rects,
                stub_blocked_rects=blocked_rects,
                obstacles=obstacles,
                occupied_segments=occupied_segments,
                away_from_x=away_from_x,
                allow_text_overlap_fallback=False,
            )
            label_is_clear = _label_clears_blockers(
                label_name,
                point,
                page_width=page_width,
                blocked_rects=label_blocked_rects,
                stub_blocked_rects=blocked_rects,
                obstacles=obstacles,
                occupied_segments=occupied_segments,
                away_from_x=away_from_x,
            )
        point_key = f"{key}:{endpoint_text}:{index}"
        lines.extend(
            _point_stub_lines(
                point,
                point_key + ":wire",
                start_terminal=endpoint_text,
            )
        )
        _record_point_stub_segment(point, occupied_segments)
        lines.extend(
            _point_label_lines(
                label_name,
                point,
                point_key + ":label",
                page_width=page_width,
                hidden=(
                    hide_duplicate_labels
                    and index > 0
                    and _endpoint_ref(endpoint_text) in dense_controller_refs
                )
                or not label_is_clear,
                away_from_x=label_away_from_x(point),
            )
        )
    return with_small_anchor_supplemental_labels(lines)


def route_sheet_nets(
    project: ResolvedProject,
    config: SheetNetRoutingConfig,
    net_points_by_name: dict[str, list[tuple[str, PinPoint]]],
    *,
    existing_items: Iterable[PlacedItem],
) -> SheetNetRoutingResult:
    sheet = project.source.sheets[config.sheet_path]
    allow_routed_local_nets = (
        bool(sheet.symbols) and not sheet.child_instances and len(sheet.symbols) <= 10
    )
    allow_shared_rails = allow_routed_local_nets and not config.uses_low_interface_local_layout
    allow_direct_nets = allow_routed_local_nets and not config.uses_low_interface_local_layout
    allow_safe_direct_nets = config.uses_low_interface_local_layout
    allow_safe_local_rails = config.uses_low_interface_local_layout
    allow_anchor_direct_nets = (
        bool(sheet.symbols)
        and not sheet.child_instances
        and not config.uses_low_interface_local_layout
    )
    allow_anchor_passive_direct_nets = False
    allow_medium_signal_rails = (
        bool(sheet.symbols)
        and not sheet.child_instances
        and len(sheet.symbols) <= 32
    )
    allow_contact_topology_nets = bool(sheet.symbols) and not sheet.child_instances
    hide_duplicate_labels = bool(sheet.symbols) and not sheet.child_instances
    dense_controller_refs = _dense_controller_refs(project, config.sheet_path)
    small_anchor_refs = _small_anchor_refs(project, config.sheet_path)
    net_obstacles = {
        coordinate
        for net_points in net_points_by_name.values()
        for coordinate in _pin_point_coordinates(
            [point for _endpoint_text, point in net_points]
        )
    } | set(config.blocked_coordinates)

    existing_problem = placed_items_layout_problem(existing_items)
    route_blockers = tuple(element.rect for element in existing_problem.elements)
    symbol_body_label_blockers = _symbol_body_label_blockers(project, existing_items)
    label_blockers = (*route_blockers, *symbol_body_label_blockers)
    occupied_net_segments = [
        segment.wire_segment() for segment in existing_problem.segments
    ]
    routed_items: list[PlacedItem] = []

    def append_routed_items(items: list[PlacedItem]) -> None:
        nonlocal label_blockers
        routed_items.extend(items)
        problem = placed_items_layout_problem(items)
        occupied_net_segments.extend(segment.wire_segment() for segment in problem.segments)
        label_blockers = (*label_blockers, *_visible_label_blockers(items))

    local_topology = build_local_topology(
        project,
        config.sheet_path,
        net_points_by_name,
    )
    circuit_regions = build_sheet_circuit_regions(project, config.sheet_path)
    reserved_endpoint_stubs = _endpoint_stub_segments_by_net(net_points_by_name)

    passive_rail_banks: list[PassiveRailBank] = []
    passive_rail_bank_candidates = (
        _passive_rail_banks(net_points_by_name)
        if bool(sheet.symbols) and not sheet.child_instances
        else []
    )
    passive_bank_candidate_net_names = {
        net_name
        for bank in passive_rail_bank_candidates
        for net_name in (bank.top_net, bank.bottom_net)
    }
    if bool(sheet.symbols) and not sheet.child_instances:
        for index, bank in enumerate(passive_rail_bank_candidates):
            bank_key = (
                f"{config.sheet_path}:passive-bank:{index}:{bank.top_net}:{bank.bottom_net}"
            )
            candidate_banks = [bank, *_passive_rail_bank_member_subbanks(bank)]
            for candidate_index, candidate_bank in enumerate(candidate_banks):
                bank_lines = _passive_rail_bank_lines(
                    candidate_bank,
                    f"{bank_key}:candidate:{candidate_index}",
                    page_width=config.page_width,
                    compact_local_labels=config.uses_low_interface_local_layout,
                    local_label_prefix=config.local_label_prefix,
                    obstacles=net_obstacles,
                    blocked_rects=route_blockers,
                    reserved_endpoint_stubs=reserved_endpoint_stubs,
                )
                if bank_lines is None:
                    continue
                passive_rail_banks.append(candidate_bank)
                append_routed_items(bank_lines)
                break

    skipped_passive_bank_endpoints = _passive_rail_bank_endpoint_texts(passive_rail_banks)
    passive_bank_net_names = {
        net_name
        for bank in passive_rail_banks
        for net_name in (bank.top_net, bank.bottom_net)
    }
    interface_label_points: dict[str, PinPoint] = {}
    interface_label_endpoint_texts: set[str] = set()
    for net_name, net_points in net_points_by_name.items():
        if net_name not in sheet.interface or not net_points:
            continue
        endpoint_text, net_point = net_points[0]
        interface_label_endpoint_texts.add(endpoint_text)
        if endpoint_text in skipped_passive_bank_endpoints:
            interface_label_points[net_name] = PinPoint(
                x=net_point.x,
                y=net_point.y,
                label_x=net_point.x,
                label_y=net_point.y,
            )
        else:
            interface_label_points[net_name] = _point_avoiding_obstacle_stub(
                net_point,
                net_obstacles,
            )

    topology_routed_endpoint_texts: set[str] = set()
    if bool(sheet.symbols) and not sheet.child_instances:
        for index, route in enumerate(
            sorted(
                local_topology.anchor_passive_nets,
                key=lambda item: (
                    item.anchor.point.y,
                    item.passive.point.y,
                    item.net_name,
                ),
            )
        ):
            if route.net_name in sheet.interface:
                continue
            if len(net_points_by_name.get(route.net_name, ())) != len(route.endpoints):
                continue
            if _is_powerish_net(route.net_name):
                continue
            if route.anchor.endpoint_text in skipped_passive_bank_endpoints:
                continue
            if route.passive.endpoint_text in skipped_passive_bank_endpoints:
                continue
            label_name = _display_net_label(
                route.net_name,
                compact_local_labels=config.uses_low_interface_local_layout,
                local_label_prefix=config.local_label_prefix,
            )
            route_lines = _safe_anchor_passive_direct_net_lines(
                label_name,
                list(route.endpoints),
                f"{config.sheet_path}:{route.net_name}:topology:{index}",
                page_width=config.page_width,
                obstacles=net_obstacles,
                occupied_segments=occupied_net_segments,
                blocked_rects=route_blockers,
                label_blocked_rects=label_blockers,
            )
            if route_lines is None:
                continue
            if hide_duplicate_labels:
                existing_label_points = {
                    _coordinate(item.at[0], item.at[1])
                    for item in route_lines
                    if isinstance(item, PlacedLabel)
                }
                route_lines = [
                    *route_lines,
                    *_small_anchor_supplemental_label_lines(
                        label_name,
                        list(route.endpoints),
                        f"{config.sheet_path}:{route.net_name}:topology:{index}",
                        page_width=config.page_width,
                        dense_controller_refs=dense_controller_refs,
                        small_anchor_refs=small_anchor_refs,
                        connect_stubs=True,
                        existing_label_points=existing_label_points,
                    ),
                ]
            topology_routed_endpoint_texts.update(
                {
                    route.anchor.endpoint_text,
                    route.passive.endpoint_text,
                }
            )
            append_routed_items(route_lines)

        for continuation_index, continuation_route in enumerate(
            sorted(
                local_topology.passive_continuation_nets,
                key=lambda item: (
                    item.source.point.y,
                    item.passive.point.y,
                    item.net_name,
                ),
            )
        ):
            if continuation_route.net_name in sheet.interface:
                continue
            if len(net_points_by_name.get(continuation_route.net_name, ())) != len(
                continuation_route.endpoints
            ):
                continue
            if _is_powerish_net(continuation_route.net_name) or _is_groundish_net(
                continuation_route.net_name
            ):
                continue
            if continuation_route.source.endpoint_text in skipped_passive_bank_endpoints:
                continue
            if continuation_route.passive.endpoint_text in skipped_passive_bank_endpoints:
                continue
            label_name = _display_net_label(
                continuation_route.net_name,
                compact_local_labels=config.uses_low_interface_local_layout,
                local_label_prefix=config.local_label_prefix,
            )
            route_lines = _safe_passive_continuation_net_lines(
                label_name,
                continuation_route.source.point,
                continuation_route.passive.point,
                f"{config.sheet_path}:{continuation_route.net_name}:passive-continuation:{continuation_index}",
                parent_anchor_x=continuation_route.anchor.point.x,
                page_width=config.page_width,
                obstacles=net_obstacles,
                occupied_segments=occupied_net_segments,
                terminal_coordinates=_terminal_coordinates(list(continuation_route.endpoints)),
                blocked_rects=route_blockers,
                label_blocked_rects=label_blockers,
            )
            if route_lines is None:
                continue
            topology_routed_endpoint_texts.update(
                {
                    continuation_route.source.endpoint_text,
                    continuation_route.passive.endpoint_text,
                }
            )
            append_routed_items(route_lines)

    for net_name, net_points in sorted(net_points_by_name.items(), key=_net_route_order):
        route_points = [
            net_point
            for net_point in net_points
            if net_point[0] not in skipped_passive_bank_endpoints
            and net_point[0] not in interface_label_endpoint_texts
            and net_point[0] not in topology_routed_endpoint_texts
        ]
        if not route_points:
            continue
        net_lines = _net_point_lines(
            net_name,
            route_points,
            f"{config.sheet_path}:{net_name}",
            allow_shared_rails=(
                allow_shared_rails and net_name not in passive_bank_candidate_net_names
            ),
            allow_direct_nets=allow_direct_nets,
            allow_safe_direct_nets=allow_safe_direct_nets,
            allow_safe_local_rails=allow_safe_local_rails,
            allow_anchor_direct_nets=allow_anchor_direct_nets,
            allow_anchor_passive_direct_nets=allow_anchor_passive_direct_nets,
            allow_medium_signal_rails=(
                allow_medium_signal_rails and net_name not in passive_bank_candidate_net_names
            ),
            allow_contact_topology_nets=allow_contact_topology_nets,
            compact_local_labels=config.uses_low_interface_local_layout,
            local_label_prefix=config.local_label_prefix,
            dense_controller_refs=dense_controller_refs,
            small_anchor_refs=small_anchor_refs,
            hide_duplicate_labels=hide_duplicate_labels,
            force_hidden_labels=(
                net_name in passive_bank_net_names and net_name not in sheet.interface
            ),
            page_width=config.page_width,
            obstacles=net_obstacles,
            occupied_segments=occupied_net_segments,
            blocked_rects=route_blockers,
            label_blocked_rects=label_blockers,
            circuit_regions=circuit_regions,
        )
        append_routed_items(net_lines)

    finalized_interface_label_points: dict[str, PinPoint] = {}
    for net_name, point in interface_label_points.items():
        adjusted = _point_with_clear_label(
            net_name,
            point,
            page_width=config.page_width,
            blocked_rects=label_blockers,
            stub_blocked_rects=route_blockers,
            obstacles=net_obstacles,
            occupied_segments=occupied_net_segments,
        )
        finalized_interface_label_points[net_name] = adjusted
        _record_point_stub_segment(adjusted, occupied_net_segments)
        label_blockers = (
            *label_blockers,
            text_rect(Point(adjusted.label_x, adjusted.label_y), net_name),
        )

    return SheetNetRoutingResult(
        items=tuple(routed_items),
        interface_label_points=finalized_interface_label_points,
    )
