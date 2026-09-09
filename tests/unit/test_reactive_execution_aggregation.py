"""Observed-repeat masses, group minima, coverage and summary version selection."""

import json
from pathlib import Path

import pytest

from elarabench.aggregation import aggregate
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    EvaluationSpecification,
    EvaluationStatus,
    SampleIdentity,
)
from elarabench.reactive_execution import ReactiveEvaluator, reactive_case_expectations
from elarabench.reactive_execution_corpus import perfect_strategy, strategy_context


def sample(case, responses, repeat=0):
    context = strategy_context(case, lambda c, r, q: responses[r.durable_model_responses])
    return AggregationSample(
        identity=SampleIdentity(case_id=case.id, repeat_index=repeat),
        category=case.category,
        tags=case.tags,
        case_weight=case.weight,
        result=ReactiveEvaluator().evaluate(context),
    )


def summary(cases, samples, repeats=1):
    return aggregate(
        samples,
        expected_samples=len(cases) * repeats,
        expected_repeats=repeats,
        reactive_case_expectations=reactive_case_expectations(cases),
        configured_evaluator_types={c.id: c.evaluation.type for c in cases},
        source_result_schema_version=4,
    )


def test_exact_uneven_repeat_regression(reactive):
    original = reactive.suite().suite.cases[0]
    cases = tuple(
        original.model_copy(
            update={
                "id": name,
                "tags": ("contrastive-group-re-pair-01", f"contrastive-variant-branch-{name}"),
                "evaluation": EvaluationSpecification(
                    type="reactive_execution",
                    config={**original.evaluation.config, "capability": "recovery_opportunity"},
                ),
            }
        )
        for name in ("a", "b")
    )
    goldens = json.loads(Path("tests/fixtures/reactive_execution/golden.json").read_text())
    scripts = {g["id"]: g["responses"] for g in goldens}
    data = [
        sample(cases[0], scripts["e5_single"]),
        sample(cases[0], ['{"type":"action","actions":[{"tool":"open","arguments":{}}]}'] * 4, 1),
        sample(cases[1], scripts["e6"]),
    ]
    result = summary(cases, data, 3)
    reactive_summary = result.reactive_execution
    assert reactive_summary.observed_case_count == 2
    assert reactive_summary.scored_sample_count == 3
    assert reactive_summary.coverage == 3 / 6
    assert reactive_summary.case_outcomes.total() == 2
    assert reactive_summary.case_outcomes.completed_without_execution_failure == 0.5
    assert reactive_summary.case_outcomes.completed_after_recovery == 1
    assert reactive_summary.case_outcomes.incomplete_within_bounds == 0.5
    assert reactive_summary.adaptation_rate.partial_value == 0.5
    assert reactive_summary.adaptation_rate.coverage == 0.5
    assert reactive_summary.adaptation_rate.headline_value is None
    assert reactive_summary.complete_group_count == 0
    missing = summary(cases, data[:2], 3).reactive_execution
    assert missing.case_outcomes.total() == 1 and missing.observed_case_count == 1
    assert missing.adaptation_rate.partial_value == 0
    assert missing.sample_outcomes.total() == 2  # Missing B is not an E failure.


def test_e6_sample_pass_is_zero_first_pass_axis(reactive):
    case = reactive.suite().suite.cases[0]
    scripts = json.loads(Path("tests/fixtures/reactive_execution/golden.json").read_text())
    data = sample(case, next(g["responses"] for g in scripts if g["id"] == "e6"))
    assert data.result.score == 1 and data.result.passed
    rate = summary((case,), [data]).reactive_execution.first_pass_completion_rate
    assert rate.numerator == rate.headline_value == 0


@pytest.fixture(scope="module")
def production():
    cases = load_benchmark_suite(
        get_builtin_suite_path("reactive_execution.core")
    ).suite.cases
    data = []
    for case in cases:
        context = strategy_context(case, perfect_strategy)
        data.append(
            AggregationSample(
                identity=context.identity,
                category=case.category,
                tags=case.tags,
                case_weight=case.weight,
                result=ReactiveEvaluator().evaluate(context),
            )
        )
    return cases, data


def test_full_partial_and_gate_coverage(production):
    cases, data = production
    full = summary(cases, data)
    assert full.schema_version == 7 and full.score == full.partial_score == 1
    assert full.reactive_execution.complete_group_count == 6
    missing_gate = next(c.id for c in cases if c.evaluation.config["authorization"] == "DENIED")
    partial = summary(cases, [s for s in data if s.identity.case_id != missing_gate])
    assert partial.score is None and partial.partial_score == 1
    assert partial.reactive_execution.balanced_reactive_execution is None
    assert AggregationSummary.model_validate(full.model_dump()) == full
    for bad in [dict(schema_version=6), dict(reactive_execution=None)]:
        with pytest.raises(ValueError):
            AggregationSummary.model_validate(full.model_dump() | bad)


def test_configured_mixed_family_suppresses_even_without_ordinary_samples(production):
    cases, data = production
    types = {c.id: c.evaluation.type for c in cases} | {"missing-ordinary": "exact_match"}
    result = aggregate(
        data,
        expected_samples=49,
        expected_repeats=1,
        reactive_case_expectations=reactive_case_expectations(cases),
        configured_evaluator_types=types,
        source_result_schema_version=4,
    )
    assert result.score is result.partial_score is None


@pytest.mark.parametrize("status", ["error", "invalid", "pending_review"])
def test_unscored_status_reduces_coverage_not_mass(production, status):
    cases, data = production
    first = data[0]
    artifacts = {}
    if status == "pending_review":
        artifacts = {
            "reactive_execution": {
                **first.result.artifacts["reactive_execution"],
                "evaluator_version": "1.0.0",
            }
        }
    changed = first.model_copy(
        update={
            "result": first.result.model_copy(
                update={
                    "status": EvaluationStatus(status),
                    "artifacts": artifacts,
                    "evaluator_version": "1.0.0" if status == "pending_review" else "1.1.0",
                    "score": None,
                    "passed": None,
                }
            )
        }
    )
    result = summary(cases, [changed, *data[1:]])
    assert result.reactive_execution.observed_case_count == 47
    assert result.reactive_execution.case_outcomes.total() == 47
    assert result.reactive_execution.coverage == 47 / 48
    assert result.score is None
