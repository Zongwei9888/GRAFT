from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
RESPONSE_SCHEMAS = (
    "completion_decision.schema.json",
    "task_analysis.schema.json",
    "verifier_plan.schema.json",
    "verifier_plan_vnext.schema.json",
    "verifier_verdict.schema.json",
    "verifier_verdict_vnext.schema.json",
)


class ResponseSchemaTests(unittest.TestCase):
    def test_codex_strict_objects_require_every_declared_property(self) -> None:
        for name in RESPONSE_SCHEMAS:
            packaged = ROOT / "src" / "graft" / "resources" / name
            schema = json.loads(packaged.read_text(encoding="utf-8"))
            errors: list[str] = []
            _check_strict_objects(schema, path="$", errors=errors)
            self.assertEqual(errors, [], f"{name}: {'; '.join(errors)}")

    def test_public_and_packaged_response_schemas_match(self) -> None:
        for name in RESPONSE_SCHEMAS:
            public = ROOT / "schemas" / name
            packaged = ROOT / "src" / "graft" / "resources" / name
            public_schema = json.loads(public.read_text(encoding="utf-8"))
            reference = public_schema.get("$ref")
            if isinstance(reference, str):
                referenced = (public.parent / reference).resolve()
                self.assertEqual(referenced, packaged.resolve(), name)
                self.assertTrue(referenced.is_file(), name)
            else:
                self.assertEqual(public.read_bytes(), packaged.read_bytes(), name)


def _check_strict_objects(value: Any, *, path: str, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        if isinstance(value, list):
            for index, item in enumerate(value):
                _check_strict_objects(item, path=f"{path}[{index}]", errors=errors)
        return
    raw_type = value.get("type")
    is_object = raw_type == "object" or (
        isinstance(raw_type, list) and "object" in raw_type
    )
    properties = value.get("properties")
    if is_object and isinstance(properties, Mapping):
        required = value.get("required")
        if set(required or ()) != set(properties):
            missing = sorted(set(properties) - set(required or ()))
            extra = sorted(set(required or ()) - set(properties))
            errors.append(f"{path} missing={missing} extra={extra}")
        if value.get("additionalProperties") is not False:
            errors.append(f"{path} must set additionalProperties=false")
    for key, item in value.items():
        _check_strict_objects(item, path=f"{path}.{key}", errors=errors)


if __name__ == "__main__":
    unittest.main()
