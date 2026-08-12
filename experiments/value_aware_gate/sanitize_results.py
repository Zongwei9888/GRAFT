from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
FROZEN = EXPERIMENT / "frozen_inputs"
RESULTS = EXPERIMENT / "results"

FILES = (
    RESULTS / "m2_selector_pilot.json",
    RESULTS / "m2_posthoc_resource_diagnostic.json",
    FROZEN / "producer-events.json",
)

ALIASES = {
    str(
        ROOT
        / "artifacts/value-aware-gate/checkpoints/value-aware-mechanism-pilot/"
        "epoch-001-round-000-166539c6a22ff69a.tar.gz"
    ): "experiments/value_aware_gate/frozen_inputs/checkpoint.tar.gz",
    str(
        ROOT
        / "artifacts/value-aware-gate/baselines/value-aware-mechanism-pilot/"
        "epoch-001-9425003a8b74b56f.tar.gz"
    ): "experiments/value_aware_gate/frozen_inputs/baseline.tar.gz",
}

TEMP_WORKSPACE = re.compile(
    r"/(?:private/var/folders/[^\s\"')]+/T|tmp)/"
    r"graft-m2-[^/\s\"')]+/(?:producer-workspace|workspace)"
)
LOCAL_REPORT = re.compile(r"\.?/artifacts/value-aware-gate/(?:diagnostic-)?reports/[^\s\"']+")


def main() -> int:
    for path in FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        sanitized = _sanitize(payload)
        path.write_text(
            json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    checkpoint = FROZEN / "checkpoint.tar.gz"
    checksum = _digest(checkpoint)
    (FROZEN / "checkpoint.tar.gz.sha256").write_text(
        f"{checksum}  checkpoint.tar.gz\n", encoding="utf-8"
    )
    manifest = {
        "version": 1,
        "files": {
            path.name: _digest(path)
            for path in (
                FROZEN / "baseline.tar.gz",
                checkpoint,
                FROZEN / "producer-events.json",
            )
        },
        "checkpoint_key": (
            "166539c6a22ff69aaf0481fa556c47f8ffc84a32fe7a201c994bc61da7eecb3f"
        ),
    }
    (FROZEN / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    forbidden = (str(ROOT), "/private/var/folders/", "/tmp/graft-m2-")
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        found = [item for item in forbidden if item in text]
        if found:
            raise RuntimeError(f"non-portable paths remain in {path}: {found}")
    print("Sanitized result and frozen-input paths; wrote SHA-256 manifest.")
    return 0


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for source, target in ALIASES.items():
        text = text.replace(source, target)
    text = TEMP_WORKSPACE.sub("<producer-workspace>", text)
    text = text.replace(str(ROOT), ".")
    text = LOCAL_REPORT.sub("<local-report-not-published>", text)
    return text


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
