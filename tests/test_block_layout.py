from pathlib import Path

from ksch.compiler import build_placed_project
from ksch.expand import load_project_ir
from ksch.kicad.symbols import index_symbol_library
from ksch.layout import Rect
from ksch.placed import PlacedGraphicRectangle, PlacedLabel, PlacedSymbol, PlacedText, PlacedWire
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


def _can_controller_sheet():
    project = _resolved_can_controller_project()
    placed = build_placed_project(project)
    return project, placed.sheets[0]


def test_declared_blocks_render_as_four_titled_frames() -> None:
    project, sheet = _can_controller_sheet()
    block_names = set(project.sheets["/"].blocks)

    frames = [item for item in sheet.items if isinstance(item, PlacedGraphicRectangle)]
    titles = [item.text for item in sheet.items if isinstance(item, PlacedText) and item.text in block_names]

    assert len(frames) == 4
    assert set(titles) == {"CAN Controller", "CAN Transceiver", "Vehicle Conn", "12V Sense"}


def test_declared_block_members_are_inside_exactly_one_frame() -> None:
    project, sheet = _can_controller_sheet()
    frames = [_rect_for_frame(item) for item in sheet.items if isinstance(item, PlacedGraphicRectangle)]
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
    frames = [_rect_for_frame(item) for item in sheet.items if isinstance(item, PlacedGraphicRectangle)]

    for index, frame in enumerate(frames):
        for other in frames[index + 1 :]:
            assert not frame.overlaps(other)


def test_cross_block_nets_use_labels_instead_of_frame_crossing_wires() -> None:
    _project, sheet = _can_controller_sheet()
    frames = [_rect_for_frame(item) for item in sheet.items if isinstance(item, PlacedGraphicRectangle)]
    can_txd_labels = [
        item
        for item in sheet.items
        if isinstance(item, PlacedLabel) and "CAN_TXD" in item.nets and item.name == "CAN_TXD"
    ]
    can_txd_wires = [item for item in sheet.items if isinstance(item, PlacedWire) and "CAN_TXD" in item.nets]

    label_frames = {
        index
        for label in can_txd_labels
        for index, frame in enumerate(frames)
        if _contains_point(frame, label.at)
    }

    assert len(label_frames) == 2
    assert can_txd_wires == []


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
