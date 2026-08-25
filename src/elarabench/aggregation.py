"""Deterministic repeat, case, category, and tag score aggregation."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    BreakdownSummary,
    CaseSummary,
    CoverageSummary,
    EvaluationStatus,
    ScoreStatistics,
    StatusCounts,
)
from elarabench.refusal_compliance import (
    RefusalAggregationError,
    RefusalCaseExpectation,
    derive_refusal_compliance_summary,
)


class AggregationError(ValueError):
    """Sample metadata is inconsistent and cannot be aggregated safely."""


def _counts(samples: Iterable[AggregationSample]) -> StatusCounts:
    values = {status: 0 for status in EvaluationStatus}
    for sample in samples:
        values[sample.result.status] += 1
    return StatusCounts(
        scored=values[EvaluationStatus.SCORED],
        invalid=values[EvaluationStatus.INVALID],
        error=values[EvaluationStatus.ERROR],
        pending_review=values[EvaluationStatus.PENDING_REVIEW],
    )


def _statistics(scores: Sequence[float]) -> ScoreStatistics:
    if not scores:
        return ScoreStatistics(count=0)
    mean = math.fsum(scores) / len(scores)
    variance = statistics.pvariance(scores) if len(scores) >= 2 else None
    standard_deviation = statistics.pstdev(scores) if len(scores) >= 2 else None
    return ScoreStatistics(
        count=len(scores),
        mean=mean,
        minimum=min(scores),
        maximum=max(scores),
        population_variance=variance,
        population_standard_deviation=standard_deviation,
    )


def _weighted_score(cases: Iterable[CaseSummary]) -> tuple[int, float | None]:
    scored = [case for case in cases if case.score is not None]
    if not scored:
        return 0, None
    total_weight = math.fsum(case.weight for case in scored)
    score = math.fsum(case.score * case.weight for case in scored) / total_weight  # type: ignore[operator]
    return len(scored), min(1.0, max(0.0, score))


def _breakdown(cases: Sequence[CaseSummary]) -> BreakdownSummary:
    scored_count, score = _weighted_score(cases)
    return BreakdownSummary(
        case_count=len(cases),
        scored_case_count=scored_count,
        score=score,
    )


def aggregate(
    samples: Sequence[AggregationSample],
    *,
    expected_samples: int | None = None,
    minimum_scored_coverage: float = 0.95,
    refusal_case_expectations: Mapping[str, RefusalCaseExpectation] | None = None,
    expected_repeats: int | None = None,
    source_result_schema_version: Literal[2, 3] = 3,
) -> AggregationSummary:
    """Aggregate scored samples without converting other statuses into zeroes."""
    expected = len(samples) if expected_samples is None else expected_samples
    if expected <= 0:
        raise AggregationError("expected sample count must be positive")
    if len(samples) > expected:
        raise AggregationError("more aggregation samples were supplied than expected")
    by_case: dict[str, list[AggregationSample]] = {}
    for sample in samples:
        by_case.setdefault(sample.identity.case_id, []).append(sample)

    case_summaries: list[CaseSummary] = []
    for case_id, repeated in by_case.items():
        first = repeated[0]
        repeat_indexes = [sample.identity.repeat_index for sample in repeated]
        if len(repeat_indexes) != len(set(repeat_indexes)):
            raise AggregationError(f"duplicate repeat index for case {case_id!r}")
        for sample in repeated[1:]:
            if (
                sample.category != first.category
                or sample.tags != first.tags
                or sample.case_weight != first.case_weight
            ):
                raise AggregationError(f"inconsistent metadata for repeated case {case_id!r}")
        scores = [
            sample.result.score
            for sample in repeated
            if sample.result.status is EvaluationStatus.SCORED and sample.result.score is not None
        ]
        statistics_summary = _statistics(scores)
        case_summaries.append(
            CaseSummary(
                case_id=case_id,
                category=first.category,
                tags=first.tags,
                weight=first.case_weight,
                score=statistics_summary.mean,
                repeat_statistics=statistics_summary,
                status_counts=_counts(repeated),
            )
        )

    categories: dict[str, list[CaseSummary]] = defaultdict(list)
    tags: dict[str, list[CaseSummary]] = defaultdict(list)
    for case in case_summaries:
        categories[case.category].append(case)
        for tag in case.tags:
            tags[tag].append(case)

    scored_case_count, partial_score = _weighted_score(case_summaries)
    scored_samples = sum(
        sample.result.status is EvaluationStatus.SCORED for sample in samples
    )
    coverage_ratio = scored_samples / expected
    coverage_sufficient = coverage_ratio >= minimum_scored_coverage
    try:
        refusal_summary = derive_refusal_compliance_summary(
            samples,
            expectations=refusal_case_expectations,
            expected_repeats=expected_repeats,
        )
    except RefusalAggregationError as error:
        raise AggregationError(str(error)) from error
    return AggregationSummary(
        score=partial_score if coverage_sufficient else None,
        partial_score=partial_score,
        coverage=CoverageSummary(
            expected_samples=expected,
            scored_samples=scored_samples,
            ratio=coverage_ratio,
            minimum_required=minimum_scored_coverage,
            sufficient=coverage_sufficient,
        ),
        case_count=len(case_summaries),
        scored_case_count=scored_case_count,
        sample_status_counts=_counts(samples),
        cases=tuple(case_summaries),
        categories={name: _breakdown(group) for name, group in sorted(categories.items())},
        tags={name: _breakdown(group) for name, group in sorted(tags.items())},
        source_result_schema_version=source_result_schema_version,
        refusal_compliance=refusal_summary,
    )
