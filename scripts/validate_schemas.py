# pyright: reportMissingModuleSource=false
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urldefrag, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "v1"


def resolve_pointer(document: object, fragment: str) -> object:
    if not fragment:
        return document
    if not fragment.startswith("/"):
        raise ValueError(f"unsupported JSON pointer: #{fragment}")
    current = document
    for raw_part in fragment.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"missing JSON pointer: #{fragment}")
        current = current[part]
    return current


def walk_refs(value: object):
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            yield ref
        for nested in value.values():
            yield from walk_refs(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_refs(nested)


def main() -> None:
    paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
    if not paths:
        raise SystemExit("no schemas found")

    documents = {path.name: json.loads(path.read_text()) for path in paths}
    ids: set[str] = set()

    for name, document in documents.items():
        schema_id = document.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise ValueError(f"{name}: missing $id")
        if schema_id in ids:
            raise ValueError(f"{name}: duplicate $id {schema_id}")
        ids.add(schema_id)
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError(f"{name}: expected JSON Schema 2020-12")

        for ref in walk_refs(document):
            target_url, fragment = urldefrag(ref)
            target_name = name if not target_url else Path(urlparse(target_url).path).name
            target = documents.get(target_name)
            if target is None:
                raise ValueError(f"{name}: unresolved schema reference {ref}")
            resolve_pointer(target, fragment)

    try:
        from jsonschema.validators import validator_for
    except ImportError:
        pass
    else:
        for name, document in documents.items():
            validator_for(document).check_schema(document)

    print(f"validated {len(documents)} schemas")


if __name__ == "__main__":
    main()
