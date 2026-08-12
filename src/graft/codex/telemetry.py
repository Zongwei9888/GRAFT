from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from graft.schema import ProducerEvidenceRecord, ProducerEvidenceSummary


_PREVIEW_LIMIT = 2_000
_SUMMARY_ITEMS = 24
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|authorization)\b"
    r"\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_EXIT_CODE_PATTERNS = (
    re.compile(r"(?i)\b(?:exit|return)[ _-]?code\s*[:=]?\s*(-?\d+)\b"),
    re.compile(r"(?i)\bprocess exited with code\s+(-?\d+)\b"),
)
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


def hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def record_from_hook_event(event: Mapping[str, Any], *, task_epoch: int) -> ProducerEvidenceRecord:
    tool_name = str(event.get("tool_name") or "unknown")
    tool_input = event.get("tool_input")
    tool_response = event.get("tool_response")
    command = _command_text(tool_input)
    response_text = _bounded_text(tool_response)
    exit_code = _exit_code(tool_response, response_text)
    duration = _duration(tool_response)
    outcome = _outcome(tool_response, exit_code)
    changed_paths = _changed_paths(tool_name, tool_input, command)
    family = "workspace_change" if changed_paths else (
        "execution" if command is not None else "tool"
    )
    return ProducerEvidenceRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=str(event.get("session_id", "unknown")),
        turn_id=(str(event["turn_id"]) if event.get("turn_id") is not None else None),
        task_epoch=task_epoch,
        tool_name=tool_name,
        family=family,
        outcome=outcome,
        input_hash=hash_json(tool_input),
        response_hash=hash_json(tool_response),
        command_preview=_redacted_preview(command) if command else None,
        result_preview=_redacted_preview(response_text) if response_text else None,
        changed_paths=changed_paths,
        exit_code=exit_code,
        duration_s=duration,
    )


class ProducerEvidenceLedger:
    def __init__(self, telemetry_dir: Path, session_id: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:160]
        self.path = telemetry_dir / f"{safe or 'unknown'}.jsonl"

    def append(self, record: ProducerEvidenceRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def records(self, *, task_epoch: int | None = None) -> tuple[ProducerEvidenceRecord, ...]:
        if not self.path.is_file():
            return ()
        parsed: list[ProducerEvidenceRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                record = ProducerEvidenceRecord(
                    timestamp=str(raw["timestamp"]),
                    session_id=str(raw["session_id"]),
                    turn_id=(str(raw["turn_id"]) if raw.get("turn_id") is not None else None),
                    task_epoch=int(raw.get("task_epoch", 1)),
                    tool_name=str(raw.get("tool_name", "unknown")),
                    family=str(raw.get("family", "tool")),
                    outcome=str(raw.get("outcome", "unknown")),
                    input_hash=str(raw["input_hash"]),
                    response_hash=str(raw["response_hash"]),
                    command_preview=_optional_string(raw.get("command_preview")),
                    result_preview=_optional_string(raw.get("result_preview")),
                    changed_paths=tuple(str(item) for item in raw.get("changed_paths", [])),
                    exit_code=(int(raw["exit_code"]) if raw.get("exit_code") is not None else None),
                    duration_s=(float(raw["duration_s"]) if raw.get("duration_s") is not None else None),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if task_epoch is None or record.task_epoch == task_epoch:
                parsed.append(record)
        return tuple(parsed)

    def summarize(self, *, task_epoch: int) -> ProducerEvidenceSummary:
        return summarize_records(self.records(task_epoch=task_epoch), task_epoch=task_epoch)


def summarize_records(
    records: Iterable[ProducerEvidenceRecord], *, task_epoch: int
) -> ProducerEvidenceSummary:
    items = tuple(records)
    durations = tuple(item.duration_s for item in items if item.duration_s is not None)
    commands = _unique_bounded(
        item.command_preview for item in items if item.command_preview
    )
    failures = _unique_bounded(
        item.result_preview
        for item in items
        if item.outcome == "failed" and item.result_preview
    )
    paths = _unique_bounded(
        path for item in items for path in item.changed_paths
    )
    return ProducerEvidenceSummary(
        task_epoch=task_epoch,
        event_count=len(items),
        succeeded=sum(item.outcome == "succeeded" for item in items),
        failed=sum(item.outcome == "failed" for item in items),
        unknown=sum(item.outcome == "unknown" for item in items),
        total_duration_s=(sum(durations) if durations else None),
        command_previews=commands,
        failure_previews=failures,
        changed_paths=paths,
    )


def _command_text(tool_input: Any) -> str | None:
    if isinstance(tool_input, Mapping):
        for key in ("command", "cmd"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return " ".join(value)
    if isinstance(tool_input, str):
        return tool_input
    return None


def _bounded_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _redacted_preview(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(r"\1<redacted>", value)
    redacted = _BEARER_TOKEN.sub("Bearer <redacted>", redacted)
    if len(redacted) <= _PREVIEW_LIMIT:
        return redacted
    half = _PREVIEW_LIMIT // 2
    return redacted[:half] + "\n...<truncated>...\n" + redacted[-half:]


def _exit_code(response: Any, response_text: str) -> int | None:
    if isinstance(response, Mapping):
        for key in ("exit_code", "return_code"):
            value = response.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        structured = response.get("structuredContent")
        if isinstance(structured, Mapping):
            nested = _exit_code(structured, _bounded_text(structured))
            if nested is not None:
                return nested
    for pattern in _EXIT_CODE_PATTERNS:
        match = pattern.search(response_text)
        if match:
            return int(match.group(1))
    return None


def _duration(response: Any) -> float | None:
    if not isinstance(response, Mapping):
        return None
    for key in ("duration_s", "wall_time_seconds", "elapsed_seconds"):
        value = response.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


def _outcome(response: Any, exit_code: int | None) -> str:
    if exit_code is not None:
        return "succeeded" if exit_code == 0 else "failed"
    if isinstance(response, Mapping):
        if response.get("isError") is True or response.get("error"):
            return "failed"
        if response.get("isError") is False:
            return "succeeded"
    return "unknown"


def _changed_paths(tool_name: str, tool_input: Any, command: str | None) -> tuple[str, ...]:
    paths: list[str] = []
    if isinstance(tool_input, Mapping):
        for key in ("path", "file_path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())
    if command and tool_name.lower() in {"apply_patch", "edit", "write"}:
        paths.extend(match.strip() for match in _PATCH_PATH.findall(command))
    return tuple(dict.fromkeys(paths))


def _unique_bounded(values: Iterable[str]) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
        if len(unique) >= _SUMMARY_ITEMS:
            break
    return tuple(unique)


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None
