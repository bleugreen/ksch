import json

from ksch.model.source import SourceDocument


def schema_json_text() -> str:
    schema = SourceDocument.model_json_schema()
    schema["title"] = "ksch Schema v1"
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"
