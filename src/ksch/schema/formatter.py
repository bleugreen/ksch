from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ksch.schema.loader import load_yaml_text

TOP_LEVEL_ORDER = [
    "ksch",
    "project",
    "sheet",
    "libraries",
    "interface",
    "sheets",
    "symbols",
    "nets",
    "power_flags",
    "no_connects",
    "assertions",
    "blocks",
    "use",
]


def _order_mapping(value: Any, top_level: bool = False) -> Any:
    if isinstance(value, dict):
        keys = list(value.keys())
        if top_level:
            rank = {key: index for index, key in enumerate(TOP_LEVEL_ORDER)}
            keys.sort(key=lambda key: (rank.get(str(key), len(rank)), str(key)))
        return {key: _order_mapping(value[key]) for key in keys}
    if isinstance(value, list):
        return [_order_mapping(item) for item in value]
    return value


def format_schema_text(text: str, path: Path | None = None) -> str:
    source_path = path or Path("<memory>")
    data = load_yaml_text(text, source_path)
    ordered = _order_mapping(data, top_level=True)
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 100
    output = StringIO()
    yaml.dump(ordered, output)
    return output.getvalue()
