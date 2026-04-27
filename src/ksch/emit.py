import json
import uuid
from pathlib import Path

from ksch.layout import layout_sheet_symbols
from ksch.model.source import PinDirection
from ksch.resolver import ResolvedProject

UUID_NAMESPACE = uuid.UUID("7d91d76e-4e61-4c8c-a1b7-4a5f2d7d6f4b")


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, key))


def _q(value: str) -> str:
    return json.dumps(value)


def _sheet_filename(project_name: str, sheet_path: str) -> Path:
    if sheet_path == "/":
        return Path(f"{project_name}.kicad_sch")
    parts = [part for part in sheet_path.split("/") if part]
    return Path("sheets").joinpath(*parts).with_suffix(".kicad_sch")


def _join_sheet_path(parent_path: str, child_name: str) -> str:
    if parent_path == "/":
        return f"/{child_name}"
    return f"{parent_path}/{child_name}"


def _child_sheet_uuid(parent_path: str, child_name: str) -> str:
    return stable_uuid(f"{parent_path}/{child_name}:sheet")


def _sheet_instance_path(sheet_path: str) -> str:
    if sheet_path == "/":
        return "/"

    parent_path = "/"
    uuids = []
    for part in [part for part in sheet_path.split("/") if part]:
        uuids.append(_child_sheet_uuid(parent_path, part))
        parent_path = _join_sheet_path(parent_path, part)
    return "/" + "/".join(uuids)


def _page_number(project: ResolvedProject, sheet_path: str) -> str:
    return str(sorted(project.source.sheets).index(sheet_path) + 1)


def _sheet_pin_shape(direction: PinDirection) -> str:
    if direction in {"power_in", "power_out"}:
        return "passive"
    return direction


def _write_project_file(project: ResolvedProject, output_dir: Path) -> None:
    data = {
        "board": {"design_settings": {"defaults": {}}},
        "meta": {"filename": f"{project.name}.kicad_pro", "version": 1},
        "schematic": {},
    }
    (output_dir / f"{project.name}.kicad_pro").write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def _schematic_text(project: ResolvedProject, sheet_path: str) -> str:
    sheet = project.source.sheets[sheet_path]
    lines = [
        "(kicad_sch",
        "  (version 20240101)",
        "  (generator \"kicad-schema\")",
        f"  (uuid {_q(stable_uuid(sheet_path))})",
        "  (paper \"A4\")",
        "  (lib_symbols)",
    ]
    positions = layout_sheet_symbols(list(sheet.symbols))
    for ref, symbol in sorted(sheet.symbols.items()):
        position = positions[ref]
        x = position.x
        y = position.y
        lines.extend(
            [
                "  (symbol",
                f"    (lib_id {_q(symbol.lib)})",
                f"    (at {x} {y} 0)",
                "    (unit 1)",
                "    (exclude_from_sim no)",
                "    (in_bom yes)",
                "    (on_board yes)",
                "    (dnp no)",
                f"    (uuid {_q(stable_uuid(sheet_path + '/' + ref))})",
                f"    (property \"Reference\" {_q(ref)} (at {x} {y - 2.54} 0))",
                f"    (property \"Value\" {_q(symbol.value or ref)} (at {x} {y + 2.54} 0))",
                f"    (property \"Footprint\" {_q(symbol.footprint or '')} (at {x} {y + 5.08} 0))",
            ]
        )
        indexed_symbol = project.symbol_library.get(symbol.lib)
        if indexed_symbol is not None:
            seen_pins: set[str] = set()
            for pin in indexed_symbol.pins:
                if pin.number in seen_pins:
                    continue
                seen_pins.add(pin.number)
                pin_uuid = stable_uuid(f"{sheet_path}/{ref}:{pin.number}")
                lines.extend(
                    [
                        f"    (pin {_q(pin.number)}",
                        f"      (uuid {_q(pin_uuid)})",
                        "    )",
                    ]
                )
        lines.extend(
            [
                "    (instances",
                f"      (project {_q(project.name)}",
                f"        (path {_q(_sheet_instance_path(sheet_path))}",
                f"          (reference {_q(ref)})",
                "          (unit 1)",
                "        )",
                "      )",
                "    )",
                "  )",
            ]
        )
    for child_name, child in sorted(sheet.child_instances.items()):
        child_sheet = project.source.sheets[child.target_path]
        child_interface = sorted(child_sheet.interface.items())
        sheet_height = max(30.0, 10.0 + len(child_interface) * 5.08)
        sheet_file = _sheet_filename(project.name, child.target_path).as_posix()
        sheet_file_y = 50 + sheet_height + 2.0
        lines.extend(
            [
                "  (sheet",
                "    (at 100 50)",
                f"    (size 40 {sheet_height})",
                "    (exclude_from_sim no)",
                "    (in_bom yes)",
                "    (on_board yes)",
                "    (dnp no)",
                "    (stroke (width 0.1524) (type solid) (color 0 0 0 0))",
                "    (fill (color 0 0 0 0))",
                f"    (uuid {_q(_child_sheet_uuid(sheet_path, child_name))})",
                f"    (property \"Sheetname\" {_q(child_name)} (at 100 48 0))",
                f"    (property \"Sheetfile\" {_q(sheet_file)} (at 100 {sheet_file_y} 0))",
            ]
        )
        for index, (port_name, direction) in enumerate(child_interface):
            y = 55.0 + index * 5.08
            pin_uuid = stable_uuid(f"{sheet_path}/{child_name}:{port_name}:pin")
            lines.extend(
                [
                    f"    (pin {_q(port_name)} {_sheet_pin_shape(direction)}",
                    f"      (at 100 {y} 180)",
                    f"      (uuid {_q(pin_uuid)})",
                    "    )",
                ]
            )
        lines.extend(
            [
                "    (instances",
                f"      (project {_q(project.name)}",
                f"        (path {_q(_sheet_instance_path(sheet_path))}",
                f"          (page {_q(_page_number(project, sheet_path))})",
                "        )",
                "      )",
                "    )",
                "  )",
            ]
        )
    lines.extend(
        [
            "  (sheet_instances",
            f"    (path {_q(_sheet_instance_path(sheet_path))}",
            f"      (page {_q(_page_number(project, sheet_path))})",
            "    )",
            "  )",
            "  (embedded_fonts no)",
        ]
    )
    lines.append(")")
    return "\n".join(lines) + "\n"


def write_project(project: ResolvedProject, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_project_file(project, output_dir)
    for sheet_path in sorted(project.source.sheets):
        target = output_dir / _sheet_filename(project.name, sheet_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_schematic_text(project, sheet_path), encoding="utf-8")
