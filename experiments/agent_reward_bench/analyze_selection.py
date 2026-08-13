from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.agent_reward_bench.run_selection import (
    DEFAULT_PROTOCOL,
    MatrixRow,
    SelectionModel,
    evaluate,
    load_matrix,
    split_rows,
)


DEFAULT_FPR_GRID = (0.10, 0.11, 0.12, 0.15, 0.20, 0.25, 0.30)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run post-hoc diagnostics for a frozen AgentRewardBench selection result. "
            "This does not replace or amend the primary protocol."
        )
    )
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    development, test = split_rows(load_matrix(args.matrix), protocol)
    payload = {
        "analysis_kind": "posthoc_diagnostic",
        "changes_primary_result": False,
        "protocol_hash": _file_hash(args.protocol),
        "matrix_hash": _file_hash(args.matrix),
        "diagnostics": analyze(development, test, protocol),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = payload["diagnostics"]["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def analyze(
    development: Sequence[MatrixRow],
    test: Sequence[MatrixRow],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    judges = tuple(sorted(development[0].predictions))
    selection = protocol["selection"]
    model = SelectionModel(
        development,
        judges,
        prior_strength=float(selection["high_order_prior_strength"]),
        lineage_overlap_threshold=int(selection["lineage_pair_overlap_threshold"]),
        pair_residual_threshold=float(
            selection["pair_residual_nomination_threshold"]
        ),
    )
    max_cardinality = max(
        int(item) for item in protocol["primary_constraints"]["cardinality_budgets"]
    )
    calibration = _calibration(model, development, test, judges, max_cardinality)
    fpr_generalization = _fpr_generalization(
        development,
        test,
        judges,
        max_cardinality,
        DEFAULT_FPR_GRID,
    )
    primary_alpha = float(protocol["primary_constraints"]["set_fpr"])
    consensus = _consensus_exploration(
        development,
        test,
        judges,
        max_cardinality,
        primary_alpha,
    )
    edge_diagnostics = _edge_diagnostics(model, judges)
    benchmark_calibration = _stratified_calibration(
        model,
        test,
        judges,
        max_cardinality,
        field="benchmark",
    )
    producer_calibration = _stratified_calibration(
        model,
        test,
        judges,
        max_cardinality,
        field="agent",
    )
    benchmark_comparisons = tuple(
        item
        for stratum in benchmark_calibration
        for item in stratum["calibration_by_cardinality"]
        if item["cardinality"] >= 2
    )
    calibration_by_cardinality = {
        item["cardinality"]: item for item in calibration
    }
    triples = calibration_by_cardinality.get(3, {})
    quadruples = calibration_by_cardinality.get(4, {})
    return {
        "summary": {
            "primary_set_fpr": primary_alpha,
            "primary_or_rule_has_feasible_single": any(
                evaluate(development, (judge,)).set_fpr <= primary_alpha
                for judge in judges
            ),
            "pairwise_beats_independence_test_mae_at_every_multi_judge_size": all(
                item["pairwise_test_mae"] < item["independent_test_mae"]
                for item in calibration
                if item["cardinality"] >= 2
            ),
            "high_order_beats_pairwise_test_mae_at_every_high_order_size": all(
                item["high_order_test_mae"] < item["pairwise_test_mae"]
                for item in calibration
                if item["cardinality"] >= 3
            ),
            "nominated_triples": triples.get("high_order_nominated"),
            "total_triples": triples.get("portfolio_count"),
            "nominated_quadruples": quadruples.get("high_order_nominated"),
            "total_quadruples": quadruples.get("portfolio_count"),
            "benchmark_size_strata_where_pairwise_beats_independence": sum(
                item["pairwise_test_mae"] < item["independent_test_mae"]
                for item in benchmark_comparisons
            ),
            "benchmark_size_strata_compared": len(benchmark_comparisons),
        },
        "edge_diagnostics": edge_diagnostics,
        "calibration_by_cardinality": calibration,
        "development_to_test_fpr_generalization": fpr_generalization,
        "test_calibration_by_benchmark": benchmark_calibration,
        "test_calibration_by_producer": producer_calibration,
        "consensus_rule_exploration": {
            "posthoc": True,
            "not_part_of_frozen_primary_method": True,
            "operating_set_fpr": primary_alpha,
            "best_development_portfolio_by_cardinality": consensus,
        },
    }


def _calibration(
    model: SelectionModel,
    development: Sequence[MatrixRow],
    test: Sequence[MatrixRow],
    judges: Sequence[str],
    max_cardinality: int,
) -> list[dict[str, Any]]:
    output = []
    for cardinality in range(1, max_cardinality + 1):
        portfolios = tuple(itertools.combinations(judges, cardinality))
        output.append(
            {
                "cardinality": cardinality,
                "portfolio_count": len(portfolios),
                "high_order_nominated": sum(
                    model.is_high_order_nominated(item) for item in portfolios
                ),
                "independent_development_mae": _mae(
                    portfolios, model.independent_recall, development
                ),
                "pairwise_development_mae": _mae(
                    portfolios, model.pairwise_recall, development
                ),
                "high_order_development_mae": _mae(
                    portfolios, model.high_order_recall, development
                ),
                "independent_test_mae": _mae(
                    portfolios, model.independent_recall, test
                ),
                "pairwise_test_mae": _mae(
                    portfolios, model.pairwise_recall, test
                ),
                "high_order_test_mae": _mae(
                    portfolios, model.high_order_recall, test
                ),
                "mean_test_recall": statistics.mean(
                    evaluate(test, item).recall for item in portfolios
                ),
                "mean_independent_predicted_recall": statistics.mean(
                    model.independent_recall(item) for item in portfolios
                ),
                "mean_pairwise_predicted_recall": statistics.mean(
                    model.pairwise_recall(item) for item in portfolios
                ),
                "mean_high_order_predicted_recall": statistics.mean(
                    model.high_order_recall(item) for item in portfolios
                ),
            }
        )
    return output


def _fpr_generalization(
    development: Sequence[MatrixRow],
    test: Sequence[MatrixRow],
    judges: Sequence[str],
    max_cardinality: int,
    thresholds: Sequence[float],
) -> list[dict[str, Any]]:
    output = []
    for threshold in thresholds:
        by_cardinality = []
        for cardinality in range(1, max_cardinality + 1):
            portfolios = tuple(itertools.combinations(judges, cardinality))
            development_feasible = tuple(
                item
                for item in portfolios
                if evaluate(development, item).set_fpr <= threshold
            )
            test_feasible = tuple(
                item
                for item in development_feasible
                if evaluate(test, item).set_fpr <= threshold
            )
            by_cardinality.append(
                {
                    "cardinality": cardinality,
                    "development_feasible": len(development_feasible),
                    "also_test_feasible": len(test_feasible),
                    "retention_rate": (
                        len(test_feasible) / len(development_feasible)
                        if development_feasible
                        else None
                    ),
                    "mean_test_fpr_of_development_feasible": (
                        statistics.mean(
                            evaluate(test, item).set_fpr
                            for item in development_feasible
                        )
                        if development_feasible
                        else None
                    ),
                }
            )
        output.append({"set_fpr": threshold, "by_cardinality": by_cardinality})
    return output


def _consensus_exploration(
    development: Sequence[MatrixRow],
    test: Sequence[MatrixRow],
    judges: Sequence[str],
    max_cardinality: int,
    alpha: float,
) -> list[dict[str, Any]]:
    output = []
    for cardinality in range(2, max_cardinality + 1):
        candidates = []
        for portfolio in itertools.combinations(judges, cardinality):
            for minimum_votes in range(2, cardinality + 1):
                development_metrics = _evaluate_votes(
                    development, portfolio, minimum_votes
                )
                if development_metrics["set_fpr"] <= alpha:
                    candidates.append(
                        (portfolio, minimum_votes, development_metrics)
                    )
        if not candidates:
            output.append(
                {"cardinality": cardinality, "status": "no_feasible_portfolio"}
            )
            continue
        portfolio, minimum_votes, development_metrics = min(
            candidates,
            key=lambda item: (
                -item[2]["recall"],
                item[2]["set_fpr"],
                item[1],
                item[0],
            ),
        )
        output.append(
            {
                "cardinality": cardinality,
                "status": "ok",
                "minimum_failure_votes": minimum_votes,
                "portfolio": list(portfolio),
                "development": development_metrics,
                "test": _evaluate_votes(test, portfolio, minimum_votes),
            }
        )
    return output


def _evaluate_votes(
    rows: Sequence[MatrixRow],
    portfolio: Sequence[str],
    minimum_votes: int,
) -> dict[str, Any]:
    failures = tuple(row for row in rows if row.label == 0)
    successes = tuple(row for row in rows if row.label == 1)

    def rejects(row: MatrixRow) -> bool:
        return sum(row.predictions[item] == 0 for item in portfolio) >= minimum_votes

    detected = sum(rejects(row) for row in failures)
    false_rejects = sum(rejects(row) for row in successes)
    return {
        "failures": len(failures),
        "successes": len(successes),
        "detected_failures": detected,
        "false_rejects": false_rejects,
        "recall": detected / len(failures) if failures else 0.0,
        "set_fpr": false_rejects / len(successes) if successes else 0.0,
    }


def _edge_diagnostics(
    model: SelectionModel, judges: Sequence[str]
) -> dict[str, Any]:
    pairs = tuple(itertools.combinations(judges, 2))
    lineage_edges = tuple(
        item
        for item in pairs
        if model._lineage_overlap(*item) >= model.lineage_overlap_threshold
    )
    residual_edges = tuple(
        item
        for item in pairs
        if model._pair_residual(*item) >= model.pair_residual_threshold
    )
    return {
        "pair_count": len(pairs),
        "lineage_edge_count": len(lineage_edges),
        "empirical_residual_edge_count": len(residual_edges),
        "union_edge_count": len(set(lineage_edges) | set(residual_edges)),
        "lineage_overlap_histogram": {
            str(value): sum(model._lineage_overlap(*item) == value for item in pairs)
            for value in sorted({model._lineage_overlap(*item) for item in pairs})
        },
    }


def _stratified_calibration(
    model: SelectionModel,
    rows: Sequence[MatrixRow],
    judges: Sequence[str],
    max_cardinality: int,
    *,
    field: str,
) -> list[dict[str, Any]]:
    values = sorted({str(getattr(row, field)) for row in rows})
    output = []
    for value in values:
        stratum = tuple(row for row in rows if str(getattr(row, field)) == value)
        if not any(row.label == 0 for row in stratum):
            continue
        by_cardinality = []
        for cardinality in range(1, max_cardinality + 1):
            portfolios = tuple(itertools.combinations(judges, cardinality))
            by_cardinality.append(
                {
                    "cardinality": cardinality,
                    "independent_test_mae": _mae(
                        portfolios, model.independent_recall, stratum
                    ),
                    "pairwise_test_mae": _mae(
                        portfolios, model.pairwise_recall, stratum
                    ),
                    "high_order_test_mae": _mae(
                        portfolios, model.high_order_recall, stratum
                    ),
                }
            )
        output.append(
            {
                "stratum": value,
                "rows": len(stratum),
                "failures": sum(row.label == 0 for row in stratum),
                "successes": sum(row.label == 1 for row in stratum),
                "calibration_by_cardinality": by_cardinality,
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


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
