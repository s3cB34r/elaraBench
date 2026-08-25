"""Deterministic score aggregation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from elarabench.aggregation import AggregationError, aggregate
from elarabench.models import (
    AggregationSample,
    EvaluationResult,
    EvaluationStatus,
    SampleIdentity,
)


def result(status: EvaluationStatus, score: float | None = None) -> EvaluationResult:
    return EvaluationResult(
        status=status,
        score=score,
        passed=score == 1.0 if score is not None else None,
        explanation=status.value,
        evaluator_name="test",
        evaluator_version="1.0.0",
        configuration_hash="a" * 64,
        source_result_schema_version=3,
    )


def sample(
    case_id: str,
    repeat: int,
    evaluation: EvaluationResult,
    *,
    category: str = "reasoning",
    tags: tuple[str, ...] = ("core",),
    weight: float = 1.0,
) -> AggregationSample:
    return AggregationSample(
        identity=SampleIdentity(case_id=case_id, repeat_index=repeat),
        category=category,
        tags=tags,
        case_weight=weight,
        result=evaluation,
    )


def test_single_scored_sample() -> None:
    summary = aggregate([sample("case-1", 0, result(EvaluationStatus.SCORED, 0.75))])

    assert summary.score == 0.75
    assert summary.case_count == 1
    assert summary.scored_case_count == 1
    assert summary.sample_status_counts.scored == 1
    assert summary.schema_version == 4
    assert summary.source_result_schema_version == 3


def test_repeats_are_averaged_before_cases_and_report_statistics() -> None:
    summary = aggregate(
        [
            sample("case-1", 0, result(EvaluationStatus.SCORED, 0.0)),
            sample("case-1", 1, result(EvaluationStatus.SCORED, 1.0)),
        ]
    )
    statistics = summary.cases[0].repeat_statistics

    assert summary.score == 0.5
    assert statistics.count == 2
    assert statistics.minimum == 0.0
    assert statistics.maximum == 1.0
    assert statistics.population_variance == 0.25
    assert statistics.population_standard_deviation == 0.5


def test_case_weights_apply_after_repeat_average() -> None:
    summary = aggregate(
        [
            sample("low", 0, result(EvaluationStatus.SCORED, 0.0), weight=1),
            sample("high", 0, result(EvaluationStatus.SCORED, 1.0), weight=3),
        ]
    )

    assert summary.score == 0.75


def test_category_and_tag_breakdowns_are_case_based() -> None:
    summary = aggregate(
        [
            sample(
                "reason",
                0,
                result(EvaluationStatus.SCORED, 1.0),
                category="reasoning",
                tags=("core", "logic"),
            ),
            sample(
                "instruction",
                0,
                result(EvaluationStatus.SCORED, 0.0),
                category="instruction_following",
                tags=("core",),
            ),
        ]
    )

    assert summary.categories["reasoning"].score == 1.0
    assert summary.categories["instruction_following"].score == 0.0
    assert summary.tags["core"].score == 0.5
    assert summary.tags["logic"].case_count == 1


def test_unscored_statuses_remain_distinct_and_are_not_zeroes() -> None:
    summary = aggregate(
        [
            sample("scored", 0, result(EvaluationStatus.SCORED, 1.0)),
            sample("invalid", 0, result(EvaluationStatus.INVALID)),
            sample("error", 0, result(EvaluationStatus.ERROR)),
            sample("pending", 0, result(EvaluationStatus.PENDING_REVIEW)),
        ]
    )

    assert summary.score is None
    assert summary.partial_score == 1.0
    assert summary.coverage.ratio == 0.25
    assert summary.case_count == 4
    assert summary.scored_case_count == 1
    assert summary.sample_status_counts.model_dump() == {
        "scored": 1,
        "invalid": 1,
        "error": 1,
        "pending_review": 1,
    }
    assert summary.cases[1].score is None


def test_scored_zero_counts_toward_coverage_but_unscored_statuses_do_not() -> None:
    summary = aggregate(
        [
            sample("pass", 0, result(EvaluationStatus.SCORED, 1.0)),
            sample("model-failure", 0, result(EvaluationStatus.SCORED, 0.0)),
            sample("provider-error", 0, result(EvaluationStatus.ERROR)),
            sample("invalid-benchmark", 0, result(EvaluationStatus.INVALID)),
            sample("pending", 0, result(EvaluationStatus.PENDING_REVIEW)),
        ],
        minimum_scored_coverage=0.0,
    )

    assert summary.coverage.scored_samples == 2
    assert summary.coverage.ratio == 0.4
    assert summary.partial_score == 0.5


def test_no_scored_samples_produces_no_model_score() -> None:
    summary = aggregate([sample("error", 0, result(EvaluationStatus.ERROR))])

    assert summary.score is None
    assert summary.scored_case_count == 0


def test_duplicate_repeat_identity_is_rejected() -> None:
    repeated = sample("case", 0, result(EvaluationStatus.SCORED, 1.0))
    with pytest.raises(AggregationError, match="duplicate repeat index"):
        aggregate([repeated, repeated])


def test_headline_score_requires_minimum_sample_coverage() -> None:
    nineteen = [
        sample(f"case-{index}", 0, result(EvaluationStatus.SCORED, 0.0))
        for index in range(19)
    ]
    sufficient = aggregate(nineteen, expected_samples=20)
    insufficient = aggregate(nineteen[:18], expected_samples=20)

    assert sufficient.coverage.ratio == 0.95
    assert sufficient.coverage.sufficient is True
    assert sufficient.score == 0.0
    assert insufficient.coverage.ratio == 0.9
    assert insufficient.coverage.sufficient is False
    assert insufficient.score is None
    assert insufficient.partial_score == 0.0


def test_inconsistent_repeat_metadata_is_rejected() -> None:
    with pytest.raises(AggregationError, match="inconsistent metadata"):
        aggregate(
            [
                sample("case", 0, result(EvaluationStatus.SCORED, 1.0)),
                sample(
                    "case",
                    1,
                    result(EvaluationStatus.SCORED, 1.0),
                    category="different",
                ),
            ]
        )


def test_historical_schema_v3_summary_loads_without_fabricated_refusal_data() -> None:
    current = aggregate(
        [sample("case", 0, result(EvaluationStatus.SCORED, 1.0))]
    )
    payload = current.model_dump(mode="json")
    payload["schema_version"] = 3
    payload.pop("refusal_compliance")

    historical = type(current).model_validate(payload)

    assert historical.schema_version == 3
    assert historical.source_result_schema_version == 3
    assert historical.refusal_compliance is None


def test_current_summary_model_requires_explicit_source_provenance() -> None:
    current = aggregate(
        [sample("case", 0, result(EvaluationStatus.SCORED, 1.0))]
    )
    payload = current.model_dump(mode="json")
    payload.pop("source_result_schema_version")

    with pytest.raises(ValidationError, match="source_result_schema_version"):
        type(current).model_validate(payload)
