"""Deterministic score aggregation tests."""

from __future__ import annotations

import pytest

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

    assert summary.score == 1.0
    assert summary.case_count == 4
    assert summary.scored_case_count == 1
    assert summary.sample_status_counts.model_dump() == {
        "scored": 1,
        "invalid": 1,
        "error": 1,
        "pending_review": 1,
    }
    assert summary.cases[1].score is None


def test_no_scored_samples_produces_no_model_score() -> None:
    summary = aggregate([sample("error", 0, result(EvaluationStatus.ERROR))])

    assert summary.score is None
    assert summary.scored_case_count == 0


def test_duplicate_repeat_identity_is_rejected() -> None:
    repeated = sample("case", 0, result(EvaluationStatus.SCORED, 1.0))
    with pytest.raises(AggregationError, match="duplicate repeat index"):
        aggregate([repeated, repeated])


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
