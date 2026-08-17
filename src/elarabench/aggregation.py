"""Deterministic repeat, case, category, and tag score aggregation."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence

from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    BreakdownSummary,
    CaseSummary,
    EvaluationStatus,
    ScoreStatistics,
    StatusCounts,
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


def aggregate(samples: Sequence[AggregationSample]) -> AggregationSummary:
    """Aggregate scored samples without converting other statuses into zeroes."""
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

    scored_case_count, score = _weighted_score(case_summaries)
    return AggregationSummary(
        score=score,
        case_count=len(case_summaries),
        scored_case_count=scored_case_count,
        sample_status_counts=_counts(samples),
        cases=tuple(case_summaries),
        categories={name: _breakdown(group) for name, group in sorted(categories.items())},
        tags={name: _breakdown(group) for name, group in sorted(tags.items())},
    )
