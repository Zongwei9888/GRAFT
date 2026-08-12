from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from graft.controller import GraftController
from graft.evidence.snapshot import freeze_source
from graft.registry import default_value_aware_config_payload, load_config
from graft.replay import _feedback_graph
from graft.schema import to_jsonable
from graft.selection import ValueAwareSelector
from graft.selection.value_aware import expected_net_value

from run_selector_pilot import (
    FrozenGraphBuilder,
    _baseline_manifest,
    _restore_checkpoint,
)


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=EXPERIMENT / "results" / "m2_selector_pilot.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=EXPERIMENT / "results" / "m2_posthoc_resource_diagnostic.json",
    )
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    graph = _feedback_graph(source["online_value_aware_decision"]["graph"])
    checkpoint = _repository_path(source["checkpoint_archive"]["path"])
    baseline = EXPERIMENT / "frozen_inputs" / "baseline.tar.gz"
    task = json.loads((EXPERIMENT / "selector_task.json").read_text(encoding="utf-8"))[
        "task"
    ]

    with tempfile.TemporaryDirectory(prefix="graft-m2-diagnostic-") as directory:
        run_root = Path(directory)
        workspace = run_root / "workspace"
        workspace.mkdir()
        _restore_checkpoint(checkpoint, workspace)
        config_path = run_root / "config.json"
        config_path.write_text(
            json.dumps(
                _model_pinned_config(), ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        config = load_config(config_path)
        baseline_tree, baseline_files, baseline_hashes = _baseline_manifest(baseline)
        snapshot = freeze_source(
            workspace,
            requirements=(task,),
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
            baseline_tree_hash=baseline_tree,
            baseline_files=baseline_files,
            baseline_file_hashes=baseline_hashes,
            baseline_archive_path=str(baseline.resolve()),
        )
        if snapshot.checkpoint_key != graph.source_hash:
            raise RuntimeError("restored checkpoint does not match the frozen graph")

        selector = ValueAwareSelector()
        intrinsic = selector.select(
            graph, budget=config.budget, policy=config.selection
        )
        verifier_budget_only = selector.select(
            graph,
            budget=config.budget,
            policy=config.selection,
            available_wall_time_s=config.selection.wall_time_budget_s,
            available_model_cost_usd=config.selection.model_cost_budget_usd,
        )
        graph_wall = sum(item.duration_s for item in graph.stage_costs)
        remaining_wall = max(
            0.0, config.selection.wall_time_budget_s - graph_wall
        )
        post_fix_resource_selection = selector.select(
            graph,
            budget=config.budget,
            policy=config.selection,
            available_wall_time_s=remaining_wall,
            available_model_cost_usd=config.selection.model_cost_budget_usd,
        )
        controller = GraftController(
            config,
            config_path=config_path,
            graph_builder=FrozenGraphBuilder(graph),
            report_root=ROOT / "artifacts/value-aware-gate/diagnostic-reports",
        )
        post_fix_decision = controller.verify(
            workspace,
            requirements=(task,),
            session_id="m2-posthoc-resource-diagnostic",
            snapshot=snapshot,
            producer_evidence=graph.producer_evidence,
            available_budget=config.budget,
            available_wall_time_s=config.selection.wall_time_budget_s,
            available_model_cost_usd=config.selection.model_cost_budget_usd,
        )

    singleton_net_values = {}
    for verifier in graph.verifiers:
        singleton_net_values[verifier.verifier_id] = expected_net_value(
            graph, (verifier,), config.budget, config.selection
        )[0]

    payload = {
        "analysis_type": "posthoc_execution_free_resource_diagnostic",
        "input_result": str(args.input.resolve()),
        "checkpoint": graph.source_hash,
        "graph_stage_wall_time_s": graph_wall,
        "configured_task_epoch_wall_time_s": config.selection.wall_time_budget_s,
        "remaining_wall_time_s": remaining_wall,
        "intrinsic_no_resource_gate": to_jsonable(intrinsic),
        "verifier_only_resource_gate": to_jsonable(verifier_budget_only),
        "post_fix_after_graph_resource_gate": to_jsonable(
            post_fix_resource_selection
        ),
        "post_fix_controller_decision": to_jsonable(post_fix_decision),
        "singleton_net_values": singleton_net_values,
        "interpretation": (
            "The intrinsic arm estimates selector behavior on the frozen modeled graph. "
            "It is post-hoc and is not a causal utility result."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _model_pinned_config() -> dict:
    payload = default_value_aware_config_payload()
    for key in ("behavior_modeler", "verifier_planner", "completion_gate"):
        payload["modeling"][key]["model"] = "gpt-5.6-sol"
    for template in payload["verifier_templates"]:
        if template["kind"] != "command":
            template["model"] = "gpt-5.6-sol"
    return payload


def _repository_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
