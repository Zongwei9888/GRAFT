from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from graft.cost_history import HistoricalCostEstimate
from graft.evidence.snapshot import freeze_source
from graft.modeling import (
    CodexFeedbackGraphBuilder,
    FeedbackGraphBuildError,
    FeedbackGraphBuilder,
)
from graft.registry import GraftConfig, load_config
from graft.schema import (
    Decision,
    DecisionKind,
    FeedbackGraph,
    ProducerEvidenceSummary,
    PromotionOutcome,
    PromotionRequirement,
    Selection,
    SourceSnapshot,
    Verdict,
    VerifierResult,
    to_jsonable,
)
from graft.selection import (
    InvalidFeedbackGraph,
    OriginalHypergraphSelector,
    ValueAwareSelector,
)
from graft.verifiers import VerifierExecutor


class GraftController:
    """Run one configured GRAFT method at an observable source checkpoint."""

    def __init__(
        self,
        config: GraftConfig,
        *,
        config_path: Path,
        graph_builder: FeedbackGraphBuilder | None = None,
        selector: OriginalHypergraphSelector | ValueAwareSelector | None = None,
        executor: VerifierExecutor | None = None,
        report_root: Path | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path.resolve()
        self.graph_builder = graph_builder or CodexFeedbackGraphBuilder()
        self.selector = selector or (
            ValueAwareSelector()
            if config.selection.strategy == "value-aware"
            else OriginalHypergraphSelector()
        )
        self.executor = executor or VerifierExecutor()
        self.report_root = report_root.resolve() if report_root else None

    @classmethod
    def from_path(
        cls, config_path: Path, *, report_root: Path | None = None
    ) -> "GraftController":
        return cls(
            load_config(config_path),
            config_path=config_path,
            report_root=report_root,
        )

    def snapshot(
        self,
        repo: Path,
        requirements: tuple[str, ...] = (),
        *,
        baseline_tree_hash: str | None = None,
        baseline_files: tuple[str, ...] = (),
        baseline_file_hashes: Mapping[str, str] | None = None,
        baseline_archive_path: str | None = None,
    ) -> SourceSnapshot:
        return freeze_source(
            repo,
            requirements=requirements,
            config_path=self.config_path,
            environment_fingerprint=self.config.environment_fingerprint,
            baseline_tree_hash=baseline_tree_hash,
            baseline_files=baseline_files,
            baseline_file_hashes=baseline_file_hashes,
            baseline_archive_path=baseline_archive_path,
        )

    def verify(
        self,
        repo: Path,
        *,
        requirements: tuple[str, ...] = (),
        session_id: str = "manual",
        snapshot: SourceSnapshot | None = None,
        producer_evidence: ProducerEvidenceSummary | None = None,
        available_budget: float | None = None,
        promotion: PromotionRequirement | None = None,
        historical_costs: Mapping[str, HistoricalCostEstimate] | None = None,
        available_wall_time_s: float | None = None,
        available_model_cost_usd: float | None = None,
    ) -> Decision:
        source = snapshot or self.snapshot(repo, requirements)
        if not self.config.enabled:
            return self._finish(
                Decision(
                    kind=DecisionKind.ALLOW,
                    reason="GRAFT is disabled.",
                    snapshot=source,
                ),
                session_id,
            )
        if not requirements:
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=(
                        "No raw user requirements were captured. GRAFT does not "
                        "invent a task specification or use the producer's summary as a substitute."
                    ),
                    snapshot=source,
                ),
                session_id,
            )

        try:
            if self.config.selection.strategy == "value-aware":
                graph = self.graph_builder.build(
                    source,
                    requirements,
                    self.config,
                    config_path=self.config_path,
                    producer_evidence=producer_evidence,
                    promotion=promotion,
                )
            else:
                graph = self.graph_builder.build(
                    source,
                    requirements,
                    self.config,
                    config_path=self.config_path,
                )
        except (FeedbackGraphBuildError, ValueError) as exc:
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=f"Could not construct the dynamic feedback graph: {exc}",
                    snapshot=source,
                ),
                session_id,
            )
        if graph.source_hash != source.checkpoint_key:
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason="The feedback graph is bound to a different source checkpoint.",
                    snapshot=source,
                    graph=graph,
                ),
                session_id,
            )
        if self.config.selection.strategy == "value-aware" and historical_costs:
            graph = _apply_historical_costs(graph, historical_costs)

        try:
            selection_budget = (
                self.config.budget
                if available_budget is None
                else max(0.0, available_budget)
            )
            if self.config.selection.strategy == "value-aware":
                graph_stage_wall = sum(item.duration_s for item in graph.stage_costs)
                graph_stage_model_cost = sum(
                    item.estimated_cost_usd
                    for item in graph.stage_costs
                    if item.estimated_cost_usd is not None
                )
                selection = self.selector.select(
                    graph,
                    budget=selection_budget,
                    policy=self.config.selection,
                    available_wall_time_s=(
                        None
                        if available_wall_time_s is None
                        else max(0.0, available_wall_time_s - graph_stage_wall)
                    ),
                    available_model_cost_usd=(
                        None
                        if available_model_cost_usd is None
                        else max(
                            0.0,
                            available_model_cost_usd - graph_stage_model_cost,
                        )
                    ),
                )
            else:
                selection = self.selector.select(
                    graph,
                    budget=selection_budget,
                    policy=self.config.selection,
                )
        except InvalidFeedbackGraph as exc:
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=f"The dynamically constructed feedback graph is invalid: {exc}",
                    snapshot=source,
                    graph=graph,
                ),
                session_id,
            )
        if not selection.verifier_ids:
            if (
                not selection.feasible
                and self.config.selection.strategy == "value-aware"
            ):
                if graph.promotion is not None:
                    reason = (
                        "The repaired candidate requires prior-feedback revalidation, but no "
                        "eligible promotion verifier fits the remaining task-epoch budget."
                    )
                else:
                    reason = (
                        "No verifier was feasible inside the remaining task-epoch resource "
                        "budget. GRAFT did not make a No-Op value judgment for this checkpoint."
                    )
                return self._finish(
                    Decision(
                        kind=DecisionKind.UNRESOLVED,
                        reason=reason,
                        snapshot=source,
                        graph=graph,
                        selection=selection,
                    ),
                    session_id,
                )
            if selection.no_op and self.config.selection.strategy == "value-aware":
                return self._finish(
                    Decision(
                        kind=DecisionKind.ALLOW,
                        reason=(
                            "GRAFT selected No-Op because no verifier had positive conservative "
                            "marginal net value over the producer evidence and execution cost."
                        ),
                        snapshot=source,
                        graph=graph,
                        selection=selection,
                    ),
                    session_id,
                )
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=(
                        "The task-specific registry contains no verifier with positive expected "
                        "value inside the evidence budget."
                    ),
                    snapshot=source,
                    graph=graph,
                    selection=selection,
                ),
                session_id,
            )

        by_id = {item.verifier_id: item for item in graph.verifiers}

        def run_one(verifier_id: str) -> VerifierResult:
            return self.executor.run(
                by_id[verifier_id],
                source,
                requirements=requirements,
                graph=graph,
                config_path=self.config_path,
                environment_fingerprint=self.config.environment_fingerprint,
            )

        with ThreadPoolExecutor(max_workers=min(4, len(selection.verifier_ids))) as pool:
            results = tuple(pool.map(run_one, selection.verifier_ids))

        stale_results = tuple(
            result
            for result in results
            if result.source_hash != source.checkpoint_key
        )
        if stale_results:
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=(
                        "Verifier evidence is bound to a different checkpoint: "
                        + ", ".join(item.verifier_id for item in stale_results)
                    ),
                    snapshot=source,
                    graph=graph,
                    selection=selection,
                    results=results,
                ),
                session_id,
            )

        current = self.snapshot(
            Path(source.root),
            requirements,
            baseline_tree_hash=source.baseline_tree_hash,
            baseline_files=source.baseline_files,
            baseline_file_hashes=source.baseline_file_hashes,
            baseline_archive_path=source.baseline_archive_path,
        )
        if current.checkpoint_key != source.checkpoint_key:
            return self._finish(
                Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=(
                        "The producer workspace changed while verification was running; evidence "
                        "is bound to the previous checkpoint and cannot gate the new state."
                    ),
                    snapshot=source,
                    graph=graph,
                    selection=selection,
                    results=results,
                ),
                session_id,
            )

        decision = self._decide(source, graph, selection, results)
        return self._finish(decision, session_id)

    def _decide(
        self,
        source: SourceSnapshot,
        graph: FeedbackGraph,
        selection: Selection,
        results: tuple[VerifierResult, ...],
    ) -> Decision:
        promotion_outcome = _assess_promotion(graph, results)
        blocking = tuple(
            result
            for result in results
            if result.verdict == Verdict.FAIL
            and result.blocking
            and result.reproducible
        )
        if blocking:
            behaviors = {item.behavior_id: item for item in graph.behaviors}
            failures = {item.failure_mode_id: item for item in graph.failure_modes}
            lines = [
                "[GRAFT Verification Failure]",
                f"Checkpoint: {source.checkpoint_key}",
                "Reproducible blocking evidence:",
            ]
            for result in blocking:
                lines.append(f"- Verifier {result.verifier_id}: {result.summary}")
                for failure_id in result.failure_modes:
                    failure = failures.get(failure_id)
                    if failure is None:
                        continue
                    behavior = behaviors.get(failure.behavior_id)
                    if behavior is not None:
                        lines.append(f"  Violated behavior: {behavior.description}")
                    lines.append(f"  Failure mode: {failure.description}")
                reproductions = [
                    " ".join(item.command)
                    for item in result.evidence
                    if item.command
                    and item.oracle_origin
                    in {
                        "authoritative_runtime",
                        "baseline_repository",
                        "requirement_derived_runtime",
                    }
                    and set(item.failure_modes) & set(result.failure_modes)
                ]
                if result.command:
                    reproductions.append(" ".join(result.command))
                if reproductions:
                    lines.append(f"  Reproduce: {reproductions[0]}")
            lines.append(
                "Inspect and resolve the evidenced behavior. Choose the repair strategy yourself. "
                "Preserve behaviors already established by the raw task and unchanged baseline "
                "oracles; do not infer stricter preconditions or protocols than those sources "
                "state. Rerun the same authoritative reproduction after the repair. "
                "Do not invoke GRAFT verification manually; the lifecycle Stop hook will verify "
                "the repaired checkpoint within the configured feedback-round budget."
            )
            return Decision(
                kind=DecisionKind.CONTINUE_WITH_EVIDENCE,
                reason="\n".join(lines),
                snapshot=source,
                graph=graph,
                selection=selection,
                results=results,
                promotion_outcome=promotion_outcome,
            )

        if graph.promotion is not None:
            if promotion_outcome != PromotionOutcome.FIXED_AND_PRESERVED:
                return Decision(
                    kind=DecisionKind.UNRESOLVED,
                    reason=(
                        "The repaired checkpoint was not promoted: no required revalidation "
                        "verifier produced executed eligible evidence that the prior finding is "
                        "fixed while required behavior remains preserved."
                    ),
                    snapshot=source,
                    graph=graph,
                    selection=selection,
                    results=results,
                    promotion_outcome=promotion_outcome,
                )
        errors = tuple(result for result in results if result.verdict == Verdict.ERROR)
        suspicions = tuple(
            result
            for result in results
            if result.verdict == Verdict.FAIL and result not in blocking
        )
        abstentions = tuple(
            result for result in results if result.verdict == Verdict.ABSTAIN
        )
        blocking_uncertainties = tuple(
            item
            for item in graph.uncertainties
            if item.startswith(("coverage_gap:", "lineage_uncertainty:"))
        )
        if errors or suspicions or abstentions or blocking_uncertainties:
            detail: list[str] = []
            detail.extend(f"{item.verifier_id}: {item.summary}" for item in errors)
            detail.extend(
                f"{item.verifier_id}: unconfirmed finding: {item.summary}"
                for item in suspicions
            )
            detail.extend(
                f"{item.verifier_id}: abstained: {item.summary}"
                for item in abstentions
            )
            detail.extend(blocking_uncertainties)
            return Decision(
                kind=DecisionKind.UNRESOLVED,
                reason=(
                    "Verification found no reproducible blocking counterexample, but the "
                    "evidence remains incomplete. " + "; ".join(detail)
                ),
                snapshot=source,
                graph=graph,
                selection=selection,
                results=results,
                promotion_outcome=promotion_outcome,
            )

        if selection.residual_risk > self.config.selection.residual_risk_threshold:
            return Decision(
                kind=DecisionKind.UNRESOLVED,
                reason=(
                    f"Residual modeled risk {selection.residual_risk:.3f} exceeds the configured "
                    f"threshold {self.config.selection.residual_risk_threshold:.3f}."
                ),
                snapshot=source,
                graph=graph,
                selection=selection,
                results=results,
                promotion_outcome=promotion_outcome,
            )

        return Decision(
            kind=DecisionKind.ALLOW,
            reason=(
                "The selected task-specific verifiers completed without a reproducible failure "
                "and modeled residual risk is within threshold. This is evidence, not proof."
            ),
            snapshot=source,
            graph=graph,
            selection=selection,
            results=results,
            promotion_outcome=promotion_outcome,
        )

    def _finish(self, decision: Decision, session_id: str) -> Decision:
        safe_session = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:160]
        report_base = self.report_root or (
            Path(decision.snapshot.root) / ".graft" / "reports"
        )
        report_dir = report_base / safe_session
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{decision.snapshot.checkpoint_key[:16]}.json"
        suffix = 1
        while report_path.exists():
            report_path = report_dir / (
                f"{decision.snapshot.checkpoint_key[:16]}-{suffix:03d}.json"
            )
            suffix += 1
        completed = replace(decision, report_path=str(report_path.resolve()))
        report_path.write_text(
            json.dumps(to_jsonable(completed), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if completed.kind == DecisionKind.CONTINUE_WITH_EVIDENCE:
            reason = completed.reason + f"\nFull report: {completed.report_path}"
            completed = replace(completed, reason=reason)
        return completed


def _apply_historical_costs(
    graph: FeedbackGraph,
    historical_costs: Mapping[str, HistoricalCostEstimate],
) -> FeedbackGraph:
    calibrated = []
    for verifier in graph.verifiers:
        key = verifier.template_id or verifier.verifier_id
        observed = historical_costs.get(key)
        if observed is None:
            calibrated.append(verifier)
            continue
        estimate = replace(
            verifier.value_estimate,
            predicted_duration_s=observed.duration_s,
            predicted_model_cost_usd=(
                observed.model_cost_usd
                if observed.model_cost_usd is not None
                else verifier.value_estimate.predicted_model_cost_usd
            ),
        )
        calibrated.append(replace(verifier, value_estimate=estimate))
    return replace(graph, verifiers=tuple(calibrated))


def _assess_promotion(
    graph: FeedbackGraph, results: tuple[VerifierResult, ...]
) -> PromotionOutcome | None:
    if graph.promotion is None:
        return None
    by_id = {item.verifier_id: item for item in graph.verifiers}
    relevant = tuple(
        result
        for result in results
        if by_id.get(result.verifier_id) is not None
        and by_id[result.verifier_id].revalidates_feedback
    )
    if any(item.promotion_outcome == PromotionOutcome.REGRESSED for item in relevant):
        return PromotionOutcome.REGRESSED
    if any(
        item.promotion_outcome == PromotionOutcome.NOT_FIXED
        or (item.verdict == Verdict.FAIL and item.reproducible)
        for item in relevant
    ):
        return PromotionOutcome.NOT_FIXED
    if any(
        item.promotion_outcome == PromotionOutcome.FIXED_AND_PRESERVED
        and item.verdict == Verdict.PASS
        and item.executed_evidence
        for item in relevant
    ):
        return PromotionOutcome.FIXED_AND_PRESERVED
    return PromotionOutcome.UNRESOLVED
