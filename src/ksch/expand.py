from pathlib import Path

from ksch.model.ir import ChildInstanceIR, ProjectIR, SheetIR
from ksch.model.source import SourceDocument
from ksch.schema.loader import load_yaml_file

NO_CONNECT = "nc"


def _load_source(path: Path) -> SourceDocument:
    return SourceDocument.model_validate(load_yaml_file(path))


def _join_sheet_path(parent_path: str, child_name: str) -> str:
    if parent_path == "/":
        return f"/{child_name}"
    return f"{parent_path}/{child_name}"


def _sheet_ir(path: str, source_path: Path, source: SourceDocument) -> SheetIR:
    title = source.sheet.title if source.sheet else source.project.title if source.project else None
    nets, net_endpoint_paths, no_connects, no_connect_paths = _normalize_connects(source)
    return SheetIR(
        path=path,
        source_path=source_path,
        title=title,
        interface=source.interface,
        symbols=source.symbols,
        nets=nets,
        net_endpoint_paths=net_endpoint_paths,
        power_flags=source.power_flags,
        no_connects=no_connects,
        no_connect_paths=no_connect_paths,
        assertions=source.assertions,
    )


def _normalize_connects(
    source: SourceDocument,
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str], list[str]]:
    nets: dict[str, list[str]] = {}
    net_endpoint_paths: dict[str, list[str]] = {}
    no_connects: list[str] = []
    no_connect_paths: list[str] = []
    for ref, symbol in source.symbols.items():
        for selector, net_name in symbol.connects.items():
            endpoint = f"{ref}.{selector}"
            if net_name == NO_CONNECT:
                no_connects.append(endpoint)
                no_connect_paths.append(f"symbols.{ref}.connects.{selector}")
            else:
                nets.setdefault(net_name, []).append(endpoint)
                net_endpoint_paths.setdefault(net_name, []).append(
                    f"symbols.{ref}.connects.{selector}"
                )
    for child_name, child in source.sheets.items():
        for port, net_name in child.connects.items():
            if net_name == NO_CONNECT:
                raise ValueError(f"sheet instance {child_name}.{port} cannot connect to nc")
            nets.setdefault(net_name, []).append(f"{child_name}.{port}")
            net_endpoint_paths.setdefault(net_name, []).append(
                f"sheets.{child_name}.connects.{port}"
            )
    for net_name, endpoint_texts in list(nets.items()):
        paired = sorted(
            zip(endpoint_texts, net_endpoint_paths.get(net_name, []), strict=False),
            key=lambda item: item[0],
        )
        nets[net_name] = [endpoint for endpoint, _path in paired]
        net_endpoint_paths[net_name] = [path for _endpoint, path in paired]
    no_connect_pairs = sorted(
        zip(no_connects, no_connect_paths, strict=False),
        key=lambda item: item[0],
    )
    no_connects = [endpoint for endpoint, _path in no_connect_pairs]
    no_connect_paths = [path for _endpoint, path in no_connect_pairs]
    return nets, net_endpoint_paths, no_connects, no_connect_paths


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
    symbol_libraries = {
        nickname: (root_path.parent / library_path).resolve()
        for nickname, library_path in source.libraries.symbols.project.items()
    }
    footprint_libraries = {
        nickname: (root_path.parent / library_path).resolve()
        for nickname, library_path in source.libraries.footprints.project.items()
    }
    return ProjectIR(
        name=source.project.name,
        root_path=root_path,
        sheets=sheets,
        symbol_libraries=symbol_libraries,
        footprint_libraries=footprint_libraries,
    )
