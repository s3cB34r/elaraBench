"""Deterministic M4.2b performance extraction and aggregation tests."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from elarabench.comparison_models import (
    ComparabilityClassification,
    ComparisonIntent,
    ComparisonReasonCode,
    FinishReasonCount,
    MetricAvailability,
    PerformanceAnalysis,
    PerformanceMetricComparison,
    PerformanceMetricName,
)
from elarabench.comparison_performance import (
    PerformanceComparabilityContext,
    SamplePerformanceEvidence,
    analyze_performance,
    performance_evidence_hash,
)
from elarabench.models import (
    AttemptOutcome,
    AttemptRecord,
    GenerationError,
    GenerationResponse,
    SampleIdentity,
    TimingMetadata,
    UsageInformation,
)


def _context(
    *,
    baseline_tokenizer: str | None = "tokenizer-v1",
    candidate_tokenizer: str | None = "tokenizer-v1",
    baseline_provider: str = "fake",
    candidate_provider: str = "fake",
    baseline_backend: str = "backend-a",
    candidate_backend: str = "backend-a",
    baseline_schema: int = 3,
    candidate_schema: int = 3,
    intent: ComparisonIntent = ComparisonIntent.REPEAT,
) -> PerformanceComparabilityContext:
    return PerformanceComparabilityContext(
        intent=intent,
        baseline_tokenizer=baseline_tokenizer,
        candidate_tokenizer=candidate_tokenizer,
        baseline_provider=baseline_provider,
        candidate_provider=candidate_provider,
        baseline_backend=baseline_backend,
        candidate_backend=candidate_backend,
        baseline_result_schema_version=baseline_schema,
        candidate_result_schema_version=candidate_schema,
        performance_reason_codes=(ComparisonReasonCode.WARM_STATE_UNKNOWN,),
    )


def _attempt(
    identity: SampleIdentity,
    index: int,
    *,
    duration: float,
    response: GenerationResponse,
    outcome: AttemptOutcome = AttemptOutcome.SUCCEEDED,
    timestamp_offset: int = 0,
) -> AttemptRecord:
    started = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=timestamp_offset)
    return AttemptRecord(
        identity=identity,
        attempt_index=index,
        retry_number=index,
        request_hash="a" * 64,
        started_at=started,
        completed_at=started + timedelta(seconds=duration),
        duration_seconds=duration,
        outcome=outcome,
        response=response,
    )


def _sample(
    *,
    case_id: str = "case-1",
    repeat_index: int = 0,
    response: GenerationResponse | None = None,
    attempts: tuple[AttemptRecord, ...] | None = None,
    schema: int = 3,
) -> SamplePerformanceEvidence:
    identity = SampleIdentity(case_id=case_id, repeat_index=repeat_index)
    response = response or GenerationResponse(
        text="answer",
        finish_reason="stop",
        usage=UsageInformation(input_tokens=5, output_tokens=20, total_tokens=25),
        timing=TimingMetadata(
            latency_seconds=3.0,
            provider_total_seconds=2.5,
            provider_load_seconds=0.25,
            provider_prompt_eval_seconds=0.5,
            provider_eval_seconds=2.0,
        ),
    )
    attempts = attempts or (_attempt(identity, 0, duration=3.25, response=response),)
    return SamplePerformanceEvidence(
        identity=identity,
        attempts=attempts,
        response=response,
        source_result_schema_version=schema,
    )


def _metric(
    analysis: PerformanceAnalysis, name: PerformanceMetricName
) -> PerformanceMetricComparison:
    return next(metric for metric in analysis.metrics if metric.metric_name is name)


def _baseline_median(
    analysis: PerformanceAnalysis, name: PerformanceMetricName
) -> float:
    summary = _metric(analysis, name).baseline_summary
    assert summary is not None
    return summary.median


def test_complete_metrics_are_paired_and_already_normalized_seconds() -> None:
    baseline = _sample()
    candidate_response = GenerationResponse(
        text="answer",
        finish_reason="length",
        usage=UsageInformation(input_tokens=6, output_tokens=30, total_tokens=36),
        timing=TimingMetadata(
            latency_seconds=4.0,
            provider_total_seconds=3.5,
            provider_load_seconds=0.5,
            provider_prompt_eval_seconds=0.75,
            provider_eval_seconds=2.0,
        ),
    )
    candidate = _sample(response=candidate_response)
    identity = baseline.identity

    analysis = analyze_performance(
        {identity: baseline}, {identity: candidate}, (identity,), context=_context()
    )

    generation = _metric(
        analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS
    )
    throughput = _metric(analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND)
    assert generation.baseline_summary is not None
    assert generation.candidate_summary is not None
    assert throughput.baseline_summary is not None
    assert throughput.candidate_summary is not None
    assert generation.baseline_summary.median == 2.0
    assert generation.candidate_summary.median == 2.0
    assert throughput.baseline_summary.median == 10.0
    assert throughput.candidate_summary.median == 15.0
    assert throughput.absolute_delta == 5.0
    assert throughput.relative_delta == 0.5
    assert analysis.finish_reasons.baseline_counts == (
        FinishReasonCount(finish_reason="stop", count=1),
    )
    assert analysis.finish_reasons.candidate_counts == (
        FinishReasonCount(finish_reason="length", count=1),
    )


def test_finish_reason_diagnostics_preserve_none_and_provider_strings() -> None:
    reasons = (None, None, "", "<missing>", "stop", "stop")
    samples: dict[SampleIdentity, SamplePerformanceEvidence] = {}
    identities: list[SampleIdentity] = []
    for index, finish_reason in enumerate(reasons):
        sample = _sample(
            case_id=f"case-{index}",
            response=GenerationResponse(text="answer", finish_reason=finish_reason),
        )
        identities.append(sample.identity)
        samples[sample.identity] = sample

    analysis = analyze_performance(
        samples, samples, tuple(identities), context=_context()
    )
    expected = (
        FinishReasonCount(finish_reason=None, count=2),
        FinishReasonCount(finish_reason="", count=1),
        FinishReasonCount(finish_reason="<missing>", count=1),
        FinishReasonCount(finish_reason="stop", count=2),
    )

    assert analysis.finish_reasons.baseline_counts == expected
    assert analysis.finish_reasons.candidate_counts == expected


def test_performance_hash_distinguishes_missing_and_literal_finish_reasons() -> None:
    hashes = set()
    for finish_reason in (None, "", "<missing>", "stop"):
        sample = _sample(
            response=GenerationResponse(text="answer", finish_reason=finish_reason)
        )
        hashes.add(performance_evidence_hash({sample.identity: sample}))

    assert len(hashes) == 4


def test_large_finite_summaries_and_deltas_never_become_nonfinite() -> None:
    identities = tuple(
        SampleIdentity(case_id=f"case-{index}", repeat_index=0) for index in range(2)
    )

    def samples_with_latency(value: float) -> dict[SampleIdentity, SamplePerformanceEvidence]:
        return {
            identity: _sample(
                case_id=identity.case_id,
                response=GenerationResponse(
                    text="answer",
                    timing=TimingMetadata(latency_seconds=value),
                ),
            )
            for identity in identities
        }

    equal_analysis = analyze_performance(
        samples_with_latency(1e308),
        samples_with_latency(1e308),
        identities,
        context=_context(),
    )
    equal_metric = _metric(
        equal_analysis, PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS
    )
    assert equal_metric.baseline_summary is not None
    assert equal_metric.candidate_summary is not None
    for summary in (equal_metric.baseline_summary, equal_metric.candidate_summary):
        assert summary.count == 2
        assert summary.mean == 1e308
        assert summary.median == 1e308
        assert summary.minimum == 1e308
        assert summary.maximum == 1e308
        assert all(
            math.isfinite(value)
            for value in (summary.mean, summary.median, summary.minimum, summary.maximum)
        )
    assert equal_metric.absolute_delta == 0.0
    assert equal_metric.relative_delta == 0.0

    extreme_delta_analysis = analyze_performance(
        samples_with_latency(5e-324),
        samples_with_latency(1e308),
        identities,
        context=_context(),
    )
    extreme_metric = _metric(
        extreme_delta_analysis, PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS
    )
    assert extreme_metric.absolute_delta == 1e308
    assert math.isfinite(extreme_metric.absolute_delta)
    assert extreme_metric.relative_delta is None
    assert (
        ComparisonReasonCode.RELATIVE_DELTA_UNREPRESENTABLE
        in extreme_metric.comparability.reason_codes
    )


def test_ordinary_summary_statistics_remain_exact() -> None:
    identities = tuple(
        SampleIdentity(case_id=f"case-{index}", repeat_index=0) for index in range(3)
    )
    samples = {
        identity: _sample(
            case_id=identity.case_id,
            response=GenerationResponse(
                text="answer",
                timing=TimingMetadata(latency_seconds=float(index + 1)),
            ),
        )
        for index, identity in enumerate(identities)
    }
    analysis = analyze_performance(samples, samples, identities, context=_context())
    metric = _metric(analysis, PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS)

    assert metric.baseline_summary is not None
    assert metric.baseline_summary.model_dump() == {
        "count": 3,
        "mean": 2.0,
        "median": 2.0,
        "minimum": 1.0,
        "maximum": 3.0,
    }
    assert metric.absolute_delta == 0.0
    assert metric.relative_delta == 0.0


def test_throughput_zero_and_missing_inputs_never_emit_nonfinite_values() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    zero_tokens = _sample(
        response=GenerationResponse(
            text="",
            usage=UsageInformation(output_tokens=0),
            timing=TimingMetadata(provider_eval_seconds=2.0),
        )
    )
    zero_duration = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(output_tokens=10),
            timing=TimingMetadata(provider_eval_seconds=0.0),
        )
    )
    missing_tokens = _sample(
        response=GenerationResponse(
            text="answer",
            timing=TimingMetadata(provider_eval_seconds=1.0),
        )
    )

    zero_analysis = analyze_performance(
        {identity: zero_tokens}, {identity: zero_tokens}, (identity,), context=_context()
    )
    zero_metric = _metric(
        zero_analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND
    )
    assert zero_metric.baseline_summary is not None
    assert zero_metric.baseline_summary.median == 0.0
    assert zero_metric.relative_delta is None
    assert (
        ComparisonReasonCode.ZERO_BASELINE_RELATIVE_DELTA_UNAVAILABLE
        in zero_metric.comparability.reason_codes
    )

    for sample in (zero_duration, missing_tokens):
        analysis = analyze_performance(
            {identity: sample}, {identity: sample}, (identity,), context=_context()
        )
        metric = _metric(
            analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND
        )
        assert metric.availability is MetricAvailability.UNAVAILABLE
        assert metric.baseline_summary is None
        assert metric.absolute_delta is None
        assert metric.relative_delta is None


def test_oversized_token_metrics_degrade_independently_without_nonfinite_output() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    oversized = 10**400

    oversized_input = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(
                input_tokens=oversized,
                output_tokens=128,
                total_tokens=129,
            ),
            timing=TimingMetadata(provider_eval_seconds=2.0),
        )
    )
    input_analysis = analyze_performance(
        {identity: oversized_input},
        {identity: oversized_input},
        (identity,),
        context=_context(),
    )
    assert (
        _metric(input_analysis, PerformanceMetricName.PROMPT_TOKENS).availability
        is MetricAvailability.UNAVAILABLE
    )
    assert _baseline_median(input_analysis, PerformanceMetricName.GENERATED_TOKENS) == 128
    assert (
        _baseline_median(
            input_analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS
        )
        == 2.0
    )
    assert (
        _baseline_median(
            input_analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND
        )
        == 64.0
    )

    oversized_output = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(
                input_tokens=5,
                output_tokens=oversized,
                total_tokens=25,
            ),
            timing=TimingMetadata(provider_eval_seconds=1.0),
        )
    )
    output_analysis = analyze_performance(
        {identity: oversized_output},
        {identity: oversized_output},
        (identity,),
        context=_context(),
    )
    assert (
        _metric(output_analysis, PerformanceMetricName.GENERATED_TOKENS).availability
        is MetricAvailability.UNAVAILABLE
    )
    assert (
        _metric(
            output_analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND
        ).availability
        is MetricAvailability.UNAVAILABLE
    )
    assert (
        _baseline_median(
            output_analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS
        )
        == 1.0
    )
    assert _baseline_median(output_analysis, PerformanceMetricName.PROMPT_TOKENS) == 5

    oversized_total = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(
                input_tokens=5,
                output_tokens=20,
                total_tokens=oversized,
            ),
            timing=TimingMetadata(provider_eval_seconds=2.0),
        )
    )
    total_analysis = analyze_performance(
        {identity: oversized_total},
        {identity: oversized_total},
        (identity,),
        context=_context(),
    )
    assert (
        _metric(total_analysis, PerformanceMetricName.TOTAL_TOKENS).availability
        is MetricAvailability.UNAVAILABLE
    )
    assert _baseline_median(total_analysis, PerformanceMetricName.PROMPT_TOKENS) == 5
    assert _baseline_median(total_analysis, PerformanceMetricName.GENERATED_TOKENS) == 20
    serialized = output_analysis.model_dump_json()
    assert "NaN" not in serialized
    assert "Infinity" not in serialized


def test_representable_huge_tokens_and_overflowing_throughput_are_scoped() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    representable = 10**308
    huge = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(output_tokens=representable),
            timing=TimingMetadata(provider_eval_seconds=1.0),
        )
    )
    huge_analysis = analyze_performance(
        {identity: huge}, {identity: huge}, (identity,), context=_context()
    )
    huge_generated = _baseline_median(
        huge_analysis, PerformanceMetricName.GENERATED_TOKENS
    )
    assert math.isfinite(huge_generated)
    assert huge_generated == float(representable)

    tiny_duration = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(output_tokens=2),
            timing=TimingMetadata(provider_eval_seconds=5e-324),
        )
    )
    tiny_analysis = analyze_performance(
        {identity: tiny_duration},
        {identity: tiny_duration},
        (identity,),
        context=_context(),
    )
    assert _baseline_median(tiny_analysis, PerformanceMetricName.GENERATED_TOKENS) == 2
    assert (
        _baseline_median(
            tiny_analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS
        )
        == 5e-324
    )
    assert (
        _metric(
            tiny_analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND
        ).availability
        is MetricAvailability.UNAVAILABLE
    )


def test_oversized_token_on_one_side_updates_only_that_metric_missingness() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    baseline = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(output_tokens=20),
            timing=TimingMetadata(provider_eval_seconds=2.0),
        )
    )
    candidate = _sample(
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(output_tokens=10**400),
            timing=TimingMetadata(provider_eval_seconds=2.0),
        )
    )
    analysis = analyze_performance(
        {identity: baseline}, {identity: candidate}, (identity,), context=_context()
    )
    generated = _metric(analysis, PerformanceMetricName.GENERATED_TOKENS)
    duration = _metric(
        analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS
    )

    assert generated.availability is MetricAvailability.UNAVAILABLE
    assert generated.missingness.baseline_available_count == 1
    assert generated.missingness.candidate_available_count == 0
    assert generated.missingness.paired_available_count == 0
    assert generated.missingness.candidate_missing_count == 1
    assert generated.absolute_delta is None
    assert duration.availability is MetricAvailability.AVAILABLE
    assert duration.absolute_delta == 0.0


def test_terminal_success_and_all_attempt_cost_have_distinct_semantics() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    failed = GenerationResponse(
        error=GenerationError(code="retry", message="retry", retryable=True)
    )
    success = GenerationResponse(
        text="answer",
        finish_reason="stop",
        usage=UsageInformation(output_tokens=12),
        timing=TimingMetadata(provider_eval_seconds=3.0),
    )
    attempts = (
        _attempt(
            identity,
            0,
            duration=1.0,
            response=failed,
            outcome=AttemptOutcome.FAILED,
        ),
        _attempt(identity, 1, duration=4.0, response=success),
    )
    sample = _sample(response=success, attempts=attempts)

    analysis = analyze_performance(
        {identity: sample}, {identity: sample}, (identity,), context=_context()
    )

    assert _baseline_median(analysis, PerformanceMetricName.ATTEMPT_COUNT) == 2
    assert _baseline_median(analysis, PerformanceMetricName.FAILED_ATTEMPT_COUNT) == 1
    assert (
        _baseline_median(
            analysis, PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS
        )
        == 4.0
    )
    assert (
        _baseline_median(
            analysis, PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS
        )
        == 5.0
    )
    assert (
        _baseline_median(
            analysis, PerformanceMetricName.GENERATION_TOKENS_PER_SECOND
        )
        == 4.0
    )


def test_interrupted_success_requires_canonical_response_for_terminal_metrics() -> None:
    complete = _sample()
    identity = complete.identity
    interrupted = replace(complete, response=None)

    analysis = analyze_performance(
        {identity: interrupted},
        {identity: interrupted},
        (identity,),
        context=_context(),
    )

    assert _baseline_median(analysis, PerformanceMetricName.ATTEMPT_COUNT) == 1
    assert _baseline_median(analysis, PerformanceMetricName.FAILED_ATTEMPT_COUNT) == 0
    assert (
        _baseline_median(
            analysis, PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS
        )
        == 3.25
    )
    for name in (
        PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS,
        PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS,
        PerformanceMetricName.PROVIDER_TOTAL_DURATION_SECONDS,
        PerformanceMetricName.PROVIDER_LOAD_DURATION_SECONDS,
        PerformanceMetricName.PROVIDER_PROMPT_EVAL_DURATION_SECONDS,
        PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS,
        PerformanceMetricName.PROMPT_TOKENS,
        PerformanceMetricName.GENERATED_TOKENS,
        PerformanceMetricName.TOTAL_TOKENS,
        PerformanceMetricName.GENERATION_TOKENS_PER_SECOND,
    ):
        assert _metric(analysis, name).availability is MetricAvailability.UNAVAILABLE
    assert analysis.finish_reasons.baseline_counts == ()
    assert analysis.finish_reasons.candidate_counts == ()
    assert performance_evidence_hash({identity: interrupted}) != performance_evidence_hash(
        {identity: complete}
    )


def test_interrupted_retry_success_retains_all_attempt_cost_only() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    failure = GenerationResponse(
        error=GenerationError(code="retry", message="retry", retryable=True)
    )
    success = GenerationResponse(
        text="answer",
        finish_reason="stop",
        usage=UsageInformation(output_tokens=12),
        timing=TimingMetadata(provider_eval_seconds=3.0),
    )
    attempts = (
        _attempt(
            identity,
            0,
            duration=1.0,
            response=failure,
            outcome=AttemptOutcome.FAILED,
        ),
        _attempt(identity, 1, duration=4.0, response=success),
    )
    interrupted = replace(_sample(response=success, attempts=attempts), response=None)

    analysis = analyze_performance(
        {identity: interrupted},
        {identity: interrupted},
        (identity,),
        context=_context(),
    )

    assert _baseline_median(analysis, PerformanceMetricName.ATTEMPT_COUNT) == 2
    assert _baseline_median(analysis, PerformanceMetricName.FAILED_ATTEMPT_COUNT) == 1
    assert (
        _baseline_median(
            analysis, PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS
        )
        == 5.0
    )
    assert (
        _metric(
            analysis, PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS
        ).availability
        is MetricAvailability.UNAVAILABLE
    )
    assert (
        _metric(analysis, PerformanceMetricName.GENERATED_TOKENS).availability
        is MetricAvailability.UNAVAILABLE
    )


def test_unstarted_sample_has_no_attempt_derived_performance_evidence() -> None:
    complete = _sample()
    identity = complete.identity
    unstarted = replace(complete, attempts=(), response=None)

    analysis = analyze_performance(
        {identity: unstarted},
        {identity: unstarted},
        (identity,),
        context=_context(),
    )

    for name in (
        PerformanceMetricName.ATTEMPT_COUNT,
        PerformanceMetricName.FAILED_ATTEMPT_COUNT,
        PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS,
        PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS,
    ):
        metric = _metric(analysis, name)
        assert metric.availability is MetricAvailability.UNAVAILABLE
        assert metric.baseline_summary is None
        assert metric.candidate_summary is None
        assert metric.missingness.baseline_available_count == 0
        assert metric.missingness.candidate_available_count == 0
        assert metric.missingness.paired_available_count == 0

    zero_response = GenerationResponse(text="answer", finish_reason="stop")
    zero_attempt = _attempt(identity, 0, duration=0.0, response=zero_response)
    zero_duration = _sample(response=zero_response, attempts=(zero_attempt,))
    assert performance_evidence_hash({identity: unstarted}) != performance_evidence_hash(
        {identity: zero_duration}
    )


@pytest.mark.parametrize("unstarted_side", ("baseline", "candidate"))
def test_unstarted_side_is_missing_from_paired_attempt_metrics(
    unstarted_side: str,
) -> None:
    executed = _sample()
    identity = executed.identity
    unstarted = replace(executed, attempts=(), response=None)
    baseline = unstarted if unstarted_side == "baseline" else executed
    candidate = unstarted if unstarted_side == "candidate" else executed

    analysis = analyze_performance(
        {identity: baseline},
        {identity: candidate},
        (identity,),
        context=_context(),
    )

    for name in (
        PerformanceMetricName.ATTEMPT_COUNT,
        PerformanceMetricName.FAILED_ATTEMPT_COUNT,
        PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS,
    ):
        metric = _metric(analysis, name)
        assert metric.availability is MetricAvailability.UNAVAILABLE
        assert metric.missingness.paired_available_count == 0
        assert metric.absolute_delta is None
        if unstarted_side == "baseline":
            assert metric.missingness.baseline_available_count == 0
            assert metric.missingness.baseline_missing_count == 1
            assert metric.missingness.candidate_available_count == 1
        else:
            assert metric.missingness.baseline_available_count == 1
            assert metric.missingness.candidate_available_count == 0
            assert metric.missingness.candidate_missing_count == 1
        serialized = metric.model_dump(mode="json")
        assert serialized["baseline_summary"] is None
        assert serialized["candidate_summary"] is None


def test_zero_duration_and_failed_attempts_remain_observed_execution_cost() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    success = GenerationResponse(text="answer", finish_reason="stop")
    zero_success_attempt = _attempt(identity, 0, duration=0.0, response=success)
    zero_success = _sample(response=success, attempts=(zero_success_attempt,))
    success_analysis = analyze_performance(
        {identity: zero_success},
        {identity: zero_success},
        (identity,),
        context=_context(),
    )
    assert _baseline_median(success_analysis, PerformanceMetricName.ATTEMPT_COUNT) == 1
    assert (
        _baseline_median(success_analysis, PerformanceMetricName.FAILED_ATTEMPT_COUNT)
        == 0
    )
    assert (
        _baseline_median(
            success_analysis, PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS
        )
        == 0.0
    )

    failure = GenerationResponse(error=GenerationError(code="failed", message="failed"))
    zero_failed_attempt = _attempt(
        identity,
        0,
        duration=0.0,
        response=failure,
        outcome=AttemptOutcome.FAILED,
    )
    zero_failed = _sample(response=failure, attempts=(zero_failed_attempt,))
    zero_failed_analysis = analyze_performance(
        {identity: zero_failed},
        {identity: zero_failed},
        (identity,),
        context=_context(),
    )
    assert (
        _baseline_median(zero_failed_analysis, PerformanceMetricName.ATTEMPT_COUNT)
        == 1
    )
    assert (
        _baseline_median(
            zero_failed_analysis, PerformanceMetricName.FAILED_ATTEMPT_COUNT
        )
        == 1
    )
    assert (
        _baseline_median(
            zero_failed_analysis,
            PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS,
        )
        == 0.0
    )

    failed_attempts = (
        _attempt(
            identity,
            0,
            duration=0.0,
            response=failure,
            outcome=AttemptOutcome.FAILED,
        ),
        _attempt(
            identity,
            1,
            duration=2.0,
            response=failure,
            outcome=AttemptOutcome.FAILED,
        ),
    )
    failed = _sample(response=failure, attempts=failed_attempts)
    failed_analysis = analyze_performance(
        {identity: failed},
        {identity: failed},
        (identity,),
        context=_context(),
    )
    assert _baseline_median(failed_analysis, PerformanceMetricName.ATTEMPT_COUNT) == 2
    assert (
        _baseline_median(failed_analysis, PerformanceMetricName.FAILED_ATTEMPT_COUNT)
        == 2
    )
    assert (
        _baseline_median(
            failed_analysis, PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS
        )
        == 2.0
    )
    assert (
        _metric(failed_analysis, PerformanceMetricName.GENERATED_TOKENS).availability
        is MetricAvailability.UNAVAILABLE
    )


def test_all_failed_samples_retain_only_execution_cost_metrics() -> None:
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    failure = GenerationResponse(error=GenerationError(code="failed", message="failed"))
    attempt = _attempt(
        identity,
        0,
        duration=1.25,
        response=failure,
        outcome=AttemptOutcome.FAILED,
    )
    sample = _sample(response=failure, attempts=(attempt,))

    analysis = analyze_performance(
        {identity: sample}, {identity: sample}, (identity,), context=_context()
    )

    assert _baseline_median(analysis, PerformanceMetricName.ATTEMPT_COUNT) == 1
    assert (
        _baseline_median(
            analysis, PerformanceMetricName.ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS
        )
        == 1.25
    )
    assert (
        _metric(analysis, PerformanceMetricName.GENERATED_TOKENS).availability
        is MetricAvailability.UNAVAILABLE
    )
    assert (
        _metric(analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS).availability
        is MetricAvailability.UNAVAILABLE
    )


def test_direct_summaries_use_only_joint_paired_metric_values() -> None:
    first = _sample(case_id="case-1")
    second_identity = SampleIdentity(case_id="case-2", repeat_index=0)
    second = _sample(case_id="case-2")
    candidate_first = _sample(
        case_id="case-1",
        response=GenerationResponse(
            text="answer",
            usage=UsageInformation(output_tokens=40),
            timing=TimingMetadata(provider_eval_seconds=2.0),
        ),
    )
    candidate_second = _sample(
        case_id="case-2",
        response=GenerationResponse(text="answer"),
    )

    analysis = analyze_performance(
        {first.identity: first, second_identity: second},
        {candidate_first.identity: candidate_first, second_identity: candidate_second},
        (first.identity, second_identity),
        context=_context(),
    )
    generated = _metric(analysis, PerformanceMetricName.GENERATED_TOKENS)

    assert generated.availability is MetricAvailability.PARTIAL
    assert generated.baseline_summary is not None
    assert generated.candidate_summary is not None
    assert generated.baseline_summary.count == 1
    assert generated.baseline_summary.median == 20
    assert generated.candidate_summary.median == 40
    assert generated.missingness.expected_paired_sample_count == 2
    assert generated.missingness.baseline_available_count == 2
    assert generated.missingness.candidate_available_count == 1
    assert generated.missingness.paired_available_count == 1
    assert generated.missingness.unpaired_available_count == 1


def test_tokenizer_and_provider_native_metric_policies_are_metric_specific() -> None:
    sample = _sample()
    identity = sample.identity
    tokenizer_analysis = analyze_performance(
        {identity: sample},
        {identity: sample},
        (identity,),
        context=_context(candidate_tokenizer="tokenizer-v2"),
    )
    token_metric = _metric(tokenizer_analysis, PerformanceMetricName.GENERATED_TOKENS)
    client_metric = _metric(
        tokenizer_analysis, PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS
    )
    assert (
        token_metric.comparability.classification
        is ComparabilityClassification.NOT_DIRECTLY_COMPARABLE
    )
    assert token_metric.absolute_delta is None
    assert client_metric.comparability.classification is ComparabilityClassification.QUALIFIED

    backend_analysis = analyze_performance(
        {identity: sample},
        {identity: sample},
        (identity,),
        context=_context(
            candidate_provider="other",
            candidate_backend="backend-b",
            intent=ComparisonIntent.BACKEND,
        ),
    )
    provider_metric = _metric(
        backend_analysis, PerformanceMetricName.PROVIDER_GENERATION_DURATION_SECONDS
    )
    client_metric = _metric(
        backend_analysis, PerformanceMetricName.CLIENT_REQUEST_DURATION_SECONDS
    )
    assert (
        provider_metric.comparability.classification
        is ComparabilityClassification.NOT_DIRECTLY_COMPARABLE
    )
    assert provider_metric.absolute_delta is None
    assert client_metric.comparability.classification is ComparabilityClassification.QUALIFIED
    assert client_metric.absolute_delta == 0.0


def test_performance_hash_tracks_duration_and_usage_but_not_wall_timestamps() -> None:
    original = _sample()
    identity = original.identity
    original_hash = performance_evidence_hash({identity: original})
    response = original.response
    assert response is not None

    shifted_attempt = original.attempts[0].model_copy(
        update={
            "started_at": original.attempts[0].started_at + timedelta(days=1),
            "completed_at": original.attempts[0].completed_at + timedelta(days=1),
        }
    )
    shifted = _sample(response=response, attempts=(shifted_attempt,))
    assert performance_evidence_hash({identity: shifted}) == original_hash

    duration_attempt = original.attempts[0].model_copy(update={"duration_seconds": 9.0})
    duration_changed = _sample(response=response, attempts=(duration_attempt,))
    assert performance_evidence_hash({identity: duration_changed}) != original_hash

    usage_response = response.model_copy(
        update={"usage": UsageInformation(input_tokens=5, output_tokens=21, total_tokens=26)}
    )
    usage_attempt = original.attempts[0].model_copy(update={"response": usage_response})
    usage_changed = _sample(response=usage_response, attempts=(usage_attempt,))
    assert performance_evidence_hash({identity: usage_changed}) != original_hash

    huge_response = response.model_copy(
        update={"usage": UsageInformation(output_tokens=10**400)}
    )
    other_huge_response = response.model_copy(
        update={"usage": UsageInformation(output_tokens=10**401)}
    )
    huge = _sample(response=huge_response)
    other_huge = _sample(response=other_huge_response)
    assert performance_evidence_hash({identity: huge}) != performance_evidence_hash(
        {identity: other_huge}
    )


def test_legacy_metric_evidence_is_observational_and_qualified() -> None:
    sample = _sample(schema=2)
    identity = sample.identity
    analysis = analyze_performance(
        {identity: sample},
        {identity: sample},
        (identity,),
        context=_context(baseline_schema=2, candidate_schema=2),
    )
    duration = _metric(
        analysis, PerformanceMetricName.TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS
    )
    assert duration.baseline_summary is not None
    assert duration.baseline_summary.median == 3.25
    assert ComparisonReasonCode.LEGACY_IDENTITY_GAP in duration.comparability.reason_codes
    assert duration.comparability.classification is ComparabilityClassification.QUALIFIED
