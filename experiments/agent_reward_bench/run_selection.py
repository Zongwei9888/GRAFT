from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocol.json"
EPSILON = 1e-9


@dataclass(frozen=True)
class MatrixRow:
    trajectory_id: str
    benchmark: str
    task_id: str
    agent: str
    label: int
    predictions: Mapping[str, int]
    metadata: Mapping[str, Mapping[str, Any]]

    @property
    def group_id(self) -> str:
        return f"{self.benchmark}::{self.task_id}"


@dataclass(frozen=True)
class PortfolioMetrics:
    failures: int
    successes: int
    detected_failures: int
    false_rejects: int
    recall: float
    set_fpr: float
    accuracy: float


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen AgentRewardBench GRAFT selection-layer protocol."
    )
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    rows = load_matrix(args.matrix)
    development, test = split_rows(rows, protocol)
    results = run_protocol(development, test, protocol)
    payload = {
        "protocol_hash": _file_hash(args.protocol),
        "matrix_hash": _file_hash(args.matrix),
        "protocol": protocol,
        "split": split_audit(development, test),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["split"], ensure_ascii=False, indent=2))
    return 0


def load_matrix(path: Path) -> tuple[MatrixRow, ...]:
    rows: list[MatrixRow] = []
    judge_ids: tuple[str, ...] | None = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if raw.get("label") is None:
                continue
            label = int(raw["label"])
            judgments = raw["judgments"]
            if label not in {0, 1} or not isinstance(judgments, dict):
                raise ValueError("invalid label or judgments")
            current_ids = tuple(sorted(str(item) for item in judgments))
            if judge_ids is None:
                judge_ids = current_ids
            if current_ids != judge_ids:
                raise ValueError("judge columns are incomplete or inconsistent")
            predictions: dict[str, int] = {}
            metadata: dict[str, Mapping[str, Any]] = {}
            for judge, judgment in judgments.items():
                if not isinstance(judgment, dict):
                    raise ValueError(f"invalid judgment for {judge}")
                prediction = int(judgment["prediction"])
                if prediction not in {0, 1}:
                    raise ValueError(f"invalid prediction for {judge}")
                predictions[str(judge)] = prediction
                metadata[str(judge)] = judgment
            rows.append(
                MatrixRow(
                    trajectory_id=str(raw["trajectory_id"]),
                    benchmark=str(raw["benchmark"]),
                    task_id=str(raw["task_id"]),
                    agent=str(raw.get("agent", "unknown")),
                    label=label,
                    predictions=predictions,
                    metadata=metadata,
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid complete matrix row {line_number}: {exc}") from exc
    if not rows:
        raise ValueError(f"Matrix is empty: {path}")
    return tuple(rows)


def split_rows(
    rows: Iterable[MatrixRow], protocol: Mapping[str, Any]
) -> tuple[tuple[MatrixRow, ...], tuple[MatrixRow, ...]]:
    split = protocol["split"]
    salt = str(split["salt"])
    dev_start, dev_end = (int(item) for item in split["development_buckets"])
    test_start, test_end = (int(item) for item in split["test_buckets"])
    development: list[MatrixRow] = []
    test: list[MatrixRow] = []
    assignments: dict[str, str] = {}
    for row in rows:
        bucket = _stable_bucket(salt, row.group_id)
        if dev_start <= bucket < dev_end:
            target = development
            split_name = "development"
        elif test_start <= bucket < test_end:
            target = test
            split_name = "test"
        else:
            raise ValueError(f"Group {row.group_id} falls outside the frozen split")
        previous = assignments.setdefault(row.group_id, split_name)
        if previous != split_name:
            raise AssertionError(f"Group leaked across splits: {row.group_id}")
        target.append(row)
    if not development or not test:
        raise ValueError("Frozen split produced an empty development or test set")
    return tuple(development), tuple(test)


def run_protocol(
    development: Sequence[MatrixRow],
    test: Sequence[MatrixRow],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    judges = tuple(sorted(development[0].predictions))
    if any(tuple(sorted(row.predictions)) != judges for row in (*development, *test)):
        raise ValueError("All rows must expose the same complete judge set")
    alpha = float(protocol["primary_constraints"]["set_fpr"])
    budgets = tuple(int(item) for item in protocol["primary_constraints"]["cardinality_budgets"])
    prior = float(protocol["selection"]["high_order_prior_strength"])
    lineage_threshold = int(protocol["selection"]["lineage_pair_overlap_threshold"])
    residual_threshold = float(
        protocol["selection"]["pair_residual_nomination_threshold"]
    )
    model = SelectionModel(
        development,
        judges,
        prior_strength=prior,
        lineage_overlap_threshold=lineage_threshold,
        pair_residual_threshold=residual_threshold,
    )
    by_budget: dict[str, Any] = {}
    for budget in budgets:
        all_candidates = tuple(
            subset
            for size in range(1, budget + 1)
            for subset in itertools.combinations(judges, size)
        )
        candidates = tuple(
            subset
            for subset in all_candidates
            if evaluate(development, subset).set_fpr <= alpha
        )
        if not candidates:
            by_budget[str(budget)] = {"status": "no_feasible_portfolio"}
            continue
        portfolios = {
            "best_single": _best(
                tuple(item for item in candidates if len(item) == 1),
                lambda item: model.individual_recall(item[0]),
                development,
            ),
            "topk_individual": _topk_individual(
                judges, development, budget=budget, alpha=alpha, model=model
            ),
            "independent": _best(candidates, model.independent_recall, development),
            "pairwise": _best(candidates, model.pairwise_recall, development),
            "greedy_mutual_information": _greedy_mutual_information(
                judges, development, budget=budget, alpha=alpha
            ),
            "lineage_nominated_shrunk_high_order": _best(
                candidates, model.high_order_recall, development
            ),
        }
        evaluated = {
            method: _portfolio_payload(
                portfolio,
                development,
                test,
                predicted_recall=(
                    model.high_order_recall(portfolio)
                    if method == "lineage_nominated_shrunk_high_order"
                    else None
                ),
                high_order_nominated=(
                    model.is_high_order_nominated(portfolio)
                    if method == "lineage_nominated_shrunk_high_order"
                    else None
                ),
            )
            for method, portfolio in portfolios.items()
        }
        oracle_candidates = tuple(
            item for item in all_candidates if evaluate(test, item).set_fpr <= alpha
        )
        if oracle_candidates:
            oracle = _best(
                oracle_candidates,
                lambda item: evaluate(test, item).recall,
                test,
            )
            evaluated["exhaustive_test_oracle"] = _portfolio_payload(
                oracle, development, test, evaluator_only=True
            )
        else:
            evaluated["exhaustive_test_oracle"] = {
                "status": "no_feasible_portfolio",
                "evaluator_only": True,
            }
        evaluated["run_all"] = _portfolio_payload(judges, development, test)
        comparator = str(protocol["selection"]["primary_baseline"])
        if comparator not in portfolios or comparator == "lineage_nominated_shrunk_high_order":
            raise ValueError(f"Invalid preregistered primary baseline: {comparator}")
        best_observed_baseline = max(
            (name for name in portfolios if name != "lineage_nominated_shrunk_high_order"),
            key=lambda name: evaluated[name]["test"]["recall"],
        )
        method_portfolio = portfolios["lineage_nominated_shrunk_high_order"]
        comparison = paired_group_bootstrap(
            test,
            method_portfolio,
            portfolios[comparator],
            samples=int(protocol["inference"]["paired_group_bootstrap_samples"]),
            seed=f"graft-arb-v1:{budget}:{comparator}",
        )
        by_budget[str(budget)] = {
            "status": "ok",
            "set_fpr_constraint": alpha,
            "portfolios": evaluated,
            "primary_comparison": {
                "comparator": comparator,
                **comparison,
            },
            "descriptive_best_observed_baseline": best_observed_baseline,
        }
    return {
        "judge_ids": judges,
        "budgets": by_budget,
        "method_diagnostics": model.diagnostics(),
    }


class SelectionModel:
    def __init__(
        self,
        rows: Sequence[MatrixRow],
        judges: Sequence[str],
        *,
        prior_strength: float,
        lineage_overlap_threshold: int,
        pair_residual_threshold: float,
    ) -> None:
        self.rows = tuple(rows)
        self.failures = tuple(row for row in rows if row.label == 0)
        if not self.failures:
            raise ValueError("Development split has no failed trajectories")
        self.judges = tuple(judges)
        self.prior_strength = prior_strength
        self.lineage_overlap_threshold = lineage_overlap_threshold
        self.pair_residual_threshold = pair_residual_threshold
        self.metadata = _judge_metadata(rows, judges)
        self._single_miss = {
            judge: _smoothed_probability(
                sum(row.predictions[judge] == 1 for row in self.failures),
                len(self.failures),
            )
            for judge in judges
        }
        self._pair_miss = {
            pair: _smoothed_probability(
                sum(
                    row.predictions[pair[0]] == 1 and row.predictions[pair[1]] == 1
                    for row in self.failures
                ),
                len(self.failures),
            )
            for pair in itertools.combinations(judges, 2)
        }

    def individual_recall(self, judge: str) -> float:
        return 1.0 - self._single_miss[judge]

    def independent_recall(self, portfolio: Sequence[str]) -> float:
        miss = math.prod(self._single_miss[item] for item in portfolio)
        return 1.0 - _clip_probability(miss)

    def pairwise_recall(self, portfolio: Sequence[str]) -> float:
        return 1.0 - self._pairwise_miss(portfolio)

    def high_order_recall(self, portfolio: Sequence[str]) -> float:
        pairwise = self._pairwise_miss(portfolio)
        if len(portfolio) < 3 or not self.is_high_order_nominated(portfolio):
            return 1.0 - pairwise
        observed_all_miss = sum(
            all(row.predictions[item] == 1 for item in portfolio)
            for row in self.failures
        )
        shrunk = (
            observed_all_miss + self.prior_strength * pairwise
        ) / (len(self.failures) + self.prior_strength)
        return 1.0 - _clip_probability(shrunk)

    def is_high_order_nominated(self, portfolio: Sequence[str]) -> bool:
        if len(portfolio) < 3:
            return False
        edges = {
            tuple(sorted(pair))
            for pair in itertools.combinations(portfolio, 2)
            if self._lineage_overlap(*pair) >= self.lineage_overlap_threshold
            or self._pair_residual(*pair) >= self.pair_residual_threshold
        }
        visited = {portfolio[0]}
        changed = True
        while changed:
            changed = False
            for left, right in edges:
                if left in visited and right not in visited:
                    visited.add(right)
                    changed = True
                elif right in visited and left not in visited:
                    visited.add(left)
                    changed = True
        return len(visited) == len(portfolio)

    def diagnostics(self) -> dict[str, Any]:
        pairs = []
        for left, right in itertools.combinations(self.judges, 2):
            pairs.append(
                {
                    "judges": [left, right],
                    "lineage_overlap": self._lineage_overlap(left, right),
                    "co_miss_residual": self._pair_residual(left, right),
                }
            )
        pairs.sort(key=lambda item: item["co_miss_residual"], reverse=True)
        return {
            "development_failures": len(self.failures),
            "top_pair_co_miss_residuals": pairs[:20],
        }

    def _pairwise_miss(self, portfolio: Sequence[str]) -> float:
        if len(portfolio) == 1:
            return self._single_miss[portfolio[0]]
        edges = []
        for left, right in itertools.combinations(portfolio, 2):
            marginal = self._single_miss[left] * self._single_miss[right]
            ratio = self._pair_miss[tuple(sorted((left, right)))] / max(
                marginal, EPSILON
            )
            edges.append((abs(math.log(max(ratio, EPSILON))), left, right, ratio))
        edges.sort(reverse=True)
        parent = {item: item for item in portfolio}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        correction = 1.0
        selected = 0
        for _, left, right, ratio in edges:
            left_root, right_root = find(left), find(right)
            if left_root == right_root:
                continue
            parent[left_root] = right_root
            correction *= ratio
            selected += 1
            if selected == len(portfolio) - 1:
                break
        miss = math.prod(self._single_miss[item] for item in portfolio) * correction
        return _clip_probability(miss)

    def _pair_residual(self, left: str, right: str) -> float:
        pair = tuple(sorted((left, right)))
        return self._pair_miss[pair] - self._single_miss[left] * self._single_miss[right]

    def _lineage_overlap(self, left: str, right: str) -> int:
        first, second = self.metadata[left], self.metadata[right]
        keys = ("model", "provider", "system_prompt_hash", "inputs")
        return sum(
            first.get(key) is not None and first.get(key) == second.get(key)
            for key in keys
        )


def evaluate(rows: Sequence[MatrixRow], portfolio: Sequence[str]) -> PortfolioMetrics:
    failures = successes = detected = false_rejects = correct = 0
    for row in rows:
        predicts_failure = any(row.predictions[item] == 0 for item in portfolio)
        if row.label == 0:
            failures += 1
            detected += int(predicts_failure)
            correct += int(predicts_failure)
        else:
            successes += 1
            false_rejects += int(predicts_failure)
            correct += int(not predicts_failure)
    return PortfolioMetrics(
        failures=failures,
        successes=successes,
        detected_failures=detected,
        false_rejects=false_rejects,
        recall=detected / failures if failures else 0.0,
        set_fpr=false_rejects / successes if successes else 0.0,
        accuracy=correct / len(rows) if rows else 0.0,
    )


def paired_group_bootstrap(
    rows: Sequence[MatrixRow],
    method: Sequence[str],
    comparator: Sequence[str],
    *,
    samples: int,
    seed: str,
) -> dict[str, Any]:
    groups: dict[str, list[MatrixRow]] = {}
    for row in rows:
        groups.setdefault(row.group_id, []).append(row)
    group_ids = tuple(sorted(groups))
    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(samples):
        sampled: list[MatrixRow] = []
        for _ in group_ids:
            sampled.extend(groups[rng.choice(group_ids)])
        differences.append(evaluate(sampled, method).recall - evaluate(sampled, comparator).recall)
    differences.sort()
    return {
        "recall_difference": evaluate(rows, method).recall
        - evaluate(rows, comparator).recall,
        "paired_group_bootstrap_95_ci": [
            _quantile(differences, 0.025),
            _quantile(differences, 0.975),
        ],
        "bootstrap_samples": samples,
    }


def split_audit(
    development: Sequence[MatrixRow], test: Sequence[MatrixRow]
) -> dict[str, Any]:
    dev_groups = {row.group_id for row in development}
    test_groups = {row.group_id for row in test}
    if dev_groups & test_groups:
        raise AssertionError("Group leakage detected")
    return {
        "development_rows": len(development),
        "test_rows": len(test),
        "development_groups": len(dev_groups),
        "test_groups": len(test_groups),
        "development_failures": sum(row.label == 0 for row in development),
        "test_failures": sum(row.label == 0 for row in test),
        "group_overlap": 0,
    }


def _best(
    candidates: Sequence[tuple[str, ...]],
    score,
    rows: Sequence[MatrixRow],
) -> tuple[str, ...]:
    if not candidates:
        raise ValueError("No feasible candidate portfolio")
    return min(
        candidates,
        key=lambda item: (
            -score(item),
            evaluate(rows, item).set_fpr,
            len(item),
            item,
        ),
    )


def _topk_individual(
    judges: Sequence[str],
    rows: Sequence[MatrixRow],
    *,
    budget: int,
    alpha: float,
    model: SelectionModel,
) -> tuple[str, ...]:
    ordered = sorted(
        judges,
        key=lambda judge: (
            -model.individual_recall(judge),
            evaluate(rows, (judge,)).set_fpr,
            judge,
        ),
    )
    selected: list[str] = []
    for judge in ordered:
        candidate = tuple(sorted((*selected, judge)))
        if len(candidate) <= budget and evaluate(rows, candidate).set_fpr <= alpha:
            selected.append(judge)
    if not selected:
        feasible = tuple((judge,) for judge in judges if evaluate(rows, (judge,)).set_fpr <= alpha)
        return _best(feasible, lambda item: model.individual_recall(item[0]), rows)
    return tuple(sorted(selected))


def _greedy_mutual_information(
    judges: Sequence[str],
    rows: Sequence[MatrixRow],
    *,
    budget: int,
    alpha: float,
) -> tuple[str, ...]:
    selected: tuple[str, ...] = ()
    while len(selected) < budget:
        candidates = []
        for judge in judges:
            if judge in selected:
                continue
            candidate = tuple(sorted((*selected, judge)))
            if evaluate(rows, candidate).set_fpr <= alpha:
                candidates.append(candidate)
        if not candidates:
            break
        best = _best(candidates, lambda item: _mutual_information(rows, item), rows)
        if selected and _mutual_information(rows, best) <= _mutual_information(rows, selected):
            break
        selected = best
    if not selected:
        raise ValueError("No feasible mutual-information portfolio")
    return selected


def _mutual_information(rows: Sequence[MatrixRow], portfolio: Sequence[str]) -> float:
    counts = {(label, prediction): 0 for label in (0, 1) for prediction in (0, 1)}
    for row in rows:
        prediction = int(any(row.predictions[item] == 0 for item in portfolio))
        counts[(row.label, prediction)] += 1
    total = len(rows)
    label_counts = {label: sum(counts[(label, p)] for p in (0, 1)) for label in (0, 1)}
    prediction_counts = {
        prediction: sum(counts[(label, prediction)] for label in (0, 1))
        for prediction in (0, 1)
    }
    value = 0.0
    for (label, prediction), count in counts.items():
        if count == 0:
            continue
        joint = count / total
        value += joint * math.log(
            joint / ((label_counts[label] / total) * (prediction_counts[prediction] / total))
        )
    return value


def _portfolio_payload(
    portfolio: Sequence[str],
    development: Sequence[MatrixRow],
    test: Sequence[MatrixRow],
    *,
    predicted_recall: float | None = None,
    high_order_nominated: bool | None = None,
    evaluator_only: bool = False,
) -> dict[str, Any]:
    payload = {
        "portfolio": list(portfolio),
        "cardinality": len(portfolio),
        "development": vars(evaluate(development, portfolio)),
        "test": vars(evaluate(test, portfolio)),
        "evaluator_only": evaluator_only,
    }
    if predicted_recall is not None:
        payload["predicted_development_recall"] = predicted_recall
    if high_order_nominated is not None:
        payload["high_order_nominated"] = high_order_nominated
    return payload


def _judge_metadata(
    rows: Sequence[MatrixRow], judges: Sequence[str]
) -> dict[str, Mapping[str, Any]]:
    metadata: dict[str, Mapping[str, Any]] = {}
    for judge in judges:
        observed = [row.metadata[judge] for row in rows]
        canonical: dict[str, Any] = {}
        for key in ("model", "provider", "system_prompt_hash", "inputs", "oracle_family"):
            values = {
                json.dumps(item.get(key), sort_keys=True)
                for item in observed
                if item.get(key) is not None
            }
            canonical[key] = json.loads(next(iter(values))) if len(values) == 1 else None
        metadata[judge] = canonical
    return metadata


def _smoothed_probability(successes: int, trials: int) -> float:
    return (successes + 0.5) / (trials + 1.0)


def _clip_probability(value: float) -> float:
    return min(1.0 - EPSILON, max(EPSILON, value))


def _stable_bucket(salt: str, group_id: str) -> int:
    digest = hashlib.sha256(f"{salt}:{group_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 100


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("Cannot take a quantile of an empty sequence")
    index = (len(values) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
