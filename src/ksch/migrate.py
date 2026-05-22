from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.errors import KschError
from ksch.model.endpoint import EndpointKind, parse_endpoint
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_file

NO_CONNECT = "nc"


def migrate_document_to_connects(data: dict[str, Any]) -> bool:
    changed = False
    nets = data.pop("nets", None)
    if nets is not None:
        if not isinstance(nets, dict):
            raise KschError("nets must be a mapping")
        for net_name, endpoints in nets.items():
            if net_name == NO_CONNECT:
                raise KschError("nc is reserved and cannot be used as a net name")
            if not isinstance(endpoints, list):
                raise KschError(f"nets.{net_name} must be a list")
            for endpoint_text in endpoints:
                if not isinstance(endpoint_text, str):
                    raise KschError(f"nets.{net_name} endpoint must be a string")
                _assign_endpoint(data, endpoint_text, str(net_name))
        changed = True

    no_connects = data.pop("no_connects", None)
    if no_connects is not None:
        if not isinstance(no_connects, list):
            raise KschError("no_connects must be a list")
        for endpoint_text in no_connects:
            if not isinstance(endpoint_text, str):
                raise KschError("no_connects endpoint must be a string")
            _assign_endpoint(data, endpoint_text, NO_CONNECT)
        changed = True
    return changed


def migrate_file_to_connects(path: Path) -> bool:
    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise KschError(f"{path} must contain a mapping")
    changed = migrate_document_to_connects(data)
    if changed:
        path.write_text(_dump_schema(data, path), encoding="utf-8")
    return changed


def migrate_project_to_connects(root_schema: Path) -> list[Path]:
    changed: list[Path] = []
    visited: set[Path] = set()

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        data = load_yaml_file(resolved)
        if not isinstance(data, dict):
            raise KschError(f"{resolved} must contain a mapping")
        sheet_sources = _sheet_sources(data)
        if migrate_document_to_connects(data):
            resolved.write_text(_dump_schema(data, resolved), encoding="utf-8")
            changed.append(resolved)
        for child_source in sheet_sources:
            visit((resolved.parent / child_source).resolve())

    visit(root_schema)
    return changed


def _assign_endpoint(data: dict[str, Any], endpoint_text: str, net_name: str) -> None:
    endpoint = parse_endpoint(endpoint_text)
    if endpoint.kind is EndpointKind.SYMBOL_PIN:
        symbols = _mapping(data.setdefault("symbols", {}), "symbols")
        symbol = _mapping(
            symbols.setdefault(endpoint.ref or "", {}),
            f"symbols.{endpoint.ref}",
        )
        connects = _mapping(
            symbol.setdefault("connects", {}),
            f"symbols.{endpoint.ref}.connects",
        )
        selector = _local_symbol_selector(endpoint_text)
        _assign_connect(connects, selector, net_name, endpoint_text)
        return

    if net_name == NO_CONNECT:
        raise KschError(f"sheet port endpoint cannot be marked nc: {endpoint_text}")
    sheets = _mapping(data.setdefault("sheets", {}), "sheets")
    sheet = _mapping(
        sheets.setdefault(endpoint.sheet or "", {}),
        f"sheets.{endpoint.sheet}",
    )
    connects = _mapping(
        sheet.setdefault("connects", {}),
        f"sheets.{endpoint.sheet}.connects",
    )
    _assign_connect(connects, endpoint.port or "", net_name, endpoint_text)


def _assign_connect(
    connects: MutableMapping[str, Any],
    selector: str,
    net_name: str,
    endpoint_text: str,
) -> None:
    existing = connects.get(selector)
    if existing is not None and existing != net_name:
        raise KschError(
            f"{endpoint_text} maps to both {existing} and {net_name}"
        )
    connects[selector] = net_name


def _local_symbol_selector(endpoint_text: str) -> str:
    _head, _sep, selector = endpoint_text.partition(".")
    if not selector:
        raise KschError(f"invalid endpoint '{endpoint_text}'")
    return selector


def _sheet_sources(data: dict[str, Any]) -> list[Path]:
    sheets = data.get("sheets", {})
    if not isinstance(sheets, dict):
        return []
    sources: list[Path] = []
    for sheet in sheets.values():
        if isinstance(sheet, dict) and isinstance(sheet.get("source"), (str, Path)):
            sources.append(Path(sheet["source"]))
    return sources


def _mapping(value: Any, path: str) -> MutableMapping[str, Any]:
    if not isinstance(value, MutableMapping):
        raise KschError(f"{path} must be a mapping")
    return value


def _dump_schema(data: dict[str, Any], path: Path) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 100
    from io import StringIO

    output = StringIO()
    yaml.dump(data, output)
    return format_schema_text(output.getvalue(), path)
