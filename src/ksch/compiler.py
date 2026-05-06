from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sexpdata import Symbol  # type: ignore[import-untyped]

from ksch.circuit_motifs import build_sheet_circuit_motifs
from ksch.emit import write_project as write_placed_project
from ksch.geometry import (
    PinPoint,
    Rect,
)
from ksch.geometry import (
    is_two_pin_symbol as _is_two_pin_symbol,
)
from ksch.geometry import (
    is_vertical_two_pin_symbol as _is_vertical_two_pin_symbol,
)
from ksch.geometry import (
    symbol_graphic_horizontal_extent as _symbol_graphic_horizontal_extent,
)
from ksch.geometry import (
    symbol_horizontal_extent as _symbol_horizontal_extent,
)
from ksch.geometry import (
    symbol_vertical_extent as _symbol_vertical_extent,
)
from ksch.ids import stable_uuid
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.layout import Point
from ksch.model.endpoint import Endpoint, EndpointKind, parse_endpoint
from ksch.model.source import PinDirection
from ksch.net_routing import (
    SheetNetRoutingConfig,
    _display_net_label,
    _label_lines,
    _point_stub_lines,
    _power_flag_positions,
    _sheet_local_label_prefix,
    _wire_lines,
    route_sheet_nets,
)
from ksch.placed import (
    PlacedHierarchicalLabel,
    PlacedItem,
    PlacedNoConnect,
    PlacedProject,
    PlacedProperty,
    PlacedSheet,
    PlacedSheetBlock,
    PlacedSheetPin,
    PlacedSymbol,
    PlacedSymbolPin,
)
from ksch.placed_normalize import normalize_placed_project
from ksch.placement import (
    _bounded_shift,
    _clamp,
    _is_groundish_net,
    _is_low_interface_local_circuit,
    _layout_sheet_symbols,
    _paper_dimensions,
    _paper_size,
    _passive_continuation_placements,
    _pin_by_number,
    _rects_overlap_any,
    _snap_grid,
    _symbol_anchor_assignments,
    _symbol_layout_bounds,
    _symbol_pin_point,
    _symbol_prefix,
    _symbol_readability_rects_at,
    _symbol_units,
    _unit_symbol_info,
)
from ksch.resolver import ResolvedProject
from ksch.routing import coordinate as _coordinate
from ksch.validation import validate_placed_project

WIRE_STUB = 10.16
PIN_LABEL_STUB = 5.08
SHEET_X = 152.4
SHEET_Y = 50.8
SHEET_WIDTH = 50.8
ROOT_SHEET_WIDTH = 101.6
SHEET_MIN_HEIGHT = 38.1
SHEET_PIN_Y_OFFSET = 7.62
SHEET_PIN_STEP = 5.08
SHEET_ROW_MARGIN = 12.7
SYMBOL_MARGIN_X = 38.1
SCHEMATIC_GRID = 2.54
SCHEMATIC_HALF_GRID = SCHEMATIC_GRID / 2
POWER_FLAG_LIB_ID = "power:PWR_FLAG"
POWER_FLAG_VALUE = "PWR_FLAG"


@dataclass(frozen=True)
class SheetBox:
    x: float
    y: float
    width: float
    height: float
    left_ports: list[tuple[str, PinDirection]]
    right_ports: list[tuple[str, PinDirection]]


@dataclass(frozen=True)
class SymbolOrientation:
    rotation: int
    preserve_pin_number: str | None = None


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


def _power_flag_symbol_definition() -> list[Any]:
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
        _symbol_definition_property("Value", POWER_FLAG_VALUE, at=(0.0, 3.81)),
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


def _sheet_filename(project_name: str, sheet_path: str) -> Path:
    if sheet_path == "/":
        return Path(f"{project_name}.kicad_sch")
    parts = [part for part in sheet_path.split("/") if part]
    return Path("sheets").joinpath(*parts).with_suffix(".kicad_sch")


def _join_sheet_path(parent_path: str, child_name: str) -> str:
    if parent_path == "/":
        return f"/{child_name}"
    return f"{parent_path}/{child_name}"


def _child_sheet_uuid(parent_path: str, child_name: str) -> str:
    return stable_uuid(f"{parent_path}/{child_name}:sheet")


def _sheet_instance_path(sheet_path: str) -> str:
    if sheet_path == "/":
        return "/"

    parent_path = "/"
    uuids = []
    for part in [part for part in sheet_path.split("/") if part]:
        uuids.append(_child_sheet_uuid(parent_path, part))
        parent_path = _join_sheet_path(parent_path, part)
    return "/" + "/".join(uuids)


def _symbol_property_points(
    symbol_x: float,
    symbol_y: float,
    symbol_info: SymbolInfo | None,
    *,
    ref: str | None = None,
) -> tuple[Point, Point, Point, Literal["left", "right"]]:
    local_min_x, _local_max_x = _symbol_horizontal_extent(symbol_info)
    local_min_y, local_max_y = _symbol_vertical_extent(symbol_info)
    if _is_vertical_two_pin_symbol(symbol_info):
        if ref is not None and _symbol_prefix(ref) in {"L", "FB"}:
            text_x = _snap_grid(symbol_x + local_min_x)
            reference = Point(
                x=text_x,
                y=_snap_grid(symbol_y - local_max_y - SCHEMATIC_GRID * 2),
            )
            value = Point(x=text_x, y=_snap_grid(reference.y + SCHEMATIC_GRID))
            footprint = Point(x=text_x, y=_snap_grid(value.y + SCHEMATIC_GRID))
            return reference, value, footprint, "left"
        graphic_extent = _symbol_graphic_horizontal_extent(symbol_info)
        body_min_x = graphic_extent[0] if graphic_extent is not None else local_min_x
        text_x = _snap_grid(symbol_x + body_min_x - SCHEMATIC_GRID * 2)
        reference = Point(x=text_x, y=_snap_half_grid(symbol_y - SCHEMATIC_HALF_GRID))
        value = Point(x=text_x, y=_snap_half_grid(symbol_y + SCHEMATIC_HALF_GRID))
        footprint = Point(x=text_x, y=_snap_grid(value.y + SCHEMATIC_GRID))
        return reference, value, footprint, "right"

    text_x = _snap_grid(symbol_x + local_min_x)
    reference = Point(x=text_x, y=_snap_grid(symbol_y - local_max_y - SCHEMATIC_GRID * 2))
    value = Point(x=text_x, y=_snap_grid(symbol_y - local_min_y + SCHEMATIC_GRID * 2))
    footprint = Point(x=text_x, y=_snap_grid(value.y + 2.54))
    return reference, value, footprint, "left"


def _snap_half_grid(value: float) -> float:
    return round(round(value / SCHEMATIC_HALF_GRID) * SCHEMATIC_HALF_GRID, 2)


def _symbol_property(
    name: str,
    value: str,
    point: Point,
    *,
    justify: Literal["left", "right"] = "left",
    hidden: bool = False,
) -> PlacedProperty:
    return PlacedProperty(
        name=name,
        value=value,
        at=(point.x, point.y),
        justify=justify,
        hidden=hidden,
    )


def _symbol_placement(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    lib_id: str,
    value: str,
    footprint: str,
    fields: dict[str, str],
    unit: int,
    position: Point,
    indexed_symbol: SymbolInfo | None,
    *,
    in_bom: bool = True,
    on_board: bool = True,
    rotation: int = 0,
) -> PlacedSymbol:
    x = position.x
    y = position.y
    unit_suffix = "" if unit == 1 else f":unit{unit}"
    unit_symbol_info = _unit_symbol_info(indexed_symbol, unit)
    reference_point, value_point, footprint_point, property_justify = _symbol_property_points(
        x,
        y,
        unit_symbol_info,
        ref=ref,
    )
    properties = [
        _symbol_property("Reference", ref, reference_point, justify=property_justify),
        _symbol_property("Value", value, value_point, justify=property_justify),
        _symbol_property(
            "Footprint",
            footprint,
            footprint_point,
            justify=property_justify,
            hidden=True,
        ),
    ]
    field_y = footprint_point.y
    for field_name, field_value in sorted(fields.items()):
        if field_name in {"Reference", "Value", "Footprint"}:
            continue
        field_y = _snap_grid(field_y + 2.54)
        properties.append(
            _symbol_property(
                field_name,
                field_value,
                Point(x=footprint_point.x, y=field_y),
                justify=property_justify,
                hidden=True,
            )
        )
    pins: list[PlacedSymbolPin] = []
    if indexed_symbol is not None:
        seen_pins: set[str] = set()
        for pin in indexed_symbol.pins:
            if pin.unit not in {0, unit} or pin.number in seen_pins:
                continue
            seen_pins.add(pin.number)
            pin_uuid = stable_uuid(f"{sheet_path}/{ref}:{unit}:{pin.number}")
            pins.append(PlacedSymbolPin(number=pin.number, uuid=pin_uuid))
    return PlacedSymbol(
        lib_id=lib_id,
        at=(x, y),
        unit=unit,
        uuid=stable_uuid(sheet_path + "/" + ref + unit_suffix),
        project_name=project.name,
        sheet_instance_path=_sheet_instance_path(sheet_path),
        reference=ref,
        properties=tuple(properties),
        pins=tuple(pins),
        in_bom=in_bom,
        on_board=on_board,
        rotation=rotation,
    )


def _power_flag_reference(sheet_path: str, net_name: str, index: int) -> str:
    source = stable_uuid(f"{sheet_path}:{net_name}:power-flag:{index}")
    number = int(source.replace("-", "")[:8], 16)
    return f"#FLG{number % 1_000_000:06d}"


def _power_flag_symbol(
    project: ResolvedProject,
    sheet_path: str,
    net_name: str,
    index: int,
    position: Point,
) -> PlacedSymbol:
    ref = _power_flag_reference(sheet_path, net_name, index)
    key = f"{sheet_path}:{net_name}:power-flag:{index}"
    return PlacedSymbol(
        lib_id=POWER_FLAG_LIB_ID,
        at=(position.x, position.y),
        unit=1,
        uuid=stable_uuid(key),
        project_name=project.name,
        sheet_instance_path=_sheet_instance_path(sheet_path),
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


def _page_number(project: ResolvedProject, sheet_path: str) -> str:
    return str(sorted(project.source.sheets).index(sheet_path) + 1)


def _sheet_pin_shape(direction: PinDirection) -> str:
    if direction in {"power_in", "power_out"}:
        return "passive"
    return direction


def _split_sheet_ports(
    ports: list[tuple[str, PinDirection]],
) -> tuple[list[tuple[str, PinDirection]], list[tuple[str, PinDirection]]]:
    if len(ports) <= 1:
        return ports, []

    left: list[tuple[str, PinDirection]] = []
    right: list[tuple[str, PinDirection]] = []
    for port in ports:
        direction = port[1]
        if direction in {"output", "power_out"}:
            right.append(port)
        elif direction in {"input", "power_in"}:
            left.append(port)
        elif len(left) <= len(right):
            left.append(port)
        else:
            right.append(port)
    return left, right


def _sheet_box_height(left_count: int, right_count: int) -> float:
    return max(SHEET_MIN_HEIGHT, 12.7 + max(left_count, right_count) * SHEET_PIN_STEP)


def _child_sheet_layouts(project: ResolvedProject, sheet_path: str) -> dict[str, SheetBox]:
    sheet = project.source.sheets[sheet_path]
    if not sheet.child_instances:
        return {}

    grid_mode = sheet_path == "/" and not sheet.symbols
    column_xs = [25.4, 165.1, 304.8] if grid_mode else [SHEET_X]
    sheet_width = ROOT_SHEET_WIDTH if grid_mode else SHEET_WIDTH
    column_bottoms = [SHEET_Y for _column in column_xs]
    layouts: dict[str, SheetBox] = {}
    for child_name, child in sorted(sheet.child_instances.items()):
        child_sheet = project.source.sheets[child.target_path]
        left_ports, right_ports = _split_sheet_ports(sorted(child_sheet.interface.items()))
        height = _sheet_box_height(len(left_ports), len(right_ports))
        column = min(range(len(column_xs)), key=lambda index: column_bottoms[index])
        x = column_xs[column]
        y = column_bottoms[column]
        layouts[child_name] = SheetBox(
            x=x,
            y=y,
            width=sheet_width,
            height=height,
            left_ports=left_ports,
            right_ports=right_ports,
        )
        column_bottoms[column] += height + SHEET_ROW_MARGIN
    return layouts


def _embedded_symbol_definition(symbol: SymbolInfo) -> list[Any] | None:
    if symbol.definition is None:
        return None
    expr: list[Any] = deepcopy(symbol.definition)
    expr[1] = symbol.lib_id
    return expr


def _sheet_port_point(sheet_origin: tuple[float, float], port_index: int) -> PinPoint:
    x, sheet_y = sheet_origin
    y = sheet_y + SHEET_PIN_Y_OFFSET + port_index * SHEET_PIN_STEP
    return PinPoint(x=x, y=y, label_x=x - WIRE_STUB, label_y=y)


def _hierarchical_label_point(port_index: int) -> PinPoint:
    x = 25.4
    y = 25.4 + port_index * 7.62
    return PinPoint(x=x, y=y, label_x=x, label_y=y)


def _no_connect_lines(x: float, y: float, key: str) -> list[PlacedItem]:
    return [PlacedNoConnect(at=(x, y), uuid=stable_uuid(key))]


def _hierarchical_label_lines(
    name: str,
    direction: PinDirection,
    x: float,
    y: float,
    key: str,
) -> list[PlacedItem]:
    return [
        PlacedHierarchicalLabel(
            name=name,
            shape=_sheet_pin_shape(direction),
            at=(x, y),
            uuid=stable_uuid(key),
        )
    ]


def _pins_for_endpoint(symbol: SymbolInfo, endpoint: Endpoint) -> list[SymbolPin]:
    pin_name = endpoint.pin_name or ""
    matches = [pin for pin in symbol.pins if pin.name == pin_name or pin.number == pin_name]
    if endpoint.pin_number is not None:
        return [pin for pin in matches if pin.number == endpoint.pin_number]
    if endpoint.all_matching:
        return matches
    return matches[:1] if len(matches) == 1 else []


def _sheet_symbol_orientations(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[tuple[str, int], SymbolOrientation]:
    sheet = project.source.sheets[sheet_path]
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return {}

    endpoints_by_symbol: dict[tuple[str, int], list[tuple[str, SymbolPin]]] = {}
    for net_name, endpoints in sorted(resolved_sheet.nets.items()):
        for endpoint in endpoints:
            if endpoint.kind is not EndpointKind.SYMBOL_PIN:
                continue
            ref = endpoint.ref or ""
            pin_number = endpoint.pin_number or ""
            symbol_decl = sheet.symbols.get(ref)
            if symbol_decl is None:
                continue
            symbol_info = project.symbol_library.get(symbol_decl.lib)
            if symbol_info is None or not _is_two_pin_symbol(symbol_info):
                continue
            pin = _pin_by_number(symbol_info, pin_number)
            if pin is None or pin.at is None:
                continue
            unit = pin.unit if pin.unit != 0 else 1
            endpoints_by_symbol.setdefault((ref, unit), []).append((net_name, pin))

    orientations: dict[tuple[str, int], SymbolOrientation] = {}
    for key, symbol_endpoints in endpoints_by_symbol.items():
        pins_by_number = {pin.number: (net_name, pin) for net_name, pin in symbol_endpoints}
        if not all(
            pin.electrical_type == "passive" for _net_name, pin in pins_by_number.values()
        ):
            continue
        ground_pins = [
            pin
            for net_name, pin in pins_by_number.values()
            if _is_groundish_net(net_name)
        ]
        other_pins = [
            pin
            for net_name, pin in pins_by_number.values()
            if not _is_groundish_net(net_name)
        ]
        if len(ground_pins) != 1 or not other_pins:
            continue
        ground_pin = ground_pins[0]
        orientation = _ground_down_two_pin_orientation(ground_pin, other_pins[0])
        if orientation == 0:
            continue
        orientations[key] = SymbolOrientation(
            rotation=orientation,
            preserve_pin_number=other_pins[0].number,
        )
    _apply_passive_continuation_orientations(project, sheet_path, orientations)
    _apply_anchor_support_branch_orientations(project, sheet_path, orientations)
    return orientations


def _apply_passive_continuation_orientations(
    project: ResolvedProject,
    sheet_path: str,
    orientations: dict[tuple[str, int], SymbolOrientation],
) -> None:
    sheet = project.source.sheets[sheet_path]
    for continuation in _passive_continuation_placements(project, sheet_path):
        source_symbol_decl = sheet.symbols.get(continuation.source_ref)
        target_symbol_decl = sheet.symbols.get(continuation.target_ref)
        if source_symbol_decl is None or target_symbol_decl is None:
            continue
        source_symbol_info = project.symbol_library.get(source_symbol_decl.lib)
        target_symbol_info = project.symbol_library.get(target_symbol_decl.lib)
        if source_symbol_info is None or target_symbol_info is None:
            continue
        source_other_pin = _other_two_pin(source_symbol_info, continuation.source_pin)
        target_other_pin = _other_two_pin(target_symbol_info, continuation.target_pin)
        if source_other_pin is None or target_other_pin is None:
            continue
        if (
            source_other_pin.electrical_type != "passive"
            or continuation.source_pin.electrical_type != "passive"
            or target_other_pin.electrical_type != "passive"
            or continuation.target_pin.electrical_type != "passive"
        ):
            continue

        source_rotation = _pin_below_pin_orientation(
            lower_pin=continuation.source_pin,
            upper_pin=source_other_pin,
        )
        target_rotation = _pin_below_pin_orientation(
            lower_pin=target_other_pin,
            upper_pin=continuation.target_pin,
        )
        if source_rotation != 0:
            orientations[
                (
                    continuation.source_ref,
                    continuation.source_pin.unit if continuation.source_pin.unit != 0 else 1,
                )
            ] = SymbolOrientation(
                rotation=source_rotation,
                preserve_pin_number=source_other_pin.number,
            )
        if target_rotation != 0:
            orientations[
                (
                    continuation.target_ref,
                    continuation.target_pin.unit if continuation.target_pin.unit != 0 else 1,
                )
            ] = SymbolOrientation(
                rotation=target_rotation,
                preserve_pin_number=continuation.target_pin.number,
            )


def _apply_anchor_support_branch_orientations(
    project: ResolvedProject,
    sheet_path: str,
    orientations: dict[tuple[str, int], SymbolOrientation],
) -> None:
    sheet = project.source.sheets[sheet_path]
    _assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )
    for refs in _anchor_support_branch_groups(project, sheet_path).values():
        for ref in refs:
            ref_pin = assigned_ref_pin.get(ref)
            anchor_pin = assigned_anchor_pin.get(ref)
            symbol_decl = sheet.symbols.get(ref)
            if ref_pin is None or anchor_pin is None or symbol_decl is None:
                continue
            symbol_info = project.symbol_library.get(symbol_decl.lib)
            if symbol_info is None or not _is_two_pin_symbol(symbol_info):
                continue
            other_pin = _other_two_pin(symbol_info, ref_pin)
            if other_pin is None:
                continue
            orientation = _pin_below_pin_orientation(lower_pin=other_pin, upper_pin=ref_pin)
            unit = ref_pin.unit if ref_pin.unit != 0 else 1
            orientations[(ref, unit)] = SymbolOrientation(
                rotation=orientation,
                preserve_pin_number=ref_pin.number,
            )


def _anchor_support_branch_groups(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[tuple[str, str, str], tuple[str, ...]]:
    sheet = project.source.sheets[sheet_path]
    assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )
    tap_stack_refs = build_sheet_circuit_motifs(project, sheet_path).tap_stack_refs()
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for ref, anchor in sorted(assigned_to_anchor.items()):
        if ref in tap_stack_refs:
            continue
        anchor_pin = assigned_anchor_pin.get(ref)
        ref_pin = assigned_ref_pin.get(ref)
        symbol_decl = sheet.symbols.get(ref)
        if anchor_pin is None or ref_pin is None or symbol_decl is None:
            continue
        if ref_pin.electrical_type != "passive":
            continue
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        if symbol_info is None or not _is_two_pin_symbol(symbol_info):
            continue
        other_pin = _other_two_pin(symbol_info, ref_pin)
        if other_pin is None or other_pin.electrical_type != "passive":
            continue
        grouped.setdefault((anchor, anchor_pin.name, anchor_pin.number), []).append(ref)
    return {
        key: tuple(
            sorted(
                refs,
                key=lambda ref: _anchor_support_branch_sort_key(project, sheet_path, ref),
            )
        )
        for key, refs in grouped.items()
        if len(refs) >= 2
    }


def _anchor_support_branch_sort_key(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
) -> tuple[int, str]:
    sheet = project.source.sheets[sheet_path]
    resolved_sheet = project.sheets.get(sheet_path)
    if resolved_sheet is None:
        return (2, ref)
    symbol_decl = sheet.symbols.get(ref)
    if symbol_decl is None:
        return (2, ref)
    symbol_info = project.symbol_library.get(symbol_decl.lib)
    _assigned_to_anchor, _assigned_anchor_pin, assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )
    ref_pin = assigned_ref_pin.get(ref)
    if symbol_info is None or ref_pin is None:
        return (2, ref)
    other_pin = _other_two_pin(symbol_info, ref_pin)
    if other_pin is None:
        return (2, ref)
    for net_name, endpoints in resolved_sheet.nets.items():
        if any(
            endpoint.kind is EndpointKind.SYMBOL_PIN
            and endpoint.ref == ref
            and endpoint.pin_number == other_pin.number
            for endpoint in endpoints
        ):
            if _is_groundish_net(net_name):
                return (1, ref)
            return (0, ref)
    return (2, ref)


def _other_two_pin(symbol_info: SymbolInfo, pin: SymbolPin) -> SymbolPin | None:
    pins = [
        candidate
        for candidate in symbol_info.pins
        if candidate.at is not None and candidate.unit in {0, pin.unit, 1}
    ]
    if len({candidate.number for candidate in pins}) != 2:
        return None
    return next((candidate for candidate in pins if candidate.number != pin.number), None)


def _pin_below_pin_orientation(*, lower_pin: SymbolPin, upper_pin: SymbolPin) -> int:
    candidates: list[tuple[float, float, int]] = []
    for rotation in (0, 90, 180, 270):
        lower_point = _symbol_pin_point(0, 0, lower_pin, symbol_rotation=rotation)
        upper_point = _symbol_pin_point(0, 0, upper_pin, symbol_rotation=rotation)
        if lower_point.y <= upper_point.y:
            continue
        candidates.append(
            (
                abs(lower_point.x - upper_point.x),
                abs(lower_point.y - upper_point.y),
                rotation,
            )
        )
    if not candidates:
        return 0
    return min(candidates)[2]


def _ground_down_two_pin_orientation(ground_pin: SymbolPin, other_pin: SymbolPin) -> int:
    return _pin_below_pin_orientation(lower_pin=ground_pin, upper_pin=other_pin)


def _pin_point_with_orientation(
    position: Point,
    pin: SymbolPin,
    symbol_info: SymbolInfo | None,
    orientation: SymbolOrientation | None,
) -> PinPoint:
    return _symbol_pin_point(
        position.x,
        position.y,
        pin,
        symbol_info=symbol_info,
        symbol_rotation=orientation.rotation if orientation is not None else 0,
    )


def _oriented_sheet_symbol_positions(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    orientations: dict[tuple[str, int], SymbolOrientation],
) -> dict[tuple[str, int], Point]:
    if not orientations:
        return positions
    sheet = project.source.sheets[sheet_path]
    oriented_positions = dict(positions)
    for (ref, unit), orientation in orientations.items():
        if orientation.rotation == 0 or orientation.preserve_pin_number is None:
            continue
        position = oriented_positions.get((ref, unit)) or oriented_positions.get((ref, 1))
        symbol_decl = sheet.symbols.get(ref)
        if position is None or symbol_decl is None:
            continue
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        if symbol_info is None:
            continue
        preserve_pin = _pin_by_number(symbol_info, orientation.preserve_pin_number)
        if preserve_pin is None:
            continue
        default_point = _symbol_pin_point(
            position.x,
            position.y,
            preserve_pin,
            symbol_info=symbol_info,
        )
        oriented_point = _symbol_pin_point(
            position.x,
            position.y,
            preserve_pin,
            symbol_info=symbol_info,
            symbol_rotation=orientation.rotation,
        )
        oriented_positions[(ref, unit)] = Point(
            x=_snap_half_grid(position.x + default_point.x - oriented_point.x),
            y=_snap_half_grid(position.y + default_point.y - oriented_point.y),
        )
    continuation_aligned = _align_passive_continuation_areas(
        project,
        sheet_path,
        oriented_positions,
        orientations,
    )
    return _align_anchor_support_branch_areas(
        project,
        sheet_path,
        continuation_aligned,
        orientations,
    )


def _align_passive_continuation_areas(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    orientations: dict[tuple[str, int], SymbolOrientation],
) -> dict[tuple[str, int], Point]:
    sheet = project.source.sheets[sheet_path]
    aligned = dict(positions)
    for continuation in _passive_continuation_placements(project, sheet_path):
        source_key = (
            continuation.source_ref,
            continuation.source_pin.unit if continuation.source_pin.unit != 0 else 1,
        )
        target_key = (
            continuation.target_ref,
            continuation.target_pin.unit if continuation.target_pin.unit != 0 else 1,
        )
        source_orientation = orientations.get(source_key)
        target_orientation = orientations.get(target_key)
        if source_orientation is None or target_orientation is None:
            continue
        source_position = aligned.get(source_key)
        target_position = aligned.get(target_key)
        if source_position is None or target_position is None:
            continue
        source_symbol_decl = sheet.symbols.get(continuation.source_ref)
        target_symbol_decl = sheet.symbols.get(continuation.target_ref)
        if source_symbol_decl is None or target_symbol_decl is None:
            continue
        source_symbol_info = _unit_symbol_info(
            project.symbol_library.get(source_symbol_decl.lib),
            source_key[1],
        )
        target_symbol_info = _unit_symbol_info(
            project.symbol_library.get(target_symbol_decl.lib),
            target_key[1],
        )
        source_point = _pin_point_with_orientation(
            source_position,
            continuation.source_pin,
            source_symbol_info,
            source_orientation,
        )
        target_point = _pin_point_with_orientation(
            target_position,
            continuation.target_pin,
            target_symbol_info,
            target_orientation,
        )
        desired_target_pin = Point(
            x=source_point.x,
            y=_snap_grid(source_point.y + 7.62),
        )
        candidate = Point(
            x=_snap_half_grid(target_position.x + desired_target_pin.x - target_point.x),
            y=_snap_half_grid(target_position.y + desired_target_pin.y - target_point.y),
        )
        aligned[target_key] = _clamp_oriented_symbol_position(
            project,
            sheet_path,
            continuation.target_ref,
            target_key[1],
            candidate,
            target_orientation.rotation,
        )
    return aligned


def _align_anchor_support_branch_areas(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    orientations: dict[tuple[str, int], SymbolOrientation],
) -> dict[tuple[str, int], Point]:
    sheet = project.source.sheets[sheet_path]
    assigned_to_anchor, assigned_anchor_pin, assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )
    branch_groups = _anchor_support_branch_groups(project, sheet_path)
    if not branch_groups:
        return positions

    aligned = dict(positions)
    for (_anchor, _pin_name, _pin_number), refs in branch_groups.items():
        first_ref = refs[0]
        anchor_ref = assigned_to_anchor.get(first_ref)
        anchor_pin = assigned_anchor_pin.get(first_ref)
        if anchor_ref is None or anchor_pin is None:
            continue
        anchor_symbol_decl = sheet.symbols.get(anchor_ref)
        anchor_position = aligned.get((anchor_ref, anchor_pin.unit)) or aligned.get(
            (anchor_ref, 1)
        )
        if anchor_symbol_decl is None or anchor_position is None:
            continue
        anchor_symbol_info = _unit_symbol_info(
            project.symbol_library.get(anchor_symbol_decl.lib),
            anchor_pin.unit,
        )
        if anchor_symbol_info is None:
            continue
        anchor_point = _symbol_pin_point(
            anchor_position.x,
            anchor_position.y,
            anchor_pin,
            symbol_info=anchor_symbol_info,
        )
        direction = -1 if anchor_point.label_x < anchor_point.x else 1
        existing_rects = [
            rect
            for (placed_ref, unit), position in aligned.items()
            if placed_ref not in set(refs)
            for rect in _symbol_readability_rects_at(
                project,
                sheet_path,
                placed_ref,
                unit,
                position,
            )
        ]
        candidate_group = _anchor_support_branch_candidate_group(
            project,
            sheet_path,
            refs,
            anchor_point,
            assigned_ref_pin,
            orientations,
            aligned,
            direction,
            existing_rects,
        )
        aligned.update(candidate_group)
    return aligned


def _anchor_support_branch_candidate_group(
    project: ResolvedProject,
    sheet_path: str,
    refs: tuple[str, ...],
    anchor_point: PinPoint,
    assigned_ref_pin: dict[str, SymbolPin],
    orientations: dict[tuple[str, int], SymbolOrientation],
    positions: dict[tuple[str, int], Point],
    direction: int,
    existing_rects: list[Rect],
) -> dict[tuple[str, int], Point]:
    for base_offset in (30.48, 43.18, 55.88, 68.58, 17.78):
        for spacing in (15.24, 17.78, 20.32):
            candidate_positions, candidate_rects = _anchor_support_branch_candidates(
                project,
                sheet_path,
                refs,
                anchor_point,
                assigned_ref_pin,
                orientations,
                positions,
                direction,
                base_offset,
                spacing,
            )
            if not candidate_positions:
                continue
            if _rects_overlap_any(candidate_rects, existing_rects):
                continue
            return candidate_positions
    fallback_positions, _fallback_rects = _anchor_support_branch_candidates(
        project,
        sheet_path,
        refs,
        anchor_point,
        assigned_ref_pin,
        orientations,
        positions,
        direction,
        30.48,
        15.24,
    )
    return fallback_positions


def _anchor_support_branch_candidates(
    project: ResolvedProject,
    sheet_path: str,
    refs: tuple[str, ...],
    anchor_point: PinPoint,
    assigned_ref_pin: dict[str, SymbolPin],
    orientations: dict[tuple[str, int], SymbolOrientation],
    positions: dict[tuple[str, int], Point],
    direction: int,
    base_offset: float,
    spacing: float,
) -> tuple[dict[tuple[str, int], Point], list[Rect]]:
    sheet = project.source.sheets[sheet_path]
    candidate_positions: dict[tuple[str, int], Point] = {}
    candidate_rects: list[Rect] = []
    for index, ref in enumerate(refs):
        ref_pin = assigned_ref_pin.get(ref)
        symbol_decl = sheet.symbols.get(ref)
        if ref_pin is None or symbol_decl is None:
            return ({}, [])
        unit = ref_pin.unit if ref_pin.unit != 0 else 1
        position = positions.get((ref, unit)) or positions.get((ref, 1))
        if position is None:
            return ({}, [])
        symbol_info = _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
        orientation = orientations.get((ref, unit), SymbolOrientation(rotation=0))
        ref_point = _pin_point_with_orientation(position, ref_pin, symbol_info, orientation)
        desired_ref_point = Point(
            x=_snap_grid(anchor_point.label_x + direction * (base_offset + index * spacing)),
            y=anchor_point.y,
        )
        candidate = Point(
            x=_snap_half_grid(position.x + desired_ref_point.x - ref_point.x),
            y=_snap_half_grid(position.y + desired_ref_point.y - ref_point.y),
        )
        candidate = _clamp_oriented_symbol_position(
            project,
            sheet_path,
            ref,
            unit,
            candidate,
            orientation.rotation,
        )
        rects = _symbol_readability_rects_at(project, sheet_path, ref, unit, candidate)
        if _rects_overlap_any(rects, candidate_rects):
            return ({}, [])
        candidate_positions[(ref, unit)] = candidate
        candidate_rects.extend(rects)
    return (candidate_positions, candidate_rects)


def _clamp_oriented_symbol_position(
    project: ResolvedProject,
    sheet_path: str,
    ref: str,
    unit: int,
    position: Point,
    rotation: int,
) -> Point:
    sheet = project.source.sheets[sheet_path]
    symbol_decl = sheet.symbols.get(ref)
    symbol_info = (
        _unit_symbol_info(project.symbol_library.get(symbol_decl.lib), unit)
        if symbol_decl is not None
        else None
    )
    min_x, max_x, min_y, max_y = _symbol_layout_bounds(project, sheet_path)
    if symbol_info is None:
        return Point(
            x=_snap_half_grid(_clamp(position.x, min_x, max_x)),
            y=_snap_half_grid(_clamp(position.y, min_y, max_y)),
        )
    points = [
        _symbol_pin_point(
            position.x,
            position.y,
            pin,
            symbol_info=symbol_info,
            symbol_rotation=rotation,
        )
        for pin in symbol_info.pins
        if pin.at is not None and pin.unit in {0, unit}
    ]
    if not points:
        return Point(
            x=_snap_half_grid(_clamp(position.x, min_x, max_x)),
            y=_snap_half_grid(_clamp(position.y, min_y, max_y)),
        )
    left = min(point.x for point in points)
    right = max(point.x for point in points)
    top = min(point.y for point in points)
    bottom = max(point.y for point in points)
    dx = _bounded_shift(0.0, min_x - left, max_x - right)
    dy = _bounded_shift(0.0, min_y - top, max_y - bottom)
    return Point(x=_snap_half_grid(position.x + dx), y=_snap_half_grid(position.y + dy))


def _no_connect_pin_points(
    project: ResolvedProject,
    sheet_path: str,
    positions: dict[tuple[str, int], Point],
    orientations: dict[tuple[str, int], SymbolOrientation],
) -> list[tuple[str, int, SymbolPin, PinPoint]]:
    sheet = project.source.sheets[sheet_path]
    pin_points: list[tuple[str, int, SymbolPin, PinPoint]] = []
    for index, endpoint_text in enumerate(sheet.no_connects):
        no_connect_endpoint = parse_endpoint(endpoint_text)
        if no_connect_endpoint.kind is not EndpointKind.SYMBOL_PIN:
            continue
        ref = no_connect_endpoint.ref or ""
        symbol_decl = sheet.symbols.get(ref)
        if symbol_decl is None:
            continue
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        if symbol_info is None:
            continue
        for pin in _pins_for_endpoint(symbol_info, no_connect_endpoint):
            pin_position = positions.get((ref, pin.unit)) or positions.get((ref, 1))
            if pin_position is None:
                continue
            point = _symbol_pin_point(
                pin_position.x,
                pin_position.y,
                pin,
                symbol_info=symbol_info,
                symbol_rotation=orientations.get(
                    (ref, pin.unit if pin.unit != 0 else 1),
                    SymbolOrientation(rotation=0),
                ).rotation,
            )
            pin_points.append((endpoint_text, index, pin, point))
    return pin_points


def _build_placed_sheet(project: ResolvedProject, sheet_path: str) -> PlacedSheet:
    sheet = project.source.sheets[sheet_path]
    page_width, _page_height = _paper_dimensions(project, sheet_path)
    uses_low_interface_local_layout = _is_low_interface_local_circuit(project, sheet_path)
    lib_symbols: list[list[Any]] = []
    for lib_id in sorted({symbol.lib for symbol in sheet.symbols.values()}):
        symbol_info = project.symbol_library.get(lib_id)
        if symbol_info is None:
            continue
        definition = _embedded_symbol_definition(symbol_info)
        if definition is not None:
            lib_symbols.append(definition)
    if sheet.power_flags:
        lib_symbols.append(_power_flag_symbol_definition())

    items: list[PlacedItem] = []
    positions = _layout_sheet_symbols(project, sheet_path)
    symbol_orientations = _sheet_symbol_orientations(project, sheet_path)
    positions = _oriented_sheet_symbol_positions(
        project,
        sheet_path,
        positions,
        symbol_orientations,
    )
    assigned_to_anchor, _assigned_anchor_pin, _assigned_ref_pin = _symbol_anchor_assignments(
        project,
        sheet_path,
    )
    no_connect_pin_points = _no_connect_pin_points(
        project,
        sheet_path,
        positions,
        symbol_orientations,
    )
    for ref, symbol in sorted(sheet.symbols.items()):
        indexed_symbol = project.symbol_library.get(symbol.lib)
        for unit in _symbol_units(symbol.units, indexed_symbol):
            position = positions[(ref, unit)]
            items.append(
                _symbol_placement(
                    project,
                    sheet_path,
                    ref,
                    symbol.lib,
                    symbol.value or ref,
                    symbol.footprint or "",
                    symbol.fields,
                    unit,
                    position,
                    indexed_symbol,
                    rotation=symbol_orientations.get(
                        (ref, unit),
                        SymbolOrientation(rotation=0),
                    ).rotation,
                )
            )
    child_layouts = _child_sheet_layouts(project, sheet_path)
    child_port_points: dict[str, dict[str, PinPoint]] = {}
    for child_name, child in sorted(sheet.child_instances.items()):
        layout = child_layouts[child_name]
        child_port_points[child_name] = {}
        sheet_file = _sheet_filename(project.name, child.target_path).as_posix()
        sheet_file_y = layout.y + layout.height + 2.54
        sheet_pins: list[PlacedSheetPin] = []
        for index, (port_name, direction) in enumerate(layout.left_ports):
            y = layout.y + SHEET_PIN_Y_OFFSET + index * SHEET_PIN_STEP
            pin_uuid = stable_uuid(f"{sheet_path}/{child_name}:{port_name}:pin")
            child_port_points[child_name][port_name] = PinPoint(
                x=layout.x,
                y=y,
                label_x=_snap_grid(layout.x - WIRE_STUB),
                label_y=y,
            )
            sheet_pins.append(
                PlacedSheetPin(
                    name=port_name,
                    shape=_sheet_pin_shape(direction),
                    at=(layout.x, y),
                    rotation=180,
                    uuid=pin_uuid,
                )
            )
        for index, (port_name, direction) in enumerate(layout.right_ports):
            y = layout.y + SHEET_PIN_Y_OFFSET + index * SHEET_PIN_STEP
            x = layout.x + layout.width
            pin_uuid = stable_uuid(f"{sheet_path}/{child_name}:{port_name}:pin")
            child_port_points[child_name][port_name] = PinPoint(
                x=x,
                y=y,
                label_x=_snap_grid(x + WIRE_STUB),
                label_y=y,
            )
            sheet_pins.append(
                PlacedSheetPin(
                    name=port_name,
                    shape=_sheet_pin_shape(direction),
                    at=(x, y),
                    rotation=0,
                    uuid=pin_uuid,
                )
            )
        items.append(
            PlacedSheetBlock(
                at=(layout.x, layout.y),
                size=(layout.width, layout.height),
                uuid=_child_sheet_uuid(sheet_path, child_name),
                sheet_name=child_name,
                sheet_file=sheet_file,
                sheet_name_at=(layout.x, layout.y - 2.54),
                sheet_file_at=(layout.x, sheet_file_y),
                pins=tuple(sheet_pins),
                project_name=project.name,
                sheet_instance_path=_sheet_instance_path(sheet_path),
                page=_page_number(project, sheet_path),
            )
        )
    resolved_sheet = project.sheets.get(sheet_path)
    interface_label_points: dict[str, PinPoint] = {}
    if resolved_sheet is not None:
        net_points_by_name: dict[str, list[tuple[str, PinPoint]]] = {}
        for net_name, endpoints in sorted(resolved_sheet.nets.items()):
            net_points: list[tuple[str, PinPoint]] = []
            for endpoint in endpoints:
                if endpoint.kind is EndpointKind.SYMBOL_PIN:
                    ref = endpoint.ref or ""
                    symbol_decl = sheet.symbols.get(ref)
                    if symbol_decl is None:
                        continue
                    symbol_info = project.symbol_library.get(symbol_decl.lib)
                    if symbol_info is None:
                        continue
                    resolved_pin = _pin_by_number(symbol_info, endpoint.pin_number or "")
                    if resolved_pin is None:
                        continue
                    pin_position = positions.get((ref, resolved_pin.unit)) or positions.get(
                        (ref, 1)
                    )
                    if pin_position is None:
                        continue
                    point = _sheet_symbol_pin_point(
                        ref,
                        pin_position.x,
                        pin_position.y,
                        resolved_pin,
                        net_name=net_name,
                        symbol_info=symbol_info,
                        symbol_rotation=symbol_orientations.get(
                            (ref, resolved_pin.unit if resolved_pin.unit != 0 else 1),
                            SymbolOrientation(rotation=0),
                        ).rotation,
                        positions=positions,
                        assigned_to_anchor=assigned_to_anchor,
                    )
                    net_points.append((endpoint.text, point))
                elif endpoint.kind is EndpointKind.SHEET_PORT and endpoint.child_sheet:
                    child_instance = sheet.child_instances.get(endpoint.child_sheet)
                    if child_instance is None or endpoint.port is None:
                        continue
                    child_sheet = project.source.sheets[child_instance.target_path]
                    ports = sorted(child_sheet.interface)
                    if endpoint.port not in ports:
                        continue
                    sheet_point = child_port_points.get(endpoint.child_sheet, {}).get(endpoint.port)
                    if sheet_point is None:
                        continue
                    net_points.append((endpoint.text, sheet_point))
            net_points_by_name[net_name] = net_points
        local_label_prefix = _sheet_local_label_prefix(
            [
                net_name
                for net_name in net_points_by_name
                if net_name not in sheet.interface
            ]
        )
        power_flag_positions = _power_flag_positions(project, sheet_path, net_points_by_name)
        for index, (net_name, position) in enumerate(power_flag_positions.items()):
            key = f"{sheet_path}:{net_name}:power-flag:{index}"
            label_x = _snap_grid(position.x + PIN_LABEL_STUB)
            if page_width is not None and label_x > page_width - SYMBOL_MARGIN_X:
                label_x = _snap_grid(position.x - PIN_LABEL_STUB)
            power_flag_items: list[PlacedItem] = [
                _power_flag_symbol(project, sheet_path, net_name, index, position)
            ]
            power_flag_items.extend(
                _wire_lines(
                    position.x,
                    position.y,
                    label_x,
                    position.y,
                    key + ":hidden-label-wire",
                )
            )
            power_flag_items.extend(
                _label_lines(
                    _display_net_label(
                        net_name,
                        compact_local_labels=uses_low_interface_local_layout,
                        local_label_prefix=local_label_prefix,
                    ),
                    label_x,
                    position.y,
                    key + ":hidden-label",
                    hidden=True,
                )
            )
            items.extend(power_flag_items)
        routed_nets = route_sheet_nets(
            project,
            SheetNetRoutingConfig(
                sheet_path=sheet_path,
                page_width=page_width,
                uses_low_interface_local_layout=uses_low_interface_local_layout,
                local_label_prefix=local_label_prefix,
                blocked_coordinates=frozenset(
                    _coordinate(point.x, point.y)
                    for _endpoint_text, _index, _pin, point in no_connect_pin_points
                ),
            ),
            net_points_by_name,
            existing_items=tuple(items),
        )
        items.extend(routed_nets.items)
        interface_label_points = dict(routed_nets.interface_label_points)
    for endpoint_text, index, pin, point in no_connect_pin_points:
        if pin.electrical_type == "no_connect":
            continue
        key = f"{sheet_path}:{endpoint_text}:{index}:{pin.number}"
        items.extend(_no_connect_lines(point.x, point.y, key))
    for index, (port_name, direction) in enumerate(sorted(sheet.interface.items())):
        if port_name in interface_label_points:
            net_point = interface_label_points[port_name]
            items.extend(
                _point_stub_lines(
                    net_point,
                    f"{sheet_path}:{port_name}:hierarchical-label-wire",
                )
            )
            point = PinPoint(
                x=net_point.label_x,
                y=net_point.label_y,
                label_x=net_point.label_x,
                label_y=net_point.label_y,
            )
        else:
            point = _hierarchical_label_point(index)
        items.extend(
            _hierarchical_label_lines(
                port_name,
                direction,
                point.x,
                point.y,
                f"{sheet_path}:{port_name}:hierarchical-label",
            )
        )
    return PlacedSheet(
        path=sheet_path,
        filename=_sheet_filename(project.name, sheet_path),
        uuid=stable_uuid(sheet_path),
        paper=_paper_size(project, sheet_path),
        lib_symbols=tuple(lib_symbols),
        items=tuple(items),
        instance_path=_sheet_instance_path(sheet_path),
        page=_page_number(project, sheet_path),
    )


def _sheet_symbol_pin_point(
    ref: str,
    symbol_x: float,
    symbol_y: float,
    pin: SymbolPin,
    *,
    net_name: str,
    symbol_info: SymbolInfo,
    symbol_rotation: int,
    positions: dict[tuple[str, int], Point],
    assigned_to_anchor: dict[str, str],
) -> PinPoint:
    point = _symbol_pin_point(
        symbol_x,
        symbol_y,
        pin,
        symbol_info=symbol_info,
        symbol_rotation=symbol_rotation,
    )
    if _is_two_pin_symbol(symbol_info) and _is_groundish_net(net_name):
        return PinPoint(
            x=point.x,
            y=point.y,
            label_x=point.x,
            label_y=_snap_grid(point.y + PIN_LABEL_STUB),
        )
    if not _is_vertical_two_pin_symbol(symbol_info):
        return point

    anchor = assigned_to_anchor.get(ref)
    if anchor is None:
        return point
    if anchor.startswith("Module"):
        return point

    ref_position = positions.get((ref, pin.unit)) or positions.get((ref, 1))
    anchor_position = positions.get((anchor, 1))
    if ref_position is None or anchor_position is None:
        return point
    if ref_position.x < anchor_position.x:
        return PinPoint(
            x=point.x,
            y=point.y,
            label_x=_snap_grid(point.x - PIN_LABEL_STUB),
            label_y=point.y,
        )
    if ref_position.x > anchor_position.x:
        return PinPoint(
            x=point.x,
            y=point.y,
            label_x=_snap_grid(point.x + PIN_LABEL_STUB),
            label_y=point.y,
        )
    return point


def build_placed_project(project: ResolvedProject) -> PlacedProject:
    sheets: list[PlacedSheet] = []
    for sheet_path in sorted(project.source.sheets):
        sheets.append(_build_placed_sheet(project, sheet_path))
    return normalize_placed_project(PlacedProject(name=project.name, sheets=tuple(sheets)))


def write_project(
    project: ResolvedProject,
    output_dir: Path,
    symbol_libraries: dict[str, Path] | None = None,
    footprint_libraries: dict[str, Path] | None = None,
) -> None:
    placed_project = build_placed_project(project)
    validate_placed_project(placed_project)
    write_placed_project(
        placed_project,
        output_dir,
        symbol_libraries=symbol_libraries,
        footprint_libraries=footprint_libraries,
    )
