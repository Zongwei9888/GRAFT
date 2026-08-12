from __future__ import annotations

import json
from pathlib import Path

from experiments.promotion_e2e.run import (
    ARTIFACT_ROOT,
    EXPERIMENT,
    _experiment_config,
    _snapshot,
    _write_json,
)
from experiments.value_aware_gate.run_selector_pilot import (
    FrozenGraphBuilder,
    _baseline_manifest,
)
from graft.codex.cli_runner import CliCodexRunner
from graft.controller import GraftController
from graft.registry import load_config
from graft.replay import load_report_graph
from graft.schema import RunConfig, TurnResult, to_jsonable
from graft.verifiers import VerifierExecutor


class RecordingRunner:
    """Capture the unmodified Codex event stream used by evidence binding."""

    def __init__(self) -> None:
        self.inner = CliCodexRunner()
        self.turns: list[TurnResult] = []

    def start_thread(
        self, prompt: str, cwd: Path, config: RunConfig | None = None
    ) -> TurnResult:
        turn = self.inner.start_thread(prompt, cwd, config)
        self.turns.append(turn)
        return turn


def main() -> int:
    saved = json.loads(
        (ARTIFACT_ROOT / "writable-resume-result.json").read_text(encoding="utf-8")
    )
    original = json.loads(
        (ARTIFACT_ROOT / "result.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((EXPERIMENT / "task.json").read_text(encoding="utf-8"))
    requirements = (str(manifest["task"]),)
    model = str(manifest["model"])
    workspace = Path(saved["workspace"])
    if not workspace.is_dir():
        raise RuntimeError(f"repaired workspace is unavailable: {workspace}")

    config_path = workspace.parent / "value-aware.json"
    config_path.write_text(
        json.dumps(_experiment_config(model), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    baseline_archive = Path(
        original["first_decision"]["snapshot"]["baseline_archive_path"]
    )
    baseline_tree, baseline_files, baseline_hashes = _baseline_manifest(
        baseline_archive
    )
    snapshot = _snapshot(
        workspace,
        requirements,
        config_path,
        config.environment_fingerprint,
        baseline_tree,
        baseline_files,
        baseline_hashes,
        baseline_archive,
    )
    if snapshot.checkpoint_key != saved["checkpoint_after"]:
        raise RuntimeError(
            "repaired checkpoint changed before audit: "
            f"expected {saved['checkpoint_after']}, observed {snapshot.checkpoint_key}"
        )

    graph = load_report_graph(Path(saved["promotion_decision"]["report_path"]))
    runner = RecordingRunner()
    controller = GraftController(
        config,
        config_path=config_path,
        graph_builder=FrozenGraphBuilder(graph),
        executor=VerifierExecutor(codex_runner=runner),
        report_root=ARTIFACT_ROOT / "promotion-event-audit-reports",
    )
    print("[PROMOTION-AUDIT] replaying the frozen promotion verifier", flush=True)
    decision = controller.verify(
        workspace,
        requirements=requirements,
        session_id=str(saved["thread_id_after"]),
        snapshot=snapshot,
        available_budget=4.0,
        promotion=graph.promotion,
    )
    payload = {
        "diagnostic": "posthoc-promotion-event-audit",
        "not_preregistered": True,
        "checkpoint": snapshot.checkpoint_key,
        "decision": to_jsonable(decision),
        "turns": [to_jsonable(turn) for turn in runner.turns],
    }
    _write_json(ARTIFACT_ROOT / "promotion-event-audit.json", payload)
    print(
        "[PROMOTION-AUDIT] "
        f"outcome={decision.promotion_outcome}; turns={len(runner.turns)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
