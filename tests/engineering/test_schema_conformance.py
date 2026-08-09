from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import validate_schemas

SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


class SchemaConformanceTests(unittest.TestCase):
    @staticmethod
    def _write_schema(
        directory: Path,
        name: str,
        *,
        schema_id: str | None = "https://example.test/schema",
        schema_draft: str = SCHEMA_DRAFT,
        members: dict[str, object] | None = None,
    ) -> None:
        document: dict[str, object] = {"$schema": schema_draft, "type": "object"}
        if schema_id is not None:
            document["$id"] = schema_id
        if members is not None:
            document.update(members)
        (directory / name).write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def _run_validator(directory: Path) -> str:
        output = io.StringIO()
        with (
            patch.object(validate_schemas, "SCHEMA_DIR", directory),
            contextlib.redirect_stdout(output),
        ):
            validate_schemas.main()
        return output.getvalue()

    def test_checked_in_inventory_validates_through_real_entry_point(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            validate_schemas.main()

        self.assertEqual(output.getvalue(), "validated 11 schemas\n")

    def test_valid_same_set_reference_and_pointer_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_schema(
                directory,
                "common.schema.json",
                schema_id="https://example.test/common",
                members={"$defs": {"value": {"type": "string"}}},
            )
            self._write_schema(
                directory,
                "root.schema.json",
                schema_id="https://example.test/root",
                members={"properties": {"value": {"$ref": "common.schema.json#/$defs/value"}}},
            )

            self.assertEqual(self._run_validator(directory), "validated 2 schemas\n")

    def test_empty_inventory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(SystemExit, "no schemas found"):
                self._run_validator(Path(temporary_directory))

    def test_malformed_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "broken.schema.json").write_text("{", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                self._run_validator(directory)

    def test_missing_schema_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_schema(directory, "missing-id.schema.json", schema_id=None)

            with self.assertRaisesRegex(ValueError, "missing-id.schema.json: missing \\$id"):
                self._run_validator(directory)

    def test_duplicate_schema_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_schema(directory, "one.schema.json")
            self._write_schema(directory, "two.schema.json")

            with self.assertRaisesRegex(ValueError, "two.schema.json: duplicate \\$id"):
                self._run_validator(directory)

    def test_wrong_schema_draft_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_schema(
                directory,
                "wrong-draft.schema.json",
                schema_draft="https://json-schema.org/draft/2019-09/schema",
            )

            with self.assertRaisesRegex(ValueError, "expected JSON Schema 2020-12"):
                self._run_validator(directory)

    def test_missing_schema_file_reference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_schema(
                directory, "root.schema.json", members={"$ref": "missing.schema.json"}
            )

            with self.assertRaisesRegex(ValueError, "unresolved schema reference"):
                self._run_validator(directory)

    def test_missing_json_pointer_reference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_schema(
                directory,
                "root.schema.json",
                members={
                    "$defs": {"present": {"type": "string"}},
                    "$ref": "#/$defs/missing",
                },
            )

            with self.assertRaisesRegex(ValueError, "missing JSON pointer"):
                self._run_validator(directory)


if __name__ == "__main__":
    unittest.main()
