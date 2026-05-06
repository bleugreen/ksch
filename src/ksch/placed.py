from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

type PlacedPoint = tuple[float, float]


@dataclass(frozen=True)
class PlacedProperty:
    name: str
    value: str
    at: PlacedPoint
    justify: Literal["left", "right"] = "left"
    hidden: bool = False


@dataclass(frozen=True)
class PlacedSymbolPin:
    number: str
    uuid: str


@dataclass(frozen=True)
class PlacedSymbol:
    lib_id: str
    at: PlacedPoint
    unit: int
    uuid: str
    project_name: str
    sheet_instance_path: str
    reference: str
    properties: tuple[PlacedProperty, ...]
    pins: tuple[PlacedSymbolPin, ...] = ()
    in_bom: bool = True
    on_board: bool = True
    exclude_from_sim: bool = False
    dnp: bool = False
    rotation: int = 0


@dataclass(frozen=True)
class PlacedSheetPin:
    name: str
    shape: str
    at: PlacedPoint
    rotation: int
    uuid: str


@dataclass(frozen=True)
class PlacedSheetBlock:
    at: PlacedPoint
    size: PlacedPoint
    uuid: str
    sheet_name: str
    sheet_file: str
    sheet_name_at: PlacedPoint
    sheet_file_at: PlacedPoint
    pins: tuple[PlacedSheetPin, ...]
    project_name: str
    sheet_instance_path: str
    page: str


@dataclass(frozen=True)
class PlacedWire:
    start: PlacedPoint
    end: PlacedPoint
    uuid: str
    nets: frozenset[str] = frozenset()
    start_terminals: frozenset[str] = frozenset()
    end_terminals: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlacedJunction:
    at: PlacedPoint
    uuid: str
    nets: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlacedLabel:
    name: str
    at: PlacedPoint
    uuid: str
    justify: Literal["left", "right"] = "left"
    hidden: bool = False
    nets: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlacedNoConnect:
    at: PlacedPoint
    uuid: str


@dataclass(frozen=True)
class PlacedHierarchicalLabel:
    name: str
    shape: str
    at: PlacedPoint
    uuid: str
    justify: Literal["left", "right"] = "left"


type PlacedItem = (
    PlacedSymbol
    | PlacedSheetBlock
    | PlacedWire
    | PlacedJunction
    | PlacedLabel
    | PlacedNoConnect
    | PlacedHierarchicalLabel
)


@dataclass(frozen=True)
class PlacedSheet:
    path: str
    filename: Path
    uuid: str
    paper: str
    lib_symbols: tuple[list[Any], ...]
    items: tuple[PlacedItem, ...]
    instance_path: str
    page: str


@dataclass(frozen=True)
class PlacedProject:
    name: str
    sheets: tuple[PlacedSheet, ...]
