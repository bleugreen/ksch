from collections.abc import Iterable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.errors import KschError
from ksch.graph import ProjectGraph
from ksch.kicad.symbols import SymbolPin, index_symbol_library
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.model.ir import ProjectIR
from ksch.model.source import SymbolDecl
from ksch.resolver import (
    LibraryContext,
    ResolvedEndpoint,
    resolve_project,
    resolved_endpoint_key,
)
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_file

NO_CONNECT = "nc"


@dataclass(frozen=True)
class EditResult:
    schema_path: Path
    sheet_path: str
    net_name: str
    added_endpoints: tuple[str, ...]
    changed: bool


@dataclass(frozen=True)
class DisconnectResult:
    schema_path: Path
    sheet_path: str
    net_name: str
    removed_endpoints: tuple[str, ...]
    deleted_net: bool
    changed: bool


@dataclass(frozen=True)
class NoConnectResult:
    schema_path: Path
    sheet_path: str
    added_endpoints: tuple[str, ...]
    changed: bool


@dataclass(frozen=True)
class ClearNoConnectResult:
    schema_path: Path
    sheet_path: str
    removed_endpoints: tuple[str, ...]
    changed: bool


@dataclass(frozen=True)
class AddSymbolResult:
    schema_path: Path
    sheet_path: str
    ref: str
    changed: bool


@dataclass(frozen=True)
class _EndpointItem:
    endpoint: ResolvedEndpoint
    order: int

    @property
    def key(self) -> tuple[str, ...]:
        return resolved_endpoint_key(self.endpoint)


def add_symbol(
    root_schema: Path,
    *,
    sheet_path: str,
    ref: str,
    lib_id: str,
    value: str | None = None,
    footprint: str | None = None,
    fields: dict[str, str] | None = None,
) -> AddSymbolResult:
    graph = ProjectGraph.from_schema(root_schema)
    sheet = graph.sheet(sheet_path)
    if sheet is None:
        raise KschError(f"unknown sheet {sheet_path}")
    if ref in sheet.symbols:
        raise KschError(f"symbol {ref} already exists in {sheet_path}")

    candidate = graph.source.model_copy(deep=True)
    candidate.sheets[sheet_path].symbols[ref] = SymbolDecl(
        lib=lib_id,
        value=value,
        footprint=footprint,
        fields=fields or {},
    )
    _validate_project(candidate)

    data = load_yaml_file(sheet.source_path)
    if not isinstance(data, dict):
        raise KschError(f"{sheet.source_path} must contain a mapping")
    symbols = data.setdefault("symbols", {})
    if not isinstance(symbols, dict):
        raise KschError(f"{sheet.source_path}: symbols must be a mapping")
    symbol_data: dict[str, Any] = {"lib": lib_id}
    if value is not None:
        symbol_data["value"] = value
    if footprint is not None:
        symbol_data["footprint"] = footprint
    if fields:
        symbol_data["fields"] = dict(sorted(fields.items()))
    symbols[ref] = symbol_data
    sheet.source_path.write_text(_dump_schema(data, sheet.source_path), encoding="utf-8")
    return AddSymbolResult(
        schema_path=sheet.source_path,
        sheet_path=sheet_path,
        ref=ref,
        changed=True,
    )


def connect_endpoints(
    root_schema: Path,
    *,
    sheet_path: str,
    net_name: str,
    endpoints: list[str],
) -> EditResult:
    if net_name == NO_CONNECT:
        raise KschError("nc is reserved and cannot be used as a net name")
    graph = ProjectGraph.from_schema(root_schema)
    sheet = graph.sheet(sheet_path)
    if sheet is None:
        raise KschError(f"unknown sheet {sheet_path}")

    added: list[str] = []
    for endpoint in endpoints:
        existing_net = graph.net_for_endpoint(sheet_path, endpoint)
        if existing_net == net_name:
            continue
        if existing_net is not None:
            raise KschError(
                f"{endpoint} is already connected to {existing_net} in {sheet_path}"
            )
        added.append(endpoint)

    if not added:
        return EditResult(
            schema_path=sheet.source_path,
            sheet_path=sheet_path,
            net_name=net_name,
            added_endpoints=(),
            changed=False,
        )

    rewritten = _rewrite_net_endpoints(
        graph,
        sheet_path,
        net_name,
        add=endpoints,
        remove=[],
    )
    _validate_rewritten_net(graph, sheet_path, net_name, rewritten)

    candidate = _candidate_with_net(graph, sheet_path, net_name, rewritten)
    _write_sheet_connects(candidate, sheet_path)
    return EditResult(
        schema_path=sheet.source_path,
        sheet_path=sheet_path,
        net_name=net_name,
        added_endpoints=tuple(added),
        changed=True,
    )


def disconnect_endpoints(
    root_schema: Path,
    *,
    sheet_path: str,
    net_name: str,
    endpoints: list[str],
) -> DisconnectResult:
    graph = ProjectGraph.from_schema(root_schema)
    sheet = graph.sheet(sheet_path)
    if sheet is None:
        raise KschError(f"unknown sheet {sheet_path}")

    requested = tuple(dict.fromkeys(endpoints))
    for endpoint in requested:
        existing_net = graph.net_for_endpoint(sheet_path, endpoint)
        if existing_net is None:
            raise KschError(f"{endpoint} is not connected in {sheet_path}")
        if existing_net != net_name:
            raise KschError(
                f"{endpoint} is connected to {existing_net}, not {net_name} in {sheet_path}"
            )

    if not requested:
        return DisconnectResult(
            schema_path=sheet.source_path,
            sheet_path=sheet_path,
            net_name=net_name,
            removed_endpoints=(),
            deleted_net=False,
            changed=False,
        )

    rewritten = _rewrite_net_endpoints(
        graph,
        sheet_path,
        net_name,
        add=[],
        remove=list(requested),
    )
    deleted_net = not rewritten
    _validate_rewritten_net(graph, sheet_path, net_name, rewritten)

    candidate = _candidate_with_net(graph, sheet_path, net_name, rewritten)
    _write_sheet_connects(candidate, sheet_path)
    return DisconnectResult(
        schema_path=sheet.source_path,
        sheet_path=sheet_path,
        net_name=net_name,
        removed_endpoints=requested,
        deleted_net=deleted_net,
        changed=True,
    )


def add_no_connects(
    root_schema: Path,
    *,
    sheet_path: str,
    endpoints: list[str],
) -> NoConnectResult:
    graph = ProjectGraph.from_schema(root_schema)
    sheet = graph.sheet(sheet_path)
    if sheet is None:
        raise KschError(f"unknown sheet {sheet_path}")

    existing_keys = _resolved_endpoint_keys(graph, sheet_path, sheet.no_connects)
    added = tuple(
        endpoint
        for endpoint in dict.fromkeys(endpoints)
        if not _resolved_endpoint_keys(graph, sheet_path, [endpoint]) <= existing_keys
    )
    if not added:
        return NoConnectResult(
            schema_path=sheet.source_path,
            sheet_path=sheet_path,
            added_endpoints=(),
            changed=False,
        )

    rewritten = _rewrite_endpoint_expressions(
        graph,
        sheet_path,
        sheet.no_connects,
        add=list(added),
        remove=[],
    )
    candidate = graph.source.model_copy(deep=True)
    candidate.sheets[sheet_path].no_connects = rewritten
    _validate_project(candidate)

    _write_sheet_connects(candidate, sheet_path)
    return NoConnectResult(
        schema_path=sheet.source_path,
        sheet_path=sheet_path,
        added_endpoints=added,
        changed=True,
    )


def clear_no_connects(
    root_schema: Path,
    *,
    sheet_path: str,
    endpoints: list[str],
) -> ClearNoConnectResult:
    graph = ProjectGraph.from_schema(root_schema)
    sheet = graph.sheet(sheet_path)
    if sheet is None:
        raise KschError(f"unknown sheet {sheet_path}")

    requested = tuple(dict.fromkeys(endpoints))
    existing_keys = _resolved_endpoint_keys(graph, sheet_path, sheet.no_connects)
    for endpoint in requested:
        requested_keys = _resolved_endpoint_keys(graph, sheet_path, [endpoint])
        if not requested_keys <= existing_keys:
            raise KschError(f"{endpoint} is not marked no-connect in {sheet_path}")

    if not requested:
        return ClearNoConnectResult(
            schema_path=sheet.source_path,
            sheet_path=sheet_path,
            removed_endpoints=(),
            changed=False,
        )

    rewritten = _rewrite_endpoint_expressions(
        graph,
        sheet_path,
        sheet.no_connects,
        add=[],
        remove=list(requested),
    )
    candidate = graph.source.model_copy(deep=True)
    candidate.sheets[sheet_path].no_connects = rewritten
    _validate_project(candidate)

    _write_sheet_connects(candidate, sheet_path)
    return ClearNoConnectResult(
        schema_path=sheet.source_path,
        sheet_path=sheet_path,
        removed_endpoints=requested,
        changed=True,
    )


def _validate_rewritten_net(
    graph: ProjectGraph,
    sheet_path: str,
    net_name: str,
    endpoints: list[str],
) -> None:
    candidate = graph.source.model_copy(deep=True)
    sheet = candidate.sheets[sheet_path]
    if endpoints:
        sheet.nets[net_name] = endpoints
    else:
        sheet.nets.pop(net_name, None)
    _validate_project(candidate)


def _candidate_with_net(
    graph: ProjectGraph,
    sheet_path: str,
    net_name: str,
    endpoints: list[str],
) -> ProjectIR:
    candidate = graph.source.model_copy(deep=True)
    sheet = candidate.sheets[sheet_path]
    if endpoints:
        sheet.nets[net_name] = endpoints
    else:
        sheet.nets.pop(net_name, None)
    return candidate


def _rewrite_net_endpoints(
    graph: ProjectGraph,
    sheet_path: str,
    net_name: str,
    *,
    add: list[str],
    remove: list[str],
) -> list[str]:
    sheet = graph.source.sheets[sheet_path]
    existing_texts = sheet.nets.get(net_name, [])
    return _rewrite_endpoint_expressions(
        graph,
        sheet_path,
        existing_texts,
        add=add,
        remove=remove,
    )


def _rewrite_endpoint_expressions(
    graph: ProjectGraph,
    sheet_path: str,
    existing_texts: list[str],
    *,
    add: list[str],
    remove: list[str],
) -> list[str]:
    items: dict[tuple[str, ...], _EndpointItem] = {}
    order = 0
    for source_text in existing_texts:
        for resolved_endpoint in graph.resolve_endpoint(sheet_path, source_text):
            key = resolved_endpoint_key(resolved_endpoint)
            items.setdefault(key, _EndpointItem(endpoint=resolved_endpoint, order=order))
            order += 1

    for source_text in remove:
        for resolved_endpoint in graph.resolve_endpoint(sheet_path, source_text):
            items.pop(resolved_endpoint_key(resolved_endpoint), None)

    for source_text in add:
        for resolved_endpoint in graph.resolve_endpoint(sheet_path, source_text):
            key = resolved_endpoint_key(resolved_endpoint)
            items.setdefault(key, _EndpointItem(endpoint=resolved_endpoint, order=order))
            order += 1

    return _render_endpoint_items(graph, sheet_path, items.values())


def _resolved_endpoint_keys(
    graph: ProjectGraph,
    sheet_path: str,
    endpoint_texts: Iterable[str],
) -> set[tuple[str, ...]]:
    return {
        resolved_endpoint_key(endpoint)
        for endpoint_text in endpoint_texts
        for endpoint in graph.resolve_endpoint(sheet_path, endpoint_text)
    }


def _render_endpoint_items(
    graph: ProjectGraph,
    sheet_path: str,
    items: Iterable[_EndpointItem],
) -> list[str]:
    grouped: dict[tuple[str, ...], list[_EndpointItem]] = {}
    for item in items:
        grouped.setdefault(_render_group_key(item.endpoint), []).append(item)

    rendered: list[str] = []
    ordered_groups = sorted(
        grouped.values(),
        key=lambda group: min(item.order for item in group),
    )
    for group_items in ordered_groups:
        endpoint = group_items[0].endpoint
        if endpoint.kind is EndpointKind.SHEET_PORT:
            rendered.append(endpoint.text)
            continue
        rendered.extend(_render_symbol_pin_group(graph, sheet_path, group_items))
    return rendered


def _render_group_key(endpoint: ResolvedEndpoint) -> tuple[str, ...]:
    if endpoint.kind is EndpointKind.SYMBOL_PIN:
        return (
            endpoint.sheet_path,
            "symbol-pin-name",
            endpoint.ref or "",
            endpoint.pin_name or "",
        )
    return resolved_endpoint_key(endpoint)


def _render_symbol_pin_group(
    graph: ProjectGraph,
    sheet_path: str,
    group_items: list[_EndpointItem],
) -> list[str]:
    endpoint = group_items[0].endpoint
    ref = endpoint.ref or ""
    pin_name = endpoint.pin_name or ""
    sheet = graph.source.sheets[sheet_path]
    symbol_decl = sheet.symbols[ref]
    symbol = graph.symbols[symbol_decl.lib]
    matching = _matching_pins(symbol.pins, pin_name)
    selected_numbers = {item.endpoint.pin_number for item in group_items}
    matching_numbers = {pin.number for pin in matching}
    if len(matching) > 1 and selected_numbers == matching_numbers:
        return [f"{ref}.{pin_name}/all"]

    rendered: list[str] = []
    for pin in matching:
        if pin.number not in selected_numbers:
            continue
        if len(matching) == 1:
            rendered.append(f"{ref}.{pin.name}")
        else:
            rendered.append(f"{ref}.{pin.name}@{pin.number}")
    return rendered


def _matching_pins(pins: list[SymbolPin], pin_name: str) -> list[SymbolPin]:
    return [pin for pin in pins if pin.name == pin_name or pin.number == pin_name]


def _validate_project(project: ProjectIR) -> None:
    symbols = {}
    for nickname, path in project.symbol_libraries.items():
        symbols.update(index_symbol_library(nickname, path).symbols)
    resolve_project(
        project,
        LibraryContext(symbols=symbols, footprints={}),
        validate_declared_symbols=True,
    )


def _write_sheet_connects(project: ProjectIR, sheet_path: str) -> None:
    sheet = project.sheets[sheet_path]
    data = load_yaml_file(sheet.source_path)
    if not isinstance(data, dict):
        raise KschError(f"{sheet.source_path} must contain a mapping")
    data.pop("nets", None)
    data.pop("no_connects", None)

    symbols = data.get("symbols", {})
    if not isinstance(symbols, dict):
        raise KschError(f"{sheet.source_path}: symbols must be a mapping")
    for symbol in symbols.values():
        if isinstance(symbol, dict):
            symbol.pop("connects", None)

    sheets = data.get("sheets", {})
    if sheets is not None and not isinstance(sheets, dict):
        raise KschError(f"{sheet.source_path}: sheets must be a mapping")
    if isinstance(sheets, dict):
        for child in sheets.values():
            if isinstance(child, dict):
                child.pop("connects", None)

    for net_name, endpoint_texts in sheet.nets.items():
        if net_name == NO_CONNECT:
            raise KschError("nc is reserved and cannot be used as a net name")
        for endpoint_text in endpoint_texts:
            _set_connect(data, endpoint_text, net_name)
    for endpoint_text in sheet.no_connects:
        _set_connect(data, endpoint_text, NO_CONNECT)
    sheet.source_path.write_text(_dump_schema(data, sheet.source_path), encoding="utf-8")


def _set_connect(data: dict[str, Any], endpoint_text: str, net_name: str) -> None:
    endpoint = parse_endpoint(endpoint_text)
    if endpoint.kind is EndpointKind.SYMBOL_PIN:
        symbols = data.get("symbols", {})
        if not isinstance(symbols, dict):
            raise KschError("symbols must be a mapping")
        symbol = symbols.get(endpoint.ref or "")
        if not isinstance(symbol, dict):
            raise KschError(f"unknown symbol reference {endpoint.ref}")
        connects = symbol.setdefault("connects", {})
        if not isinstance(connects, dict):
            raise KschError(f"symbols.{endpoint.ref}.connects must be a mapping")
        connects[_local_symbol_selector(endpoint_text)] = net_name
        return

    if net_name == NO_CONNECT:
        raise KschError(f"sheet port endpoint cannot be marked nc: {endpoint_text}")
    sheets = data.get("sheets", {})
    if not isinstance(sheets, dict):
        raise KschError("sheets must be a mapping")
    child = sheets.get(endpoint.sheet or "")
    if not isinstance(child, dict):
        raise KschError(f"unknown child sheet {endpoint.sheet}")
    connects = child.setdefault("connects", {})
    if not isinstance(connects, dict):
        raise KschError(f"sheets.{endpoint.sheet}.connects must be a mapping")
    connects[endpoint.port or ""] = net_name


def _local_symbol_selector(endpoint_text: str) -> str:
    _ref, _sep, selector = endpoint_text.partition(".")
    if not selector:
        raise KschError(f"invalid endpoint '{endpoint_text}'")
    return selector


def _dump_schema(data: dict[str, Any], path: Path) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 100
    output = StringIO()
    yaml.dump(data, output)
    return format_schema_text(output.getvalue(), path)
