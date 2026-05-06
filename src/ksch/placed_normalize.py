from dataclasses import replace

from ksch.placed import PlacedItem, PlacedLabel, PlacedProject, PlacedSheet


def normalize_placed_project(project: PlacedProject) -> PlacedProject:
    return replace(
        project,
        sheets=tuple(normalize_placed_sheet(sheet) for sheet in project.sheets),
    )


def normalize_placed_sheet(sheet: PlacedSheet) -> PlacedSheet:
    return replace(sheet, items=normalize_placed_items(sheet.items))


def normalize_placed_items(items: tuple[PlacedItem, ...]) -> tuple[PlacedItem, ...]:
    normalized: list[PlacedItem] = []
    seen_labels: set[tuple[str, tuple[float, float], str, bool, frozenset[str]]] = set()
    for item in items:
        if isinstance(item, PlacedLabel):
            key = (
                item.name,
                (round(item.at[0], 2), round(item.at[1], 2)),
                item.justify,
                item.hidden,
                item.nets,
            )
            if key in seen_labels:
                continue
            seen_labels.add(key)
        normalized.append(item)
    return tuple(normalized)
