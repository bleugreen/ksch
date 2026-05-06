import json
from pathlib import Path
from typing import Any

from sexpdata import Symbol  # type: ignore[import-untyped]

from ksch.kicad.sexpr import dump_sexpr
from ksch.placed import (
    PlacedHierarchicalLabel,
    PlacedItem,
    PlacedJunction,
    PlacedLabel,
    PlacedNoConnect,
    PlacedProject,
    PlacedProperty,
    PlacedSheet,
    PlacedSheetBlock,
    PlacedSheetPin,
    PlacedSymbol,
    PlacedSymbolPin,
    PlacedWire,
)


def _a(value: str) -> Symbol:
    return Symbol(value)


def _yes_no(value: bool) -> Symbol:
    return _a("yes" if value else "no")


def _write_project_file(project: PlacedProject, output_dir: Path) -> None:
    data = {
        "board": {"design_settings": {"defaults": {}}},
        "meta": {"filename": f"{project.name}.kicad_pro", "version": 1},
        "schematic": {},
    }
    (output_dir / f"{project.name}.kicad_pro").write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_library_table(
    libraries: dict[str, Path],
    output_dir: Path,
    *,
    table_kind: str,
    filename: str,
) -> None:
    if not libraries:
        return
    expr = [
        _a(table_kind),
        [_a("version"), 7],
        *[
            [
                _a("lib"),
                [_a("name"), nickname],
                [_a("type"), "KiCad"],
                [_a("uri"), str(path.resolve())],
                [_a("options"), ""],
                [_a("descr"), ""],
            ]
            for nickname, path in sorted(libraries.items())
        ],
    ]
    (output_dir / filename).write_text(_format_sexpr(expr) + "\n", encoding="utf-8")


def _format_sexpr(value: Any, *, indent: int = 0) -> str:
    if not isinstance(value, list):
        return dump_sexpr(value)
    if not value:
        return "()"
    if all(not isinstance(item, list) for item in value):
        return " " * indent + "(" + " ".join(dump_sexpr(item) for item in value) + ")"

    prefix = " " * indent
    child_indent = indent + 2
    lines = [prefix + "(" + dump_sexpr(value[0])]
    for item in value[1:]:
        if isinstance(item, list):
            lines.append(_format_sexpr(item, indent=child_indent))
        else:
            lines[-1] += " " + dump_sexpr(item)
    lines.append(prefix + ")")
    return "\n".join(lines)


def _effects(*, justify: str = "left", hidden: bool = False) -> list[Any]:
    expr: list[Any] = [
        _a("effects"),
        [_a("font"), [_a("size"), 1.27, 1.27]],
        [_a("justify"), _a(justify)],
    ]
    if hidden:
        expr.append([_a("hide"), _a("yes")])
    return expr


def _property_expr(property_: PlacedProperty) -> list[Any]:
    return [
        _a("property"),
        property_.name,
        property_.value,
        [_a("at"), property_.at[0], property_.at[1], 0],
        _effects(justify=property_.justify, hidden=property_.hidden),
    ]


def _symbol_pin_expr(pin: PlacedSymbolPin) -> list[Any]:
    return [_a("pin"), pin.number, [_a("uuid"), pin.uuid]]


def _symbol_expr(symbol: PlacedSymbol) -> list[Any]:
    return [
        _a("symbol"),
        [_a("lib_id"), symbol.lib_id],
        [_a("at"), symbol.at[0], symbol.at[1], symbol.rotation],
        [_a("unit"), symbol.unit],
        [_a("exclude_from_sim"), _yes_no(symbol.exclude_from_sim)],
        [_a("in_bom"), _yes_no(symbol.in_bom)],
        [_a("on_board"), _yes_no(symbol.on_board)],
        [_a("dnp"), _yes_no(symbol.dnp)],
        [_a("uuid"), symbol.uuid],
        *(_property_expr(property_) for property_ in symbol.properties),
        *(_symbol_pin_expr(pin) for pin in symbol.pins),
        [
            _a("instances"),
            [
                _a("project"),
                symbol.project_name,
                [
                    _a("path"),
                    symbol.sheet_instance_path,
                    [_a("reference"), symbol.reference],
                    [_a("unit"), symbol.unit],
                ],
            ],
        ],
    ]


def _sheet_pin_expr(pin: PlacedSheetPin) -> list[Any]:
    return [
        _a("pin"),
        pin.name,
        _a(pin.shape),
        [_a("at"), pin.at[0], pin.at[1], pin.rotation],
        [_a("uuid"), pin.uuid],
    ]


def _sheet_block_expr(sheet: PlacedSheetBlock) -> list[Any]:
    return [
        _a("sheet"),
        [_a("at"), sheet.at[0], sheet.at[1]],
        [_a("size"), sheet.size[0], sheet.size[1]],
        [_a("exclude_from_sim"), _a("no")],
        [_a("in_bom"), _a("yes")],
        [_a("on_board"), _a("yes")],
        [_a("dnp"), _a("no")],
        [
            _a("stroke"),
            [_a("width"), 0.1524],
            [_a("type"), _a("solid")],
            [_a("color"), 0, 0, 0, 0],
        ],
        [_a("fill"), [_a("color"), 0, 0, 0, 0]],
        [_a("uuid"), sheet.uuid],
        [
            _a("property"),
            "Sheetname",
            sheet.sheet_name,
            [_a("at"), sheet.sheet_name_at[0], sheet.sheet_name_at[1], 0],
        ],
        [
            _a("property"),
            "Sheetfile",
            sheet.sheet_file,
            [_a("at"), sheet.sheet_file_at[0], sheet.sheet_file_at[1], 0],
        ],
        *(_sheet_pin_expr(pin) for pin in sheet.pins),
        [
            _a("instances"),
            [
                _a("project"),
                sheet.project_name,
                [
                    _a("path"),
                    sheet.sheet_instance_path,
                    [_a("page"), sheet.page],
                ],
            ],
        ],
    ]


def _wire_expr(wire: PlacedWire) -> list[Any]:
    return [
        _a("wire"),
        [
            _a("pts"),
            [_a("xy"), wire.start[0], wire.start[1]],
            [_a("xy"), wire.end[0], wire.end[1]],
        ],
        [_a("stroke"), [_a("width"), 0], [_a("type"), _a("solid")]],
        [_a("uuid"), wire.uuid],
    ]


def _junction_expr(junction: PlacedJunction) -> list[Any]:
    return [
        _a("junction"),
        [_a("at"), junction.at[0], junction.at[1]],
        [_a("diameter"), 0],
        [_a("color"), 0, 0, 0, 0],
        [_a("uuid"), junction.uuid],
    ]


def _label_expr(label: PlacedLabel) -> list[Any]:
    return [
        _a("label"),
        label.name,
        [_a("at"), label.at[0], label.at[1], 0],
        _effects(justify=label.justify, hidden=label.hidden),
        [_a("uuid"), label.uuid],
    ]


def _no_connect_expr(no_connect: PlacedNoConnect) -> list[Any]:
    return [
        _a("no_connect"),
        [_a("at"), no_connect.at[0], no_connect.at[1]],
        [_a("uuid"), no_connect.uuid],
    ]


def _hierarchical_label_expr(label: PlacedHierarchicalLabel) -> list[Any]:
    return [
        _a("hierarchical_label"),
        label.name,
        [_a("shape"), _a(label.shape)],
        [_a("at"), label.at[0], label.at[1], 0],
        _effects(justify=label.justify),
        [_a("uuid"), label.uuid],
    ]


def _item_expr(item: PlacedItem) -> list[Any]:
    if isinstance(item, PlacedSymbol):
        return _symbol_expr(item)
    if isinstance(item, PlacedSheetBlock):
        return _sheet_block_expr(item)
    if isinstance(item, PlacedWire):
        return _wire_expr(item)
    if isinstance(item, PlacedJunction):
        return _junction_expr(item)
    if isinstance(item, PlacedLabel):
        return _label_expr(item)
    if isinstance(item, PlacedNoConnect):
        return _no_connect_expr(item)
    if isinstance(item, PlacedHierarchicalLabel):
        return _hierarchical_label_expr(item)
    raise TypeError(f"unknown placed item: {item!r}")


def _schematic_expr(sheet: PlacedSheet) -> list[Any]:
    return [
        _a("kicad_sch"),
        [_a("version"), 20240101],
        [_a("generator"), "kicad-schema"],
        [_a("uuid"), sheet.uuid],
        [_a("paper"), sheet.paper],
        [_a("lib_symbols"), *sheet.lib_symbols],
        *(_item_expr(item) for item in sheet.items),
        [
            _a("sheet_instances"),
            [
                _a("path"),
                sheet.instance_path,
                [_a("page"), sheet.page],
            ],
        ],
        [_a("embedded_fonts"), _a("no")],
    ]


def write_project(
    project: PlacedProject,
    output_dir: Path,
    symbol_libraries: dict[str, Path] | None = None,
    footprint_libraries: dict[str, Path] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_project_file(project, output_dir)
    _write_library_table(
        symbol_libraries or {},
        output_dir,
        table_kind="sym_lib_table",
        filename="sym-lib-table",
    )
    _write_library_table(
        footprint_libraries or {},
        output_dir,
        table_kind="fp_lib_table",
        filename="fp-lib-table",
    )
    for sheet in project.sheets:
        target = output_dir / sheet.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_format_sexpr(_schematic_expr(sheet)) + "\n", encoding="utf-8")
