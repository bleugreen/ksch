from dataclasses import dataclass
from math import ceil, floor
from typing import Any

from ksch.circuit_motifs import TapStackMotif, build_sheet_circuit_motifs
from ksch.circuit_regions import build_sheet_circuit_regions
from ksch.geometry import (
    Coordinate,
    PinPoint,
    Rect,
    WireSegment,
)
from ksch.geometry import (
    is_two_pin_symbol as _is_two_pin_symbol,
)
from ksch.geometry import (
    is_vertical_two_pin_symbol as _is_vertical_two_pin_symbol,
)
from ksch.geometry import (
    pin_label_keepout_rects as _pin_label_keepout_rects,
)
from ksch.geometry import (
    rects_intersect as _rects_intersect,
)
from ksch.geometry import (
    symbol_body_rect as _symbol_body_rect,
)
from ksch.geometry import (
    symbol_graphic_extent as _symbol_graphic_extent,
)
from ksch.geometry import (
    symbol_graphic_horizontal_extent as _symbol_graphic_horizontal_extent,
)
from ksch.geometry import (
    symbol_horizontal_extent as _symbol_horizontal_extent,
)
from ksch.geometry import (
    symbol_pin_coordinate as _symbol_pin_coordinate,
)
from ksch.geometry import (
    symbol_rect as _symbol_rect,
)
from ksch.geometry import (
    symbol_vertical_extent as _symbol_vertical_extent,
)
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.layout import (
    ContactLink,
    LayoutNode,
    Point,
    layout_energy,
    solve_contact_layout,
)
from ksch.layout import (
    Rect as LayoutRect,
)
from ksch.layout_problem import LayoutElement, LayoutProblem, text_rect
from ksch.model.endpoint import EndpointKind
from ksch.resolver import ResolvedProject
from ksch.routing import (
    coordinate as _coordinate,
)
from ksch.routing import (
    pin_point_obstacle_coordinates as _pin_point_obstacle_coordinates,
)
from ksch.routing import (
    pin_stub_segments as _pin_stub_segments,
)
from ksch.routing import (
    point_on_segment as _point_on_segment,
)
from ksch.routing import (
    segments_touch as _segments_touch,
)

WIRE_STUB = 10.16
PIN_LABEL_STUB = 5.08
SYMBOL_MARGIN_X = 38.1
SYMBOL_MARGIN_Y = 38.1
SCHEMATIC_GRID = 2.54
DENSE_CONTROLLER_PIN_COUNT = 12
PAPER_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
}
LOCAL_SIGNAL_SUFFIXES = {
    "BOOT",
    "BUCK_EN",
    "COMP",
    "COMP_RC",
    "FB",
    "REV_GATE",
    "RT_CLK",
    "SW",
    "VBAT_FUSED",
}


@dataclass(frozen=True)
class _PassiveContinuationPlacement:
    net_name: str
    source_ref: str
    source_pin: SymbolPin
    target_ref: str
    target_pin: SymbolPin


def _symbol_prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _symbol_ref_key(ref: str) -> tuple[str, bool, int, str]:
    prefix = _symbol_prefix(ref)
    index = len(prefix)
    digits = []
    while index < len(ref) and ref[index].isdigit():
        digits.append(ref[index])
        index += 1
    return (prefix, not digits, int("".join(digits)) if digits else 0, ref[index:])


def _symbol_lane_index(ref: str) -> int:
    prefix = _symbol_prefix(ref)
    if prefix in {"J", "P", "CN"}:
        return 0
    if prefix in {"F"}:
        return 1
    if prefix in {"Q"}:
        return 2
    if prefix in {"U", "IC"} or ref.startswith("Module"):
        return 3
    if prefix in {"L", "FB"}:
        return 4
    if prefix in {"R", "C", "D", "TP", "Y"}:
        return 5
    return 5


def _symbol_lane(ref: str, min_x: float = 50.8, max_x: float = 330.2) -> tuple[float, int]:
    lane = _symbol_lane_index(ref)
    step = (max_x - min_x) / 5
    return (_snap_grid(min_x + lane * step), lane)


def _is_anchor_ref(ref: str) -> bool:
    prefix = _symbol_prefix(ref)
    return prefix in {"J", "P", "CN", "U", "IC", "Q", "F", "L", "FB"} or ref.startswith(
        "Module"
    )


def _anchor_score(ref: str) -> tuple[int, str]:
    prefix = _symbol_prefix(ref)
    if ref.startswith("Module") or prefix in {"U", "IC"}:
        return (0, ref)
    if prefix in {"Q"}:
        return (1, ref)
    if prefix in {"L", "FB"}:
        return (2, ref)
    if prefix in {"F"}:
        return (3, ref)
    if prefix in {"J", "P", "CN"}:
        return (4, ref)
    return (5, ref)


def _unit_symbol_info(symbol_info: SymbolInfo | None, unit: int) -> SymbolInfo | None:
    if symbol_info is None:
        return None
    return SymbolInfo(
        lib_id=symbol_info.lib_id,
        name=symbol_info.name,
        footprint=symbol_info.footprint,
        pins=[pin for pin in symbol_info.pins if pin.unit in {0, unit}],
        definition=symbol_info.definition,
    )


def _symbol_units(symbol_decl_units: list[int] | None, symbol_info: SymbolInfo | None) -> list[int]:
    if symbol_decl_units:
        return sorted(set(symbol_decl_units))
    if symbol_info is None:
        return [1]
    units = {pin.unit for pin in symbol_info.pins if pin.unit > 0}
    return [min(units)] if units else [1]


def _paper_dimensions(project: ResolvedProject, sheet_path: str) -> tuple[float, float]:
    return PAPER_SIZES.get(_paper_size(project, sheet_path), PAPER_SIZES["A4"])


def _symbol_layout_bounds(
    project: ResolvedProject,
    sheet_path: str,
) -> tuple[float, float, float, float]:
    width, height = _paper_dimensions(project, sheet_path)
    return (
        SYMBOL_MARGIN_X,
        width - SYMBOL_MARGIN_X,
        SYMBOL_MARGIN_Y,
        height - SYMBOL_MARGIN_Y,
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _snap_grid(value: float) -> float:
    return round(round(value / SCHEMATIC_GRID) * SCHEMATIC_GRID, 2)


def _snap_half_grid(value: float) -> float:
    half_grid = SCHEMATIC_GRID / 2
    return round(round(value / half_grid) * half_grid, 2)


def _is_connection_grid_aligned(value: float) -> bool:
    return abs(value / SCHEMATIC_GRID - round(value / SCHEMATIC_GRID)) < 0.001


def _snap_passive_pin_aligned_y(anchor_y: float, pin: SymbolPin) -> float:
    if pin.at is None:
        return _snap_grid(anchor_y)
    route_clearance = 5.08
    if pin.at[1] < 0:
        value = anchor_y + route_clearance + pin.at[1]
        scaled = value / SCHEMATIC_GRID
        return round(ceil(scaled) * SCHEMATIC_GRID, 2)
    if pin.at[1] > 0:
        value = anchor_y - route_clearance + pin.at[1]
        scaled = value / SCHEMATIC_GRID
        return round(floor(scaled) * SCHEMATIC_GRID, 2)
    return _snap_grid(anchor_y)


def _bounded_shift(desired: float, low_room: float, high_room: float) -> float:
    if low_room <= high_room:
        return _snap_grid(_clamp(desired, low_room, high_room))
    return 0.0


def _is_low_interface_local_circuit(project: ResolvedProject, sheet_path: str) -> bool:
    sheet = project.source.sheets[sheet_path]
    if (
        not sheet.symbols
        or sheet.child_instances
        or not sheet.interface
        or len(sheet.interface) > 4
        or len(sheet.symbols) > 32
    ):
        return False

    prefixes = {_symbol_prefix(ref) for ref in sheet.symbols}
    has_controller = bool(prefixes & {"U", "IC"}) or any(
        ref.startswith("Module") for ref in sheet.symbols
    )
    has_flow_part = bool(prefixes & {"J", "P", "CN", "F", "Q", "L", "FB"})
    return has_controller and has_flow_part


def _local_signal_suffix(name: str) -> str | None:
    upper = name.upper()
    for suffix in sorted(LOCAL_SIGNAL_SUFFIXES, key=len, reverse=True):
        if upper == suffix or upper.endswith(f"_{suffix}"):
            return suffix
    return None


def _place_ref_units(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    x: float,
    y: float,
    positions: dict[tuple[str, int], Point],
) -> None:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols[ref]
    symbol_info = project.symbol_library.get(symbol_decl.lib)
    for index, unit in enumerate(_symbol_units(symbol_decl.units, symbol_info)):
        positions[(ref, unit)] = Point(x=_snap_grid(x), y=_snap_grid(y + index * 27.94))


def _local_circuit_anchor_stage(ref: str) -> int | None:
    prefix = _symbol_prefix(ref)
    if prefix in {"J", "P", "CN"}:
        return 0
    if prefix == "F":
        return 1
    if prefix == "Q":
        return 2
    if ref.startswith("Module") or prefix in {"U", "IC"}:
        return 3
    if prefix in {"L", "FB"}:
        return 4
    return None


def _is_controller_anchor_ref(ref: str) -> bool:
    return ref.startswith("Module") or _symbol_prefix(ref) in {"U", "IC"}


def _is_fixed_layout_ref(ref: str) -> bool:
    prefix = _symbol_prefix(ref)
    return ref.startswith("Module") or prefix in {"J", "P", "CN", "U", "IC", "Q"}


def _power_island_anchor_refs(project: ResolvedProject, sheet_path: str) -> set[str]:
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return set()

    sheet = project.source.sheets[sheet_path]
    anchors: set[str] = set()
    for net_name, endpoints in resolved_sheet.nets.items():
        suffix = _local_signal_suffix(net_name)
        if suffix not in {"BOOT", "SW", "FB", "COMP", "COMP_RC"}:
            continue
        for endpoint in endpoints:
            if (
                endpoint.kind is EndpointKind.SYMBOL_PIN
                and endpoint.ref in sheet.symbols
                and endpoint.ref is not None
                and _is_controller_anchor_ref(endpoint.ref)
            ):
                anchors.add(endpoint.ref)
    return anchors


def _power_island_path_refs(project: ResolvedProject, sheet_path: str) -> set[str]:
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return set()

    sheet = project.source.sheets[sheet_path]
    power_anchors = _power_island_anchor_refs(project, sheet_path)
    if not power_anchors:
        return set()

    path_refs = set(power_anchors)
    for net_name, endpoints in resolved_sheet.nets.items():
        net_refs = {
            endpoint.ref
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN
            and endpoint.ref is not None
            and endpoint.ref in sheet.symbols
        }
        if not (net_refs & power_anchors):
            continue
        if _is_groundish_net(net_name):
            continue
        path_refs.update(
            ref
            for ref in net_refs
            if _symbol_prefix(ref) in {"L", "FB", "F"} or ref in power_anchors
        )
    return path_refs


def _interface_path_refs(project: ResolvedProject, sheet_path: str) -> set[str]:
    graph, connector_boundary_refs, controller_boundary_refs = _interface_path_graph_info(
        project,
        sheet_path,
    )
    path_refs: set[str] = set()
    seen: set[str] = set()
    for start in sorted(graph):
        if start in seen:
            continue
        component: set[str] = set()
        frontier = [start]
        while frontier:
            ref = frontier.pop()
            if ref in component:
                continue
            component.add(ref)
            frontier.extend(sorted(graph[ref] - component))
        seen.update(component)

        has_connector = bool(component & connector_boundary_refs)
        has_controller = bool(component & controller_boundary_refs)
        if not (has_connector and has_controller):
            continue
        path_refs.update(component)
    return path_refs


def _interface_path_flow_distances(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[str, int]:
    graph, connector_boundary_refs, _controller_boundary_refs = _interface_path_graph_info(
        project,
        sheet_path,
    )
    path_refs = _interface_path_refs(project, sheet_path)
    starts = sorted(path_refs & connector_boundary_refs, key=_symbol_ref_key)
    if not starts:
        return {}

    distances: dict[str, int] = {}
    frontier = [(ref, 0) for ref in starts]
    while frontier:
        ref, distance = frontier.pop(0)
        if ref in distances and distances[ref] <= distance:
            continue
        distances[ref] = distance
        for neighbor in sorted(graph.get(ref, set()) & path_refs, key=_symbol_ref_key):
            frontier.append((neighbor, distance + 1))
    return distances


def _interface_path_graph_info(
    project: ResolvedProject,
    sheet_path: str,
) -> tuple[dict[str, set[str]], set[str], set[str]]:
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return ({}, set(), set())

    sheet = project.source.sheets[sheet_path]
    movable_refs = {ref for ref in sheet.symbols if not _is_fixed_layout_ref(ref)}
    graph: dict[str, set[str]] = {ref: set() for ref in movable_refs}
    connector_boundary_refs: set[str] = set()
    controller_boundary_refs: set[str] = set()
    for net_name, endpoints in resolved_sheet.nets.items():
        if _is_groundish_net(net_name):
            continue
        net_refs = sorted(
            {
                endpoint.ref
                for endpoint in endpoints
                if endpoint.kind is EndpointKind.SYMBOL_PIN
                and endpoint.ref is not None
                and endpoint.ref in sheet.symbols
            }
        )
        net_movable_refs = [ref for ref in net_refs if ref in movable_refs]
        prefixes = {_symbol_prefix(ref) for ref in net_refs}
        has_connector = bool(prefixes & {"J", "P", "CN"})
        has_controller = any(_is_controller_anchor_ref(ref) for ref in net_refs)
        if has_connector:
            connector_boundary_refs.update(net_movable_refs)
        if net_name in sheet.interface and not has_controller:
            connector_boundary_refs.update(net_movable_refs)
        if has_controller:
            controller_boundary_refs.update(net_movable_refs)
        if _is_powerish_net(net_name):
            continue
        for first_index, first in enumerate(net_movable_refs):
            for second in net_movable_refs[first_index + 1 :]:
                graph[first].add(second)
                graph[second].add(first)
    return (graph, connector_boundary_refs, controller_boundary_refs)


def _interface_path_compaction_refs(project: ResolvedProject, sheet_path: str) -> set[str]:
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return set()

    sheet = project.source.sheets[sheet_path]
    path_refs = _interface_path_refs(project, sheet_path)
    if not path_refs:
        return set()

    compact_refs = set(path_refs)
    for net_name, endpoints in resolved_sheet.nets.items():
        if _is_groundish_net(net_name) or _is_powerish_net(net_name):
            continue
        net_refs = {
            endpoint.ref
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN
            and endpoint.ref is not None
            and endpoint.ref in sheet.symbols
        }
        if net_refs & path_refs:
            compact_refs.update(net_refs)
    return compact_refs


def _functional_anchor_lane_overrides(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[str, tuple[float, int]]:
    min_x, max_x, _min_y, _max_y = _symbol_layout_bounds(project, sheet_path)
    usable_width = max_x - min_x
    power_anchor_x = _snap_grid(min_x + usable_width * 0.18)
    power_path_x = _snap_grid(power_anchor_x + 50.8)
    interface_stage_x = {
        1: _snap_grid(min_x + usable_width * 0.18),
        2: _snap_grid(min_x + usable_width * 0.32),
        3: _snap_grid(min_x + usable_width * 0.42),
    }
    power_anchors = _power_island_anchor_refs(project, sheet_path)
    power_path_refs = _power_island_path_refs(project, sheet_path) - power_anchors
    overrides: dict[str, tuple[float, int]] = {}
    for ref in _interface_path_refs(project, sheet_path):
        if not _is_anchor_ref(ref):
            continue
        prefix = _symbol_prefix(ref)
        stage = 1 if prefix == "F" else 2 if prefix in {"L", "FB"} else 3
        overrides[ref] = (interface_stage_x[stage], stage)
    overrides.update({ref: (power_anchor_x, 2) for ref in power_anchors})
    overrides.update({ref: (power_path_x, 3) for ref in power_path_refs})
    return overrides


def _uses_functional_island_layout(project: ResolvedProject, sheet_path: str) -> bool:
    sheet = project.source.sheets[sheet_path]
    if len(sheet.symbols) <= 32:
        return True
    if len(sheet.symbols) > 72:
        return False
    anchor_refs = {ref for ref in sheet.symbols if _is_anchor_ref(ref)}
    return (
        bool(_power_island_anchor_refs(project, sheet_path))
        or bool(_interface_path_refs(project, sheet_path))
    ) and len(anchor_refs) <= 20


def _ref_net_names(resolved_sheet: Any, sheet_symbols: set[str]) -> dict[str, set[str]]:
    ref_nets: dict[str, set[str]] = {ref: set() for ref in sheet_symbols}
    for net_name, endpoints in resolved_sheet.nets.items():
        for endpoint in endpoints:
            if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref in ref_nets:
                ref_nets[endpoint.ref or ""].add(net_name)
    return ref_nets


def _local_circuit_category(sheet_interface: set[str], nets: set[str]) -> str:
    suffixes = {_local_signal_suffix(net) for net in nets}
    if "BOOT" in suffixes:
        return "boot"
    if "SW" in suffixes:
        return "switch"
    if "FB" in suffixes:
        return "feedback"
    if suffixes & {"COMP", "COMP_RC"}:
        return "comp"
    if "RT_CLK" in suffixes:
        return "timing"
    if "BUCK_EN" in suffixes:
        return "enable"
    if "REV_GATE" in suffixes:
        return "gate"

    upper_nets = {net.upper() for net in nets}
    non_ground_interface_nets = {
        net.upper()
        for net in nets
        if net in sheet_interface and "GND" not in net.upper()
    }
    if any("OUT" in net or "5V" in net or "VOUT" in net for net in non_ground_interface_nets):
        return "output"
    if any("VIN" in net or "VBAT" in net for net in upper_nets):
        return "input"
    return "fallback"


def _controller_tap_stack_refs(
    project: ResolvedProject,
    sheet_path: str,
    *,
    assigned_to_anchor: dict[str, str],
    assigned_anchor_pin: dict[str, SymbolPin],
    assigned_ref_pin: dict[str, SymbolPin],
) -> set[str]:
    _ = (assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin)
    return build_sheet_circuit_motifs(project, sheet_path).tap_stack_refs()


def _clamped_slot(
    x: float,
    y: float,
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
) -> Point:
    return Point(x=_snap_grid(_clamp(x, min_x, max_x)), y=_snap_grid(_clamp(y, min_y, max_y)))


def _clamped_symbol_slot(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    unit: int,
    x: float,
    y: float,
) -> Point:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols[ref]
    symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
    body_left, body_top, body_right, body_bottom = _symbol_body_rect(symbol_info, 0.0, 0.0)
    local_min_x = body_left
    local_max_x = body_right
    local_min_y = -body_bottom
    local_max_y = -body_top
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    current_left, current_top, current_right, current_bottom = _symbol_body_rect(
        symbol_info,
        x,
        y,
    )
    tolerance = SCHEMATIC_GRID
    if (
        current_left >= min_x - tolerance
        and current_right <= max_x + tolerance
        and current_top >= min_y - tolerance
        and current_bottom <= max_y + tolerance
    ):
        return Point(x=_snap_grid(x), y=_snap_grid(y))

    allowed_min_x = min_x - local_min_x
    allowed_max_x = max_x - local_max_x
    if allowed_min_x <= allowed_max_x:
        clamped_x = _clamp(x, allowed_min_x, allowed_max_x)
    else:
        clamped_x = (min_x + max_x - local_min_x - local_max_x) / 2

    allowed_min_y = min_y + local_max_y
    allowed_max_y = max_y + local_min_y
    if allowed_min_y <= allowed_max_y:
        clamped_y = _clamp(y, allowed_min_y, allowed_max_y)
    else:
        clamped_y = (min_y + max_y + local_min_y + local_max_y) / 2

    return Point(x=_snap_grid(clamped_x), y=_snap_grid(clamped_y))


def _clamped_symbol_geometry_position(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    unit: int,
    x: float,
    y: float,
) -> Point:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols[ref]
    symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
    body_left, body_top, body_right, body_bottom = _symbol_body_rect(symbol_info, 0.0, 0.0)
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)

    allowed_min_x = min_x - body_left
    allowed_max_x = max_x - body_right
    clamped_x = (
        _clamp(x, allowed_min_x, allowed_max_x)
        if allowed_min_x <= allowed_max_x
        else (min_x + max_x - body_left - body_right) / 2
    )

    local_min_y = -body_bottom
    local_max_y = -body_top
    allowed_min_y = min_y + local_max_y
    allowed_max_y = max_y + local_min_y
    clamped_y = (
        _clamp(y, allowed_min_y, allowed_max_y)
        if allowed_min_y <= allowed_max_y
        else (min_y + max_y + local_min_y + local_max_y) / 2
    )

    return Point(x=round(clamped_x, 2), y=round(clamped_y, 2))


def _symbol_body_rect_at(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    unit: int,
    position: Point,
) -> Rect:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols[ref]
    symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
    return _symbol_body_rect(symbol_info, position.x, position.y)


def _layout_rect_to_geometry_rect(rect: LayoutRect) -> Rect:
    return (rect.left, rect.top, rect.right, rect.bottom)


def _symbol_readability_rects_at(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    unit: int,
    position: Point,
) -> list[Rect]:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols[ref]
    symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
    rects = [_rect_with_margin(_symbol_body_rect(symbol_info, position.x, position.y), 2.54)]
    local_min_x, _local_max_x = _symbol_horizontal_extent(symbol_info)
    local_min_y, local_max_y = _symbol_vertical_extent(symbol_info)
    value = symbol_decl.value or ref

    if _is_vertical_two_pin_symbol(symbol_info):
        if _symbol_prefix(ref) in {"L", "FB"}:
            text_x = _snap_grid(position.x + local_min_x)
            field_points = (
                (
                    ref,
                    Point(text_x, _snap_grid(position.y - local_max_y - SCHEMATIC_GRID * 2)),
                    "left",
                ),
                (
                    value,
                    Point(
                        text_x,
                        _snap_grid(position.y - local_max_y - SCHEMATIC_GRID),
                    ),
                    "left",
                ),
            )
        else:
            graphic_extent = _symbol_graphic_horizontal_extent(symbol_info)
            body_min_x = graphic_extent[0] if graphic_extent is not None else local_min_x
            text_x = _snap_grid(position.x + body_min_x - SCHEMATIC_GRID * 2)
            field_points = (
                (ref, Point(text_x, _snap_half_grid(position.y - SCHEMATIC_GRID / 4)), "right"),
                (value, Point(text_x, _snap_half_grid(position.y + SCHEMATIC_GRID / 4)), "right"),
            )
    else:
        text_x = _snap_grid(position.x + local_min_x)
        field_points = (
            (ref, Point(text_x, _snap_grid(position.y - local_max_y - SCHEMATIC_GRID * 2)), "left"),
            (
                value,
                Point(text_x, _snap_grid(position.y - local_min_y + SCHEMATIC_GRID * 2)),
                "left",
            ),
        )

    for text, anchor, justify in field_points:
        field_rect = text_rect(anchor, text, justify=justify)
        rects.append(_rect_with_margin(_layout_rect_to_geometry_rect(field_rect), 1.27))
    return rects


def _rects_overlap_any(first_rects: list[Rect], second_rects: list[Rect]) -> bool:
    return any(
        _rects_intersect(first, second)
        for first in first_rects
        for second in second_rects
    )


def _rect_with_margin(rect: Rect, margin: float) -> Rect:
    left, top, right, bottom = rect
    return (left - margin, top - margin, right + margin, bottom + margin)


def _slot_positions(
    base_x: float,
    base_y: float,
    *,
    count: int,
    dx: float = 0.0,
    dy: float = 20.32,
) -> list[tuple[float, float]]:
    return [(base_x + (index // 4) * dx, base_y + (index % 4) * dy) for index in range(count)]


def _row_positions(
    base_x: float,
    base_y: float,
    *,
    count: int,
    dx: float = 25.4,
    dy: float = 22.86,
    per_row: int = 4,
) -> list[tuple[float, float]]:
    return [
        (base_x + (index % per_row) * dx, base_y + (index // per_row) * dy)
        for index in range(count)
    ]


def _layout_low_interface_local_circuit(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[tuple[str, int], Point] | None:
    if not _is_low_interface_local_circuit(project, sheet_path):
        return None

    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return None

    sheet = project.source.sheets[sheet_path]
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    usable_width = max_x - min_x
    flow_left = min_x + usable_width * 0.08
    flow_right = max_x - usable_width * 0.08
    stage_step = (flow_right - flow_left) / 4
    stage_x = {stage: _snap_grid(flow_left + stage * stage_step) for stage in range(5)}
    flow_y = _snap_grid(min_y + (max_y - min_y) * 0.52)

    positions: dict[tuple[str, int], Point] = {}
    occupied_slots: set[Coordinate] = set()

    def place_unique_ref(ref: str, x: float, y: float) -> None:
        point = _clamped_slot(x, y, min_x, max_x, min_y, max_y)
        attempts = 0
        while _coordinate(point.x, point.y) in occupied_slots and attempts < 80:
            attempts += 1
            next_y = point.y + 7.62
            next_x = point.x
            if next_y > max_y:
                next_y = min_y
                next_x += 12.7
            point = _clamped_slot(next_x, next_y, min_x, max_x, min_y, max_y)
        occupied_slots.add(_coordinate(point.x, point.y))
        _place_ref_units(project, sheet_path, ref, point.x, point.y, positions)

    stage_refs: dict[int, list[str]] = {stage: [] for stage in range(5)}
    for ref in sorted(sheet.symbols):
        stage = _local_circuit_anchor_stage(ref)
        if stage is not None:
            stage_refs[stage].append(ref)

    for stage, refs in stage_refs.items():
        for index, ref in enumerate(refs):
            centered = index - (len(refs) - 1) / 2
            y = flow_y + centered * 27.94
            place_unique_ref(ref, stage_x[stage], y)

    assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )

    def anchor_pin_point(ref: str, anchor: str) -> PinPoint | None:
        if anchor.startswith("Module"):
            return None
        anchor_pin = assigned_anchor_pin.get(ref)
        if anchor_pin is None:
            return None
        anchor_symbol_decl = sheet.symbols.get(anchor)
        if anchor_symbol_decl is None:
            return None
        anchor_symbol_info = _unit_symbol_info(
            project.symbol_library.get(anchor_symbol_decl.lib),
            anchor_pin.unit,
        )
        anchor_position = positions.get((anchor, anchor_pin.unit)) or positions.get((anchor, 1))
        if anchor_symbol_info is None or anchor_position is None:
            return None
        return _symbol_pin_point(
            anchor_position.x,
            anchor_position.y,
            anchor_pin,
            symbol_info=anchor_symbol_info,
        )

    def occupied_readability_rects() -> list[Rect]:
        return [
            rect
            for (ref, unit), position in sorted(positions.items())
            for rect in _symbol_readability_rects_at(project, sheet_path, ref, unit, position)
        ]

    def support_candidate_position(
        ref: str,
        unit: int,
        ref_pin: SymbolPin,
        anchor_point: PinPoint,
        x_offset: float,
        y_offset: float,
    ) -> Point:
        direction = -1 if anchor_point.label_x < anchor_point.x else 1
        pin_local_x = ref_pin.at[0] if ref_pin.at else 0.0
        pin_local_y = ref_pin.at[1] if ref_pin.at else 0.0
        pin_x = anchor_point.label_x + direction * x_offset
        pin_y = anchor_point.y + y_offset
        return _clamped_symbol_geometry_position(
            project,
            sheet_path,
            ref,
            unit,
            pin_x - pin_local_x,
            pin_y + pin_local_y,
        )

    def place_anchor_support_ref(ref: str, anchor: str) -> bool:
        if (ref, 1) in positions or anchor.startswith("Module"):
            return False
        ref_pin = assigned_ref_pin.get(ref)
        if ref_pin is None or ref_pin.at is None:
            return False
        anchor_pin = assigned_anchor_pin.get(ref)
        if anchor_pin is None or _pin_side(anchor_pin) not in {"left", "right"}:
            return False
        symbol_decl = sheet.symbols.get(ref)
        if symbol_decl is None:
            return False
        symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), ref_pin.unit)
        if not _is_vertical_two_pin_symbol(symbol_info):
            return False
        anchor_point = anchor_pin_point(ref, anchor)
        if anchor_point is None:
            return False
        if not _is_connection_grid_aligned(anchor_point.y):
            return False

        x_offsets = (17.78, 22.86, 30.48, 38.1, 48.26, 60.96, 73.66)
        y_offsets = (
            0.0,
            -12.7,
            12.7,
            -22.86,
            22.86,
            -33.02,
            33.02,
            -43.18,
            43.18,
            -55.88,
            55.88,
        )
        for x_offset in x_offsets:
            for y_offset in y_offsets:
                candidate = support_candidate_position(
                    ref,
                    ref_pin.unit,
                    ref_pin,
                    anchor_point,
                    x_offset,
                    y_offset,
                )
                candidate_rects = _symbol_readability_rects_at(
                    project,
                    sheet_path,
                    ref,
                    ref_pin.unit,
                    candidate,
                )
                if _rects_overlap_any(candidate_rects, occupied_readability_rects()):
                    continue
                positions[(ref, ref_pin.unit)] = candidate
                occupied_slots.add(_coordinate(candidate.x, candidate.y))
                return True
        return False

    for anchor, refs in sorted(
        {
            anchor: sorted(
                ref for ref, candidate_anchor in assigned_to_anchor.items()
                if candidate_anchor == anchor
            )
            for anchor in set(assigned_to_anchor.values())
        }.items()
    ):
        for ref in refs:
            place_anchor_support_ref(ref, anchor)

    positions = _place_passive_continuation_refs(
        project,
        sheet_path,
        positions,
        assigned_to_anchor=assigned_to_anchor,
    )

    ref_nets = _ref_net_names(resolved_sheet, set(sheet.symbols))
    category_refs: dict[str, list[str]] = {}
    for ref in sorted(sheet.symbols):
        if (ref, 1) in positions:
            continue
        category = _local_circuit_category(set(sheet.interface), ref_nets.get(ref, set()))
        category_refs.setdefault(category, []).append(ref)

    slot_templates: dict[str, list[tuple[float, float]]] = {
        "input": _slot_positions(stage_x[2] - 17.78, flow_y - 45.72, count=8, dx=-25.4),
        "gate": _slot_positions(stage_x[2] - 30.48, flow_y + 27.94, count=6, dx=-25.4),
        "enable": _slot_positions(stage_x[3] - 43.18, flow_y - 25.4, count=4),
        "comp": _slot_positions(stage_x[3] - 43.18, flow_y + 12.7, count=6),
        "timing": _slot_positions(stage_x[3] - 17.78, flow_y + 63.5, count=4),
        "boot": _slot_positions(stage_x[3] + 43.18, flow_y - 45.72, count=4),
        "switch": _slot_positions(stage_x[3] + 43.18, flow_y - 12.7, count=4),
        "feedback": _slot_positions(stage_x[3] + 43.18, flow_y + 27.94, count=6),
        "output": _row_positions(stage_x[4] - 25.4, flow_y + 76.2, count=8),
        "fallback": _slot_positions(stage_x[0], flow_y + 63.5, count=16, dx=27.94),
    }

    for category, refs in sorted(category_refs.items()):
        slots = slot_templates.get(category) or slot_templates["fallback"]
        for index, ref in enumerate(refs):
            slot_x, slot_y = slots[index % len(slots)]
            if index >= len(slots):
                slot_y += (index // len(slots)) * 22.86
            place_unique_ref(ref, slot_x, slot_y)

    resolved_positions = _resolve_symbol_body_overlaps(project, sheet_path, positions)
    return _place_passive_continuation_refs(
        project,
        sheet_path,
        resolved_positions,
        assigned_to_anchor=assigned_to_anchor,
    )


def _center_symbol_positions(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
) -> dict[tuple[str, int], Point]:
    if not positions:
        return positions

    sheet = project.source.sheets[sheet_path]
    if sheet.interface:
        return positions

    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    left = float("inf")
    right = float("-inf")
    top = float("inf")
    bottom = float("-inf")
    for (ref, unit), position in positions.items():
        symbol_decl = sheet.symbols[ref]
        symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
        local_min_x, local_max_x = _symbol_horizontal_extent(symbol_info)
        local_min_y, local_max_y = _symbol_vertical_extent(symbol_info)
        left = min(left, position.x + local_min_x)
        right = max(right, position.x + local_max_x)
        top = min(top, position.y - local_max_y)
        bottom = max(bottom, position.y - local_min_y)

    usable_center_y = (min_y + max_y) / 2
    block_center_y = (top + bottom) / 2
    dx = 0.0
    dy = _bounded_shift(usable_center_y - block_center_y, min_y - top, max_y - bottom)
    if dx == 0 and dy == 0:
        return positions

    shifted: dict[tuple[str, int], Point] = {}
    for key, position in positions.items():
        shifted[key] = Point(x=_snap_grid(position.x + dx), y=_snap_grid(position.y + dy))
    return shifted


def _symbol_anchor_assignments(
    project: ResolvedProject,
    sheet_path: str,
) -> tuple[dict[str, str], dict[str, SymbolPin], dict[str, SymbolPin]]:
    sheet = project.source.sheets[sheet_path]
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return ({}, {}, {})

    anchor_refs = {ref for ref in sheet.symbols if _is_anchor_ref(ref)}
    rail_bank_refs = _passive_rail_bank_member_refs(project, sheet_path)
    passive_continuation_target_refs = _passive_continuation_target_refs(
        project,
        sheet_path,
    )
    assignment_candidates: dict[
        str,
        tuple[tuple[int, int, int, str], str, SymbolPin | None, SymbolPin | None],
    ] = {}
    for net_name, endpoints in resolved_sheet.nets.items():
        net_refs = {
            endpoint.ref
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref in sheet.symbols
        }
        anchors = sorted(
            (ref for ref in net_refs if ref in anchor_refs),
            key=_anchor_score,
        )
        if not anchors:
            continue
        anchor = anchors[0]
        anchor_symbol_decl = sheet.symbols[anchor]
        anchor_symbol_info = project.symbol_library.get(anchor_symbol_decl.lib)
        anchor_pin = next(
            (
                pin
                for endpoint in endpoints
                if endpoint.ref == anchor and anchor_symbol_info is not None
                for pin in [_pin_by_number(anchor_symbol_info, endpoint.pin_number or "")]
                if pin is not None
            ),
            None,
        )
        for ref in sorted(net_refs - anchor_refs):
            if ref in rail_bank_refs or ref in passive_continuation_target_refs:
                continue
            ref_symbol_decl = sheet.symbols[ref]
            ref_symbol_info = project.symbol_library.get(ref_symbol_decl.lib)
            ref_pin = next(
                (
                    pin
                    for endpoint in endpoints
                    if endpoint.ref == ref and ref_symbol_info is not None
                    for pin in [_pin_by_number(ref_symbol_info, endpoint.pin_number or "")]
                    if pin is not None
                ),
                None,
            )
            priority = (
                _anchor_assignment_net_rank(sheet, net_name),
                _anchor_score(anchor)[0],
                len(net_refs),
                anchor,
            )
            current = assignment_candidates.get(ref)
            if current is None or priority < current[0]:
                assignment_candidates[ref] = (priority, anchor, anchor_pin, ref_pin)

    assigned_to_anchor = {ref: candidate[1] for ref, candidate in assignment_candidates.items()}
    assigned_anchor_pin = {
        ref: candidate[2]
        for ref, candidate in assignment_candidates.items()
        if candidate[2] is not None
    }
    assigned_ref_pin = {
        ref: candidate[3]
        for ref, candidate in assignment_candidates.items()
        if candidate[3] is not None
    }
    return assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin


def _passive_continuation_target_refs(
    project: ResolvedProject,
    sheet_path: str,
) -> set[str]:
    return {
        placement.target_ref
        for placement in _passive_continuation_placements(project, sheet_path)
    }


def _passive_continuation_placements(
    project: ResolvedProject,
    sheet_path: str,
) -> tuple[_PassiveContinuationPlacement, ...]:
    sheet = project.source.sheets[sheet_path]
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return ()

    rail_bank_refs = _passive_rail_bank_member_refs(project, sheet_path)
    anchor_refs = {ref for ref in sheet.symbols if _is_anchor_ref(ref)}
    two_pin_passive_refs = {
        ref
        for ref, symbol_decl in sheet.symbols.items()
        if ref not in rail_bank_refs
        and not _is_anchor_ref(ref)
        and _is_vertical_two_pin_symbol(project.symbol_library.get(symbol_decl.lib))
    }
    anchored_passive_refs: set[str] = set()
    for net_name, endpoints in resolved_sheet.nets.items():
        if _is_groundish_net(net_name) or _is_powerish_net(net_name):
            continue
        net_refs = {
            endpoint.ref
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN and endpoint.ref in sheet.symbols
        }
        if not any(ref in anchor_refs for ref in net_refs):
            continue
        anchored_passive_refs.update(
            ref
            for ref in net_refs
            if ref in two_pin_passive_refs and ref not in anchor_refs
        )

    placements: list[_PassiveContinuationPlacement] = []
    for net_name, endpoints in resolved_sheet.nets.items():
        if _is_groundish_net(net_name) or _is_powerish_net(net_name):
            continue
        passive_endpoints = [
            endpoint
            for endpoint in endpoints
            if endpoint.kind is EndpointKind.SYMBOL_PIN
            and endpoint.ref in two_pin_passive_refs
            and endpoint.pin_number is not None
        ]
        if len(passive_endpoints) != 2:
            continue
        first, second = passive_endpoints
        first_anchored = first.ref in anchored_passive_refs
        second_anchored = second.ref in anchored_passive_refs
        if first_anchored == second_anchored:
            continue
        source_endpoint = first if first_anchored else second
        target_endpoint = second if first_anchored else first
        source_pin_number = source_endpoint.pin_number
        target_pin_number = target_endpoint.pin_number
        if (
            source_endpoint.ref is None
            or target_endpoint.ref is None
            or source_pin_number is None
            or target_pin_number is None
        ):
            continue
        source_symbol_decl = sheet.symbols[source_endpoint.ref]
        target_symbol_decl = sheet.symbols[target_endpoint.ref]
        source_symbol_info = project.symbol_library.get(source_symbol_decl.lib)
        target_symbol_info = project.symbol_library.get(target_symbol_decl.lib)
        source_pin = (
            _pin_by_number(source_symbol_info, source_pin_number)
            if source_symbol_info is not None
            else None
        )
        target_pin = (
            _pin_by_number(target_symbol_info, target_pin_number)
            if target_symbol_info is not None
            else None
        )
        if (
            source_pin is None
            or target_pin is None
            or source_pin.at is None
            or target_pin.at is None
        ):
            continue
        placements.append(
            _PassiveContinuationPlacement(
                net_name=net_name,
                source_ref=source_endpoint.ref,
                source_pin=source_pin,
                target_ref=target_endpoint.ref,
                target_pin=target_pin,
            )
        )
    return tuple(sorted(placements, key=lambda item: (item.source_ref, item.net_name)))


def _passive_rail_bank_member_refs(
    project: ResolvedProject,
    sheet_path: str,
) -> set[str]:
    return {
        ref
        for _top_net, _bottom_net, refs in _passive_rail_bank_ref_groups(project, sheet_path)
        for ref in refs
    }


def _passive_rail_bank_ref_groups(
    project: ResolvedProject,
    sheet_path: str,
) -> list[tuple[str, str, tuple[str, ...]]]:
    return [
        (motif.top_net, motif.bottom_net, tuple(sorted(motif.refs, key=_symbol_ref_key)))
        for motif in build_sheet_circuit_motifs(project, sheet_path).rail_banks
    ]


def _anchor_assignment_net_rank(sheet: Any, net_name: str) -> int:
    if _local_signal_suffix(net_name) is not None:
        return 0
    if sheet.interface.get(net_name) in {"power_in", "power_out"}:
        return 2
    if _is_groundish_net(net_name) or _is_powerish_net(net_name):
        return 2
    return 1


def _layout_node_id(ref: str, unit: int) -> str:
    return f"{ref}:{unit}"


def _layout_node_key(node_id: str) -> tuple[str, int]:
    ref, unit_text = node_id.rsplit(":", 1)
    return (ref, int(unit_text))


def _symbol_body_layout_problem(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
) -> LayoutProblem:
    sheet = project.source.sheets[sheet_path]
    elements: list[LayoutElement] = []
    for (ref, unit), position in sorted(positions.items()):
        symbol_decl = sheet.symbols[ref]
        symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
        left, top, right, bottom = _symbol_body_rect(symbol_info, position.x, position.y)
        elements.append(
            LayoutElement(
                id=_layout_node_id(ref, unit),
                owner=ref,
                kind="symbol",
                rect=LayoutRect(left=left, top=top, right=right, bottom=bottom),
                movable=not _is_anchor_ref(ref),
            )
        )
    return LayoutProblem(elements=tuple(elements))


def _resolve_symbol_body_overlaps(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
) -> dict[tuple[str, int], Point]:
    if not _symbol_body_layout_problem(project, sheet_path, positions).overlaps():
        return positions

    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    nodes: dict[str, LayoutNode] = {}
    local_centers: dict[str, tuple[float, float]] = {}
    for key, position in positions.items():
        ref, _unit = key
        node, local_center = _symbol_body_layout_node(
            project,
            sheet_path,
            key,
            position,
            movable=not _is_fixed_layout_ref(ref),
        )
        nodes[node.id] = node
        local_centers[node.id] = local_center

    solved = solve_contact_layout(
        nodes,
        [],
        bounds=LayoutRect(left=min_x, top=min_y, right=max_x, bottom=max_y),
        iterations=1,
        grid=SCHEMATIC_GRID,
        minimum_gap=5.08,
        max_step=10.16,
    )
    relaxed: dict[tuple[str, int], Point] = {}
    for node_id, node in solved.items():
        local_center_x, local_center_y = local_centers[node_id]
        relaxed[_layout_node_key(node_id)] = Point(
            x=_snap_grid(node.center.x - local_center_x),
            y=_snap_grid(node.center.y + local_center_y),
        )
    return relaxed


def _symbol_body_layout_node(
    project: ResolvedProject,
    sheet_path: str,
    key: tuple[str, int],
    position: Point,
    *,
    movable: bool,
) -> tuple[LayoutNode, tuple[float, float]]:
    sheet = project.source.sheets[sheet_path]
    ref, unit = key
    symbol_decl = sheet.symbols[ref]
    symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
    left, top, right, bottom = _symbol_body_rect(symbol_info, position.x, position.y)
    center = LayoutRect(left=left, top=top, right=right, bottom=bottom).center
    local_center = (center.x - position.x, position.y - center.y)
    return (
        LayoutNode(
            id=_layout_node_id(ref, unit),
            center=center,
            width=max(right - left, SCHEMATIC_GRID),
            height=max(bottom - top, SCHEMATIC_GRID),
            movable=movable,
        ),
        local_center,
    )


def _symbol_layout_node(
    project: ResolvedProject,
    sheet_path: str,
    key: tuple[str, int],
    position: Point,
    *,
    movable: bool,
) -> tuple[LayoutNode, tuple[float, float]]:
    sheet = project.source.sheets[sheet_path]
    ref, unit = key
    symbol_decl = sheet.symbols[ref]
    symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
    local_min_x, local_max_x = _symbol_horizontal_extent(symbol_info)
    local_min_y, local_max_y = _symbol_vertical_extent(symbol_info)
    local_center_x = (local_min_x + local_max_x) / 2
    local_center_y = (local_min_y + local_max_y) / 2
    return (
        LayoutNode(
            id=_layout_node_id(ref, unit),
            center=Point(
                x=position.x + local_center_x,
                y=position.y - local_center_y,
            ),
            width=max(local_max_x - local_min_x, SCHEMATIC_GRID * 4),
            height=max(local_max_y - local_min_y, SCHEMATIC_GRID * 4),
            movable=movable,
        ),
        (local_center_x, local_center_y),
    )


def _layout_rect_node(node_id: str, rect: Rect) -> LayoutNode | None:
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return None
    return LayoutNode(
        id=node_id,
        center=Point(x=(left + right) / 2, y=(top + bottom) / 2),
        width=right - left,
        height=bottom - top,
        movable=False,
    )


def _anchor_layout_node_id(
    positions: dict[tuple[str, int], Point],
    anchor: str,
    anchor_pin: SymbolPin | None,
) -> str | None:
    units = sorted(unit for ref, unit in positions if ref == anchor)
    if not units:
        return None
    if anchor_pin is not None and anchor_pin.unit in units:
        return _layout_node_id(anchor, anchor_pin.unit)
    if 1 in units:
        return _layout_node_id(anchor, 1)
    return _layout_node_id(anchor, units[0])


def _position_routing_risk_score(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
) -> float:
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return 0.0

    sheet = project.source.sheets[sheet_path]
    route_segments: list[tuple[str, WireSegment]] = []
    for net_name, endpoints in sorted(resolved_sheet.nets.items()):
        points: list[PinPoint] = []
        for endpoint in endpoints:
            if (
                endpoint.kind is not EndpointKind.SYMBOL_PIN
                or endpoint.ref is None
                or endpoint.pin_number is None
            ):
                continue
            symbol_decl = sheet.symbols.get(endpoint.ref)
            if symbol_decl is None:
                continue
            symbol_info = project.symbol_library.get(symbol_decl.lib)
            if symbol_info is None:
                continue
            pin = _pin_by_number(symbol_info, endpoint.pin_number)
            if pin is None:
                continue
            position = positions.get((endpoint.ref, pin.unit)) or positions.get((endpoint.ref, 1))
            if position is None:
                continue
            points.append(_symbol_pin_point(position.x, position.y, pin, symbol_info=symbol_info))
        if len(points) < 2 or len(points) > 8:
            continue
        ordered_points = sorted(points, key=lambda point: (point.x, point.y))
        hub = ordered_points[0]
        for point in ordered_points[1:]:
            mid = (_snap_grid(point.x), _snap_grid(hub.y))
            route_segments.append((net_name, (hub.x, hub.y, mid[0], mid[1])))
            route_segments.append((net_name, (mid[0], mid[1], point.x, point.y)))

    score = 0.0
    for _net_name, segment in route_segments:
        score += (
            abs(segment[0] - segment[2]) + abs(segment[1] - segment[3])
        ) * 0.001
    for first_index, (first_net, first_segment) in enumerate(route_segments):
        if first_segment[0:2] == first_segment[2:4]:
            continue
        for second_net, second_segment in route_segments[first_index + 1 :]:
            if first_net == second_net or second_segment[0:2] == second_segment[2:4]:
                continue
            if _segments_touch(first_segment, second_segment):
                score += 1000.0
    return score


def _relax_symbol_positions(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    *,
    assigned_to_anchor: dict[str, str],
    assigned_anchor_pin: dict[str, SymbolPin],
) -> dict[tuple[str, int], Point]:
    if not positions:
        return positions
    sheet = project.source.sheets[sheet_path]
    interface_sheet = bool(sheet.interface)
    path_seed_refs = _interface_path_refs(project, sheet_path)
    interface_movable_refs = (
        _interface_path_compaction_refs(project, sheet_path) if interface_sheet else set()
    )
    if interface_sheet and not interface_movable_refs:
        return positions

    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    nodes: dict[str, LayoutNode] = {}
    local_centers: dict[str, tuple[float, float]] = {}
    rail_bank_groups = _passive_rail_bank_ref_groups(project, sheet_path)
    rail_bank_refs = {ref for _top_net, _bottom_net, refs in rail_bank_groups for ref in refs}
    for key, position in positions.items():
        ref, _unit = key
        movable = not _is_fixed_layout_ref(ref) and (
            not interface_sheet or ref in interface_movable_refs
        )
        node, local_center = _symbol_layout_node(
            project,
            sheet_path,
            key,
            position,
            movable=movable,
        )
        nodes[node.id] = node
        local_centers[node.id] = local_center

        if _is_anchor_ref(ref):
            symbol_decl = project.source.sheets[sheet_path].symbols[ref]
            symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), key[1])
            points = _pin_points_for_placement(symbol_info, position.x, position.y)
            for index, keepout in enumerate(
                _anchor_symbol_contact_keepout_rects(points, symbol_info, position.x, position.y)
            ):
                keepout_node = _layout_rect_node(f"keepout:{ref}:{key[1]}:{index}", keepout)
                if keepout_node is not None:
                    nodes[keepout_node.id] = keepout_node

    links: list[ContactLink] = []
    for ref, anchor in sorted(assigned_to_anchor.items()):
        if interface_sheet and ref not in interface_movable_refs:
            continue
        target_id = _anchor_layout_node_id(positions, anchor, assigned_anchor_pin.get(ref))
        if target_id is None:
            continue
        for unit in sorted(unit for candidate_ref, unit in positions if candidate_ref == ref):
            source_id = _layout_node_id(ref, unit)
            if source_id in nodes and source_id != target_id:
                is_rail_bank_member = ref in rail_bank_refs
                links.append(
                    ContactLink(
                        source=source_id,
                        target=target_id,
                        preferred_gap=12.7 if is_rail_bank_member else 7.62,
                        strength=0.05 if is_rail_bank_member else 0.16,
                    )
                )

    resolved_sheet = project.sheets.get(sheet_path)
    local_net_graph: dict[str, set[str]] = {}
    interface_flow_distances = (
        _interface_path_flow_distances(project, sheet_path) if interface_sheet else {}
    )
    if resolved_sheet is not None:
        for net_name, endpoints in sorted(resolved_sheet.nets.items()):
            if not interface_sheet:
                continue
            if _is_groundish_net(net_name) or _is_powerish_net(net_name):
                continue
            net_refs = sorted(
                {
                    endpoint.ref
                    for endpoint in endpoints
                    if endpoint.kind is EndpointKind.SYMBOL_PIN
                    and endpoint.ref is not None
                    and (endpoint.ref, 1) in positions
                },
                key=lambda ref: (
                    positions[(ref, 1)].x,
                    positions[(ref, 1)].y,
                    _symbol_ref_key(ref),
                ),
            )
            if len(net_refs) < 2 or len(net_refs) > 6:
                continue
            if path_seed_refs and not (set(net_refs) & path_seed_refs):
                continue
            if not path_seed_refs and interface_sheet:
                continue
            for first_index, first in enumerate(net_refs):
                local_net_graph.setdefault(first, set())
                for second in net_refs[first_index + 1 :]:
                    local_net_graph.setdefault(second, set())
                    local_net_graph[first].add(second)
                    local_net_graph[second].add(first)
            link_pairs = (
                [
                    (first, second)
                    for first_index, first in enumerate(net_refs)
                    for second in net_refs[first_index + 1 :]
                ]
                if len(net_refs) <= 4
                else list(zip(net_refs, net_refs[1:], strict=False))
            )
            for first, second in link_pairs:
                first_id = _layout_node_id(first, 1)
                second_id = _layout_node_id(second, 1)
                if first_id not in nodes or second_id not in nodes:
                    continue
                first_distance = interface_flow_distances.get(first)
                second_distance = interface_flow_distances.get(second)
                if (
                    first_distance is not None
                    and second_distance is not None
                    and first_distance != second_distance
                ):
                    target, source = (
                        (first, second)
                        if first_distance < second_distance
                        else (second, first)
                    )
                    links.append(
                        ContactLink(
                            source=_layout_node_id(source, 1),
                            target=_layout_node_id(target, 1),
                            preferred_gap=10.16,
                            strength=0.52,
                            axis="x",
                            direction=1,
                        )
                    )
                    continue
                has_fixed_ref = _is_fixed_layout_ref(first) or _is_fixed_layout_ref(second)
                links.append(
                    ContactLink(
                        source=second_id,
                        target=first_id,
                        preferred_gap=12.7 if has_fixed_ref else 2.54,
                        strength=0.08 if has_fixed_ref else 0.28,
                    )
                )

    seen_component_refs: set[str] = set()
    for start_ref in sorted(local_net_graph, key=_symbol_ref_key):
        if start_ref in seen_component_refs:
            continue
        component_refs: set[str] = set()
        frontier = [start_ref]
        while frontier:
            ref = frontier.pop()
            if ref in component_refs:
                continue
            component_refs.add(ref)
            frontier.extend(sorted(local_net_graph.get(ref, set()) - component_refs))
        seen_component_refs.update(component_refs)
        movable_refs = [
            ref
            for ref in sorted(component_refs, key=_symbol_ref_key)
            if not _is_fixed_layout_ref(ref) and (ref, 1) in positions
        ]
        if len(movable_refs) < 3 or len(movable_refs) > 12:
            continue
        center_x = sum(positions[(ref, 1)].x for ref in movable_refs) / len(movable_refs)
        center_y = sum(positions[(ref, 1)].y for ref in movable_refs) / len(movable_refs)
        island_id = f"island:{sheet_path}:{len(nodes)}"
        nodes[island_id] = LayoutNode(
            id=island_id,
            center=Point(x=_snap_grid(center_x), y=_snap_grid(center_y)),
            width=0.01,
            height=0.01,
            movable=False,
        )
        for ref in movable_refs:
            links.append(
                ContactLink(
                    source=_layout_node_id(ref, 1),
                    target=island_id,
                    preferred_gap=2.54,
                    strength=0.18,
                )
            )

    for _top_net, _bottom_net, group_refs in rail_bank_groups:
        members = [ref for ref in group_refs if (ref, 1) in positions]
        if len(members) < 2:
            continue
        xs = [positions[(ref, 1)].x for ref in members]
        ys = [positions[(ref, 1)].y for ref in members]
        if max(xs) - min(xs) >= max(ys) - min(ys):
            members = sorted(
                members,
                key=lambda ref: (positions[(ref, 1)].x, positions[(ref, 1)].y, ref),
            )
        else:
            members = sorted(
                members,
                key=lambda ref: (positions[(ref, 1)].y, positions[(ref, 1)].x, ref),
            )
        for first, second in zip(members, members[1:], strict=False):
            links.append(
                ContactLink(
                    source=_layout_node_id(second, 1),
                    target=_layout_node_id(first, 1),
                    preferred_gap=2.54,
                    strength=0.45,
                )
            )

    if not links:
        return positions

    solved = solve_contact_layout(
        nodes,
        links,
        bounds=LayoutRect(left=min_x, top=min_y, right=max_x, bottom=max_y),
        iterations=60,
        grid=SCHEMATIC_GRID,
        minimum_gap=5.08,
        max_step=7.62,
    )
    if layout_energy(solved, links, minimum_gap=5.08) >= layout_energy(
        nodes,
        links,
        minimum_gap=5.08,
    ):
        return positions

    relaxed: dict[tuple[str, int], Point] = {}
    for node_id, node in solved.items():
        if node_id not in local_centers:
            continue
        local_center_x, local_center_y = local_centers[node_id]
        relaxed[_layout_node_key(node_id)] = Point(
            x=_snap_grid(node.center.x - local_center_x),
            y=_snap_grid(node.center.y + local_center_y),
        )
    if _position_routing_risk_score(project, sheet_path, relaxed) > _position_routing_risk_score(
        project,
        sheet_path,
        positions,
    ):
        return positions
    return relaxed


def _realign_anchor_pin_passives(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    *,
    assigned_to_anchor: dict[str, str],
    assigned_anchor_pin: dict[str, SymbolPin],
    assigned_ref_pin: dict[str, SymbolPin],
) -> dict[tuple[str, int], Point]:
    realigned = dict(positions)
    sheet = project.source.sheets[sheet_path]
    tap_stack_refs = _controller_tap_stack_refs(
        project,
        sheet_path,
        assigned_to_anchor=assigned_to_anchor,
        assigned_anchor_pin=assigned_anchor_pin,
        assigned_ref_pin=assigned_ref_pin,
    )
    for ref, anchor in sorted(assigned_to_anchor.items()):
        if ref in tap_stack_refs:
            continue
        if anchor.startswith("Module"):
            continue
        ref_pin = assigned_ref_pin.get(ref)
        anchor_pin = assigned_anchor_pin.get(ref)
        if ref_pin is None or anchor_pin is None or ref_pin.at is None:
            continue
        if _pin_side(anchor_pin) not in {"left", "right"}:
            continue
        symbol_decl = sheet.symbols.get(ref)
        if symbol_decl is None:
            continue
        symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), ref_pin.unit)
        if not _is_vertical_two_pin_symbol(symbol_info):
            continue
        anchor_symbol_decl = sheet.symbols.get(anchor)
        if anchor_symbol_decl is None:
            continue
        anchor_symbol_info = _unit_symbol_info(
            project.symbol_library.get(anchor_symbol_decl.lib),
            anchor_pin.unit,
        )
        anchor_position = realigned.get((anchor, anchor_pin.unit)) or realigned.get((anchor, 1))
        ref_position = realigned.get((ref, ref_pin.unit)) or realigned.get((ref, 1))
        if anchor_position is None or ref_position is None or anchor_symbol_info is None:
            continue
        anchor_point = _symbol_pin_point(
            anchor_position.x,
            anchor_position.y,
            anchor_pin,
            symbol_info=anchor_symbol_info,
        )
        if (
            (_symbol_prefix(anchor) in {"U", "IC"} or anchor.startswith("Module"))
            and _is_connection_grid_aligned(anchor_point.y)
        ):
            aligned_y = round(anchor_point.y + ref_pin.at[1], 2)
            realigned[(ref, ref_pin.unit)] = _clamped_symbol_geometry_position(
                project,
                sheet_path,
                ref,
                ref_pin.unit,
                ref_position.x,
                aligned_y,
            )
        else:
            aligned_y = _snap_passive_pin_aligned_y(anchor_point.y, ref_pin)
            realigned[(ref, ref_pin.unit)] = _clamped_symbol_slot(
                project,
                sheet_path,
                ref,
                ref_pin.unit,
                ref_position.x,
                aligned_y,
            )
    duplicate_slots: dict[Coordinate, list[tuple[str, int]]] = {}
    for ref in assigned_ref_pin:
        key = (ref, assigned_ref_pin[ref].unit)
        point = realigned.get(key)
        if point is not None:
            duplicate_slots.setdefault(_coordinate(point.x, point.y), []).append(key)
    for keys in duplicate_slots.values():
        if len(keys) < 2:
            continue
        for index, key in enumerate(sorted(keys, key=lambda item: _symbol_ref_key(item[0]))):
            ref, unit = key
            point = realigned[key]
            offset = (index - (len(keys) - 1) / 2) * 10.16
            realigned[key] = _clamped_symbol_slot(
                project,
                sheet_path,
                ref,
                unit,
                point.x,
                point.y + offset,
            )
    duplicate_pin_slots: dict[Coordinate, list[tuple[str, int]]] = {}
    for ref, ref_pin in assigned_ref_pin.items():
        key = (ref, ref_pin.unit)
        point = realigned.get(key)
        symbol_decl = sheet.symbols.get(ref)
        symbol_info = (
            _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), ref_pin.unit)
            if symbol_decl is not None
            else None
        )
        if point is not None and symbol_info is not None:
            pin_point = _symbol_pin_point(point.x, point.y, ref_pin, symbol_info=symbol_info)
            duplicate_pin_slots.setdefault(_coordinate(pin_point.x, pin_point.y), []).append(key)
    for keys in duplicate_pin_slots.values():
        if len(keys) < 2:
            continue
        for index, key in enumerate(sorted(keys, key=lambda item: _symbol_ref_key(item[0]))):
            ref, unit = key
            point = realigned[key]
            offset = (index - (len(keys) - 1) / 2) * 10.16
            realigned[key] = _clamped_symbol_slot(
                project,
                sheet_path,
                ref,
                unit,
                point.x,
                point.y + offset,
            )
    return realigned


def _place_passive_continuation_refs(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    *,
    assigned_to_anchor: dict[str, str],
) -> dict[tuple[str, int], Point]:
    continuations = _passive_continuation_placements(project, sheet_path)
    if not continuations:
        return positions

    sheet = project.source.sheets[sheet_path]
    sheet_regions = build_sheet_circuit_regions(project, sheet_path)
    placed = dict(positions)

    def readability_rects_except(ref: str) -> list[Rect]:
        rects: list[Rect] = []
        for (other_ref, unit), position in placed.items():
            if other_ref == ref:
                continue
            rects.extend(
                _symbol_readability_rects_at(
                    project,
                    sheet_path,
                    other_ref,
                    unit,
                    position,
                )
            )
        return rects

    def pin_geometry_except(refs: set[str]) -> tuple[set[Coordinate], list[WireSegment]]:
        occupied_coordinates: set[Coordinate] = set()
        occupied_segments: list[WireSegment] = []
        for (other_ref, unit), position in placed.items():
            if other_ref in refs:
                continue
            symbol_decl = sheet.symbols.get(other_ref)
            if symbol_decl is None:
                continue
            symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
            points = _pin_points_for_placement(symbol_info, position.x, position.y)
            occupied_coordinates.update(_pin_point_obstacle_coordinates(points))
            occupied_segments.extend(_pin_stub_segments(points))
        return occupied_coordinates, occupied_segments

    def route_candidates(start: PinPoint, end: PinPoint) -> list[list[WireSegment]]:
        if abs(start.y - end.y) < 0.001:
            return [[(start.x, start.y, end.x, end.y)]]
        if abs(start.x - end.x) < 0.001:
            return [[(start.x, start.y, end.x, end.y)]]
        mid_x = _snap_grid((start.x + end.x) / 2)
        mid_y = _snap_grid((start.y + end.y) / 2)
        candidates = [
            [
                (start.x, start.y, mid_x, start.y),
                (mid_x, start.y, mid_x, end.y),
                (mid_x, end.y, end.x, end.y),
            ],
            [
                (start.x, start.y, start.x, mid_y),
                (start.x, mid_y, end.x, mid_y),
                (end.x, mid_y, end.x, end.y),
            ],
        ]
        for offset in (5.08, 10.16, 15.24, 20.32, 25.4):
            candidates.extend(
                [
                    [
                        (start.x, start.y, start.x, _snap_grid(min(start.y, end.y) - offset)),
                        (
                            start.x,
                            _snap_grid(min(start.y, end.y) - offset),
                            end.x,
                            _snap_grid(min(start.y, end.y) - offset),
                        ),
                        (end.x, _snap_grid(min(start.y, end.y) - offset), end.x, end.y),
                    ],
                    [
                        (start.x, start.y, start.x, _snap_grid(max(start.y, end.y) + offset)),
                        (
                            start.x,
                            _snap_grid(max(start.y, end.y) + offset),
                            end.x,
                            _snap_grid(max(start.y, end.y) + offset),
                        ),
                        (end.x, _snap_grid(max(start.y, end.y) + offset), end.x, end.y),
                    ],
                ]
            )
        return candidates

    def route_has_clear_path(
        start: PinPoint,
        end: PinPoint,
        *,
        occupied_coordinates: set[Coordinate],
        occupied_segments: list[WireSegment],
    ) -> bool:
        if abs(start.x - end.x) >= 0.001 and abs(start.y - end.y) >= 0.001:
            return True
        allowed = {_coordinate(start.x, start.y), _coordinate(end.x, end.y)}
        for segments in route_candidates(start, end):
            segments = [segment for segment in segments if segment[0:2] != segment[2:4]]
            if any(
                coordinate not in allowed and _point_on_segment(coordinate, segment)
                for coordinate in occupied_coordinates
                for segment in segments
            ):
                continue
            if any(
                _segments_touch(segment, occupied)
                for segment in segments
                for occupied in occupied_segments
            ):
                continue
            return True
        return False

    for continuation in continuations:
        source_position = placed.get(
            (continuation.source_ref, continuation.source_pin.unit)
        ) or placed.get((continuation.source_ref, 1))
        if source_position is None:
            continue
        source_symbol_decl = sheet.symbols.get(continuation.source_ref)
        if source_symbol_decl is None:
            continue
        source_symbol_info = _unit_symbol_info(
            project.symbol_library.get(source_symbol_decl.lib),
            continuation.source_pin.unit,
        )
        source_point = _symbol_pin_point(
            source_position.x,
            source_position.y,
            continuation.source_pin,
            symbol_info=source_symbol_info,
        )
        anchor_x: float | None = None
        anchor_ref = assigned_to_anchor.get(continuation.source_ref)
        if anchor_ref is not None:
            anchor_position = placed.get((anchor_ref, 1))
            if anchor_position is not None:
                anchor_x = anchor_position.x
        if anchor_x is None:
            anchor_x = source_position.x
        direction = -1 if source_point.x <= anchor_x else 1
        directions = (direction, -direction)
        target_pin_at = continuation.target_pin.at
        if target_pin_at is None:
            continue
        target_pin_local_x = target_pin_at[0]
        target_pin_local_y = target_pin_at[1]
        target_symbol_decl = sheet.symbols.get(continuation.target_ref)
        if target_symbol_decl is None:
            continue
        target_symbol_info = _unit_symbol_info(
            project.symbol_library.get(target_symbol_decl.lib),
            continuation.target_pin.unit,
        )
        occupied_coordinates, occupied_segments = pin_geometry_except(
            {continuation.source_ref, continuation.target_ref}
        )
        in_same_region = sheet_regions.same_region(
            continuation.source_ref,
            continuation.target_ref,
        )
        same_column_specs = [
            (0, 0.0, pin_y_delta)
            for pin_y_delta in (
                -17.78,
                17.78,
                -25.4,
                25.4,
                -33.02,
                33.02,
                -43.18,
                43.18,
            )
        ]
        side_specs = [
            (route_direction, offset, pin_y_delta)
            for pin_y_delta in (0.0, -10.16, 10.16, -15.24, 15.24, -20.32, 20.32, -27.94, 27.94)
            for route_direction in directions
            for offset in (17.78, 22.86, 27.94, 33.02, 38.1, 45.72, 53.34, 63.5)
        ]
        candidate_specs = [*same_column_specs, *side_specs] if in_same_region else side_specs
        for route_direction, offset, pin_y_delta in candidate_specs:
            target_y = round(source_point.y + target_pin_local_y + pin_y_delta, 2)
            candidate = _clamped_symbol_geometry_position(
                project,
                sheet_path,
                continuation.target_ref,
                continuation.target_pin.unit,
                source_point.x + route_direction * offset - target_pin_local_x,
                target_y,
            )
            candidate_rects = _symbol_readability_rects_at(
                project,
                sheet_path,
                continuation.target_ref,
                continuation.target_pin.unit,
                candidate,
            )
            if _rects_overlap_any(
                candidate_rects,
                readability_rects_except(continuation.target_ref),
            ):
                continue
            candidate_points = _pin_points_for_placement(
                target_symbol_info,
                candidate.x,
                candidate.y,
            )
            if _placement_collides_with_existing_pin_stubs(
                candidate_points,
                target_symbol_info,
                candidate.x,
                candidate.y,
                occupied_coordinates=occupied_coordinates,
                occupied_segments=occupied_segments,
            ):
                continue
            target_point = _symbol_pin_point(
                candidate.x,
                candidate.y,
                continuation.target_pin,
                symbol_info=target_symbol_info,
            )
            if not route_has_clear_path(
                source_point,
                target_point,
                occupied_coordinates=occupied_coordinates,
                occupied_segments=occupied_segments,
            ):
                continue
            placed[(continuation.target_ref, continuation.target_pin.unit)] = candidate
            break
    return placed


def _layout_sheet_symbols(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[tuple[str, int], Point]:
    low_interface_layout = _layout_low_interface_local_circuit(project, sheet_path)
    if low_interface_layout is not None:
        return low_interface_layout

    sheet = project.source.sheets[sheet_path]
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )
    functional_anchor_lanes = _functional_anchor_lane_overrides(project, sheet_path)
    enable_peripheral_clustering = _uses_functional_island_layout(project, sheet_path)
    default_top = 96.52 if sheet.symbols else 50.8

    def lane_for(ref: str) -> tuple[float, int]:
        if ref in functional_anchor_lanes:
            return functional_anchor_lanes[ref]
        return _symbol_lane(ref, min_x, max_x)

    def resolve_body_overlaps_for_small_interface(
        positions: dict[tuple[str, int], Point],
    ) -> dict[tuple[str, int], Point]:
        resolved_sheet = project.sheets.get(sheet_path)
        has_local_signal_nets = (
            resolved_sheet is not None
            and any(_local_signal_suffix(net_name) is not None for net_name in resolved_sheet.nets)
        )
        if sheet.interface and (len(sheet.interface) > 5 or not has_local_signal_nets):
            return positions
        return _resolve_symbol_body_overlaps(project, sheet_path, positions)

    if not enable_peripheral_clustering:
        ordered = sorted(sheet.symbols, key=lambda ref: (lane_for(ref)[1], _symbol_ref_key(ref)))
        large_lane_bottoms: dict[tuple[int, int], float] = {}
        large_lane_columns: dict[int, int] = {}
        large_positions: dict[tuple[str, int], Point] = {}
        occupied_coordinates: set[Coordinate] = set()
        occupied_segments: list[WireSegment] = []
        occupied_keepouts: list[Rect] = []
        margin = 7.62
        _page_width, page_height = _paper_dimensions(project, sheet_path)
        _layout_min_x, _layout_max_x, layout_min_y, layout_max_y = _symbol_layout_bounds(
            project,
            sheet_path,
        )
        large_default_top = layout_min_y
        usable_center_x = (min_x + max_x) / 2
        column_spacing = 25.4
        for ref in ordered:
            symbol_decl = sheet.symbols[ref]
            symbol_info = project.symbol_library.get(symbol_decl.lib)
            for unit in _symbol_units(symbol_decl.units, symbol_info):
                unit_symbol_info = _unit_symbol_info(symbol_info, unit)
                local_min_y, local_max_y = _symbol_vertical_extent(unit_symbol_info)
                symbol_height = local_max_y - local_min_y
                x, lane = lane_for(ref)
                column = large_lane_columns.get(lane, 0)
                top = large_lane_bottoms.get((lane, column), large_default_top)
                direction = -1 if x >= usable_center_x else 1
                attempts = 0
                column_hops = 0
                avoid_keepouts = True
                while True:
                    if top + symbol_height > layout_max_y and top > large_default_top:
                        column += 1
                        column_hops += 1
                        large_lane_columns[lane] = column
                        top = layout_min_y
                        attempts = 0
                        if column_hops > 32:
                            if avoid_keepouts:
                                avoid_keepouts = False
                                column = large_lane_columns.get(lane, 0)
                                top = large_lane_bottoms.get((lane, column), large_default_top)
                                attempts = 0
                                column_hops = 0
                                continue
                            raise RuntimeError(
                                f"could not place {ref} on {sheet_path} without pin collisions"
                            )
                    candidate_x = _snap_grid(
                        _clamp(x + direction * column * column_spacing, min_x, max_x)
                    )
                    same_x_bottoms = [
                        bottom
                        for (other_lane, other_column), bottom in large_lane_bottoms.items()
                        if other_lane == lane
                        and _snap_grid(
                            _clamp(
                                x + direction * other_column * column_spacing,
                                min_x,
                                max_x,
                            )
                        )
                        == candidate_x
                    ]
                    if same_x_bottoms:
                        top = max(top, max(same_x_bottoms))
                    if top + symbol_height > layout_max_y and top > large_default_top:
                        column += 1
                        column_hops += 1
                        large_lane_columns[lane] = column
                        top = layout_min_y
                        attempts = 0
                        if column_hops > 32:
                            if avoid_keepouts:
                                avoid_keepouts = False
                                column = large_lane_columns.get(lane, 0)
                                top = large_lane_bottoms.get((lane, column), large_default_top)
                                attempts = 0
                                column_hops = 0
                                continue
                            raise RuntimeError(
                                f"could not place {ref} on {sheet_path} without pin collisions"
                            )
                        continue
                    candidate_y = _snap_grid(top + local_max_y)
                    candidate = _clamped_symbol_slot(
                        project,
                        sheet_path,
                        ref,
                        unit,
                        candidate_x,
                        candidate_y,
                    )
                    if not sheet.interface:
                        candidate_x = candidate.x
                        candidate_y = candidate.y
                    bottom = candidate_y - local_min_y
                    if bottom > page_height:
                        candidate_y = _snap_grid(candidate_y - (bottom - layout_max_y))
                        bottom = candidate_y - local_min_y
                    points = _pin_points_for_placement(
                        unit_symbol_info,
                        candidate_x,
                        candidate_y,
                    )
                    collides = _placement_collides_with_existing_pin_stubs(
                        points,
                        unit_symbol_info,
                        candidate_x,
                        candidate_y,
                        occupied_coordinates=occupied_coordinates,
                        occupied_segments=occupied_segments,
                        occupied_keepouts=occupied_keepouts if avoid_keepouts else None,
                    )
                    attempts += 1
                    if not collides:
                        large_lane_bottoms[(lane, column)] = bottom + margin
                        large_positions[(ref, unit)] = Point(x=candidate_x, y=candidate_y)
                        occupied_coordinates.update(_pin_point_obstacle_coordinates(points))
                        occupied_segments.extend(_pin_stub_segments(points))
                        if _is_anchor_ref(ref):
                            occupied_keepouts.extend(
                                _anchor_symbol_keepout_rects(
                                    points,
                                    unit_symbol_info,
                                    candidate_x,
                                    candidate_y,
                                )
                            )
                        break
                    top = _snap_grid(top + SCHEMATIC_GRID)
                    if attempts > 80:
                        column += 1
                        column_hops += 1
                        large_lane_columns[lane] = column
                        top = layout_min_y
                        attempts = 0
                        if column_hops > 32:
                            if avoid_keepouts:
                                avoid_keepouts = False
                                column = large_lane_columns.get(lane, 0)
                                top = large_lane_bottoms.get((lane, column), large_default_top)
                                attempts = 0
                                column_hops = 0
                                continue
                            raise RuntimeError(
                                f"could not place {ref} on {sheet_path} without pin collisions"
                            )
        relaxed_large_positions = _relax_symbol_positions(
            project,
            sheet_path,
            large_positions,
            assigned_to_anchor=assigned_to_anchor,
            assigned_anchor_pin=assigned_anchor_pin,
        )
        centered_large_positions = _center_symbol_positions(
            project,
            sheet_path,
            relaxed_large_positions,
        )
        resolved_large_positions = resolve_body_overlaps_for_small_interface(
            centered_large_positions
        )
        return _place_passive_continuation_refs(
            project,
            sheet_path,
            resolved_large_positions,
            assigned_to_anchor=assigned_to_anchor,
        )

    resolved_sheet = project.sheets.get(sheet_path)
    anchor_refs = {ref for ref in sheet.symbols if _is_anchor_ref(ref)}

    ordered_anchors = sorted(anchor_refs, key=lambda ref: (lane_for(ref)[1], _symbol_ref_key(ref)))
    lane_bottoms: dict[int, float] = {}
    positions: dict[tuple[str, int], Point] = {}
    medium_ref_nets = (
        _ref_net_names(resolved_sheet, set(sheet.symbols)) if resolved_sheet is not None else {}
    )
    sheet_motifs = build_sheet_circuit_motifs(project, sheet_path)
    sheet_regions = build_sheet_circuit_regions(project, sheet_path, motifs=sheet_motifs)
    margin = 20.32

    def local_signal_nets(ref: str) -> set[str]:
        return {
            net_name
            for net_name in medium_ref_nets.get(ref, set())
            if not _is_groundish_net(net_name) and not _is_powerish_net(net_name)
        }

    def medium_support_ref_sort_key(ref: str) -> tuple[float, int, tuple[str, bool, int, str]]:
        two_pin_order = {
            "series_path": 0,
            "shunt": 1,
            "clamp": 2,
            "two_pin": 3,
        }
        two_pin_kinds = {motif.ref: motif.kind for motif in sheet_motifs.two_pin_refs}
        region = sheet_regions.region_for_ref(ref)
        region_order = 0 if region is not None and region.kind == "anchor_support" else 1
        anchor_pin = assigned_anchor_pin.get(ref)
        pin_y = anchor_pin.at[1] if anchor_pin is not None and anchor_pin.at is not None else 0.0
        return (
            pin_y,
            region_order * 10 + two_pin_order.get(two_pin_kinds.get(ref, "two_pin"), 4),
            _symbol_ref_key(ref),
        )

    def separate_conflicting_duplicate_positions(
        raw_positions: dict[tuple[str, int], Point],
    ) -> dict[tuple[str, int], Point]:
        separated: dict[tuple[str, int], Point] = {}
        occupied: dict[Coordinate, str] = {}
        for key, position in sorted(
            raw_positions.items(),
            key=lambda item: (_symbol_ref_key(item[0][0]), item[0][1]),
        ):
            ref, _unit = key
            point = position
            attempts = 0
            while attempts < 16:
                other_ref = occupied.get(_coordinate(point.x, point.y))
                if other_ref is None or local_signal_nets(ref) & local_signal_nets(other_ref):
                    break
                attempts += 1
                point = _clamped_slot(point.x + 12.7, point.y, min_x, max_x, min_y, max_y)
            occupied[_coordinate(point.x, point.y)] = ref
            separated[key] = point
        return separated

    def place_symbol(ref: str, x: float, top: float) -> float:
        symbol_decl = sheet.symbols[ref]
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        current_top = top
        for unit in _symbol_units(symbol_decl.units, symbol_info):
            local_min_y, local_max_y = _symbol_vertical_extent(_unit_symbol_info(symbol_info, unit))
            y = _snap_grid(current_top + local_max_y)
            point = (
                _clamped_symbol_slot(project, sheet_path, ref, unit, x, y)
                if not sheet.interface
                else Point(x=_snap_grid(x), y=y)
            )
            bottom = point.y - local_min_y
            positions[(ref, unit)] = point
            current_top = bottom + margin
        return current_top

    for ref in ordered_anchors:
        x, lane = lane_for(ref)
        top = lane_bottoms.get(lane, default_top)
        lane_bottoms[lane] = place_symbol(ref, x, top)

    def medium_occupied_readability_rects(exclude_ref: str | None = None) -> list[Rect]:
        return [
            rect
            for (placed_ref, unit), position in sorted(positions.items())
            if placed_ref != exclude_ref
            for rect in _symbol_readability_rects_at(
                project,
                sheet_path,
                placed_ref,
                unit,
                position,
            )
        ]

    def medium_pin_geometry_except(refs: set[str]) -> tuple[set[Coordinate], list[WireSegment]]:
        occupied_coordinates: set[Coordinate] = set()
        occupied_segments: list[WireSegment] = []
        for (placed_ref, unit), position in positions.items():
            if placed_ref in refs:
                continue
            symbol_decl = sheet.symbols.get(placed_ref)
            if symbol_decl is None:
                continue
            symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
            points = _pin_points_for_placement(symbol_info, position.x, position.y)
            occupied_coordinates.update(_pin_point_obstacle_coordinates(points))
            occupied_segments.extend(_pin_stub_segments(points))
        return occupied_coordinates, occupied_segments

    def medium_route_candidates(start: PinPoint, end: PinPoint) -> list[list[WireSegment]]:
        if abs(start.y - end.y) < 0.001 or abs(start.x - end.x) < 0.001:
            return [[(start.x, start.y, end.x, end.y)]]
        mid_x = _snap_grid((start.x + end.x) / 2)
        mid_y = _snap_grid((start.y + end.y) / 2)
        return [
            [
                (start.x, start.y, mid_x, start.y),
                (mid_x, start.y, mid_x, end.y),
                (mid_x, end.y, end.x, end.y),
            ],
            [
                (start.x, start.y, start.x, mid_y),
                (start.x, mid_y, end.x, mid_y),
                (end.x, mid_y, end.x, end.y),
            ],
        ]

    def medium_route_has_clear_path(
        start: PinPoint,
        end: PinPoint,
        *,
        occupied_coordinates: set[Coordinate],
        occupied_segments: list[WireSegment],
    ) -> bool:
        allowed = {_coordinate(start.x, start.y), _coordinate(end.x, end.y)}
        for segments in medium_route_candidates(start, end):
            if any(
                coordinate not in allowed and _point_on_segment(coordinate, segment)
                for coordinate in occupied_coordinates
                for segment in segments
            ):
                continue
            if any(
                _segments_touch(segment, occupied)
                for segment in segments
                for occupied in occupied_segments
            ):
                continue
            return True
        return False

    def medium_passive_lane_touches_endpoint(
        anchor_point: PinPoint,
        ref_point: PinPoint,
        *,
        occupied_coordinates: set[Coordinate],
    ) -> bool:
        min_y = min(anchor_point.y, ref_point.y) - 10.16
        max_y = max(anchor_point.y, ref_point.y) + 10.16
        return any(
            abs(coordinate[0] - ref_point.x) < 0.001 and min_y <= coordinate[1] <= max_y
            for coordinate in occupied_coordinates
        )

    def place_medium_support_ref(
        ref: str,
        unit: int,
        x: float,
        y: float,
        *,
        anchor_ref: str,
        anchor_point: PinPoint | None,
        ref_pin: SymbolPin | None,
        side: str,
        use_geometry_support: bool,
    ) -> None:
        symbol_decl = sheet.symbols.get(ref)
        symbol_info = (
            _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
            if symbol_decl is not None
            else None
        )
        direction = -1 if side == "left" else 1 if side == "right" else 0
        x_offsets = (0.0, 10.16, 20.32, 30.48, 43.18, 55.88, 68.58)
        y_offsets = (0.0, -12.7, 12.7, -25.4, 25.4, -38.1, 38.1, -50.8, 50.8)
        occupied_coordinates, occupied_segments = medium_pin_geometry_except(
            {ref, anchor_ref}
        )
        candidate_specs: list[tuple[float, float, float]] = []
        for x_offset in x_offsets:
            for y_offset in y_offsets:
                candidate_specs.append((abs(y_offset) + x_offset * 0.2, x_offset, y_offset))
        for _score, x_offset, y_offset in sorted(candidate_specs):
            candidate_x = x + direction * x_offset
            candidate_y = y + y_offset
            candidate = (
                _clamped_symbol_geometry_position(
                    project,
                    sheet_path,
                    ref,
                    unit,
                    candidate_x,
                    candidate_y,
                )
                if use_geometry_support
                else _clamped_symbol_slot(
                    project,
                    sheet_path,
                    ref,
                    unit,
                    candidate_x,
                    candidate_y,
                )
                if not sheet.interface
                else Point(
                    x=_snap_grid(_clamp(candidate_x, min_x, max_x)),
                    y=_snap_grid(candidate_y),
                )
            )
            candidate_rects = _symbol_readability_rects_at(
                project,
                sheet_path,
                ref,
                unit,
                candidate,
            )
            if _rects_overlap_any(candidate_rects, medium_occupied_readability_rects(ref)):
                continue
            candidate_points = _pin_points_for_placement(symbol_info, candidate.x, candidate.y)
            if _placement_collides_with_existing_pin_stubs(
                candidate_points,
                symbol_info,
                candidate.x,
                candidate.y,
                occupied_coordinates=occupied_coordinates,
                occupied_segments=occupied_segments,
            ):
                continue
            if anchor_point is not None and ref_pin is not None:
                ref_point = _symbol_pin_point(
                    candidate.x,
                    candidate.y,
                    ref_pin,
                    symbol_info=symbol_info,
                )
                if medium_passive_lane_touches_endpoint(
                    anchor_point,
                    ref_point,
                    occupied_coordinates=occupied_coordinates,
                ):
                    continue
                if not medium_route_has_clear_path(
                    anchor_point,
                    ref_point,
                    occupied_coordinates=occupied_coordinates,
                    occupied_segments=occupied_segments,
                ):
                    continue
            positions[(ref, unit)] = candidate
            return
        positions[(ref, unit)] = (
            _clamped_symbol_geometry_position(project, sheet_path, ref, unit, x, y)
            if use_geometry_support
            else _clamped_symbol_slot(project, sheet_path, ref, unit, x, y)
        )

    grouped_refs: dict[str, list[str]] = {}
    for ref, anchor in assigned_to_anchor.items():
        grouped_refs.setdefault(anchor, []).append(ref)
    for anchor, refs in sorted(grouped_refs.items()):
        anchor_position = positions.get((anchor, 1))
        if anchor_position is None:
            continue
        side_refs: dict[str, list[str]] = {"left": [], "right": [], "top": [], "bottom": []}
        for ref in refs:
            side = _pin_side(assigned_anchor_pin[ref]) if ref in assigned_anchor_pin else "right"
            side_refs[side].append(ref)
        row_spacing = 20.32
        column_spacing = 38.1
        horizontal_offset = 55.88
        vertical_offset = 43.18
        bottom_offset = 48.26
        rows_per_column = 5
        pin_horizontal_offset = 44.45
        geometry_pin_horizontal_offset = 17.78

        def anchor_pin_point(ref: str, anchor: str) -> PinPoint | None:
            if anchor.startswith("Module"):
                return None
            pin = assigned_anchor_pin.get(ref)
            if pin is None:
                return None
            anchor_symbol_decl = sheet.symbols[anchor]
            anchor_symbol_info = project.symbol_library.get(anchor_symbol_decl.lib)
            if anchor_symbol_info is None:
                return None
            anchor_unit_position = positions.get((anchor, pin.unit)) or positions.get((anchor, 1))
            if anchor_unit_position is None:
                return None
            return _symbol_pin_point(
                anchor_unit_position.x,
                anchor_unit_position.y,
                pin,
                symbol_info=anchor_symbol_info,
            )

        def use_geometry_support_position(ref: str, anchor: str) -> bool:
            if _symbol_prefix(anchor) not in {"U", "IC"} and not anchor.startswith("Module"):
                return False
            ref_pin = assigned_ref_pin.get(ref)
            if ref_pin is None:
                return False
            anchor_point = anchor_pin_point(ref, anchor)
            if anchor_point is None or not _is_connection_grid_aligned(anchor_point.y):
                return False
            symbol_decl = sheet.symbols.get(ref)
            symbol_info = (
                _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), ref_pin.unit)
                if symbol_decl is not None
                else None
            )
            return _is_vertical_two_pin_symbol(symbol_info)

        def pin_aligned_symbol_y(
            ref: str,
            anchor: str,
            anchor_point: PinPoint | None,
        ) -> float | None:
            if anchor_point is None:
                return None
            ref_pin = assigned_ref_pin.get(ref)
            if ref_pin is None or ref_pin.at is None:
                return None
            if not use_geometry_support_position(ref, anchor):
                return _snap_passive_pin_aligned_y(anchor_point.y, ref_pin)
            return round(anchor_point.y + ref_pin.at[1], 2)

        def place_controller_tap_stack(
            motif: TapStackMotif,
            *,
            anchor_ref: str,
            side: str,
            anchor_point: PinPoint,
        ) -> set[str]:
            if side not in {"left", "right"}:
                return set()

            ordered_refs = [motif.top_ref, motif.bottom_ref]
            if any(ref not in assigned_ref_pin for ref in ordered_refs):
                return set()
            direction = -1 if side == "left" else 1
            tap_offsets = {
                motif.top_ref: -SCHEMATIC_GRID * 2,
                motif.bottom_ref: SCHEMATIC_GRID * 2,
            }
            top_rail_ref_positions = [
                position
                for (placed_ref, unit), position in positions.items()
                if unit == 1
                and placed_ref not in set(ordered_refs)
                and motif.top_net in medium_ref_nets.get(placed_ref, set())
            ]
            occupied_coordinates, occupied_segments = medium_pin_geometry_except(
                set(ordered_refs) | {anchor_ref}
            )
            x_offsets = (17.78, 22.86, 27.94, 33.02, 40.64, 50.8)
            tap_center_bias = 15.24 if top_rail_ref_positions else 0.0
            y_offsets = (0.0, 5.08, -5.08, 10.16, -10.16, 15.24, -15.24)
            for x_offset in x_offsets:
                for y_offset in y_offsets:
                    candidate_positions: dict[tuple[str, int], Point] = {}
                    candidate_rects: list[Rect] = []
                    candidate_points: list[PinPoint] = []
                    candidate_segments: list[WireSegment] = []
                    for ref in ordered_refs:
                        ref_pin = assigned_ref_pin[ref]
                        ref_pin_at = ref_pin.at
                        if ref_pin_at is None:
                            break
                        symbol_decl = sheet.symbols[ref]
                        symbol_info = _unit_symbol_info(
                            project.symbol_library.get(symbol_decl.lib),
                            ref_pin.unit,
                        )
                        pin_local_x = ref_pin_at[0]
                        candidate = _clamped_symbol_geometry_position(
                            project,
                            sheet_path,
                            ref,
                            ref_pin.unit,
                            (
                                max(
                                    anchor_point.label_x + direction * x_offset,
                                    max(position.x for position in top_rail_ref_positions) + 15.24,
                                )
                                if direction > 0 and top_rail_ref_positions
                                else min(
                                    anchor_point.label_x + direction * x_offset,
                                    min(position.x for position in top_rail_ref_positions) - 15.24,
                                )
                                if direction < 0 and top_rail_ref_positions
                                else anchor_point.label_x + direction * x_offset
                            )
                            - pin_local_x,
                            anchor_point.y
                            + tap_center_bias
                            + tap_offsets[ref]
                            + ref_pin_at[1]
                            + y_offset,
                        )
                        candidate_positions[(ref, ref_pin.unit)] = candidate
                        rects = _symbol_readability_rects_at(
                            project,
                            sheet_path,
                            ref,
                            ref_pin.unit,
                            candidate,
                        )
                        if _rects_overlap_any(rects, candidate_rects):
                            break
                        candidate_rects.extend(rects)
                        points = _pin_points_for_placement(
                            symbol_info,
                            candidate.x,
                            candidate.y,
                        )
                        candidate_points.extend(points)
                        candidate_segments.extend(_pin_stub_segments(points))
                    else:
                        if _rects_overlap_any(candidate_rects, medium_occupied_readability_rects()):
                            continue
                        if _placement_collides_with_existing_pin_stubs(
                            candidate_points,
                            None,
                            0.0,
                            0.0,
                            occupied_coordinates=occupied_coordinates,
                            occupied_segments=occupied_segments,
                        ):
                            continue
                        if any(
                            _segments_touch(segment, occupied)
                            for segment in candidate_segments
                            for occupied in occupied_segments
                        ):
                            continue
                        positions.update(candidate_positions)
                        return set(ordered_refs)
            return set()

        handled_support_refs: set[str] = set()
        if _symbol_prefix(anchor) in {"U", "IC"} or anchor.startswith("Module"):
            grouped_by_pin: dict[tuple[str, str], list[str]] = {}
            for ref in refs:
                pin = assigned_anchor_pin.get(ref)
                if pin is None:
                    continue
                grouped_by_pin.setdefault((pin.name, pin.number), []).append(ref)
            for pin_refs in grouped_by_pin.values():
                anchor_point = anchor_pin_point(pin_refs[0], anchor)
                if anchor_point is None:
                    continue
                anchor_pin = assigned_anchor_pin[pin_refs[0]]
                tap_stack = sheet_motifs.tap_stack_for_anchor_pin(
                    anchor,
                    anchor_pin.name,
                    anchor_pin.number,
                )
                if tap_stack is None or not set(tap_stack.refs) <= set(pin_refs):
                    continue
                side = _pin_side(anchor_pin)
                handled_support_refs.update(
                    place_controller_tap_stack(
                        tap_stack,
                        anchor_ref=anchor,
                        side=side,
                        anchor_point=anchor_point,
                    )
                )

        for side, side_items in side_refs.items():
            side_items = [ref for ref in side_items if ref not in handled_support_refs]
            for index, ref in enumerate(sorted(side_items, key=medium_support_ref_sort_key)):
                column = index // rows_per_column
                row = index % rows_per_column
                row_count = min(rows_per_column, len(side_items) - column * rows_per_column)
                centered_row = row - (row_count - 1) / 2
                anchor_point = anchor_pin_point(ref, anchor)
                aligned_y = pin_aligned_symbol_y(ref, anchor, anchor_point)
                use_geometry_support = use_geometry_support_position(ref, anchor)
                pin_offset = (
                    geometry_pin_horizontal_offset
                    if use_geometry_support
                    else pin_horizontal_offset
                )
                if side == "left":
                    if anchor_point is not None and use_geometry_support:
                        x = anchor_point.label_x - pin_offset - column * column_spacing
                    elif anchor_point is not None:
                        x = anchor_point.x - pin_offset - column * column_spacing
                    else:
                        x = anchor_position.x - horizontal_offset - column * column_spacing
                    y = (
                        aligned_y
                        if aligned_y is not None
                        else anchor_position.y + centered_row * row_spacing
                    )
                elif side == "right":
                    if anchor_point is not None and use_geometry_support:
                        x = anchor_point.label_x + pin_offset + column * column_spacing
                    elif anchor_point is not None:
                        x = anchor_point.x + pin_offset + column * column_spacing
                    else:
                        x = anchor_position.x + horizontal_offset + column * column_spacing
                    y = (
                        aligned_y
                        if aligned_y is not None
                        else anchor_position.y + centered_row * row_spacing
                    )
                elif side == "top":
                    x = anchor_position.x + centered_row * column_spacing
                    y = anchor_position.y - vertical_offset - column * row_spacing
                else:
                    x = anchor_position.x + centered_row * column_spacing
                    y = anchor_position.y + bottom_offset + column * row_spacing
                if _symbol_prefix(anchor) in {"U", "IC"} or anchor.startswith("Module"):
                    place_medium_support_ref(
                        ref,
                        1,
                        x,
                        y,
                        anchor_ref=anchor,
                        anchor_point=anchor_point,
                        ref_pin=assigned_ref_pin.get(ref),
                        side=side,
                        use_geometry_support=use_geometry_support,
                    )
                elif use_geometry_support:
                    positions[(ref, 1)] = _clamped_symbol_geometry_position(
                        project,
                        sheet_path,
                        ref,
                        1,
                        x,
                        y,
                    )
                else:
                    positions[(ref, 1)] = (
                        _clamped_symbol_slot(project, sheet_path, ref, 1, x, y)
                        if not sheet.interface
                        else Point(x=_snap_grid(_clamp(x, min_x, max_x)), y=_snap_grid(y))
                    )

    positions = _place_passive_continuation_refs(
        project,
        sheet_path,
        positions,
        assigned_to_anchor=assigned_to_anchor,
    )

    rail_bank_groups = _passive_rail_bank_ref_groups(project, sheet_path)
    power_path_refs = _power_island_path_refs(project, sheet_path)

    def positioned_net_points(
        net_name: str,
        exclude_refs: set[str],
        *,
        only_refs: set[str] | None = None,
    ) -> list[PinPoint]:
        if resolved_sheet is None:
            return []
        net_points: list[PinPoint] = []
        for endpoint in resolved_sheet.nets.get(net_name, []):
            if (
                endpoint.kind is not EndpointKind.SYMBOL_PIN
                or endpoint.ref is None
                or endpoint.pin_number is None
                or endpoint.ref in exclude_refs
                or (only_refs is not None and endpoint.ref not in only_refs)
            ):
                continue
            symbol_decl = sheet.symbols.get(endpoint.ref)
            if symbol_decl is None:
                continue
            symbol_info = project.symbol_library.get(symbol_decl.lib)
            if symbol_info is None:
                continue
            pin = _pin_by_number(symbol_info, endpoint.pin_number)
            if pin is None:
                continue
            position = positions.get((endpoint.ref, pin.unit)) or positions.get((endpoint.ref, 1))
            if position is None:
                continue
            net_points.append(
                _symbol_pin_point(position.x, position.y, pin, symbol_info=symbol_info)
            )
        return net_points

    def readability_rects_except_refs(exclude_refs: set[str]) -> list[Rect]:
        return [
            rect
            for (placed_ref, unit), position in sorted(positions.items())
            if placed_ref not in exclude_refs
            for rect in _symbol_readability_rects_at(
                project,
                sheet_path,
                placed_ref,
                unit,
                position,
            )
        ]

    def place_passive_rail_bank(
        top_net: str,
        bottom_net: str,
        rail_refs: tuple[str, ...],
        group_index: int,
    ) -> None:
        refs_to_place = tuple(ref for ref in rail_refs if (ref, 1) not in positions)
        if not refs_to_place:
            return
        excluded_refs = set(rail_refs)
        top_points = positioned_net_points(top_net, excluded_refs)
        bottom_points = positioned_net_points(bottom_net, excluded_refs)
        preferred_top_points = positioned_net_points(
            top_net,
            excluded_refs,
            only_refs=power_path_refs,
        )
        source_points = [*top_points, *bottom_points]
        source_x_points = preferred_top_points or top_points
        source_x = max((point.x for point in source_x_points), default=max_x - 127.0)
        source_y_points = preferred_top_points or top_points or source_points
        source_y = max((point.y for point in source_y_points), default=default_top)
        occupied_coordinates, occupied_segments = medium_pin_geometry_except(set(refs_to_place))

        for spacing in (35.56, 43.18, 30.48, 50.8):
            row_width = (len(refs_to_place) - 1) * spacing
            allowed_start_min = min_x
            allowed_start_max = max_x - row_width
            if allowed_start_min > allowed_start_max:
                continue
            raw_preferred_start = (
                source_x - row_width / 2
                if preferred_top_points
                else max(source_x + 15.24, min_x)
            )
            preferred_start = _clamp(raw_preferred_start, allowed_start_min, allowed_start_max)
            x_candidates = tuple(
                dict.fromkeys(
                    _snap_grid(_clamp(candidate, allowed_start_min, allowed_start_max))
                    for candidate in (
                        preferred_start,
                        max_x - row_width - 12.7,
                        max(source_x + 25.4, min_x),
                        max(source_x - row_width - 15.24, min_x),
                        min_x + group_index * 12.7,
                    )
                )
            )
            y_candidates = tuple(
                dict.fromkeys(
                    _snap_grid(_clamp(source_y + offset, min_y, max_y))
                    for offset in (
                        17.78 + group_index * 12.7,
                        25.4 + group_index * 12.7,
                        35.56 + group_index * 12.7,
                        48.26 + group_index * 12.7,
                        -17.78 - group_index * 12.7,
                        -30.48 - group_index * 12.7,
                    )
                )
            )
            for y in y_candidates:
                for start_x in x_candidates:
                    candidate_positions: dict[tuple[str, int], Point] = {}
                    candidate_rects: list[Rect] = []
                    candidate_points: list[PinPoint] = []
                    candidate_segments: list[WireSegment] = []
                    for index, ref in enumerate(refs_to_place):
                        candidate = _clamped_symbol_slot(
                            project,
                            sheet_path,
                            ref,
                            1,
                            start_x + index * spacing,
                            y,
                        )
                        candidate_positions[(ref, 1)] = candidate
                        rects = _symbol_readability_rects_at(
                            project,
                            sheet_path,
                            ref,
                            1,
                            candidate,
                        )
                        if _rects_overlap_any(rects, candidate_rects):
                            break
                        candidate_rects.extend(rects)
                        symbol_decl = sheet.symbols[ref]
                        symbol_info = _unit_symbol_info(
                            project.symbol_library.get(symbol_decl.lib),
                            1,
                        )
                        points = _pin_points_for_placement(
                            symbol_info,
                            candidate.x,
                            candidate.y,
                        )
                        candidate_points.extend(points)
                        candidate_segments.extend(_pin_stub_segments(points))
                    else:
                        if _rects_overlap_any(
                            candidate_rects,
                            readability_rects_except_refs(excluded_refs),
                        ):
                            continue
                        if _placement_collides_with_existing_pin_stubs(
                            candidate_points,
                            None,
                            0.0,
                            0.0,
                            occupied_coordinates=occupied_coordinates,
                            occupied_segments=occupied_segments,
                        ):
                            continue
                        if any(
                            _segments_touch(segment, occupied)
                            for segment in candidate_segments
                            for occupied in occupied_segments
                        ):
                            continue
                        positions.update(candidate_positions)
                        return

        fallback_y = _snap_grid(
            _clamp(source_y + 50.8 + group_index * 17.78, min_y, max_y)
        )
        fallback_spacing = 35.56
        fallback_start = _snap_grid(
            _clamp(
                max_x - (len(refs_to_place) - 1) * fallback_spacing - 12.7,
                min_x,
                max_x,
            )
        )
        for index, ref in enumerate(refs_to_place):
            positions[(ref, 1)] = _clamped_symbol_slot(
                project,
                sheet_path,
                ref,
                1,
                fallback_start + index * fallback_spacing,
                fallback_y,
            )

    for group_index, (top_net, bottom_net, rail_refs) in enumerate(rail_bank_groups):
        place_passive_rail_bank(top_net, bottom_net, rail_refs, group_index)

    fallback_refs = [
        ref
        for ref in sorted(
            sheet.symbols,
            key=lambda item: (lane_for(item)[1], _symbol_ref_key(item)),
        )
        if (ref, 1) not in positions
    ]
    for ref in fallback_refs:
        x, lane = lane_for(ref)
        top = lane_bottoms.get(lane, default_top)
        lane_bottoms[lane] = place_symbol(ref, x, top)

    separated_positions = separate_conflicting_duplicate_positions(positions)
    relaxed_positions = _relax_symbol_positions(
        project,
        sheet_path,
        separated_positions,
        assigned_to_anchor=assigned_to_anchor,
        assigned_anchor_pin=assigned_anchor_pin,
    )
    pin_aligned_positions = _realign_anchor_pin_passives(
        project,
        sheet_path,
        relaxed_positions,
        assigned_to_anchor=assigned_to_anchor,
        assigned_anchor_pin=assigned_anchor_pin,
        assigned_ref_pin=assigned_ref_pin,
    )
    centered_positions = _center_symbol_positions(project, sheet_path, pin_aligned_positions)
    resolved_positions = resolve_body_overlaps_for_small_interface(centered_positions)
    realigned_positions = _realign_anchor_pin_passives(
        project,
        sheet_path,
        resolved_positions,
        assigned_to_anchor=assigned_to_anchor,
        assigned_anchor_pin=assigned_anchor_pin,
        assigned_ref_pin=assigned_ref_pin,
    )
    return _place_passive_continuation_refs(
        project,
        sheet_path,
        realigned_positions,
        assigned_to_anchor=assigned_to_anchor,
    )


def _paper_size(project: ResolvedProject, sheet_path: str) -> str:
    sheet = project.source.sheets[sheet_path]
    interface_count = sum(
        len(project.source.sheets[child.target_path].interface)
        for child in sheet.child_instances.values()
    )
    if sheet_path == "/" and (len(sheet.child_instances) >= 3 and interface_count >= 40):
        return "A3"
    if len(sheet.symbols) > 20:
        return "A3"
    return "A4"


def _pin_by_number(symbol: SymbolInfo, pin_number: str) -> SymbolPin | None:
    for pin in symbol.pins:
        if pin.number == pin_number:
            return pin
    return None


def _symbol_pin_point(
    symbol_x: float,
    symbol_y: float,
    pin: SymbolPin,
    *,
    symbol_info: SymbolInfo | None = None,
    symbol_rotation: int = 0,
) -> PinPoint:
    symbol_rotation = symbol_rotation % 360
    rotation = (int(pin.at[2] if pin.at else 0.0) + symbol_rotation) % 360
    x, y = _symbol_pin_coordinate(
        symbol_x,
        symbol_y,
        pin,
        symbol_rotation=symbol_rotation,
    )
    if _is_vertical_two_pin_symbol(symbol_info) and rotation in {90, 270}:
        label_x = x + PIN_LABEL_STUB
        label_y = y
    elif rotation == 180:
        label_x = x + PIN_LABEL_STUB
        label_y = y
    elif rotation == 90:
        label_x = x
        label_y = y + PIN_LABEL_STUB
    elif rotation == 270:
        label_x = x
        label_y = y - PIN_LABEL_STUB
    else:
        label_x = x - PIN_LABEL_STUB
        label_y = y
    if symbol_info is not None and not _is_two_pin_symbol(symbol_info):
        label_x, label_y = _label_point_escaping_symbol_body(
            symbol_info,
            symbol_x,
            symbol_y,
            symbol_rotation,
            x,
            y,
            label_x,
            label_y,
        )
    if abs(label_x - x) >= abs(label_y - y):
        label_x = _snap_grid(label_x)
    else:
        label_y = _snap_grid(label_y)
    return PinPoint(x=x, y=y, label_x=label_x, label_y=label_y)


def _label_point_escaping_symbol_body(
    symbol_info: SymbolInfo,
    symbol_x: float,
    symbol_y: float,
    symbol_rotation: int,
    pin_x: float,
    pin_y: float,
    label_x: float,
    label_y: float,
) -> tuple[float, float]:
    body = _rotated_symbol_body_bounds(symbol_info, symbol_x, symbol_y, symbol_rotation)
    body_left, body_top, body_right, body_bottom = body
    stub = max(abs(label_x - pin_x), abs(label_y - pin_y), PIN_LABEL_STUB)
    tolerance = 0.635
    if abs(label_x - pin_x) < abs(label_y - pin_y):
        if pin_y >= body_bottom - tolerance and label_y < pin_y:
            return (pin_x, pin_y + stub)
        if pin_y <= body_top + tolerance and label_y > pin_y:
            return (pin_x, pin_y - stub)
    else:
        if pin_x <= body_left + tolerance and label_x > pin_x:
            return (pin_x - stub, pin_y)
        if pin_x >= body_right - tolerance and label_x < pin_x:
            return (pin_x + stub, pin_y)
    return (label_x, label_y)


def _rotated_symbol_body_bounds(
    symbol_info: SymbolInfo,
    symbol_x: float,
    symbol_y: float,
    symbol_rotation: int,
) -> tuple[float, float, float, float]:
    graphic_extent = _symbol_graphic_extent(symbol_info)
    if graphic_extent is None:
        local_min_x, local_max_x = _symbol_horizontal_extent(symbol_info)
        local_min_y, local_max_y = _symbol_vertical_extent(symbol_info)
    else:
        local_min_x, local_max_x, local_min_y, local_max_y = graphic_extent

    transformed_points: list[tuple[float, float]] = []
    for local_x, local_y in (
        (local_min_x, local_min_y),
        (local_min_x, local_max_y),
        (local_max_x, local_min_y),
        (local_max_x, local_max_y),
    ):
        rotated_x, rotated_y = _rotated_symbol_local_point(local_x, local_y, symbol_rotation)
        transformed_points.append((symbol_x + rotated_x, symbol_y - rotated_y))
    xs = [point[0] for point in transformed_points]
    ys = [point[1] for point in transformed_points]
    return (min(xs), min(ys), max(xs), max(ys))


def _rotated_symbol_local_point(
    local_x: float,
    local_y: float,
    symbol_rotation: int,
) -> tuple[float, float]:
    symbol_rotation = symbol_rotation % 360
    if symbol_rotation == 90:
        return (-local_y, local_x)
    if symbol_rotation == 180:
        return (-local_x, -local_y)
    if symbol_rotation == 270:
        return (local_y, -local_x)
    return (local_x, local_y)


def _pin_side(pin: SymbolPin) -> str:
    rotation = int(pin.at[2] if pin.at else 0.0) % 360
    if rotation == 180:
        return "right"
    if rotation == 90:
        return "bottom"
    if rotation == 270:
        return "top"
    return "left"


def _anchor_symbol_keepout_rects(
    points: list[PinPoint],
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
) -> list[Rect]:
    if symbol_info is None or len(symbol_info.pins) <= 4:
        return []
    return [
        _symbol_rect(symbol_info, x, y, margin=5.08),
        *_pin_label_keepout_rects(points),
    ]


def _anchor_symbol_contact_keepout_rects(
    points: list[PinPoint],
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
) -> list[Rect]:
    keepouts = _anchor_symbol_keepout_rects(points, symbol_info, x, y)
    if symbol_info is None or len(symbol_info.pins) <= 4:
        return keepouts
    label_rects = keepouts[1:]
    label_field_margin = 7.62
    label_field = (
        [
            (
                min(rect[0] for rect in label_rects) - label_field_margin,
                min(rect[1] for rect in label_rects) - label_field_margin,
                max(rect[2] for rect in label_rects) + label_field_margin,
                max(rect[3] for rect in label_rects) + label_field_margin,
            )
        ]
        if label_rects
        else []
    )
    return [*keepouts, *label_field]


def _pin_points_for_placement(
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
) -> list[PinPoint]:
    if symbol_info is None:
        return []
    return [_symbol_pin_point(x, y, pin, symbol_info=symbol_info) for pin in symbol_info.pins]


def _placement_collides_with_existing_pin_stubs(
    points: list[PinPoint],
    symbol_info: SymbolInfo | None = None,
    symbol_x: float = 0.0,
    symbol_y: float = 0.0,
    *,
    occupied_coordinates: set[Coordinate],
    occupied_segments: list[WireSegment],
    occupied_keepouts: list[Rect] | None = None,
) -> bool:
    own_coordinates = _pin_point_obstacle_coordinates(points)
    if own_coordinates & occupied_coordinates:
        return True

    own_segments = _pin_stub_segments(points)
    for coordinate in own_coordinates:
        if any(_point_on_segment(coordinate, segment) for segment in occupied_segments):
            return True
    for segment in own_segments:
        if any(_point_on_segment(coordinate, segment) for coordinate in occupied_coordinates):
            return True
    if occupied_keepouts:
        own_rect = _symbol_rect(symbol_info, symbol_x, symbol_y, margin=2.54)
        if any(_rects_intersect(own_rect, keepout) for keepout in occupied_keepouts):
            return True
    return False


def _is_powerish_net(name: str) -> bool:
    upper = name.upper()
    if _local_signal_suffix(name) is not None:
        return False
    return (
        upper == "GND"
        or upper.startswith("+")
        or "GND" in upper
        or "VBAT" in upper
        or "VCC" in upper
        or "VDD" in upper
        or "3V3" in upper
        or "5V" in upper
    )


def _is_groundish_net(name: str) -> bool:
    upper = name.upper()
    return upper == "GND" or "GND" in upper
