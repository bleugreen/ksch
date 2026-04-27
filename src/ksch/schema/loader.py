from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.nodes import MappingNode, SequenceNode

from ksch.errors import KschError


class StrictYaml:
    def __init__(self) -> None:
        self.yaml = YAML(typ="rt")
        self.yaml.allow_duplicate_keys = False

    def load_text(self, text: str, path: Path) -> Any:
        try:
            root = self.yaml.compose(text)
            if root is not None:
                self._reject_forbidden_nodes(root, path)
            data = self.yaml.load(text)
        except DuplicateKeyError as exc:
            key = str(exc.context_mark).splitlines()[0] if exc.context_mark else "unknown"
            duplicate = str(exc.problem).split('"')[1] if '"' in str(exc.problem) else "unknown"
            raise KschError(f"duplicate key '{duplicate}'", path, key) from exc
        except KschError:
            raise
        except Exception as exc:
            raise KschError(str(exc), path) from exc
        return self._plain(data)

    def _reject_forbidden_nodes(self, node: object, path: Path) -> None:
        anchor = getattr(node, "anchor", None)
        if anchor:
            raise KschError("anchors and aliases are not allowed", path)
        tag = getattr(node, "tag", None)
        if tag and not str(tag).startswith("tag:yaml.org,2002:"):
            raise KschError("custom YAML tags are not allowed", path)
        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                self._reject_forbidden_nodes(key_node, path)
                self._reject_forbidden_nodes(value_node, path)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                self._reject_forbidden_nodes(item, path)

    def _plain(self, value: Any) -> Any:
        if isinstance(value, CommentedMap):
            return {self._plain(k): self._plain(v) for k, v in value.items()}
        if isinstance(value, CommentedSeq):
            return [self._plain(v) for v in value]
        return value


def load_yaml_text(text: str, path: Path) -> Any:
    return StrictYaml().load_text(text, path)


def load_yaml_file(path: Path) -> Any:
    return load_yaml_text(path.read_text(encoding="utf-8"), path)
