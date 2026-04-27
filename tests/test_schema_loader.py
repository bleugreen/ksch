from pathlib import Path

import pytest

from ksch.errors import KschError
from ksch.schema.formatter import format_schema_text
from ksch.schema.loader import load_yaml_text


def test_loads_plain_yaml_mapping() -> None:
    data = load_yaml_text("ksch: 1\nproject:\n  name: demo\n", Path("demo.ksch.yaml"))
    assert data == {"ksch": 1, "project": {"name": "demo"}}


def test_rejects_duplicate_keys() -> None:
    text = "ksch: 1\nproject: {}\nproject: {}\n"
    with pytest.raises(KschError, match="duplicate key 'project'"):
        load_yaml_text(text, Path("bad.ksch.yaml"))


def test_rejects_yaml_aliases() -> None:
    text = "ksch: 1\nshared: &x {name: demo}\nproject: *x\n"
    with pytest.raises(KschError, match="anchors and aliases are not allowed"):
        load_yaml_text(text, Path("bad.ksch.yaml"))


def test_rejects_custom_yaml_tags() -> None:
    text = "ksch: 1\nproject: !demo\n  name: demo\n"
    with pytest.raises(KschError, match="custom YAML tags are not allowed"):
        load_yaml_text(text, Path("bad.ksch.yaml"))


def test_wraps_yaml_parse_errors() -> None:
    with pytest.raises(KschError):
        load_yaml_text("ksch: [\n", Path("bad.ksch.yaml"))


def test_formatter_orders_top_level_keys() -> None:
    text = "nets: {}\nksch: 1\nproject:\n  name: demo\n"
    assert format_schema_text(text) == "ksch: 1\nproject:\n  name: demo\nnets: {}\n"
