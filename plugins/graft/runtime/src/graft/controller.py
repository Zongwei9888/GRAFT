from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from graft.evidence.snapshot import freeze_source
from graft.registry import GraftConfig, load_config
from graft.schema import (
    Decision,
    DecisionKind,
    Selection,
    SourceSnapshot,
    Verdict,
    VerifierResult,
    to_jsonable,
)
from graft.selection import ExactEmpiricalSelector
from graft.verifiers import VerifierExecutor


class GraftController:
    def __init__(
        self,
        config: GraftConfig,
        *,
        config_path: Path,
        selector: ExactEmpiricalSelector | None = None,
        executor: VerifierExecutor | None = None,
        report_root: Path | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path.resolve()
        self.selector = selector or ExactEmpiricalSelector()
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
        self, repo: Path, requirements: tuple[str, ...] = ()
    ) -> SourceSnapshot:
        return freeze_source(
            repo,
            requirements=requirements,
            config_path=self.config_path,
            environment_fingerprint=self.config.environment_fingerprint,
        )

    def verify(
        self,
        repo: Path,
        *,
        requirements: tuple[str, ...] = (),
        session_id: str = "manual",
        snapshot: SourceSnapshot | None = None,
    ) -> Decision:
        source = snapshot or self.snapshot(repo, requirements)
        if not self.config.enabled:
            return Decision(DecisionKind.ALLOW, "GRAFT is disabled.", source)
        if not self.config.verifiers:
            return self._finish(
                Decision(
                    DecisionKind.UNRESOLVED,
                    "No verifiers are configured.",
                    source,
                ),
                session_id,
            )
        if not self.config.calibration.failure_scenarios:
            return self._finish(
                Decision(
                    DecisionKind.UNRESOLVED,
                    "No failure calibration scenarios are configured; selection would be ungrounded.",
                    source,
                ),
                session_id,
            )

        selection = self.selector.select(
            list(self.config.verifiers),
            self.config.calibration,
            budget=self.config.budget,
            max_set_fpr=self.config.max_set_fpr,
        )
        if not selection.verifier_ids:
            return self._finish(
                Decision(
                    DecisionKind.UNRESOLVED,
                    "No non-empty verifier subset satisfies the configured constraints.",
                    source,
                    selection=selection,
                ),
                session_id,
            )

        by_id = {item.verifier_id: item for item in self.config.verifiers}
        schema_path = self._verdict_schema(Path(repo))

        def run_one(verifier_id: str) -> VerifierResult:
            return self.executor.run(
                by_id[verifier_id],
                source,
                requirements=requirements,
                config_path=self.config_path,
                verdict_schema=schema_path,
            )

        with ThreadPoolExecutor(max_workers=min(4, len(selection.verifier_ids))) as pool:
            results = tuple(pool.map(run_one, selection.verifier_ids))

        decision = self._decide(source, selection, results)
        return self._finish(decision, session_id)

    def _decide(
        self,
        source: SourceSnapshot,
        selection: Selection,
        results: tuple[VerifierResult, ...],
    ) -> Decision:
        blocking = [
            result
            for result in results
            if result.verdict == Verdict.FAIL
            and result.blocking
            and result.reproducible
        ]
        if blocking:
            lines = [
                "[GRAFT Verification Failure]",
                f"Checkpoint: {source.checkpoint_key}",
                "Reproducible blocking evidence:",
            ]
            for result in blocking:
                command = " ".join(result.command) if result.command else "see report"
                lines.append(f"- {result.verifier_id}: {result.summary}")
                lines.append(f"  Reproduce: {command}")
            lines.append(
                "Inspect and resolve the evidenced behavior. Choose the repair strategy yourself."
            )
            return Decision(
                DecisionKind.CONTINUE_WITH_EVIDENCE,
                "\n".join(lines),
                source,
                selection,
                results,
            )

        errors = [result for result in results if result.verdict == Verdict.ERROR]
        suspicions = [
            result
            for result in results
            if result.verdict == Verdict.FAIL and result not in blocking
        ]
        abstentions = [
            result for result in results if result.verdict == Verdict.ABSTAIN
        ]
        if errors or suspicions or abstentions:
            detail = []
            detail.extend(f"{item.verifier_id}: {item.summary}" for item in errors)
            detail.extend(
                f"{item.verifier_id}: unconfirmed finding: {item.summary}"
                for item in suspicions
            )
            detail.extend(
                f"{item.verifier_id}: abstained: {item.summary}"
                for item in abstentions
            )
            return Decision(
                DecisionKind.UNRESOLVED,
                "Verification did not produce a reproducible blocking failure, but some "
                "evidence is unresolved. " + "; ".join(detail),
                source,
                selection,
                results,
            )

        return Decision(
            DecisionKind.ALLOW,
            "Selected verifiers completed without a reproducible failure. This is evidence, not a proof of correctness.",
            source,
            selection,
            results,
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

    def _verdict_schema(self, repo: Path) -> Path:
        candidates = (
            self.config_path.parent.parent
            / "schemas"
            / "verifier_verdict.schema.json",
            repo / "schemas" / "verifier_verdict.schema.json",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        packaged = files("graft").joinpath(
            "resources", "verifier_verdict.schema.json"
        )
        return Path(str(packaged))
