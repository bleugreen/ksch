from dataclasses import dataclass
from math import hypot
from typing import Literal


@dataclass(frozen=True, order=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center(self) -> Point:
        return Point(x=(self.left + self.right) / 2, y=(self.top + self.bottom) / 2)

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(
            left=self.left + dx,
            top=self.top + dy,
            right=self.right + dx,
            bottom=self.bottom + dy,
        )

    def overlaps(self, other: "Rect") -> bool:
        return not (
            self.right <= other.left
            or other.right <= self.left
            or self.bottom <= other.top
            or other.bottom <= self.top
        )

    def gap_to(self, other: "Rect") -> float:
        if self.right <= other.left:
            gap_x = other.left - self.right
        elif other.right <= self.left:
            gap_x = self.left - other.right
        else:
            gap_x = 0.0

        if self.bottom <= other.top:
            gap_y = other.top - self.bottom
        elif other.bottom <= self.top:
            gap_y = self.top - other.bottom
        else:
            gap_y = 0.0

        if gap_x > 0 and gap_y > 0:
            return hypot(gap_x, gap_y)
        if gap_x > 0:
            return gap_x
        if gap_y > 0:
            return gap_y

        overlap_x = min(self.right, other.right) - max(self.left, other.left)
        overlap_y = min(self.bottom, other.bottom) - max(self.top, other.top)
        return -min(overlap_x, overlap_y)


@dataclass(frozen=True)
class LayoutNode:
    id: str
    center: Point
    width: float
    height: float
    movable: bool = True

    def rect(self) -> Rect:
        half_width = self.width / 2
        half_height = self.height / 2
        return Rect(
            left=self.center.x - half_width,
            top=self.center.y - half_height,
            right=self.center.x + half_width,
            bottom=self.center.y + half_height,
        )

    def moved(self, dx: float, dy: float) -> "LayoutNode":
        return LayoutNode(
            id=self.id,
            center=Point(x=self.center.x + dx, y=self.center.y + dy),
            width=self.width,
            height=self.height,
            movable=self.movable,
        )


@dataclass(frozen=True)
class ContactLink:
    source: str
    target: str
    preferred_gap: float = 7.62
    strength: float = 0.2
    axis: Literal["auto", "x", "y"] = "auto"
    direction: Literal[-1, 1] | None = None


def layout_energy(
    nodes: dict[str, LayoutNode],
    links: list[ContactLink],
    *,
    minimum_gap: float = 2.54,
) -> float:
    """Score a candidate layout; lower is better.

    This is intentionally independent from KiCad concepts. Callers translate a
    sheet into nodes and links, then use the same score before and after a solver
    pass to decide whether the candidate is actually an improvement.
    """

    energy = 0.0
    for link in links:
        source = nodes.get(link.source)
        target = nodes.get(link.target)
        if source is None or target is None:
            continue
        if link.axis == "auto":
            gap = source.rect().gap_to(target.rect())
            delta = gap - link.preferred_gap
        else:
            delta = _directed_link_delta(source, target, link)
        energy += max(link.strength, 0.01) * delta * delta

    node_ids = sorted(nodes)
    for first_index, first_id in enumerate(node_ids):
        first = nodes[first_id]
        for second_id in node_ids[first_index + 1 :]:
            second = nodes[second_id]
            gap = first.rect().gap_to(second.rect())
            if gap >= minimum_gap:
                continue
            penalty = minimum_gap - gap
            energy += 100.0 * penalty * penalty
    return energy


def _snap(value: float, grid: float) -> float:
    if grid <= 0:
        return value
    return round(round(value / grid) * grid, 2)


def _center_clamped_to_bounds(node: LayoutNode, bounds: Rect) -> Point:
    half_width = node.width / 2
    half_height = node.height / 2
    return Point(
        x=max(bounds.left + half_width, min(node.center.x, bounds.right - half_width)),
        y=max(bounds.top + half_height, min(node.center.y, bounds.bottom - half_height)),
    )


def _axis_direction(source: LayoutNode, target: LayoutNode, axis: str) -> float:
    source_value = source.center.x if axis == "x" else source.center.y
    target_value = target.center.x if axis == "x" else target.center.y
    if source_value > target_value:
        return 1.0
    if source_value < target_value:
        return -1.0
    return 1.0 if source.id > target.id else -1.0


def _link_displacement(source: LayoutNode, target: LayoutNode, link: ContactLink) -> Point:
    dx = source.center.x - target.center.x
    dy = source.center.y - target.center.y
    if link.axis == "x" or (link.axis == "auto" and abs(dx) >= abs(dy)):
        direction = link.direction if link.direction is not None else _axis_direction(
            source,
            target,
            "x",
        )
        desired = target.width / 2 + source.width / 2 + link.preferred_gap
        desired_x = target.center.x + direction * desired
        return Point(x=(desired_x - source.center.x) * link.strength, y=0.0)

    direction = link.direction if link.direction is not None else _axis_direction(
        source,
        target,
        "y",
    )
    desired = target.height / 2 + source.height / 2 + link.preferred_gap
    desired_y = target.center.y + direction * desired
    return Point(x=0.0, y=(desired_y - source.center.y) * link.strength)


def _directed_link_delta(source: LayoutNode, target: LayoutNode, link: ContactLink) -> float:
    direction = link.direction if link.direction is not None else _axis_direction(
        source,
        target,
        link.axis,
    )
    if link.axis == "x":
        desired = target.width / 2 + source.width / 2 + link.preferred_gap
        return source.center.x - (target.center.x + direction * desired)
    desired = target.height / 2 + source.height / 2 + link.preferred_gap
    return source.center.y - (target.center.y + direction * desired)


def _overlap_push(first: LayoutNode, second: LayoutNode, minimum_gap: float) -> Point | None:
    first_rect = first.rect()
    second_rect = second.rect()

    if first_rect.right <= second_rect.left:
        gap_x = second_rect.left - first_rect.right
    elif second_rect.right <= first_rect.left:
        gap_x = first_rect.left - second_rect.right
    else:
        gap_x = -(
            min(first_rect.right, second_rect.right) - max(first_rect.left, second_rect.left)
        )

    if first_rect.bottom <= second_rect.top:
        gap_y = second_rect.top - first_rect.bottom
    elif second_rect.bottom <= first_rect.top:
        gap_y = first_rect.top - second_rect.bottom
    else:
        gap_y = -(
            min(first_rect.bottom, second_rect.bottom) - max(first_rect.top, second_rect.top)
        )

    if gap_x >= minimum_gap or gap_y >= minimum_gap:
        return None

    push_x = minimum_gap - gap_x
    push_y = minimum_gap - gap_y
    center_dx = abs(first.center.x - second.center.x)
    center_dy = abs(first.center.y - second.center.y)
    if center_dx < 0.001 and center_dy >= 0.001:
        direction = _axis_direction(first, second, "y")
        return Point(x=0.0, y=direction * push_y)
    if center_dy < 0.001 and center_dx >= 0.001:
        direction = _axis_direction(first, second, "x")
        return Point(x=direction * push_x, y=0.0)

    if push_x <= push_y:
        direction = _axis_direction(first, second, "x")
        return Point(x=direction * push_x, y=0.0)

    direction = _axis_direction(first, second, "y")
    return Point(x=0.0, y=direction * push_y)


def solve_contact_layout(
    nodes: dict[str, LayoutNode],
    links: list[ContactLink],
    *,
    bounds: Rect,
    iterations: int = 80,
    grid: float = 2.54,
    minimum_gap: float = 2.54,
    max_step: float = 10.16,
) -> dict[str, LayoutNode]:
    current = {
        node_id: LayoutNode(
            id=node.id,
            center=_center_clamped_to_bounds(node, bounds) if node.movable else node.center,
            width=node.width,
            height=node.height,
            movable=node.movable,
        )
        for node_id, node in nodes.items()
    }

    for _iteration in range(iterations):
        deltas = {node_id: Point(0.0, 0.0) for node_id in current}

        for link in links:
            source = current.get(link.source)
            target = current.get(link.target)
            if source is None or target is None:
                continue
            displacement = _link_displacement(source, target, link)
            if source.movable and target.movable:
                deltas[source.id] = Point(
                    deltas[source.id].x + displacement.x / 2,
                    deltas[source.id].y + displacement.y / 2,
                )
                deltas[target.id] = Point(
                    deltas[target.id].x - displacement.x / 2,
                    deltas[target.id].y - displacement.y / 2,
                )
            elif source.movable:
                deltas[source.id] = Point(
                    deltas[source.id].x + displacement.x,
                    deltas[source.id].y + displacement.y,
                )
            elif target.movable:
                deltas[target.id] = Point(
                    deltas[target.id].x - displacement.x,
                    deltas[target.id].y - displacement.y,
                )

        node_ids = sorted(current)
        for first_index, first_id in enumerate(node_ids):
            for second_id in node_ids[first_index + 1 :]:
                push = _overlap_push(current[first_id], current[second_id], minimum_gap)
                if push is None:
                    continue
                first = current[first_id]
                second = current[second_id]
                if first.movable and second.movable:
                    deltas[first_id] = Point(
                        deltas[first_id].x + push.x / 2,
                        deltas[first_id].y + push.y / 2,
                    )
                    deltas[second_id] = Point(
                        deltas[second_id].x - push.x / 2,
                        deltas[second_id].y - push.y / 2,
                    )
                elif first.movable:
                    deltas[first_id] = Point(
                        deltas[first_id].x + push.x,
                        deltas[first_id].y + push.y,
                    )
                elif second.movable:
                    deltas[second_id] = Point(
                        deltas[second_id].x - push.x,
                        deltas[second_id].y - push.y,
                    )

        next_nodes: dict[str, LayoutNode] = {}
        for node_id, node in current.items():
            delta = deltas[node_id]
            if not node.movable:
                next_nodes[node_id] = node
                continue
            dx = max(-max_step, min(max_step, delta.x))
            dy = max(-max_step, min(max_step, delta.y))
            moved = node.moved(dx, dy)
            clamped = _center_clamped_to_bounds(moved, bounds)
            next_nodes[node_id] = LayoutNode(
                id=node.id,
                center=clamped,
                width=node.width,
                height=node.height,
                movable=node.movable,
            )
        current = next_nodes

    for _cleanup_iteration in range(20):
        moved_any = False
        deltas = {node_id: Point(0.0, 0.0) for node_id in current}
        node_ids = sorted(current)
        for first_index, first_id in enumerate(node_ids):
            for second_id in node_ids[first_index + 1 :]:
                push = _overlap_push(current[first_id], current[second_id], minimum_gap)
                if push is None:
                    continue
                first = current[first_id]
                second = current[second_id]
                if first.movable and second.movable:
                    deltas[first_id] = Point(
                        deltas[first_id].x + push.x / 2,
                        deltas[first_id].y + push.y / 2,
                    )
                    deltas[second_id] = Point(
                        deltas[second_id].x - push.x / 2,
                        deltas[second_id].y - push.y / 2,
                    )
                elif first.movable:
                    deltas[first_id] = Point(
                        deltas[first_id].x + push.x,
                        deltas[first_id].y + push.y,
                    )
                elif second.movable:
                    deltas[second_id] = Point(
                        deltas[second_id].x - push.x,
                        deltas[second_id].y - push.y,
                    )

        next_nodes = {}
        for node_id, node in current.items():
            delta = deltas[node_id]
            if not node.movable:
                next_nodes[node_id] = node
                continue
            dx = max(-max_step, min(max_step, delta.x))
            dy = max(-max_step, min(max_step, delta.y))
            moved_any = moved_any or abs(dx) > 0.001 or abs(dy) > 0.001
            moved = node.moved(dx, dy)
            clamped = _center_clamped_to_bounds(moved, bounds)
            next_nodes[node_id] = LayoutNode(
                id=node.id,
                center=clamped,
                width=node.width,
                height=node.height,
                movable=node.movable,
            )
        current = next_nodes
        if not moved_any:
            break

    return {
        node_id: LayoutNode(
            id=node.id,
            center=(
                Point(x=_snap(node.center.x, grid), y=_snap(node.center.y, grid))
                if node.movable
                else node.center
            ),
            width=node.width,
            height=node.height,
            movable=node.movable,
        )
        for node_id, node in current.items()
    }


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
