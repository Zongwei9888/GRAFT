from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.agent_reward_bench.run_selection import (
    DEFAULT_PROTOCOL,
    MatrixRow,
    SelectionModel,
    evaluate,
    load_matrix,
    run_protocol,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_CROSSFIT_PROTOCOL = ROOT / "crossfit_protocol.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen post-hoc grouped cross-fit robustness analysis."
    )
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_CROSSFIT_PROTOCOL)
    parser.add_argument("--primary-protocol", type=Path, default=DEFAULT_PROTOCOL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    crossfit_protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    primary_protocol = json.loads(args.primary_protocol.read_text(encoding="utf-8"))
    expected_primary_hash = str(crossfit_protocol["primary_protocol_sha256"])
    observed_primary_hash = _file_hash(args.primary_protocol)
    if observed_primary_hash != expected_primary_hash:
        raise SystemExit(
            "Primary protocol hash mismatch: cross-fit analysis must use its frozen source."
        )
    payload = {
        "analysis_kind": "posthoc_group_crossfit",
        "confirmatory": False,
        "changes_primary_result": False,
        "crossfit_protocol_hash": _file_hash(args.protocol),
        "primary_protocol_hash": observed_primary_hash,
        "matrix_hash": _file_hash(args.matrix),
        "results": run_crossfit(
            load_matrix(args.matrix), crossfit_protocol, primary_protocol
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["results"]["summary"], indent=2, sort_keys=True))
    return 0


def run_crossfit(
    rows: Sequence[MatrixRow],
    crossfit_protocol: Mapping[str, Any],
    primary_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    fold_config = crossfit_protocol["folds"]
    fold_count = int(fold_config["count"])
    salt = str(fold_config["salt"])
    assignments = {
        row.group_id: _fold(salt, row.group_id, fold_count) for row in rows
    }
    folds = []
    for fold_id in range(fold_count):
        training = tuple(row for row in rows if assignments[row.group_id] != fold_id)
        heldout = tuple(row for row in rows if assignments[row.group_id] == fold_id)
        if not training or not heldout:
            raise ValueError(f"Fold {fold_id} has an empty training or held-out partition")
        folds.append(
            _run_fold(
                fold_id,
                training,
                heldout,
                crossfit_protocol,
                primary_protocol,
            )
        )

    pairwise_comparisons = tuple(
        item
        for fold in folds
        for item in fold["calibration_by_cardinality"]
        if item["cardinality"] >= 2
    )
    pairwise_wins = sum(
        item["pairwise_heldout_mae"] < item["independent_heldout_mae"]
        for item in pairwise_comparisons
    )
    high_order_comparisons = tuple(
        item for item in pairwise_comparisons if item["cardinality"] >= 3
    )
    high_order_wins = sum(
        item["high_order_heldout_mae"] < item["pairwise_heldout_mae"]
        for item in high_order_comparisons
    )
    pairwise_required = math.ceil(0.8 * len(pairwise_comparisons))
    # The v1 prose accidentally applied the same 12/15 rule to high-order even
    # though the high-order model is identical to pairwise at cardinality two.
    # Preserve that frozen threshold, but do not emit a pass/fail claim for a
    # structurally unreachable rule after the result has been observed.
    frozen_high_order_required = pairwise_required
    high_order_rule_valid = frozen_high_order_required <= len(high_order_comparisons)
    return {
        "summary": {
            "folds": fold_count,
            "task_groups": len(assignments),
            "group_overlap": 0,
            "pairwise_fold_size_comparisons": len(pairwise_comparisons),
            "pairwise_beats_independence": pairwise_wins,
            "pairwise_wins_required_by_frozen_rule": pairwise_required,
            "pairwise_measurement_supported": pairwise_wins >= pairwise_required,
            "high_order_applicable_fold_size_comparisons": len(
                high_order_comparisons
            ),
            "high_order_beats_pairwise": high_order_wins,
            "frozen_high_order_wins_required": frozen_high_order_required,
            "high_order_decision_rule_structurally_valid": high_order_rule_valid,
            "high_order_candidate_supported": (
                high_order_wins >= frozen_high_order_required
                if high_order_rule_valid
                else None
            ),
            "positive_method_claim": False,
        },
        "aggregate_calibration": _aggregate_calibration(folds),
        "folds": folds,
    }


def _run_fold(
    fold_id: int,
    training: Sequence[MatrixRow],
    heldout: Sequence[MatrixRow],
    crossfit_protocol: Mapping[str, Any],
    primary_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    judges = tuple(sorted(training[0].predictions))
    selection = primary_protocol["selection"]
    model = SelectionModel(
        training,
        judges,
        prior_strength=float(selection["high_order_prior_strength"]),
        lineage_overlap_threshold=int(selection["lineage_pair_overlap_threshold"]),
        pair_residual_threshold=float(
            selection["pair_residual_nomination_threshold"]
        ),
    )
    calibration = []
    for cardinality in tuple(int(item) for item in crossfit_protocol["cardinalities"]):
        portfolios = tuple(itertools.combinations(judges, cardinality))
        calibration.append(
            {
                "cardinality": cardinality,
                "portfolio_count": len(portfolios),
                "high_order_nominated": sum(
                    model.is_high_order_nominated(item) for item in portfolios
                ),
                "independent_heldout_mae": _mae(
                    portfolios, model.independent_recall, heldout
                ),
                "pairwise_heldout_mae": _mae(
                    portfolios, model.pairwise_recall, heldout
                ),
                "high_order_heldout_mae": _mae(
                    portfolios, model.high_order_recall, heldout
                ),
            }
        )

    selector_protocol = copy.deepcopy(primary_protocol)
    selector = crossfit_protocol["selector"]
    selector_protocol["primary_constraints"]["set_fpr"] = float(
        selector["set_fpr"]
    )
    selector_protocol["primary_constraints"]["cardinality_budgets"] = list(
        selector["cardinality_budgets"]
    )
    selector_protocol["inference"]["paired_group_bootstrap_samples"] = int(
        selector["paired_group_bootstrap_samples"]
    )
    selector_results = run_protocol(training, heldout, selector_protocol)
    return {
        "fold": fold_id,
        "training_rows": len(training),
        "heldout_rows": len(heldout),
        "training_groups": len({row.group_id for row in training}),
        "heldout_groups": len({row.group_id for row in heldout}),
        "training_failures": sum(row.label == 0 for row in training),
        "heldout_failures": sum(row.label == 0 for row in heldout),
        "calibration_by_cardinality": calibration,
        "selector_budgets": selector_results["budgets"],
    }


def _aggregate_calibration(folds: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cardinalities = sorted(
        {
            item["cardinality"]
            for fold in folds
            for item in fold["calibration_by_cardinality"]
        }
    )
    output = []
    for cardinality in cardinalities:
        items = tuple(
            item
            for fold in folds
            for item in fold["calibration_by_cardinality"]
            if item["cardinality"] == cardinality
        )
        output.append(
            {
                "cardinality": cardinality,
                "folds": len(items),
                "mean_independent_heldout_mae": statistics.mean(
                    item["independent_heldout_mae"] for item in items
                ),
                "mean_pairwise_heldout_mae": statistics.mean(
                    item["pairwise_heldout_mae"] for item in items
                ),
                "mean_high_order_heldout_mae": statistics.mean(
                    item["high_order_heldout_mae"] for item in items
                ),
                "mean_nomination_density": statistics.mean(
                    item["high_order_nominated"] / item["portfolio_count"]
                    for item in items
                ),
            }
        )
    return output


def _mae(
    portfolios: Sequence[Sequence[str]],
    predictor,
    rows: Sequence[MatrixRow],
) -> float:
    return statistics.mean(
        abs(predictor(portfolio) - evaluate(rows, portfolio).recall)
        for portfolio in portfolios
    )


def _fold(salt: str, group_id: str, fold_count: int) -> int:
    digest = hashlib.sha256(f"{salt}:{group_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % fold_count


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
