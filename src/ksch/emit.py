import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import dump_sexpr
from ksch.kicad.symbols import SymbolInfo, SymbolPin
from ksch.layout import layout_sheet_symbols
from ksch.model.endpoint import Endpoint, EndpointKind, parse_endpoint
from ksch.model.source import PinDirection
from ksch.resolver import ResolvedProject

UUID_NAMESPACE = uuid.UUID("7d91d76e-4e61-4c8c-a1b7-4a5f2d7d6f4b")
WIRE_STUB = 10.16
SHEET_X = 152.4
SHEET_Y = 50.8
SHEET_WIDTH = 50.8
SHEET_MIN_HEIGHT = 38.1
SHEET_PIN_Y_OFFSET = 7.62
SHEET_PIN_STEP = 5.08


@dataclass(frozen=True)
class PinPoint:
    x: float
    y: float
    label_x: float


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


def _page_number(project: ResolvedProject, sheet_path: str) -> str:
    return str(sorted(project.source.sheets).index(sheet_path) + 1)


def _sheet_pin_shape(direction: PinDirection) -> str:
    if direction in {"power_in", "power_out"}:
        return "passive"
    return direction


def _embedded_symbol_definition(symbol: SymbolInfo) -> str | None:
    if symbol.definition is None:
        return None
    expr: list[Any] = deepcopy(symbol.definition)
    expr[1] = symbol.lib_id
    return dump_sexpr(expr)


def _pin_by_number(symbol: SymbolInfo, pin_number: str) -> SymbolPin | None:
    for pin in symbol.pins:
        if pin.number == pin_number:
            return pin
    return None


def _symbol_pin_point(symbol_x: float, symbol_y: float, pin: SymbolPin) -> PinPoint:
    local_x = pin.at[0] if pin.at else 0.0
    local_y = pin.at[1] if pin.at else 0.0
    x = symbol_x + local_x
    y = symbol_y - local_y
    label_x = x - WIRE_STUB if local_x <= 0 else x + WIRE_STUB
    return PinPoint(x=x, y=y, label_x=label_x)


def _sheet_port_point(port_index: int) -> PinPoint:
    x = SHEET_X
    y = SHEET_Y + SHEET_PIN_Y_OFFSET + port_index * SHEET_PIN_STEP
    return PinPoint(x=x, y=y, label_x=x - WIRE_STUB)


def _hierarchical_label_point(port_index: int) -> PinPoint:
    x = 25.4
    y = 25.4 + port_index * 7.62
    return PinPoint(x=x, y=y, label_x=x)


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


def _route_lines(start: PinPoint, end: PinPoint, key: str) -> list[str]:
    mid_x = start.x + 25.4
    lines = _wire_lines(start.x, start.y, mid_x, start.y, key + ":h1")
    lines.extend(_wire_lines(mid_x, start.y, mid_x, end.y, key + ":v"))
    lines.extend(_wire_lines(mid_x, end.y, end.label_x, end.y, key + ":h2"))
    return lines


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
        "  (paper \"A4\")",
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
    positions = layout_sheet_symbols(list(sheet.symbols))
    for ref, symbol in sorted(sheet.symbols.items()):
        position = positions[ref]
        x = position.x
        y = position.y
        lines.extend(
            [
                "  (symbol",
                f"    (lib_id {_q(symbol.lib)})",
                f"    (at {x} {y} 0)",
                "    (unit 1)",
                "    (exclude_from_sim no)",
                "    (in_bom yes)",
                "    (on_board yes)",
                "    (dnp no)",
                f"    (uuid {_q(stable_uuid(sheet_path + '/' + ref))})",
                f"    (property \"Reference\" {_q(ref)} (at {x} {y - 2.54} 0))",
                f"    (property \"Value\" {_q(symbol.value or ref)} (at {x} {y + 2.54} 0))",
                f"    (property \"Footprint\" {_q(symbol.footprint or '')} (at {x} {y + 5.08} 0))",
            ]
        )
        indexed_symbol = project.symbol_library.get(symbol.lib)
        if indexed_symbol is not None:
            seen_pins: set[str] = set()
            for pin in indexed_symbol.pins:
                if pin.number in seen_pins:
                    continue
                seen_pins.add(pin.number)
                pin_uuid = stable_uuid(f"{sheet_path}/{ref}:{pin.number}")
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
                "          (unit 1)",
                "        )",
                "      )",
                "    )",
                "  )",
            ]
        )
    for child_name, child in sorted(sheet.child_instances.items()):
        child_sheet = project.source.sheets[child.target_path]
        child_interface = sorted(child_sheet.interface.items())
        sheet_height = max(SHEET_MIN_HEIGHT, 12.7 + len(child_interface) * SHEET_PIN_STEP)
        sheet_file = _sheet_filename(project.name, child.target_path).as_posix()
        sheet_file_y = SHEET_Y + sheet_height + 2.54
        lines.extend(
            [
                "  (sheet",
                f"    (at {SHEET_X} {SHEET_Y})",
                f"    (size {SHEET_WIDTH} {sheet_height})",
                "    (exclude_from_sim no)",
                "    (in_bom yes)",
                "    (on_board yes)",
                "    (dnp no)",
                "    (stroke (width 0.1524) (type solid) (color 0 0 0 0))",
                "    (fill (color 0 0 0 0))",
                f"    (uuid {_q(_child_sheet_uuid(sheet_path, child_name))})",
                f"    (property \"Sheetname\" {_q(child_name)} (at {SHEET_X} {SHEET_Y - 2.54} 0))",
                f"    (property \"Sheetfile\" {_q(sheet_file)} (at {SHEET_X} {sheet_file_y} 0))",
            ]
        )
        for index, (port_name, direction) in enumerate(child_interface):
            y = SHEET_Y + SHEET_PIN_Y_OFFSET + index * SHEET_PIN_STEP
            pin_uuid = stable_uuid(f"{sheet_path}/{child_name}:{port_name}:pin")
            lines.extend(
                [
                    f"    (pin {_q(port_name)} {_sheet_pin_shape(direction)}",
                    f"      (at {SHEET_X} {y} 180)",
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
    if resolved_sheet is not None:
        for net_name, endpoints in sorted(resolved_sheet.nets.items()):
            for index, endpoint in enumerate(endpoints):
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
                    position = positions[ref]
                    point = _symbol_pin_point(position.x, position.y, resolved_pin)
                    key = f"{sheet_path}:{net_name}:{endpoint.text}:{index}"
                    lines.extend(
                        _wire_lines(point.x, point.y, point.label_x, point.y, key + ":wire")
                    )
                    lines.extend(_label_lines(net_name, point.label_x, point.y, key + ":label"))
                    net_label_points.setdefault(net_name, []).append(point)
                elif endpoint.kind is EndpointKind.SHEET_PORT and endpoint.child_sheet:
                    child_instance = sheet.child_instances.get(endpoint.child_sheet)
                    if child_instance is None or endpoint.port is None:
                        continue
                    child_sheet = project.source.sheets[child_instance.target_path]
                    ports = sorted(child_sheet.interface)
                    if endpoint.port not in ports:
                        continue
                    point = _sheet_port_point(ports.index(endpoint.port))
                    key = f"{sheet_path}:{net_name}:{endpoint.text}:{index}"
                    lines.extend(
                        _wire_lines(point.x, point.y, point.label_x, point.y, key + ":wire")
                    )
                    lines.extend(_label_lines(net_name, point.label_x, point.y, key + ":label"))
                    net_label_points.setdefault(net_name, []).append(point)
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
        position = positions[ref]
        for pin in _pins_for_endpoint(symbol_info, no_connect_endpoint):
            point = _symbol_pin_point(position.x, position.y, pin)
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
        if port_name in net_label_points:
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
