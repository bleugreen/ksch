import json
import uuid
from pathlib import Path

from ksch.resolver import ResolvedProject

UUID_NAMESPACE = uuid.UUID("7d91d76e-4e61-4c8c-a1b7-4a5f2d7d6f4b")


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, key))


def _sheet_filename(project_name: str, sheet_path: str) -> Path:
    if sheet_path == "/":
        return Path(f"{project_name}.kicad_sch")
    parts = [part for part in sheet_path.split("/") if part]
    return Path("sheets").joinpath(*parts).with_suffix(".kicad_sch")


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
        f"  (uuid {stable_uuid(sheet_path)})",
        "  (paper \"A4\")",
        "  (lib_symbols)",
    ]
    for ref, symbol in sorted(sheet.symbols.items()):
        lines.extend(
            [
                f"  (symbol \"{symbol.lib}\"",
                "    (at 50 50 0)",
                "    (unit 1)",
                "    (in_bom yes)",
                "    (on_board yes)",
                f"    (uuid {stable_uuid(sheet_path + '/' + ref)})",
                f"    (property \"Reference\" \"{ref}\" (at 50 47.46 0))",
                f"    (property \"Value\" \"{symbol.value or ref}\" (at 50 52.54 0))",
                f"    (property \"Footprint\" \"{symbol.footprint or ''}\" (at 50 55.08 0))",
                "  )",
            ]
        )
    for child_name, child in sorted(sheet.child_instances.items()):
        sheet_file = _sheet_filename(project.name, child.target_path).as_posix()
        lines.extend(
            [
                "  (sheet",
                "    (at 100 50)",
                "    (size 40 30)",
                f"    (uuid {stable_uuid(sheet_path + '/' + child_name + ':sheet')})",
                f"    (property \"Sheetname\" \"{child_name}\" (at 100 48 0))",
                f"    (property \"Sheetfile\" \"{sheet_file}\" (at 100 82 0))",
                "  )",
            ]
        )
    lines.append("  (path \"/\" (page \"1\"))")
    lines.append(")")
    return "\n".join(lines) + "\n"


def write_project(project: ResolvedProject, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_project_file(project, output_dir)
    for sheet_path in sorted(project.source.sheets):
        target = output_dir / _sheet_filename(project.name, sheet_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_schematic_text(project, sheet_path), encoding="utf-8")
