"""Provider-neutral performance analysis over validated immutable run evidence."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from elarabench.comparison_models import (
    ComparabilityAssessment,
    ComparabilityClassification,
    ComparisonIntent,
    ComparisonReasonCode,
    FinishReasonCount,
    FinishReasonDiagnostics,
    MetricAvailability,
    MetricDirection,
    MetricMissingness,
    MetricSummary,
    PerformanceAnalysis,
    PerformanceMetricComparison,
    PerformanceMetricName,
    PerformanceMetricUnit,
)
from elarabench.hashing import hash_canonical
from elarabench.models import (
    AttemptOutcome,
    AttemptRecord,
    GenerationResponse,
    SampleIdentity,
)

PERFORMANCE_METRICS_SEMANTIC: Literal["performance_metrics_v1"] = (
    "performance_metrics_v1"
)
PERFORMANCE_AGGREGATION_SEMANTIC: Literal["paired_sample_median_v1"] = (
    "paired_sample_median_v1"
)


@dataclass(frozen=True)
class SamplePerformanceEvidence:
    """Validated physical performance inputs for one planned sample."""

    identity: SampleIdentity
    attempts: tuple[AttemptRecord, ...]
    response: GenerationResponse | None
    source_result_schema_version: int


@dataclass(frozen=True)
class PerformanceComparabilityContext:
    """Identity and environment facts used by metric-specific policy."""

    intent: ComparisonIntent
    baseline_tokenizer: str | None
    candidate_tokenizer: str | None
    baseline_provider: str
    candidate_provider: str
    baseline_backend: str
    candidate_backend: str
    baseline_result_schema_version: int
    candidate_result_schema_version: int
    performance_reason_codes: tuple[ComparisonReasonCode, ...]


@dataclass(frozen=True)
class _MetricSpecification:
    name: PerformanceMetricName
    unit: PerformanceMetricUnit
    token_based: bool = False
    provider_native: bool = False
    execution_cost: bool = False


_METRICS = (
    _MetricSpecification(
        PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
    ),
    _MetricSpecification(
        PerformanceMetricName.PROVIDER_TOTAL_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
        provider_native=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.PROVIDER_LOAD_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
        provider_native=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.PROVIDER_PROMPT_EVAL_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
        provider_native=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
        provider_native=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
    ),
    _MetricSpecification(
        PerformanceMetricName.PROMPT_TOKENS,
        PerformanceMetricUnit.TOKENS,
        token_based=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.GENERATED_TOKENS,
        PerformanceMetricUnit.TOKENS,
        token_based=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.TOTAL_TOKENS,
        PerformanceMetricUnit.TOKENS,
        token_based=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.GENERATION_TOKENS_PER_SECOND,
        PerformanceMetricUnit.TOKENS_PER_SECOND,
        token_based=True,
        provider_native=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.ATTEMPT_COUNT,
        PerformanceMetricUnit.COUNT,
        execution_cost=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.FAILED_ATTEMPT_COUNT,
        PerformanceMetricUnit.COUNT,
        execution_cost=True,
    ),
    _MetricSpecification(
        PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS,
        PerformanceMetricUnit.SECONDS,
        execution_cost=True,
    ),
)

_IDENTITY_SPECIFIC_REASONS = {
    ComparisonReasonCode.TOKENIZER_UNKNOWN,
    ComparisonReasonCode.TOKENIZER_DIFFERENCE,
    ComparisonReasonCode.PROVIDER_DIFFERENCE,
    ComparisonReasonCode.BACKEND_DIFFERENCE,
    ComparisonReasonCode.PERFORMANCE_METRIC_MISSING,
    ComparisonReasonCode.ATTEMPT_COUNT_DIFFERENCE,
    ComparisonReasonCode.LEGACY_IDENTITY_GAP,
}


def performance_evidence_hash(
    samples: Mapping[SampleIdentity, SamplePerformanceEvidence],
) -> str:
    """Hash only persisted values that affect M4.2b performance analysis."""

    ordered = []
    for identity in sorted(samples, key=lambda item: (item.case_id, item.repeat_index)):
        sample = samples[identity]
        ordered.append(
            {
                "identity": identity.model_dump(mode="json"),
                "source_result_schema_version": sample.source_result_schema_version,
                "attempts": [
                    {
                        "attempt_index": attempt.attempt_index,
                        "retry_number": attempt.retry_number,
                        "outcome": attempt.outcome,
                        "duration_seconds": attempt.duration_seconds,
                    }
                    for attempt in sample.attempts
                ],
                "terminal_response": (
                    {
                        "usage": (
                            sample.response.usage.model_dump(mode="json")
                            if sample.response.usage is not None
                            else None
                        ),
                        "timing": (
                            sample.response.timing.model_dump(mode="json")
                            if sample.response.timing is not None
                            else None
                        ),
                        "finish_reason": sample.response.finish_reason,
                    }
                    if sample.response is not None
                    else None
                ),
            }
        )
    return hash_canonical(
        {
            "semantic_version": PERFORMANCE_METRICS_SEMANTIC,
            "samples": ordered,
        }
    )


def analyze_performance(
    baseline_samples: Mapping[SampleIdentity, SamplePerformanceEvidence],
    candidate_samples: Mapping[SampleIdentity, SamplePerformanceEvidence],
    selected_sample_identities: Sequence[SampleIdentity],
    *,
    execution_cost_sample_identities: Sequence[SampleIdentity] | None = None,
    context: PerformanceComparabilityContext,
) -> PerformanceAnalysis:
    """Aggregate every metric over the same directional paired sample population."""

    identities = tuple(selected_sample_identities)
    execution_identities = tuple(execution_cost_sample_identities or identities)
    metrics = tuple(
        _compare_metric(
            specification,
            baseline_samples,
            candidate_samples,
            execution_identities if specification.execution_cost else identities,
            context=context,
        )
        for specification in _METRICS
    )
    return PerformanceAnalysis(
        semantic_version=PERFORMANCE_METRICS_SEMANTIC,
        aggregation_semantic=PERFORMANCE_AGGREGATION_SEMANTIC,
        baseline_evidence_hash=performance_evidence_hash(baseline_samples),
        candidate_evidence_hash=performance_evidence_hash(candidate_samples),
        selected_sample_identities=identities,
        execution_cost_sample_identities=execution_identities,
        metrics=metrics,
        finish_reasons=FinishReasonDiagnostics(
            baseline_counts=_finish_reason_counts(baseline_samples, identities),
            candidate_counts=_finish_reason_counts(candidate_samples, identities),
        ),
    )


def _compare_metric(
    specification: _MetricSpecification,
    baseline_samples: Mapping[SampleIdentity, SamplePerformanceEvidence],
    candidate_samples: Mapping[SampleIdentity, SamplePerformanceEvidence],
    identities: Sequence[SampleIdentity],
    *,
    context: PerformanceComparabilityContext,
) -> PerformanceMetricComparison:
    baseline_values = {
        identity: value
        for identity in identities
        if (
            value := _metric_value(baseline_samples.get(identity), specification.name)
        )
        is not None
    }
    candidate_values = {
        identity: value
        for identity in identities
        if (
            value := _metric_value(candidate_samples.get(identity), specification.name)
        )
        is not None
    }
    paired_ids = tuple(
        identity
        for identity in identities
        if identity in baseline_values and identity in candidate_values
    )
    baseline_paired = tuple(baseline_values[identity] for identity in paired_ids)
    candidate_paired = tuple(candidate_values[identity] for identity in paired_ids)
    expected = len(identities)
    paired = len(paired_ids)
    availability = (
        MetricAvailability.AVAILABLE
        if expected > 0 and paired == expected
        else MetricAvailability.PARTIAL
        if paired > 0
        else MetricAvailability.UNAVAILABLE
    )
    baseline_summary = _summary(baseline_paired)
    candidate_summary = _summary(candidate_paired)
    comparability, can_compute_delta = _metric_comparability(
        specification,
        context,
        availability=availability,
        baseline_summary=baseline_summary,
    )
    absolute_delta: float | None = None
    relative_delta: float | None = None
    direction: MetricDirection | None = None
    if can_compute_delta and baseline_summary is not None and candidate_summary is not None:
        candidate_median = candidate_summary.median
        baseline_median = baseline_summary.median
        computed_delta = candidate_median - baseline_median
        if math.isfinite(computed_delta):
            absolute_delta = computed_delta
            if baseline_median != 0.0:
                computed_relative_delta = absolute_delta / baseline_median
                if math.isfinite(computed_relative_delta):
                    relative_delta = computed_relative_delta
                else:
                    comparability = comparability.model_copy(
                        update={
                            "reason_codes": tuple(
                                dict.fromkeys(
                                    (
                                        *comparability.reason_codes,
                                        ComparisonReasonCode.RELATIVE_DELTA_UNREPRESENTABLE,
                                    )
                                )
                            )
                        }
                    )
            direction = (
                MetricDirection.HIGHER
                if absolute_delta > 0.0
                else MetricDirection.LOWER
                if absolute_delta < 0.0
                else MetricDirection.UNCHANGED
            )
    return PerformanceMetricComparison(
        metric_name=specification.name,
        unit=specification.unit,
        selected_sample_identities=tuple(identities),
        availability=availability,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        missingness=MetricMissingness(
            expected_paired_sample_count=expected,
            baseline_available_count=len(baseline_values),
            candidate_available_count=len(candidate_values),
            paired_available_count=paired,
            baseline_missing_count=expected - len(baseline_values),
            candidate_missing_count=expected - len(candidate_values),
            unpaired_available_count=len(set(baseline_values) ^ set(candidate_values)),
        ),
        comparability=comparability,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        direction=direction,
        semantic_version=PERFORMANCE_METRICS_SEMANTIC,
    )


def _metric_comparability(
    specification: _MetricSpecification,
    context: PerformanceComparabilityContext,
    *,
    availability: MetricAvailability,
    baseline_summary: MetricSummary | None,
) -> tuple[ComparabilityAssessment, bool]:
    reasons = [
        reason
        for reason in context.performance_reason_codes
        if reason not in _IDENTITY_SPECIFIC_REASONS
    ]
    hard = availability is MetricAvailability.UNAVAILABLE
    if availability is not MetricAvailability.AVAILABLE:
        reasons.append(ComparisonReasonCode.PERFORMANCE_METRIC_MISSING)

    if specification.token_based:
        if context.baseline_tokenizer is None or context.candidate_tokenizer is None:
            reasons.append(ComparisonReasonCode.TOKENIZER_UNKNOWN)
        elif context.baseline_tokenizer != context.candidate_tokenizer:
            reasons.append(ComparisonReasonCode.TOKENIZER_DIFFERENCE)
            hard = True

    provider_differs = context.baseline_provider != context.candidate_provider
    backend_differs = context.baseline_backend != context.candidate_backend
    if specification.provider_native and (provider_differs or backend_differs):
        if provider_differs:
            reasons.append(ComparisonReasonCode.PROVIDER_DIFFERENCE)
        if backend_differs:
            reasons.append(ComparisonReasonCode.BACKEND_DIFFERENCE)
        hard = True
    elif provider_differs or backend_differs:
        if provider_differs:
            reasons.append(ComparisonReasonCode.PROVIDER_DIFFERENCE)
        if backend_differs:
            reasons.append(ComparisonReasonCode.BACKEND_DIFFERENCE)

    if (
        context.baseline_result_schema_version == 2
        or context.candidate_result_schema_version == 2
    ):
        reasons.append(ComparisonReasonCode.LEGACY_IDENTITY_GAP)

    if baseline_summary is not None and baseline_summary.median == 0.0:
        reasons.append(ComparisonReasonCode.ZERO_BASELINE_RELATIVE_DELTA_UNAVAILABLE)

    reason_codes = tuple(dict.fromkeys(reasons))
    classification = (
        ComparabilityClassification.NOT_DIRECTLY_COMPARABLE
        if hard
        else ComparabilityClassification.QUALIFIED
    )
    return (
        ComparabilityAssessment(
            classification=classification,
            reason_codes=reason_codes,
        ),
        not hard,
    )


def _metric_value(
    sample: SamplePerformanceEvidence | None,
    metric: PerformanceMetricName,
) -> float | None:
    if sample is None:
        return None
    attempts = sample.attempts
    if not attempts:
        return None
    terminal = attempts[-1]
    successful = (
        terminal if terminal.outcome is AttemptOutcome.SUCCEEDED else None
    )
    # A successful attempt can be durable before the runner finishes the canonical
    # response write. Attempt costs remain evidence in that interrupted state, but
    # terminal-generation observations require the completed persistence contract.
    response = sample.response if successful is not None else None
    timing = response.timing if response is not None else None
    usage = response.usage if response is not None else None

    if metric is PerformanceMetricName.ATTEMPT_COUNT:
        return float(len(attempts))
    if metric is PerformanceMetricName.FAILED_ATTEMPT_COUNT:
        return float(sum(attempt.outcome is AttemptOutcome.FAILED for attempt in attempts))
    if metric is PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS:
        try:
            value = math.fsum(attempt.duration_seconds for attempt in attempts)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None
    if successful is None or response is None:
        return None
    if metric is PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS:
        return successful.duration_seconds
    if metric is PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS:
        return timing.latency_seconds if timing is not None else None
    if metric is PerformanceMetricName.PROVIDER_TOTAL_DURATION_SECONDS:
        return timing.provider_total_seconds if timing is not None else None
    if metric is PerformanceMetricName.PROVIDER_LOAD_DURATION_SECONDS:
        return timing.provider_load_seconds if timing is not None else None
    if metric is PerformanceMetricName.PROVIDER_PROMPT_EVAL_DURATION_SECONDS:
        return timing.provider_prompt_eval_seconds if timing is not None else None
    if metric is PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS:
        return timing.provider_eval_seconds if timing is not None else None
    if metric is PerformanceMetricName.PROMPT_TOKENS:
        return (
            _finite_token_count(usage.input_tokens)
            if usage is not None and usage.input_tokens is not None
            else None
        )
    if metric is PerformanceMetricName.GENERATED_TOKENS:
        return (
            _finite_token_count(usage.output_tokens)
            if usage is not None and usage.output_tokens is not None
            else None
        )
    if metric is PerformanceMetricName.TOTAL_TOKENS:
        return (
            _finite_token_count(usage.total_tokens)
            if usage is not None and usage.total_tokens is not None
            else None
        )
    if metric is PerformanceMetricName.GENERATION_TOKENS_PER_SECOND:
        if (
            usage is None
            or usage.output_tokens is None
            or timing is None
            or timing.provider_eval_seconds is None
            or timing.provider_eval_seconds <= 0.0
            or not math.isfinite(timing.provider_eval_seconds)
        ):
            return None
        generated_tokens = _finite_token_count(usage.output_tokens)
        if generated_tokens is None:
            return None
        value = generated_tokens / timing.provider_eval_seconds
        return value if math.isfinite(value) else None
    raise AssertionError(f"unhandled performance metric {metric}")


def _finite_token_count(value: int) -> float | None:
    """Convert optional token telemetry without saturating unrepresentable values."""
    try:
        converted = float(value)
    except OverflowError:
        return None
    return converted if math.isfinite(converted) else None


def _summary(values: Sequence[float]) -> MetricSummary | None:
    if not values or any(value < 0.0 or not math.isfinite(value) for value in values):
        return None
    count = len(values)
    maximum = max(values)
    try:
        mean = (
            0.0
            if maximum == 0.0
            else maximum * (math.fsum(value / maximum for value in values) / count)
        )
    except OverflowError:
        return None
    ordered = sorted(values)
    middle = count // 2
    median = (
        ordered[middle]
        if count % 2
        else ordered[middle - 1]
        + (ordered[middle] - ordered[middle - 1]) / 2.0
    )
    minimum = ordered[0]
    if not all(math.isfinite(value) for value in (mean, median, minimum, maximum)):
        return None
    return MetricSummary(
        count=count,
        mean=mean,
        median=median,
        minimum=minimum,
        maximum=maximum,
    )


def _finish_reason_counts(
    samples: Mapping[SampleIdentity, SamplePerformanceEvidence],
    identities: Sequence[SampleIdentity],
) -> tuple[FinishReasonCount, ...]:
    counts: Counter[str | None] = Counter()
    for identity in identities:
        sample = samples.get(identity)
        if sample is None or not sample.attempts:
            continue
        terminal = sample.attempts[-1]
        if (
            terminal.outcome is not AttemptOutcome.SUCCEEDED
            or sample.response is None
        ):
            continue
        counts[sample.response.finish_reason] += 1
    ordered = sorted(
        counts.items(),
        key=lambda item: (item[0] is not None, item[0] if item[0] is not None else ""),
    )
    return tuple(
        FinishReasonCount(finish_reason=finish_reason, count=count)
        for finish_reason, count in ordered
    )
