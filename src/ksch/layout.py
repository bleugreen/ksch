from dataclasses import dataclass
from math import floor, hypot

GEOMETRY_EPSILON = 1e-6
SCHEMATIC_GRID = 2.54
PAGE_FRAME_MARGIN = 15.24
PAGE_TITLE_BLOCK_CLEARANCE = 35.56
PAGE_TITLE_BLOCK_WIDTH = 116.84
PAPER_SIZES_MM = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}


def snap_grid(value: float, grid: float = SCHEMATIC_GRID) -> float:
    return round(floor(value / grid + 0.5 + 1e-9) * grid, 2)


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
            self.right <= other.left + GEOMETRY_EPSILON
            or other.right <= self.left + GEOMETRY_EPSILON
            or self.bottom <= other.top + GEOMETRY_EPSILON
            or other.bottom <= self.top + GEOMETRY_EPSILON
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


def page_rect_for_paper(paper: str) -> Rect | None:
    size = PAPER_SIZES_MM.get(paper)
    if size is None:
        return None
    width, height = size
    return Rect(left=0.0, top=0.0, right=width, bottom=height)


def usable_page_rect_for_paper(paper: str) -> Rect | None:
    page = page_rect_for_paper(paper)
    if page is None:
        return None
    return Rect(
        left=page.left + PAGE_FRAME_MARGIN,
        top=page.top + PAGE_FRAME_MARGIN,
        right=page.right - PAGE_FRAME_MARGIN,
        bottom=page.bottom - PAGE_FRAME_MARGIN,
    )


def title_block_rect_for_paper(paper: str) -> Rect | None:
    page = page_rect_for_paper(paper)
    if page is None:
        return None
    return Rect(
        left=page.right - PAGE_FRAME_MARGIN - PAGE_TITLE_BLOCK_WIDTH,
        top=page.bottom - PAGE_FRAME_MARGIN - PAGE_TITLE_BLOCK_CLEARANCE,
        right=page.right - PAGE_FRAME_MARGIN,
        bottom=page.bottom - PAGE_FRAME_MARGIN,
    )
