from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class SymbolPin:
    name: str
    number: str
    electrical_type: str
    unit: int = 1
    at: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SymbolInfo:
    lib_id: str
    name: str
    footprint: str | None
    pins: list[SymbolPin] = field(default_factory=list)
    definition: list[Any] | None = None


@dataclass(frozen=True)
class SymbolLibraryIndex:
    nickname: str
    path: Path
    symbols: dict[str, SymbolInfo]


def _property_value(symbol: list[Any], key: str) -> str | None:
    for item in symbol:
        if (
            isinstance(item, list)
            and len(item) >= 3
            and atom(item[0]) == "property"
            and atom(item[1]) == key
        ):
            return atom(item[2])
    return None


def _extends_value(symbol: list[Any]) -> str | None:
    for item in symbol:
        if isinstance(item, list) and len(item) >= 2 and atom(item[0]) == "extends":
            return atom(item[1])
    return None


def _top_level_properties(symbol: list[Any]) -> dict[str, list[Any]]:
    properties: dict[str, list[Any]] = {}
    for item in symbol:
        if isinstance(item, list) and len(item) >= 3 and atom(item[0]) == "property":
            properties[atom(item[1])] = item
    return properties


def _rename_nested_symbol_defs(expr: list[Any], old_name: str, new_name: str) -> None:
    for item in expr:
        if not isinstance(item, list) or not item:
            continue
        if atom(item[0]) == "symbol" and len(item) >= 2:
            item_name = atom(item[1])
            if item_name.startswith(f"{old_name}_"):
                item[1] = f"{new_name}_{item_name[len(old_name) + 1 :]}"
        _rename_nested_symbol_defs(item, old_name, new_name)


def _flatten_definition(
    expr: list[Any],
    name: str,
    base: SymbolInfo | None,
) -> list[Any]:
    if base is None or base.definition is None:
        return expr

    flattened = deepcopy(base.definition)
    flattened[1] = name
    _rename_nested_symbol_defs(flattened[2:], base.name, name)

    derived_properties = _top_level_properties(expr)
    seen_properties: set[str] = set()
    for index, item in enumerate(flattened):
        if isinstance(item, list) and len(item) >= 3 and atom(item[0]) == "property":
            key = atom(item[1])
            if key in derived_properties:
                flattened[index] = deepcopy(derived_properties[key])
                seen_properties.add(key)
    for key, item in derived_properties.items():
        if key not in seen_properties:
            flattened.insert(-1, deepcopy(item))
    return flattened


def _find_pin_fields(pin_expr: list[Any]) -> tuple[str, str, tuple[float, float, float] | None]:
    name = ""
    number = ""
    at = None
    for item in pin_expr:
        if isinstance(item, list) and item:
            token = atom(item[0])
            if token == "name":
                name = atom(item[1])
            elif token == "number":
                number = atom(item[1])
            elif token == "at":
                at = (
                    float(atom(item[1])),
                    float(atom(item[2])),
                    float(atom(item[3])) if len(item) > 3 else 0.0,
                )
    return name, number, at


def _symbol_unit(name: str) -> int:
    parts = name.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1])
    return 1


def _is_nested_unit_symbol_name(name: str) -> bool:
    parts = name.rsplit("_", 2)
    return len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit()


def _collect_pins(expr: list[Any], current_unit: int = 1) -> list[SymbolPin]:
    pins: list[SymbolPin] = []
    for item in expr:
        if isinstance(item, list) and item:
            token = atom(item[0])
            if token == "pin":
                name, number, at = _find_pin_fields(item)
                pins.append(
                    SymbolPin(
                        name=name,
                        number=number,
                        electrical_type=atom(item[1]),
                        unit=current_unit,
                        at=at,
                    )
                )
            elif token == "symbol":
                unit = _symbol_unit(atom(item[1])) if len(item) > 1 else current_unit
                pins.extend(_collect_pins(item[1:], unit))
    return pins


def _merge_inherited_symbol(
    nickname: str,
    name: str,
    symbol_exprs: dict[str, list[Any]],
    resolved: dict[str, SymbolInfo],
    resolving: set[str],
) -> SymbolInfo:
    if name in resolved:
        return resolved[name]
    if name in resolving:
        raise ValueError(f"cyclic symbol inheritance in {nickname}:{name}")
    expr = symbol_exprs[name]
    resolving.add(name)

    base_name = _extends_value(expr)
    base: SymbolInfo | None = None
    if base_name:
        base_expr = symbol_exprs.get(base_name)
        if base_expr is not None:
            base = _merge_inherited_symbol(
                nickname,
                base_name,
                symbol_exprs,
                resolved,
                resolving,
            )

    lib_id = f"{nickname}:{name}"
    own_pins = _collect_pins(expr)
    pins = own_pins if own_pins else list(base.pins) if base is not None else []
    footprint = _property_value(expr, "Footprint")
    if footprint is None and base is not None:
        footprint = base.footprint
    resolved[name] = SymbolInfo(
        lib_id=lib_id,
        name=name,
        footprint=footprint,
        pins=pins,
        definition=_flatten_definition(expr, name, base),
    )
    resolving.remove(name)
    return resolved[name]


def index_symbol_library(nickname: str, path: Path) -> SymbolLibraryIndex:
    expr = load_sexpr_file(path)
    symbol_exprs: dict[str, list[Any]] = {}
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "symbol":
            continue
        name = atom(item[1])
        if _is_nested_unit_symbol_name(name):
            continue
        symbol_exprs[name] = item

    symbols: dict[str, SymbolInfo] = {}
    resolved: dict[str, SymbolInfo] = {}
    for name in symbol_exprs:
        symbol = _merge_inherited_symbol(nickname, name, symbol_exprs, resolved, set())
        symbols[symbol.lib_id] = symbol
    return SymbolLibraryIndex(nickname=nickname, path=path, symbols=symbols)
