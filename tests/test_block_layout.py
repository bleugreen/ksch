from pathlib import Path

from ksch.compiler import build_placed_project
from ksch.expand import load_project_ir
from ksch.geometry import symbol_pin_coordinate
from ksch.kicad.symbols import index_symbol_library
from ksch.layout import Rect
from ksch.layout_solver import _rotated_point
from ksch.placed import (
    PlacedGraphicRectangle,
    PlacedLabel,
    PlacedSheet,
    PlacedSymbol,
    PlacedText,
    PlacedWire,
)
from ksch.resolver import LibraryContext, ResolvedProject, resolve_project
from ksch.schematic_geometry import placed_items_geometry
from ksch.validation import placed_layout_report
from ksch.verify import schema_net_mates


def _resolved_can_controller_project() -> ResolvedProject:
    project = load_project_ir(Path("examples/can-controller/schematic/project.ksch.yaml"))
    symbols = index_symbol_library(
        "Can",
        Path("examples/can-controller/schematic/lib/CanController.kicad_sym"),
    )
    return resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))


def _resolved_basic_board_project() -> ResolvedProject:
    project = load_project_ir(Path("examples/basic-board/schematic/project.ksch.yaml"))
    symbols = index_symbol_library(
        "Starter",
        Path("examples/basic-board/schematic/lib/Starter.kicad_sym"),
    )
    return resolve_project(project, LibraryContext(symbols=symbols.symbols, footprints={}))


def _rect_for_frame(frame: PlacedGraphicRectangle) -> Rect:
    return Rect(
        frame.at[0],
        frame.at[1],
        frame.at[0] + frame.size[0],
        frame.at[1] + frame.size[1],
    )


def _contains(outer: Rect, inner: Rect) -> bool:
    return (
        inner.left >= outer.left - 0.01
        and inner.top >= outer.top - 0.01
        and inner.right <= outer.right + 0.01
        and inner.bottom <= outer.bottom + 0.01
    )


def _contains_point(outer: Rect, point: tuple[float, float]) -> bool:
    return (
        outer.left - 0.01 <= point[0] <= outer.right + 0.01
        and outer.top - 0.01 <= point[1] <= outer.bottom + 0.01
    )


def _can_controller_sheet() -> tuple[ResolvedProject, PlacedSheet]:
    project = _resolved_can_controller_project()
    placed = build_placed_project(project)
    return project, placed.sheets[0]


def test_declared_blocks_render_as_four_titled_frames() -> None:
    project, sheet = _can_controller_sheet()
    block_names = set(project.sheets["/"].blocks)

    frames = [item for item in sheet.items if isinstance(item, PlacedGraphicRectangle)]
    titles = [
        item.text
        for item in sheet.items
        if isinstance(item, PlacedText) and item.text in block_names
    ]

    assert len(frames) == 4
    assert set(titles) == {"CAN Controller", "CAN Transceiver", "Vehicle Conn", "12V Sense"}


def test_declared_block_members_are_inside_exactly_one_frame() -> None:
    project, sheet = _can_controller_sheet()
    frames = [
        _rect_for_frame(item)
        for item in sheet.items
        if isinstance(item, PlacedGraphicRectangle)
    ]
    geometry = placed_items_geometry(sheet.items, symbol_library=project.symbol_library)
    body_rect_by_uuid = {
        box.id.removesuffix(":body"): box.rect
        for box in geometry.boxes
        if box.kind == "symbol_body" and box.id.endswith(":body")
    }
    declared_refs = {ref for refs in project.sheets["/"].blocks.values() for ref in refs}

    for symbol in sheet.items:
        if not isinstance(symbol, PlacedSymbol) or symbol.reference not in declared_refs:
            continue
        body = body_rect_by_uuid[symbol.uuid]
        containing = [frame for frame in frames if _contains(frame, body)]
        assert len(containing) == 1, symbol.reference


def test_declared_block_frames_do_not_overlap() -> None:
    _project, sheet = _can_controller_sheet()
    frames = [
        _rect_for_frame(item)
        for item in sheet.items
        if isinstance(item, PlacedGraphicRectangle)
    ]

    for index, frame in enumerate(frames):
        for other in frames[index + 1 :]:
            assert not frame.overlaps(other)


def test_cross_block_nets_use_labels_instead_of_frame_crossing_wires() -> None:
    _project, sheet = _can_controller_sheet()
    frames = [
        _rect_for_frame(item)
        for item in sheet.items
        if isinstance(item, PlacedGraphicRectangle)
    ]
    can_txd_labels = [
        item
        for item in sheet.items
        if isinstance(item, PlacedLabel) and "CAN_TXD" in item.nets and item.name == "CAN_TXD"
    ]
    can_txd_wires = [
        item
        for item in sheet.items
        if isinstance(item, PlacedWire) and "CAN_TXD" in item.nets
    ]

    label_frames = {
        index
        for label in can_txd_labels
        for index, frame in enumerate(frames)
        if _contains_point(frame, label.at)
    }

    assert len(label_frames) == 2
    for wire in can_txd_wires:
        start_frames = {
            index for index, frame in enumerate(frames) if _contains_point(frame, wire.start)
        }
        end_frames = {
            index for index, frame in enumerate(frames) if _contains_point(frame, wire.end)
        }
        assert start_frames == end_frames


def test_undeclared_sheet_path_emits_no_block_frames_and_keeps_netlist_parity() -> None:
    project = _resolved_basic_board_project()
    placed = build_placed_project(project)
    report = placed_layout_report(placed).to_dict()["counts"]
    mates, _nets = schema_net_mates(project)

    frames = [
        item
        for sheet in placed.sheets
        for item in sheet.items
        if isinstance(item, PlacedGraphicRectangle)
    ]

    assert frames == []
    assert report["layout_errors"] == 0
    assert report["out_of_bounds"] == 0
    assert mates[("J1", "1")] == frozenset({("U1", "1"), ("C1", "1")})


def test_declared_block_frames_tile_in_reading_order_rows() -> None:
    _project, sheet = _can_controller_sheet()
    frames = [
        _rect_for_frame(item)
        for item in sheet.items
        if isinstance(item, PlacedGraphicRectangle)
    ]

    rows: dict[float, list[Rect]] = {}
    for frame in frames:
        rows.setdefault(round(frame.top, 2), []).append(frame)

    assert len(rows) >= 2
    assert any(len(row) >= 2 for row in rows.values())
    assert max(frame.right for frame in frames) - min(frame.left for frame in frames) > 300.0
    for row in rows.values():
        ordered = sorted(row, key=lambda frame: frame.left)
        assert ordered == row or len(row) == 1


def test_can_controller_label_count_is_below_rebased_template_baseline() -> None:
    _project, sheet = _can_controller_sheet()

    labels = [item for item in sheet.items if isinstance(item, PlacedLabel)]

    assert len(labels) == 22


def test_labels_anchor_to_wires_or_pins_without_a_floating_channel_gap() -> None:
    _project, sheet = _can_controller_sheet()
    wire_points = {
        point
        for item in sheet.items
        if isinstance(item, PlacedWire)
        for point in (item.start, item.end)
    }

    for label in (item for item in sheet.items if isinstance(item, PlacedLabel)):
        assert any(
            abs(label.at[0] - point[0]) + abs(label.at[1] - point[1]) <= 2.54 + 0.01
            for point in wire_points
        ), label


U1_FLUSH_FANOUT_NETS = {
    "CAN_INT": "9",
    "CAN_RXD": "8",
    "CAN_SPI_MISO": "2",
    "CAN_SPI_MOSI": "1",
    "CAN_SPI_SCLK": "4",
    "CAN_TXD": "7",
}


def test_u1_pin_fanout_labels_are_flush_to_pin_termini() -> None:
    project, sheet = _can_controller_sheet()
    u1 = next(
        item for item in sheet.items if isinstance(item, PlacedSymbol) and item.reference == "U1"
    )
    info = project.symbol_library[u1.lib_id]
    pin_points = {}
    for pin in info.pins:
        if pin.unit not in (0, u1.unit):
            continue
        local = symbol_pin_coordinate(0.0, 0.0, pin)
        rotated = _rotated_point(local, u1.rotation)
        pin_points[pin.number] = (u1.at[0] + rotated[0], u1.at[1] + rotated[1])

    distances = {}
    for net_name, pin_number in U1_FLUSH_FANOUT_NETS.items():
        pin_point = pin_points[pin_number]
        label = min(
            (
                item
                for item in sheet.items
                if isinstance(item, PlacedLabel) and item.name == net_name
            ),
            key=lambda item: abs(item.at[0] - pin_point[0]) + abs(item.at[1] - pin_point[1]),
        )
        distances[net_name] = abs(label.at[0] - pin_point[0]) + abs(
            label.at[1] - pin_point[1]
        )

    assert distances
    assert max(distances.values()) <= 2.54 + 0.01
    assert distances["CAN_TXD"] == 2.54
