import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import dump_sexpr
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.layout import Point
from ksch.model.endpoint import Endpoint, EndpointKind, parse_endpoint
from ksch.model.source import PinDirection
from ksch.resolver import ResolvedProject

UUID_NAMESPACE = uuid.UUID("7d91d76e-4e61-4c8c-a1b7-4a5f2d7d6f4b")
WIRE_STUB = 10.16
PIN_LABEL_STUB = 2.54
SHEET_X = 152.4
SHEET_Y = 50.8
SHEET_WIDTH = 50.8
ROOT_SHEET_WIDTH = 101.6
SHEET_MIN_HEIGHT = 38.1
SHEET_PIN_Y_OFFSET = 7.62
SHEET_PIN_STEP = 5.08
SHEET_ROW_MARGIN = 12.7


@dataclass(frozen=True)
class PinPoint:
    x: float
    y: float
    label_x: float
    label_y: float


@dataclass(frozen=True)
class SheetBox:
    x: float
    y: float
    width: float
    height: float
    left_ports: list[tuple[str, PinDirection]]
    right_ports: list[tuple[str, PinDirection]]


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, key))


def _q(value: str) -> str:
    return json.dumps(value)


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


def _symbol_prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _symbol_lane(ref: str) -> tuple[float, int]:
    prefix = _symbol_prefix(ref)
    if prefix in {"J", "P", "CN"}:
        return (50.8, 0)
    if prefix in {"U", "IC"} or ref.startswith("Module"):
        return (190.5, 1)
    if prefix in {"R", "C", "L", "FB", "D", "TP", "F", "Y", "Q"}:
        return (330.2, 2)
    return (469.9, 3)


def _is_anchor_ref(ref: str) -> bool:
    prefix = _symbol_prefix(ref)
    return prefix in {"J", "P", "CN", "U", "IC"} or ref.startswith("Module")


def _anchor_score(ref: str) -> tuple[int, str]:
    prefix = _symbol_prefix(ref)
    if ref.startswith("Module") or prefix in {"U", "IC"}:
        return (0, ref)
    if prefix in {"J", "P", "CN"}:
        return (1, ref)
    return (2, ref)


def _symbol_vertical_extent(symbol: SymbolInfo | None) -> tuple[float, float]:
    if symbol is None or not symbol.pins:
        return (-12.7, 12.7)
    ys = [pin.at[1] for pin in symbol.pins if pin.at is not None]
    if not ys:
        return (-12.7, 12.7)
    return (min(ys) - 7.62, max(ys) + 7.62)


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


def _layout_sheet_symbols(
    project: ResolvedProject,
    sheet_path: str,
) -> dict[tuple[str, int], Point]:
    sheet = project.source.sheets[sheet_path]
    enable_peripheral_clustering = len(sheet.symbols) <= 20
    if not enable_peripheral_clustering:
        ordered = sorted(sheet.symbols, key=lambda ref: (_symbol_lane(ref)[1], ref))
        large_lane_bottoms: dict[int, float] = {}
        large_positions: dict[tuple[str, int], Point] = {}
        margin = 15.24
        for ref in ordered:
            symbol_decl = sheet.symbols[ref]
            symbol_info = project.symbol_library.get(symbol_decl.lib)
            for unit in _symbol_units(symbol_decl.units, symbol_info):
                local_min_y, local_max_y = _symbol_vertical_extent(
                    _unit_symbol_info(symbol_info, unit)
                )
                x, lane = _symbol_lane(ref)
                top = large_lane_bottoms.get(lane, 50.8)
                y = top + local_max_y
                bottom = y - local_min_y
                large_lane_bottoms[lane] = bottom + margin
                large_positions[(ref, unit)] = Point(x=x, y=y)
        return large_positions

    resolved_sheet = project.sheets.get(sheet_path)
    anchor_refs = {ref for ref in sheet.symbols if _is_anchor_ref(ref)}
    assigned_to_anchor: dict[str, str] = {}
    if resolved_sheet is not None:
        for endpoints in resolved_sheet.nets.values():
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
            for ref in sorted(net_refs - anchor_refs):
                assigned_to_anchor.setdefault(ref, anchor)

    ordered_anchors = sorted(anchor_refs, key=lambda ref: (_symbol_lane(ref)[1], ref))
    lane_bottoms: dict[int, float] = {}
    positions: dict[tuple[str, int], Point] = {}
    margin = 20.32

    def place_symbol(ref: str, x: float, top: float) -> float:
        symbol_decl = sheet.symbols[ref]
        symbol_info = project.symbol_library.get(symbol_decl.lib)
        current_top = top
        for unit in _symbol_units(symbol_decl.units, symbol_info):
            local_min_y, local_max_y = _symbol_vertical_extent(_unit_symbol_info(symbol_info, unit))
            y = current_top + local_max_y
            bottom = y - local_min_y
            positions[(ref, unit)] = Point(x=x, y=y)
            current_top = bottom + margin
        return current_top

    for ref in ordered_anchors:
        x, lane = _symbol_lane(ref)
        top = lane_bottoms.get(lane, 50.8)
        lane_bottoms[lane] = place_symbol(ref, x, top)

    grouped_refs: dict[str, list[str]] = {}
    for ref, anchor in assigned_to_anchor.items():
        grouped_refs.setdefault(anchor, []).append(ref)
    for anchor, refs in sorted(grouped_refs.items()):
        anchor_position = positions.get((anchor, 1))
        if anchor_position is None:
            continue
        row_spacing = 22.86
        rows_per_column = 8
        for index, ref in enumerate(sorted(refs)):
            column = index // rows_per_column
            row = index % rows_per_column
            row_count = min(rows_per_column, len(refs) - column * rows_per_column)
            x = anchor_position.x + 101.6 + column * 45.72
            top_y = anchor_position.y - (row_count - 1) * row_spacing / 2
            y = top_y + row * row_spacing
            positions[(ref, 1)] = Point(x=x, y=y)

    fallback_refs = [
        ref
        for ref in sorted(sheet.symbols, key=lambda item: (_symbol_lane(item)[1], item))
        if (ref, 1) not in positions
    ]
    for ref in fallback_refs:
        x, lane = _symbol_lane(ref)
        top = lane_bottoms.get(lane, 50.8)
        lane_bottoms[lane] = place_symbol(ref, x, top)

    return positions


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


def _embedded_symbol_definition(symbol: SymbolInfo) -> str | None:
    if symbol.definition is None:
        return None
    expr: list[Any] = deepcopy(symbol.definition)
    expr[1] = symbol.lib_id
    return dump_sexpr(expr)


def _paper_size(project: ResolvedProject, sheet_path: str) -> str:
    sheet = project.source.sheets[sheet_path]
    interface_count = sum(
        len(project.source.sheets[child.target_path].interface)
        for child in sheet.child_instances.values()
    )
    if sheet_path == "/" and (len(sheet.child_instances) >= 3 and interface_count >= 40):
        return "A3"
    if len(sheet.symbols) > 60:
        return "A3"
    return "A4"


def _pin_by_number(symbol: SymbolInfo, pin_number: str) -> SymbolPin | None:
    for pin in symbol.pins:
        if pin.number == pin_number:
            return pin
    return None


def _symbol_pin_point(symbol_x: float, symbol_y: float, pin: SymbolPin) -> PinPoint:
    local_x = pin.at[0] if pin.at else 0.0
    local_y = pin.at[1] if pin.at else 0.0
    rotation = int(pin.at[2] if pin.at else 0.0) % 360
    x = symbol_x + local_x
    y = symbol_y - local_y
    if rotation == 180:
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
    return PinPoint(x=x, y=y, label_x=label_x, label_y=label_y)


def _sheet_port_point(sheet_origin: tuple[float, float], port_index: int) -> PinPoint:
    x, sheet_y = sheet_origin
    y = sheet_y + SHEET_PIN_Y_OFFSET + port_index * SHEET_PIN_STEP
    return PinPoint(x=x, y=y, label_x=x - WIRE_STUB, label_y=y)


def _hierarchical_label_point(port_index: int) -> PinPoint:
    x = 25.4
    y = 25.4 + port_index * 7.62
    return PinPoint(x=x, y=y, label_x=x, label_y=y)


def _wire_lines(start_x: float, start_y: float, end_x: float, end_y: float, key: str) -> list[str]:
    return [
        "  (wire",
        "    (pts",
        f"      (xy {start_x} {start_y}) (xy {end_x} {end_y})",
        "    )",
        "    (stroke",
        "      (width 0)",
        "      (type solid)",
        "    )",
        f"    (uuid {_q(stable_uuid(key))})",
        "  )",
    ]


def _label_lines(name: str, x: float, y: float, key: str) -> list[str]:
    return [
        f"  (label {_q(name)}",
        f"    (at {x} {y} 0)",
        "    (effects",
        "      (font",
        "        (size 1.27 1.27)",
        "      )",
        "      (justify left)",
        "    )",
        f"    (uuid {_q(stable_uuid(key))})",
        "  )",
    ]


def _point_stub_lines(point: PinPoint, key: str) -> list[str]:
    if point.x == point.label_x and point.y == point.label_y:
        return []
    return _wire_lines(point.x, point.y, point.label_x, point.label_y, key)


def _route_lines(start: PinPoint, end: PinPoint, key: str) -> list[str]:
    mid_x = start.x + 25.4
    lines = _wire_lines(start.x, start.y, mid_x, start.y, key + ":h1")
    lines.extend(_wire_lines(mid_x, start.y, mid_x, end.y, key + ":v"))
    lines.extend(_wire_lines(mid_x, end.y, end.label_x, end.y, key + ":h2"))
    return lines


def _is_powerish_net(name: str) -> bool:
    upper = name.upper()
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


def _should_emit_rail(name: str, points: list[PinPoint]) -> bool:
    return len(points) >= 3 and _is_powerish_net(name)


def _rail_side(point: PinPoint) -> str:
    return "left" if point.label_x <= point.x else "right"


def _rail_lines(name: str, points: list[PinPoint], key: str) -> list[str]:
    grouped: dict[str, list[PinPoint]] = {"left": [], "right": []}
    for point in points:
        grouped[_rail_side(point)].append(point)

    lines: list[str] = []
    for side, side_points in grouped.items():
        if not side_points:
            continue
        if len(side_points) < 3:
            for index, point in enumerate(side_points):
                point_key = f"{key}:{side}:{index}"
                lines.extend(_point_stub_lines(point, point_key + ":stub"))
                lines.extend(_label_lines(name, point.label_x, point.label_y, point_key + ":label"))
            continue

        if side == "left":
            rail_x = min(min(point.x, point.label_x) for point in side_points) - 5.08
        else:
            rail_x = max(max(point.x, point.label_x) for point in side_points) + 5.08
        rail_min_y = min(point.label_y for point in side_points)
        rail_max_y = max(point.label_y for point in side_points)
        lines.extend(_wire_lines(rail_x, rail_min_y, rail_x, rail_max_y, key + f":{side}:rail"))
        for index, point in enumerate(side_points):
            point_key = f"{key}:{side}:{index}"
            lines.extend(_point_stub_lines(point, point_key + ":stub"))
            lines.extend(
                _wire_lines(
                    point.label_x,
                    point.label_y,
                    rail_x,
                    point.label_y,
                    point_key + ":rail-join",
                )
            )
        lines.extend(_label_lines(name, rail_x, rail_min_y - 2.54, key + f":{side}:label"))
    return lines


def _net_point_lines(
    name: str,
    points: list[tuple[str, PinPoint]],
    key: str,
    *,
    allow_shared_rails: bool,
) -> list[str]:
    plain_points = [point for _endpoint_text, point in points]
    if allow_shared_rails and _should_emit_rail(name, plain_points):
        return _rail_lines(name, plain_points, key)

    lines: list[str] = []
    for index, (endpoint_text, point) in enumerate(points):
        point_key = f"{key}:{endpoint_text}:{index}"
        lines.extend(_point_stub_lines(point, point_key + ":wire"))
        lines.extend(_label_lines(name, point.label_x, point.label_y, point_key + ":label"))
    return lines


def _no_connect_lines(x: float, y: float, key: str) -> list[str]:
    return [
        "  (no_connect",
        f"    (at {x} {y})",
        f"    (uuid {_q(stable_uuid(key))})",
        "  )",
    ]


def _hierarchical_label_lines(
    name: str,
    direction: PinDirection,
    x: float,
    y: float,
    key: str,
) -> list[str]:
    return [
        f"  (hierarchical_label {_q(name)}",
        f"    (shape {_sheet_pin_shape(direction)})",
        f"    (at {x} {y} 0)",
        "    (effects",
        "      (font",
        "        (size 1.27 1.27)",
        "      )",
        "      (justify left)",
        "    )",
        f"    (uuid {_q(stable_uuid(key))})",
        "  )",
    ]


def _pins_for_endpoint(symbol: SymbolInfo, endpoint: Endpoint) -> list[SymbolPin]:
    pin_name = endpoint.pin_name or ""
    matches = [pin for pin in symbol.pins if pin.name == pin_name or pin.number == pin_name]
    if endpoint.pin_number is not None:
        return [pin for pin in matches if pin.number == endpoint.pin_number]
    if endpoint.all_matching:
        return matches
    return matches[:1] if len(matches) == 1 else []


def _write_project_file(project: ResolvedProject, output_dir: Path) -> None:
    data = {
        "board": {"design_settings": {"defaults": {}}},
        "meta": {"filename": f"{project.name}.kicad_pro", "version": 1},
        "schematic": {},
    }
    (output_dir / f"{project.name}.kicad_pro").write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_symbol_library_table(symbol_libraries: dict[str, Path], output_dir: Path) -> None:
    if not symbol_libraries:
        return
    lines = [
        "(sym_lib_table",
        "  (version 7)",
    ]
    for nickname, path in sorted(symbol_libraries.items()):
        lines.append(
            f"  (lib (name {_q(nickname)})(type \"KiCad\")"
            f"(uri {_q(str(path.resolve()))})(options \"\")(descr \"\"))"
        )
    lines.append(")")
    (output_dir / "sym-lib-table").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _schematic_text(project: ResolvedProject, sheet_path: str) -> str:
    sheet = project.source.sheets[sheet_path]
    lines = [
        "(kicad_sch",
        "  (version 20240101)",
        "  (generator \"kicad-schema\")",
        f"  (uuid {_q(stable_uuid(sheet_path))})",
        f"  (paper {_q(_paper_size(project, sheet_path))})",
        "  (lib_symbols",
    ]
    for lib_id in sorted({symbol.lib for symbol in sheet.symbols.values()}):
        symbol_info = project.symbol_library.get(lib_id)
        if symbol_info is None:
            continue
        definition = _embedded_symbol_definition(symbol_info)
        if definition is not None:
            lines.append(f"    {definition}")
    lines.append("  )")
    positions = _layout_sheet_symbols(project, sheet_path)
    for ref, symbol in sorted(sheet.symbols.items()):
        indexed_symbol = project.symbol_library.get(symbol.lib)
        for unit in _symbol_units(symbol.units, indexed_symbol):
            position = positions[(ref, unit)]
            x = position.x
            y = position.y
            unit_suffix = "" if unit == 1 else f":unit{unit}"
            lines.extend(
                [
                    "  (symbol",
                    f"    (lib_id {_q(symbol.lib)})",
                    f"    (at {x} {y} 0)",
                    f"    (unit {unit})",
                    "    (exclude_from_sim no)",
                    "    (in_bom yes)",
                    "    (on_board yes)",
                    "    (dnp no)",
                    f"    (uuid {_q(stable_uuid(sheet_path + '/' + ref + unit_suffix))})",
                    f"    (property \"Reference\" {_q(ref)} (at {x} {y - 2.54} 0))",
                    f"    (property \"Value\" {_q(symbol.value or ref)} (at {x} {y + 2.54} 0))",
                    f"    (property \"Footprint\" {_q(symbol.footprint or '')} "
                    f"(at {x} {y + 5.08} 0))",
                ]
            )
            if indexed_symbol is not None:
                seen_pins: set[str] = set()
                for pin in indexed_symbol.pins:
                    if pin.unit not in {0, unit} or pin.number in seen_pins:
                        continue
                    seen_pins.add(pin.number)
                    pin_uuid = stable_uuid(f"{sheet_path}/{ref}:{unit}:{pin.number}")
                    lines.extend(
                        [
                            f"    (pin {_q(pin.number)}",
                            f"      (uuid {_q(pin_uuid)})",
                            "    )",
                        ]
                    )
            lines.extend(
                [
                    "    (instances",
                    f"      (project {_q(project.name)}",
                    f"        (path {_q(_sheet_instance_path(sheet_path))}",
                    f"          (reference {_q(ref)})",
                    f"          (unit {unit})",
                    "        )",
                    "      )",
                    "    )",
                    "  )",
                ]
            )
    child_layouts = _child_sheet_layouts(project, sheet_path)
    child_port_points: dict[str, dict[str, PinPoint]] = {}
    for child_name, child in sorted(sheet.child_instances.items()):
        layout = child_layouts[child_name]
        child_port_points[child_name] = {}
        sheet_file = _sheet_filename(project.name, child.target_path).as_posix()
        sheet_file_y = layout.y + layout.height + 2.54
        lines.extend(
            [
                "  (sheet",
                f"    (at {layout.x} {layout.y})",
                f"    (size {layout.width} {layout.height})",
                "    (exclude_from_sim no)",
                "    (in_bom yes)",
                "    (on_board yes)",
                "    (dnp no)",
                "    (stroke (width 0.1524) (type solid) (color 0 0 0 0))",
                "    (fill (color 0 0 0 0))",
                f"    (uuid {_q(_child_sheet_uuid(sheet_path, child_name))})",
                f"    (property \"Sheetname\" {_q(child_name)} "
                f"(at {layout.x} {layout.y - 2.54} 0))",
                f"    (property \"Sheetfile\" {_q(sheet_file)} "
                f"(at {layout.x} {sheet_file_y} 0))",
            ]
        )
        for index, (port_name, direction) in enumerate(layout.left_ports):
            y = layout.y + SHEET_PIN_Y_OFFSET + index * SHEET_PIN_STEP
            pin_uuid = stable_uuid(f"{sheet_path}/{child_name}:{port_name}:pin")
            child_port_points[child_name][port_name] = PinPoint(
                x=layout.x,
                y=y,
                label_x=layout.x,
                label_y=y,
            )
            lines.extend(
                [
                    f"    (pin {_q(port_name)} {_sheet_pin_shape(direction)}",
                    f"      (at {layout.x} {y} 180)",
                    f"      (uuid {_q(pin_uuid)})",
                    "    )",
                ]
            )
        for index, (port_name, direction) in enumerate(layout.right_ports):
            y = layout.y + SHEET_PIN_Y_OFFSET + index * SHEET_PIN_STEP
            x = layout.x + layout.width
            pin_uuid = stable_uuid(f"{sheet_path}/{child_name}:{port_name}:pin")
            child_port_points[child_name][port_name] = PinPoint(
                x=x,
                y=y,
                label_x=x,
                label_y=y,
            )
            lines.extend(
                [
                    f"    (pin {_q(port_name)} {_sheet_pin_shape(direction)}",
                    f"      (at {x} {y} 0)",
                    f"      (uuid {_q(pin_uuid)})",
                    "    )",
                ]
            )
        lines.extend(
            [
                "    (instances",
                f"      (project {_q(project.name)}",
                f"        (path {_q(_sheet_instance_path(sheet_path))}",
                f"          (page {_q(_page_number(project, sheet_path))})",
                "        )",
                "      )",
                "    )",
                "  )",
            ]
        )
    resolved_sheet = project.sheets.get(sheet_path)
    net_label_points: dict[str, list[PinPoint]] = {}
    allow_shared_rails = len(sheet.symbols) <= 3 and not sheet.child_instances
    if resolved_sheet is not None:
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
                    point = _symbol_pin_point(pin_position.x, pin_position.y, resolved_pin)
                    net_points.append((endpoint.text, point))
                    net_label_points.setdefault(net_name, []).append(point)
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
                    net_label_points.setdefault(net_name, []).append(sheet_point)
            lines.extend(
                _net_point_lines(
                    net_name,
                    net_points,
                    f"{sheet_path}:{net_name}",
                    allow_shared_rails=allow_shared_rails,
                )
            )
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
            point = _symbol_pin_point(pin_position.x, pin_position.y, pin)
            key = f"{sheet_path}:{endpoint_text}:{index}:{pin.number}"
            lines.extend(_no_connect_lines(point.x, point.y, key))
    for index, (port_name, direction) in enumerate(sorted(sheet.interface.items())):
        point = _hierarchical_label_point(index)
        lines.extend(
            _hierarchical_label_lines(
                port_name,
                direction,
                point.x,
                point.y,
                f"{sheet_path}:{port_name}:hierarchical-label",
            )
        )
        if len(sheet.interface) <= 8 and port_name in net_label_points:
            lines.extend(
                _route_lines(
                    point,
                    net_label_points[port_name][0],
                    f"{sheet_path}:{port_name}:hierarchical-route",
                )
            )
    lines.extend(
        [
            "  (sheet_instances",
            f"    (path {_q(_sheet_instance_path(sheet_path))}",
            f"      (page {_q(_page_number(project, sheet_path))})",
            "    )",
            "  )",
            "  (embedded_fonts no)",
        ]
    )
    lines.append(")")
    return "\n".join(lines) + "\n"


def write_project(
    project: ResolvedProject,
    output_dir: Path,
    symbol_libraries: dict[str, Path] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_project_file(project, output_dir)
    _write_symbol_library_table(symbol_libraries or {}, output_dir)
    for sheet_path in sorted(project.source.sheets):
        target = output_dir / _sheet_filename(project.name, sheet_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_schematic_text(project, sheet_path), encoding="utf-8")
