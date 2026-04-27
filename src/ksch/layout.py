from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Point:
    x: float
    y: float


def _prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _lane(prefix: str) -> tuple[float, int]:
    if prefix in {"J", "P", "CN"}:
        return (38.1, 0)
    if prefix in {"U", "IC"}:
        return (101.6, 1)
    if prefix in {"R", "C", "L", "FB", "D", "TP"}:
        return (101.6, 2)
    return (165.1, 3)


def layout_sheet_symbols(refs: list[str]) -> dict[str, Point]:
    ordered = sorted(refs, key=lambda ref: (_lane(_prefix(ref))[1], ref))
    lane_counts: dict[int, int] = {}
    positions: dict[str, Point] = {}
    for ref in ordered:
        x, lane = _lane(_prefix(ref))
        index = lane_counts.get(lane, 0)
        lane_counts[lane] = index + 1
        y = 50.8 + index * 31.75
        if lane == 2:
            y += 63.5
        positions[ref] = Point(x=x, y=y)
    return positions
