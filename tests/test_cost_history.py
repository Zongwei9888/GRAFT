from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graft.cost_history import CostHistoryStore
from graft.schema import (
    Decision,
    DecisionKind,
    FeedbackGraph,
    Lineage,
    SourceSnapshot,
    Verdict,
    VerifierResult,
    VerifierSpec,
)


class CostHistoryTests(unittest.TestCase):
    def test_records_only_cost_metadata_and_returns_conservative_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CostHistoryStore(Path(temporary), quantile=0.75)
            store.record(_decision(duration=1.0, model_cost=0.02))
            store.record(_decision(duration=5.0, model_cost=None))
            store.record(_decision(duration=3.0, model_cost=0.04))

            estimate = store.estimates()["semantic-review"]
            self.assertEqual(estimate.sample_count, 3)
            self.assertEqual(estimate.duration_s, 5.0)
            self.assertEqual(estimate.model_cost_usd, 0.04)
            self.assertEqual(estimate.total_tokens, 30)

            payload_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in Path(temporary).glob("*.json")
            )
            self.assertNotIn("project source", payload_text)
            payload = json.loads(next(Path(temporary).glob("*.json")).read_text())
            self.assertEqual(
                set(payload["observations"][0]),
                {"template_id", "duration_s", "model_cost_usd", "total_tokens"},
            )

    def test_ignores_malformed_history_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "broken.json").write_text("not-json", encoding="utf-8")
            self.assertEqual(CostHistoryStore(root).estimates(), {})

    def test_retention_is_bounded_to_recent_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = CostHistoryStore(root, max_files=2)
            for duration in (1.0, 2.0, 3.0):
                store.record(_decision(duration=duration, model_cost=None))
            self.assertEqual(len(tuple(root.glob("*.json"))), 2)


def _decision(*, duration: float, model_cost: float | None) -> Decision:
    snapshot = SourceSnapshot(
        root="/tmp/repo",
        tree_hash="tree",
        requirement_hash="requirements",
        config_hash="config",
        checkpoint_key="checkpoint",
        files=(),
        created_at="now",
    )
    verifier = VerifierSpec(
        verifier_id="review-1",
        template_id="semantic-review",
        kind="codex_agent",
        cost=1.0,
        blocking=True,
        failure_modes=(),
        lineage=Lineage(),
    )
    graph = FeedbackGraph(
        source_hash="checkpoint",
        behaviors=(),
        failure_modes=(),
        verifiers=(verifier,),
        shared_blind_spots=(),
    )
    result = VerifierResult(
        verifier_id="review-1",
        verdict=Verdict.PASS,
        summary="project source is never persisted",
        source_hash="checkpoint",
        blocking=True,
        reproducible=True,
        duration_s=duration,
        usage={"input_tokens": 10, "output_tokens": 20},
        estimated_cost_usd=model_cost,
    )
    return Decision(
        kind=DecisionKind.ALLOW,
        reason="done",
        snapshot=snapshot,
        graph=graph,
        results=(result,),
    )


if __name__ == "__main__":
    unittest.main()
