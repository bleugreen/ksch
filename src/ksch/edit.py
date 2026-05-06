from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.errors import KschError
from ksch.graph import ProjectGraph
from ksch.kicad.symbols import index_symbol_library
from ksch.model.ir import ProjectIR
from ksch.model.source import SymbolDecl
from ksch.resolver import LibraryContext, resolve_project
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_file


@dataclass(frozen=True)
class EditResult:
    schema_path: Path
    sheet_path: str
    net_name: str
    added_endpoints: tuple[str, ...]
    changed: bool


@dataclass(frozen=True)
class AddSymbolResult:
    schema_path: Path
    sheet_path: str
    ref: str
    changed: bool


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

    _validate_connected_project(graph, sheet_path, net_name, added)

    data = load_yaml_file(sheet.source_path)
    if not isinstance(data, dict):
        raise KschError(f"{sheet.source_path} must contain a mapping")
    nets = data.setdefault("nets", {})
    if not isinstance(nets, dict):
        raise KschError(f"{sheet.source_path}: nets must be a mapping")
    net_endpoints = nets.setdefault(net_name, [])
    if not isinstance(net_endpoints, list):
        raise KschError(f"{sheet.source_path}: nets.{net_name} must be a list")
    net_endpoints.extend(added)
    sheet.source_path.write_text(_dump_schema(data, sheet.source_path), encoding="utf-8")
    return EditResult(
        schema_path=sheet.source_path,
        sheet_path=sheet_path,
        net_name=net_name,
        added_endpoints=tuple(added),
        changed=True,
    )


def _validate_connected_project(
    graph: ProjectGraph,
    sheet_path: str,
    net_name: str,
    added: list[str],
) -> None:
    candidate = graph.source.model_copy(deep=True)
    sheet = candidate.sheets[sheet_path]
    sheet.nets.setdefault(net_name, [])
    sheet.nets[net_name].extend(added)
    _validate_project(candidate)


def _validate_project(project: ProjectIR) -> None:
    symbols = {}
    for nickname, path in project.symbol_libraries.items():
        symbols.update(index_symbol_library(nickname, path).symbols)
    resolve_project(
        project,
        LibraryContext(symbols=symbols, footprints={}),
        validate_declared_symbols=True,
    )


def _dump_schema(data: dict[str, Any], path: Path) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 100
    output = StringIO()
    yaml.dump(data, output)
    return format_schema_text(output.getvalue(), path)
