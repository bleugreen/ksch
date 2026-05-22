import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.geometry import symbol_pin_coordinate
from ksch.kicad.libraries import parse_library_table
from ksch.kicad.sexpr import atom, load_sexpr_file
from ksch.kicad.symbols import SymbolInfo, symbol_info_from_definition
from ksch.migrate import migrate_document_to_connects
from ksch.model.source import PinDirection
from ksch.schema.formatter import format_schema_text
from ksch.verify import run_kicad_cli

type CoordinateKey = tuple[int, int]
type WireSegmentKey = tuple[CoordinateKey, CoordinateKey]


@dataclass(frozen=True)
class ImportedComponent:
    ref: str
    lib_id: str
    value: str | None
    footprint: str | None
    fields: dict[str, str]
    sheet_path: str
    sheet_file: str | None


@dataclass(frozen=True)
class ImportedPin:
    number: str
    name: str
    electrical_type: str


@dataclass(frozen=True)
class ImportedNode:
    ref: str
    pin_number: str
    pin_name: str


@dataclass(frozen=True)
class ImportedNet:
    name: str
    nodes: list[ImportedNode]


@dataclass
class SheetInfo:
    sheet_path: str
    source: Path
    title: str | None = None
    children: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportedProject:
    root_schema: Path
    generated_files: list[Path]


class _UnionFind:
    def __init__(self) -> None:
        self._parents: dict[CoordinateKey, CoordinateKey] = {}

    def add(self, item: CoordinateKey) -> None:
        self._parents.setdefault(item, item)

    def find(self, item: CoordinateKey) -> CoordinateKey:
        self.add(item)
        parent = self._parents[item]
        if parent != item:
            parent = self.find(parent)
            self._parents[item] = parent
        return parent

    def union(self, first: CoordinateKey, second: CoordinateKey) -> None:
        self._parents[self.find(second)] = self.find(first)


def import_project(root_schematic: Path, out_dir: Path) -> ImportedProject:
    root = root_schematic.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    netlist = _export_netlist(root)
    components, symbol_pins, nets = _parse_netlist(netlist)
    sheets = _read_sheet_tree(root)
    symbol_units = _read_symbol_units(sheets)
    power_flags = _read_power_flags(sheets)
    sheet_by_file = {
        info.source.resolve().name: info.sheet_path for info in sheets.values()
    }
    no_connects = _read_no_connects(sheets, components, symbol_pins)
    root_name = root.stem
    schema_by_sheet = _build_schema_documents(
        root_name=root_name,
        project_dir=root.parent,
        out_dir=out_dir,
        sheets=sheets,
        sheet_by_file=sheet_by_file,
        components=components,
        symbol_pins=symbol_pins,
        symbol_units=symbol_units,
        nets=nets,
        power_flags=power_flags,
        no_connects=no_connects,
    )
    generated: list[Path] = []
    for sheet_path, data in schema_by_sheet.items():
        target = _schema_path(out_dir, sheet_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump_schema(data, target), encoding="utf-8")
        generated.append(target)
    return ImportedProject(root_schema=_schema_path(out_dir, "/"), generated_files=generated)


def _export_netlist(root: Path) -> list[Any]:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "netlist.net"
        result = run_kicad_cli(
            [
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadsexpr",
                "--output",
                str(target),
                str(root),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return load_sexpr_file(target)


def _parse_netlist(
    expr: list[Any],
) -> tuple[dict[str, ImportedComponent], dict[str, dict[str, ImportedPin]], list[ImportedNet]]:
    components: dict[str, ImportedComponent] = {}
    symbol_pins: dict[str, dict[str, ImportedPin]] = {}
    nets: list[ImportedNet] = []
    for section in expr[1:]:
        if not isinstance(section, list) or not section:
            continue
        token = atom(section[0])
        if token == "components":
            components.update(_parse_components(section))
        elif token == "libparts":
            symbol_pins.update(_parse_libparts(section))
        elif token == "nets":
            nets.extend(_parse_nets(section))
    return components, symbol_pins, nets


def _parse_components(section: list[Any]) -> dict[str, ImportedComponent]:
    components: dict[str, ImportedComponent] = {}
    for comp in _children(section, "comp"):
        ref = _child_atom(comp, "ref") or ""
        if not ref:
            continue
        value = _child_atom(comp, "value")
        footprint = _child_atom(comp, "footprint")
        fields = _parse_fields(_first_child(comp, "fields"))
        libsource = _first_child(comp, "libsource")
        lib_id = ""
        if libsource is not None:
            lib = _child_atom(libsource, "lib") or ""
            part = _child_atom(libsource, "part") or ""
            lib_id = f"{lib}:{part}" if lib and part else part
        sheetpath = _first_child(comp, "sheetpath")
        sheet_path = _child_atom(sheetpath, "names") if sheetpath is not None else "/"
        sheet_file = _component_property(comp, "Sheetfile")
        components[ref] = ImportedComponent(
            ref=ref,
            lib_id=lib_id,
            value=value,
            footprint=footprint,
            fields=fields,
            sheet_path=sheet_path or "/",
            sheet_file=sheet_file,
        )
    return components


def _parse_fields(fields_expr: list[Any] | None) -> dict[str, str]:
    fields: dict[str, str] = {}
    if fields_expr is None:
        return fields
    for field_expr in _children(fields_expr, "field"):
        name = _child_atom(field_expr, "name")
        if name and len(field_expr) >= 3:
            fields[name] = atom(field_expr[2])
    return fields


def _parse_libparts(section: list[Any]) -> dict[str, dict[str, ImportedPin]]:
    symbols: dict[str, dict[str, ImportedPin]] = {}
    for libpart in _children(section, "libpart"):
        lib = _child_atom(libpart, "lib") or ""
        part = _child_atom(libpart, "part") or ""
        lib_id = f"{lib}:{part}" if lib and part else part
        pins: dict[str, ImportedPin] = {}
        pins_expr = _first_child(libpart, "pins")
        if pins_expr is not None:
            for pin_expr in _children(pins_expr, "pin"):
                number = _child_atom(pin_expr, "num") or ""
                name = _child_atom(pin_expr, "name") or number
                electrical_type = _child_atom(pin_expr, "type") or "passive"
                pins[number] = ImportedPin(
                    number=number,
                    name=name,
                    electrical_type=electrical_type,
                )
        symbols[lib_id] = pins
    return symbols


def _parse_nets(section: list[Any]) -> list[ImportedNet]:
    nets: list[ImportedNet] = []
    for net_expr in _children(section, "net"):
        raw_name = _child_atom(net_expr, "name") or ""
        nodes = []
        for node_expr in _children(net_expr, "node"):
            ref = _child_atom(node_expr, "ref") or ""
            pin = _child_atom(node_expr, "pin") or ""
            pin_name = _child_atom(node_expr, "pinfunction") or pin
            if ref and pin:
                nodes.append(ImportedNode(ref=ref, pin_number=pin, pin_name=pin_name))
        if nodes:
            nets.append(ImportedNet(name=_schema_net_name(raw_name), nodes=nodes))
    return nets


def _read_sheet_tree(root: Path) -> dict[str, SheetInfo]:
    sheets: dict[str, SheetInfo] = {}

    def visit(path: Path, sheet_path: str) -> None:
        expr = load_sexpr_file(path)
        title = _title_from_schematic(expr)
        info = SheetInfo(sheet_path=sheet_path, source=path.resolve(), title=title)
        sheets[sheet_path] = info
        for sheet_expr in _children(expr, "sheet"):
            name = _property_value(sheet_expr, "Sheetname")
            sheet_file = _property_value(sheet_expr, "Sheetfile")
            if not name or not sheet_file:
                continue
            child_key = _schema_identifier(name)
            child_path = _join_sheet_path(sheet_path, child_key)
            child_source = (path.parent / sheet_file).resolve()
            info.children[child_key] = child_source
            visit(child_source, child_path)

    visit(root, "/")
    return sheets


def _read_symbol_units(sheets: dict[str, SheetInfo]) -> dict[str, list[int]]:
    units: dict[str, set[int]] = defaultdict(set)
    for sheet in sheets.values():
        expr = load_sexpr_file(sheet.source)
        for symbol_expr in _children(expr, "symbol"):
            ref = _property_value(symbol_expr, "Reference")
            unit_text = _child_atom(symbol_expr, "unit")
            if not ref:
                continue
            try:
                unit = int(unit_text or "1")
            except ValueError:
                unit = 1
            units[ref].add(unit)
    return {ref: sorted(values) for ref, values in units.items()}


def _read_power_flags(sheets: dict[str, SheetInfo]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    for sheet_path, sheet in sheets.items():
        net_names = _power_flag_net_names(load_sexpr_file(sheet.source))
        if net_names:
            flags[sheet_path] = net_names
    return flags


def _read_no_connects(
    sheets: dict[str, SheetInfo],
    components: dict[str, ImportedComponent],
    symbol_pins: dict[str, dict[str, ImportedPin]],
) -> dict[str, list[str]]:
    no_connects: dict[str, list[str]] = {}
    for sheet_path, sheet in sheets.items():
        expr = load_sexpr_file(sheet.source)
        no_connect_points = {
            point for item in _children(expr, "no_connect") for point in [_at_point(item)]
            if point is not None
        }
        if not no_connect_points:
            continue

        embedded_symbols = _embedded_symbol_infos(expr)
        nodes: list[ImportedNode] = []
        for symbol_expr in _children(expr, "symbol"):
            ref = _property_value(symbol_expr, "Reference")
            lib_id = _child_atom(symbol_expr, "lib_id")
            at_expr = _first_child(symbol_expr, "at")
            if ref is None or lib_id is None or at_expr is None or len(at_expr) < 3:
                continue
            symbol_info = embedded_symbols.get(lib_id)
            if symbol_info is None:
                continue
            try:
                symbol_x = float(atom(at_expr[1]))
                symbol_y = float(atom(at_expr[2]))
                symbol_rotation = int(float(atom(at_expr[3]))) if len(at_expr) > 3 else 0
                unit = int(_child_atom(symbol_expr, "unit") or "1")
            except ValueError:
                continue

            for pin in symbol_info.pins:
                if pin.unit not in {0, unit}:
                    continue
                point = symbol_pin_coordinate(
                    symbol_x,
                    symbol_y,
                    pin,
                    symbol_rotation=symbol_rotation,
                )
                if _coordinate_key(point[0], point[1]) not in no_connect_points:
                    continue
                nodes.append(
                    ImportedNode(ref=ref, pin_number=pin.number, pin_name=pin.name)
                )
        if nodes:
            no_connects[sheet_path] = _endpoints_for_nodes(nodes, components, symbol_pins)
    return no_connects


def _embedded_symbol_infos(expr: list[Any]) -> dict[str, SymbolInfo]:
    lib_symbols = _first_child(expr, "lib_symbols")
    if lib_symbols is None:
        return {}
    return {
        lib_id: symbol_info_from_definition(lib_id, symbol_expr)
        for symbol_expr in _children(lib_symbols, "symbol")
        for lib_id in [atom(symbol_expr[1]) if len(symbol_expr) > 1 else ""]
        if lib_id
    }


def _power_flag_net_names(expr: list[Any]) -> list[str]:
    flag_points: list[CoordinateKey] = []
    label_points: dict[CoordinateKey, set[str]] = defaultdict(set)
    relevant_points: set[CoordinateKey] = set()
    wire_segments: list[WireSegmentKey] = []

    for symbol_expr in _children(expr, "symbol"):
        lib_id = _child_atom(symbol_expr, "lib_id") or ""
        point = _at_point(symbol_expr)
        if point is None or not lib_id.startswith("power:"):
            continue
        if lib_id == "power:PWR_FLAG":
            flag_points.append(point)
            relevant_points.add(point)
            continue
        value = _property_value(symbol_expr, "Value") or lib_id.split(":", 1)[1]
        label_points[point].add(_schema_net_name(value))
        relevant_points.add(point)

    for token in ("label", "global_label", "hierarchical_label"):
        for label_expr in _children(expr, token):
            if len(label_expr) < 2:
                continue
            point = _at_point(label_expr)
            if point is None:
                continue
            label_points[point].add(_schema_net_name(atom(label_expr[1])))
            relevant_points.add(point)

    for junction_expr in _children(expr, "junction"):
        point = _at_point(junction_expr)
        if point is not None:
            relevant_points.add(point)

    for wire_expr in _children(expr, "wire"):
        points_expr = _first_child(wire_expr, "pts")
        if points_expr is None:
            continue
        wire_points: list[CoordinateKey] = []
        for point_expr in points_expr[1:]:
            if not isinstance(point_expr, list):
                continue
            point = _xy_point(point_expr)
            if point is not None:
                wire_points.append(point)
        for start, end in zip(wire_points, wire_points[1:], strict=False):
            wire_segments.append((start, end))
            relevant_points.add(start)
            relevant_points.add(end)

    graph = _UnionFind()
    for point in relevant_points:
        graph.add(point)
    for start, end in wire_segments:
        graph.union(start, end)
        for point in relevant_points:
            if point not in {start, end} and _point_on_segment(point, start, end):
                graph.union(start, point)

    labels_by_root: dict[CoordinateKey, set[str]] = defaultdict(set)
    for point, labels in label_points.items():
        labels_by_root[graph.find(point)].update(labels)

    net_names: list[str] = []
    for flag_point in flag_points:
        label_names = sorted(labels_by_root.get(graph.find(flag_point), set()))
        if label_names:
            net_names.append(label_names[0])
    return sorted(set(net_names))


def _at_point(expr: list[Any]) -> CoordinateKey | None:
    at_expr = _first_child(expr, "at")
    if at_expr is None or len(at_expr) < 3:
        return None
    return _coordinate_key(float(atom(at_expr[1])), float(atom(at_expr[2])))


def _xy_point(expr: list[Any]) -> CoordinateKey | None:
    if len(expr) < 3 or atom(expr[0]) != "xy":
        return None
    return _coordinate_key(float(atom(expr[1])), float(atom(expr[2])))


def _coordinate_key(x: float, y: float) -> CoordinateKey:
    return (int(round(x * 1000)), int(round(y * 1000)))


def _point_on_segment(
    point: CoordinateKey,
    start: CoordinateKey,
    end: CoordinateKey,
) -> bool:
    x, y = point
    start_x, start_y = start
    end_x, end_y = end
    return (
        (x - start_x) * (end_y - start_y) == (y - start_y) * (end_x - start_x)
        and min(start_x, end_x) <= x <= max(start_x, end_x)
        and min(start_y, end_y) <= y <= max(start_y, end_y)
    )


def _build_schema_documents(
    *,
    root_name: str,
    project_dir: Path,
    out_dir: Path,
    sheets: dict[str, SheetInfo],
    sheet_by_file: dict[str, str],
    components: dict[str, ImportedComponent],
    symbol_pins: dict[str, dict[str, ImportedPin]],
    symbol_units: dict[str, list[int]],
    nets: list[ImportedNet],
    power_flags: dict[str, list[str]] | None = None,
    no_connects: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    component_sheet = {
        ref: sheet_by_file.get(component.sheet_file or "", component.sheet_path.rstrip("/") or "/")
        for ref, component in components.items()
    }
    cross_sheet_ports: dict[str, dict[str, PinDirection]] = defaultdict(dict)
    sheet_nets: dict[str, dict[str, list[str]]] = defaultdict(dict)
    sheet_no_connects: dict[str, list[str]] = defaultdict(list)
    for sheet_path, endpoints in (no_connects or {}).items():
        sheet_no_connects[sheet_path].extend(endpoints)
    for net in nets:
        if _is_kicad_unconnected_net(net) and len(net.nodes) == 1:
            node = net.nodes[0]
            sheet_path = component_sheet.get(node.ref, "/")
            sheet_no_connects[sheet_path].extend(
                _endpoints_for_nodes([node], components, symbol_pins)
            )
            continue

        nodes_by_sheet: dict[str, list[ImportedNode]] = defaultdict(list)
        for node in net.nodes:
            sheet_path = component_sheet.get(node.ref, "/")
            nodes_by_sheet[sheet_path].append(node)
        for sheet_path, nodes in nodes_by_sheet.items():
            endpoints = _endpoints_for_nodes(nodes, components, symbol_pins)
            if len(nodes_by_sheet) > 1 and sheet_path != "/":
                cross_sheet_ports[sheet_path][net.name] = "passive"
            sheet_nets[sheet_path][net.name] = sorted(set(endpoints))
        if len(nodes_by_sheet) > 1:
            root_endpoints = set(sheet_nets["/"].get(net.name, []))
            for sheet_path in nodes_by_sheet:
                if sheet_path == "/":
                    continue
                root_endpoints.add(f"{_sheet_instance_name(sheet_path)}.{net.name}")
            sheet_nets["/"][net.name] = sorted(root_endpoints)

    symbol_library_paths = _project_libraries(project_dir, out_dir, "sym-lib-table")
    footprint_library_paths = _project_libraries(project_dir, out_dir, "fp-lib-table")
    for sheet_path, sheet in sheets.items():
        symbols = {
            ref: _symbol_decl(component, symbol_units.get(ref))
            for ref, component in sorted(components.items())
            if component_sheet.get(ref, "/") == sheet_path and component.lib_id
        }
        if sheet_path == "/":
            data: dict[str, Any] = {
                "ksch": 1,
                "project": {"name": root_name},
            }
            libraries: dict[str, Any] = {}
            if symbol_library_paths:
                libraries["symbols"] = {"project": symbol_library_paths}
            if footprint_library_paths:
                libraries["footprints"] = {"project": footprint_library_paths}
            if libraries:
                data["libraries"] = libraries
            if sheet.children:
                data["sheets"] = {
                    name: {"source": _relative_schema_path(out_dir, child_path)}
                    for name, child_source in sorted(sheet.children.items())
                    for child_path in [_sheet_path_for_source(sheets, child_source)]
                }
        else:
            data = {
                "ksch": 1,
                "sheet": {"id": _sheet_instance_name(sheet_path)},
            }
            if sheet.title:
                data["sheet"]["title"] = sheet.title
            if cross_sheet_ports.get(sheet_path):
                data["interface"] = dict(sorted(cross_sheet_ports[sheet_path].items()))
            if sheet.children:
                data["sheets"] = {
                    name: {
                        "source": _relative_schema_path(
                            _schema_path(out_dir, sheet_path).parent,
                            child_path,
                        )
                    }
                    for name, child_source in sorted(sheet.children.items())
                    for child_path in [_sheet_path_for_source(sheets, child_source)]
                }
        if symbols:
            data["symbols"] = symbols
        if sheet_nets.get(sheet_path):
            data["nets"] = dict(sorted(sheet_nets[sheet_path].items()))
        sheet_power_flags = _canonical_power_flag_names(
            (power_flags or {}).get(sheet_path, []),
            set(sheet_nets.get(sheet_path, {})),
        )
        if sheet_power_flags:
            data["power_flags"] = sheet_power_flags
        if sheet_no_connects.get(sheet_path):
            data["no_connects"] = sorted(set(sheet_no_connects[sheet_path]))
        migrate_document_to_connects(data)
        docs[sheet_path] = data
    return docs


def _is_kicad_unconnected_net(net: ImportedNet) -> bool:
    return net.name.startswith("unconnected-(")


def _canonical_power_flag_names(candidates: list[str], sheet_net_names: set[str]) -> list[str]:
    resolved: list[str] = []
    for candidate in candidates:
        if candidate in sheet_net_names:
            resolved.append(candidate)
            continue
        suffix = f"_{candidate}"
        suffix_matches = sorted(name for name in sheet_net_names if name.endswith(suffix))
        resolved.append(suffix_matches[0] if len(suffix_matches) == 1 else candidate)
    return sorted(set(resolved))


def _symbol_decl(component: ImportedComponent, units: list[int] | None) -> dict[str, Any]:
    data: dict[str, Any] = {"lib": component.lib_id}
    if component.value:
        data["value"] = component.value
    if component.footprint:
        data["footprint"] = component.footprint
    fields = {
        key: value
        for key, value in component.fields.items()
        if key not in {"Footprint", "Datasheet", "Description"} and value
    }
    if fields:
        data["fields"] = dict(sorted(fields.items()))
    if units and units != [1]:
        data["units"] = units
    return data


def _endpoints_for_nodes(
    nodes: list[ImportedNode],
    components: dict[str, ImportedComponent],
    symbol_pins: dict[str, dict[str, ImportedPin]],
) -> list[str]:
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    pin_names: dict[tuple[str, str], str] = {}
    pin_maps: dict[str, dict[str, ImportedPin]] = {}
    for node in nodes:
        component = components[node.ref]
        pins = symbol_pins.get(component.lib_id, {})
        pin_maps[node.ref] = pins
        pin = pins.get(node.pin_number)
        pin_name = pin.name if pin is not None else node.pin_name or node.pin_number
        key = (node.ref, pin_name)
        grouped[key].add(node.pin_number)
        pin_names[key] = pin_name

    endpoints: list[str] = []
    for (ref, pin_name), numbers in sorted(grouped.items()):
        pins = pin_maps.get(ref, {})
        same_name = {candidate.number for candidate in pins.values() if candidate.name == pin_name}
        if len(same_name) > 1 and numbers == same_name:
            endpoints.append(f"{ref}.{pin_name}/all")
        elif len(same_name) <= 1:
            endpoints.append(f"{ref}.{pin_name}")
        else:
            endpoints.extend(f"{ref}.{pin_name}@{number}" for number in sorted(numbers))
    return endpoints


def _project_libraries(project_dir: Path, out_dir: Path, table_name: str) -> dict[str, str]:
    table = project_dir / table_name
    if not table.exists():
        return {}
    parsed = parse_library_table(table, {"KIPRJMOD": str(project_dir)})
    paths: dict[str, str] = {}
    for entry in sorted(parsed.entries.values(), key=lambda item: item.name):
        path = entry.path.resolve()
        try:
            paths[entry.name] = path.relative_to(out_dir).as_posix()
        except ValueError:
            paths[entry.name] = str(path)
    return paths


def _dump_schema(data: dict[str, Any], path: Path) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 100
    output = StringIO()
    yaml.dump(data, output)
    return format_schema_text(output.getvalue(), path)


def _schema_path(out_dir: Path, sheet_path: str) -> Path:
    if sheet_path == "/":
        return out_dir / "project.ksch.yaml"
    parts = [part for part in sheet_path.split("/") if part]
    return out_dir / "sheets" / Path(*parts).with_suffix(".ksch.yaml")


def _relative_schema_path(base_dir: Path, sheet_path: str) -> str:
    root_dir = base_dir if base_dir.name != "sheets" else base_dir.parent
    return _schema_path(root_dir, sheet_path).relative_to(base_dir).as_posix()


def _sheet_path_for_source(sheets: dict[str, SheetInfo], source: Path) -> str:
    resolved = source.resolve()
    for sheet_path, info in sheets.items():
        if info.source == resolved:
            return sheet_path
    raise ValueError(f"sheet source not found: {source}")


def _sheet_instance_name(sheet_path: str) -> str:
    parts = [part for part in sheet_path.split("/") if part]
    return parts[-1] if parts else "root"


def _join_sheet_path(parent_path: str, child_name: str) -> str:
    if parent_path == "/":
        return f"/{child_name}"
    return f"{parent_path}/{child_name}"


def _schema_identifier(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip()).strip("_").lower()
    if not text:
        return "sheet"
    if text[0].isdigit():
        return f"s{text}"
    return text


def _schema_net_name(value: str) -> str:
    if value.startswith("/"):
        value = value[1:]
    value = value.replace("/", "_")
    return value or "Net"


def _title_from_schematic(expr: list[Any]) -> str | None:
    title_block = _first_child(expr, "title_block")
    if title_block is None:
        return None
    return _child_atom(title_block, "title")


def _component_property(comp: list[Any], name: str) -> str | None:
    for prop in _children(comp, "property"):
        if _child_atom(prop, "name") == name:
            return _child_atom(prop, "value")
    return None


def _property_value(expr: list[Any], name: str) -> str | None:
    for prop in _children(expr, "property"):
        if len(prop) >= 3 and atom(prop[1]) == name:
            return atom(prop[2])
    return None


def _child_atom(expr: list[Any] | None, name: str) -> str | None:
    if expr is None:
        return None
    child = _first_child(expr, name)
    if child is None or len(child) < 2:
        return None
    return atom(child[1])


def _first_child(expr: list[Any], name: str) -> list[Any] | None:
    for child in _children(expr, name):
        return child
    return None


def _children(expr: list[Any], name: str) -> list[list[Any]]:
    return [
        item
        for item in expr[1:]
        if isinstance(item, list) and item and atom(item[0]) == name
    ]
