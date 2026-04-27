from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ksch.kicad.sexpr import atom, load_sexpr_file


@dataclass(frozen=True)
class SymbolPin:
    name: str
    number: str
    electrical_type: str
    at: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SymbolInfo:
    lib_id: str
    name: str
    footprint: str | None
    pins: list[SymbolPin] = field(default_factory=list)


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


def _collect_pins(expr: list[Any]) -> list[SymbolPin]:
    pins: list[SymbolPin] = []
    for item in expr:
        if isinstance(item, list) and item:
            token = atom(item[0])
            if token == "pin":
                name, number, at = _find_pin_fields(item)
                pins.append(
                    SymbolPin(name=name, number=number, electrical_type=atom(item[1]), at=at)
                )
            elif token == "symbol":
                pins.extend(_collect_pins(item[1:]))
    return pins


def index_symbol_library(nickname: str, path: Path) -> SymbolLibraryIndex:
    expr = load_sexpr_file(path)
    symbols: dict[str, SymbolInfo] = {}
    for item in expr[1:]:
        if not isinstance(item, list) or not item or atom(item[0]) != "symbol":
            continue
        name = atom(item[1])
        if "_" in name and name.rsplit("_", 2)[-1].isdigit():
            continue
        lib_id = f"{nickname}:{name}"
        symbols[lib_id] = SymbolInfo(
            lib_id=lib_id,
            name=name,
            footprint=_property_value(item, "Footprint"),
            pins=_collect_pins(item),
        )
    return SymbolLibraryIndex(nickname=nickname, path=path, symbols=symbols)
