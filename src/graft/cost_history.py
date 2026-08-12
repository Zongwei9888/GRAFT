from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from graft.costing import usage_total_tokens
from graft.schema import Decision


@dataclass(frozen=True)
class HistoricalCostEstimate:
    """A conservative estimate derived only from observed verifier executions."""

    sample_count: int
    duration_s: float
    model_cost_usd: float | None
    total_tokens: int | None


class CostHistoryStore:
    """Append-only, content-free verifier cost observations for one workspace."""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 500,
        max_samples_per_template: int = 100,
        quantile: float = 0.75,
    ) -> None:
        if max_files < 1 or max_samples_per_template < 1:
            raise ValueError("Cost history bounds must be positive")
        if not 0 < quantile <= 1:
            raise ValueError("Cost history quantile must be in (0, 1]")
        self.root = root.resolve()
        self.max_files = max_files
        self.max_samples_per_template = max_samples_per_template
        self.quantile = quantile

    def record(self, decision: Decision) -> Path | None:
        graph = decision.graph
        if graph is None or not decision.results:
            return None
        verifier_templates = {
            item.verifier_id: item.template_id or item.verifier_id
            for item in graph.verifiers
        }
        observations: list[dict[str, Any]] = []
        for result in decision.results:
            template_id = verifier_templates.get(result.verifier_id)
            if template_id is None:
                continue
            observations.append(
                {
                    "template_id": template_id,
                    "duration_s": max(0.0, float(result.duration_s)),
                    "model_cost_usd": _nonnegative_float(
                        result.estimated_cost_usd
                    ),
                    "total_tokens": usage_total_tokens(result.usage),
                }
            )
        if not observations:
            return None

        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "observations": observations,
        }
        name = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            + f"-{uuid.uuid4().hex}.json"
        )
        destination = self.root / name
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".cost-", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        self._prune_files()
        return destination

    def estimates(self) -> dict[str, HistoricalCostEstimate]:
        samples: dict[str, list[tuple[float, float | None, int | None]]] = {}
        if not self.root.is_dir():
            return {}
        paths = sorted(self.root.glob("*.json"), reverse=True)[: self.max_files]
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("version") != 1:
                continue
            raw_observations = payload.get("observations")
            if not isinstance(raw_observations, list):
                continue
            for raw in raw_observations:
                parsed = _parse_observation(raw)
                if parsed is None:
                    continue
                template_id, observation = parsed
                bucket = samples.setdefault(template_id, [])
                if len(bucket) < self.max_samples_per_template:
                    bucket.append(observation)

        return {
            template_id: HistoricalCostEstimate(
                sample_count=len(values),
                duration_s=_nearest_rank(
                    [value[0] for value in values], self.quantile
                ),
                model_cost_usd=_optional_nearest_rank(
                    [value[1] for value in values], self.quantile
                ),
                total_tokens=_optional_nearest_rank_int(
                    [value[2] for value in values], self.quantile
                ),
            )
            for template_id, values in samples.items()
            if values
        }

    def _prune_files(self) -> None:
        try:
            stale = sorted(self.root.glob("*.json"), reverse=True)[self.max_files :]
            for path in stale:
                path.unlink(missing_ok=True)
        except OSError:
            # Retention cleanup is best effort; estimates already bound their read set.
            pass


def _parse_observation(
    value: Any,
) -> tuple[str, tuple[float, float | None, int | None]] | None:
    if not isinstance(value, dict):
        return None
    template_id = value.get("template_id")
    duration_s = _nonnegative_float(value.get("duration_s"))
    if not isinstance(template_id, str) or not template_id or duration_s is None:
        return None
    model_cost = _nonnegative_float(value.get("model_cost_usd"))
    total_tokens = _nonnegative_int(value.get("total_tokens"))
    return template_id, (duration_s, model_cost, total_tokens)


def _nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _optional_nearest_rank(
    values: list[float | None], quantile: float
) -> float | None:
    known = [value for value in values if value is not None]
    return _nearest_rank(known, quantile) if known else None


def _optional_nearest_rank_int(
    values: list[int | None], quantile: float
) -> int | None:
    known = [value for value in values if value is not None]
    return int(_nearest_rank([float(value) for value in known], quantile)) if known else None


def _nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None
