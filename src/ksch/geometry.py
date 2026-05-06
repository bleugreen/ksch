from dataclasses import dataclass
from typing import Any

from ksch.kicad.sexpr import atom
from ksch.kicad.symbols import SymbolInfo, SymbolPin

Coordinate = tuple[float, float]
WireSegment = tuple[float, float, float, float]
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class PinPoint:
    x: float
    y: float
    label_x: float
    label_y: float


def sexpr_child(expr: list[Any], token: str) -> list[Any] | None:
    for item in expr[1:]:
        if isinstance(item, list) and item and atom(item[0]) == token:
            return item
    return None


def sexpr_point(expr: list[Any], token: str) -> Coordinate | None:
    child = sexpr_child(expr, token)
    if child is None or len(child) < 3:
        return None
    return (float(atom(child[1])), float(atom(child[2])))


def symbol_pin_coordinate(
    symbol_x: float,
    symbol_y: float,
    pin: SymbolPin,
    *,
    symbol_rotation: int = 0,
) -> Coordinate:
    local_x = pin.at[0] if pin.at else 0.0
    local_y = pin.at[1] if pin.at else 0.0
    symbol_rotation = symbol_rotation % 360
    if symbol_rotation == 90:
        local_x, local_y = -local_y, local_x
    elif symbol_rotation == 180:
        local_x, local_y = -local_x, -local_y
    elif symbol_rotation == 270:
        local_x, local_y = local_y, -local_x
    return (symbol_x + local_x, symbol_y - local_y)


def symbol_graphic_points(expr: list[Any]) -> list[Coordinate]:
    if not expr:
        return []

    token = atom(expr[0])
    if token == "rectangle":
        return [
            point
            for point in (sexpr_point(expr, "start"), sexpr_point(expr, "end"))
            if point is not None
        ]
    if token == "polyline":
        pts = sexpr_child(expr, "pts")
        if pts is None:
            return []
        return [
            (float(atom(point[1])), float(atom(point[2])))
            for point in pts[1:]
            if isinstance(point, list) and len(point) >= 3 and atom(point[0]) == "xy"
        ]
    if token == "circle":
        center = sexpr_point(expr, "center")
        radius_expr = sexpr_child(expr, "radius")
        if center is None or radius_expr is None or len(radius_expr) < 2:
            return []
        radius = float(atom(radius_expr[1]))
        return [
            (center[0] - radius, center[1] - radius),
            (center[0] + radius, center[1] + radius),
        ]
    if token == "arc":
        return [
            point
            for point in (
                sexpr_point(expr, "start"),
                sexpr_point(expr, "mid"),
                sexpr_point(expr, "end"),
            )
            if point is not None
        ]

    points: list[Coordinate] = []
    for item in expr[1:]:
        if isinstance(item, list):
            points.extend(symbol_graphic_points(item))
    return points


def symbol_graphic_extent(
    symbol: SymbolInfo | None,
) -> tuple[float, float, float, float] | None:
    if symbol is None or symbol.definition is None:
        return None
    points = symbol_graphic_points(symbol.definition)
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), max(xs), min(ys), max(ys))


def symbol_graphic_horizontal_extent(symbol: SymbolInfo | None) -> tuple[float, float] | None:
    extent = symbol_graphic_extent(symbol)
    if extent is None:
        return None
    return (extent[0], extent[1])


def symbol_vertical_extent(symbol: SymbolInfo | None) -> tuple[float, float]:
    if symbol is None:
        return (-12.7, 12.7)
    ys = [pin.at[1] for pin in symbol.pins if pin.at is not None]
    graphic_extent = symbol_graphic_extent(symbol)
    if graphic_extent is not None:
        ys.extend([graphic_extent[2], graphic_extent[3]])
    if not ys:
        return (-12.7, 12.7)
    pin_ys = [pin.at[1] for pin in symbol.pins if pin.at is not None]
    if not pin_ys:
        return (min(ys), max(ys))
    return (min(min(pin_ys) - 7.62, min(ys)), max(max(pin_ys) + 7.62, max(ys)))


def symbol_horizontal_extent(symbol: SymbolInfo | None) -> tuple[float, float]:
    if symbol is None:
        return (-12.7, 12.7)
    xs = [pin.at[0] for pin in symbol.pins if pin.at is not None]
    graphic_extent = symbol_graphic_extent(symbol)
    if graphic_extent is not None:
        xs.extend([graphic_extent[0], graphic_extent[1]])
    if not xs:
        return (-12.7, 12.7)
    pin_xs = [pin.at[0] for pin in symbol.pins if pin.at is not None]
    if not pin_xs:
        return (min(xs), max(xs))
    return (min(min(pin_xs) - 12.7, min(xs)), max(max(pin_xs) + 12.7, max(xs)))


def is_vertical_two_pin_symbol(symbol_info: SymbolInfo | None) -> bool:
    if symbol_info is None:
        return False
    pins = [pin for pin in symbol_info.pins if pin.at is not None and pin.unit in {0, 1}]
    if len(pins) != 2:
        return False
    pin_xs = {round(pin.at[0], 3) for pin in pins if pin.at is not None}
    rotations = {int(pin.at[2]) % 360 for pin in pins if pin.at is not None}
    return len(pin_xs) == 1 and rotations <= {90, 270}


def is_two_pin_symbol(symbol_info: SymbolInfo | None) -> bool:
    if symbol_info is None:
        return False
    pins = [pin for pin in symbol_info.pins if pin.at is not None and pin.unit in {0, 1}]
    return len(pins) == 2


def rects_intersect(first: Rect, second: Rect) -> bool:
    first_left, first_top, first_right, first_bottom = first
    second_left, second_top, second_right, second_bottom = second
    return not (
        first_right < second_left
        or second_right < first_left
        or first_bottom < second_top
        or second_bottom < first_top
    )


def symbol_rect(
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
    *,
    margin: float = 0.0,
) -> Rect:
    local_min_x, local_max_x = symbol_horizontal_extent(symbol_info)
    local_min_y, local_max_y = symbol_vertical_extent(symbol_info)
    return (
        x + local_min_x - margin,
        y - local_max_y - margin,
        x + local_max_x + margin,
        y - local_min_y + margin,
    )


def symbol_body_rect(
    symbol_info: SymbolInfo | None,
    x: float,
    y: float,
    *,
    margin: float = 1.27,
) -> Rect:
    graphic_extent = symbol_graphic_extent(symbol_info)
    if graphic_extent is None:
        return symbol_rect(symbol_info, x, y, margin=margin)
    local_min_x, local_max_x, local_min_y, local_max_y = graphic_extent
    return (
        x + local_min_x - margin,
        y - local_max_y - margin,
        x + local_max_x + margin,
        y - local_min_y + margin,
    )


def pin_label_keepout_rects(points: list[PinPoint]) -> list[Rect]:
    rects: list[Rect] = []
    text_width = 35.56
    text_half_height = 3.81
    for point in points:
        if point.label_x > point.x:
            rects.append(
                (
                    point.x,
                    point.y - text_half_height,
                    point.label_x + text_width,
                    point.y + text_half_height,
                )
            )
        elif point.label_x < point.x:
            rects.append(
                (
                    point.label_x - text_width,
                    point.y - text_half_height,
                    point.x,
                    point.y + text_half_height,
                )
            )
        elif point.label_y > point.y:
            rects.append(
                (
                    point.x - 12.7,
                    point.y,
                    point.x + 12.7,
                    point.label_y + text_width / 2,
                )
            )
        elif point.label_y < point.y:
            rects.append(
                (
                    point.x - 12.7,
                    point.label_y - text_width / 2,
                    point.x + 12.7,
                    point.y,
                )
            )
    return rects
