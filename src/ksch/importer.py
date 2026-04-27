import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.kicad.libraries import parse_library_table
from ksch.kicad.sexpr import atom, load_sexpr_file
from ksch.model.source import PinDirection
from ksch.schema.formatter import format_schema_text
from ksch.verify import run_kicad_cli


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


def import_project(root_schematic: Path, out_dir: Path) -> ImportedProject:
    root = root_schematic.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    netlist = _export_netlist(root)
    components, symbol_pins, nets = _parse_netlist(netlist)
    sheets = _read_sheet_tree(root)
    symbol_units = _read_symbol_units(sheets)
    sheet_by_file = {
        info.source.resolve().name: info.sheet_path for info in sheets.values()
    }
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
) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    component_sheet = {
        ref: sheet_by_file.get(component.sheet_file or "", component.sheet_path.rstrip("/") or "/")
        for ref, component in components.items()
    }
    cross_sheet_ports: dict[str, dict[str, PinDirection]] = defaultdict(dict)
    sheet_nets: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for net in nets:
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

    library_paths = _project_symbol_libraries(project_dir, out_dir)
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
            if library_paths:
                data["libraries"] = {"symbols": {"project": library_paths}}
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
        docs[sheet_path] = data
    return docs


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


def _project_symbol_libraries(project_dir: Path, out_dir: Path) -> dict[str, str]:
    table = project_dir / "sym-lib-table"
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
