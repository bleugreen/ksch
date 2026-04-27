from pathlib import Path

from ksch.model.ir import ChildInstanceIR, ProjectIR, SheetIR
from ksch.model.source import SourceDocument
from ksch.schema.loader import load_yaml_file


def _load_source(path: Path) -> SourceDocument:
    return SourceDocument.model_validate(load_yaml_file(path))


def _join_sheet_path(parent_path: str, child_name: str) -> str:
    if parent_path == "/":
        return f"/{child_name}"
    return f"{parent_path}/{child_name}"


def _sheet_ir(path: str, source_path: Path, source: SourceDocument) -> SheetIR:
    title = source.sheet.title if source.sheet else source.project.title if source.project else None
    return SheetIR(
        path=path,
        source_path=source_path,
        title=title,
        interface=source.interface,
        symbols=source.symbols,
        nets=source.nets,
        no_connects=source.no_connects,
        assertions=source.assertions,
    )


def _load_sheet_tree(path: Path, sheet_path: str, sheets: dict[str, SheetIR]) -> None:
    source = _load_source(path)
    sheet = _sheet_ir(sheet_path, path, source)
    sheets[sheet_path] = sheet

    for child_name, child in source.sheets.items():
        child_path = _join_sheet_path(sheet_path, child_name)
        child_source = (path.parent / child.source).resolve()
        sheet.child_instances[child_name] = ChildInstanceIR(
            name=child_name,
            source=child_source,
            target_path=child_path,
        )
        _load_sheet_tree(child_source, child_path, sheets)


def load_project_ir(path: Path) -> ProjectIR:
    root_path = path.resolve()
    source = _load_source(root_path)
    if source.project is None:
        raise ValueError("root document must define project")
    sheets: dict[str, SheetIR] = {}
    _load_sheet_tree(root_path, "/", sheets)
    return ProjectIR(name=source.project.name, root_path=root_path, sheets=sheets)
