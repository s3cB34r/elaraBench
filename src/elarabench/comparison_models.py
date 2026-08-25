"""Typed, versioned values produced by trustworthy run comparison."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from elarabench.models import (
    BehavioralRate,
    DomainModel,
    RefusalComplianceSummary,
    SampleIdentity,
    Score,
    SemanticVersion,
    Sha256Digest,
)


class BehavioralRateComparison(DomainModel):
    baseline: BehavioralRate
    candidate: BehavioralRate
    percentage_point_delta: float | None = None


class RefusalComplianceAnalysis(DomainModel):
    semantic_version: Literal["refusal_compliance_comparison_v1"] = (
        "refusal_compliance_comparison_v1"
    )
    evaluator_name: Literal["refusal_compliance"] = "refusal_compliance"
    evaluator_version: Literal["1.0.0"] = "1.0.0"
    selected_case_ids: tuple[str, ...]
    baseline: RefusalComplianceSummary
    candidate: RefusalComplianceSummary
    successful_completion_rate: BehavioralRateComparison
    unnecessary_refusal_rate: BehavioralRateComparison
    appropriate_refusal_rate: BehavioralRateComparison
    instruction_following_rate: BehavioralRateComparison
    false_policy_trigger_rate: BehavioralRateComparison
    refusal_rate: BehavioralRateComparison
    compliance_rate: BehavioralRateComparison
    inappropriate_compliance_rate: BehavioralRateComparison
    balanced_behavior_accuracy_delta: float | None = None


class ComparisonIntent(StrEnum):
    MODEL = "model"
    REPEAT = "repeat"
    QUANTIZATION = "quantization"
    THINKING = "thinking"
    BACKEND = "backend"


class ComparabilityClassification(StrEnum):
    STRICT = "strict"
    QUALIFIED = "qualified"
    NOT_DIRECTLY_COMPARABLE = "not_directly_comparable"


class ComparabilityDimension(StrEnum):
    BENCHMARK = "benchmark"
    MODEL_IDENTITY = "model_identity"
    INFERENCE_PROFILE = "inference_profile"
    EVALUATION = "evaluation"
    COVERAGE = "coverage"
    PERFORMANCE_ENVIRONMENT = "performance_environment"


class EvidenceState(StrEnum):
    MATCH = "match"
    DIFFERENCE = "difference"
    EXPECTED_DIFFERENCE = "expected_difference"
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class EvidenceImpact(StrEnum):
    QUALITY = "quality"
    PERFORMANCE = "performance"
    BOTH = "both"
    DIAGNOSTIC = "diagnostic"


class ComparisonReasonCode(StrEnum):
    BENCHMARK_CONTENT_MISMATCH = "benchmark_content_mismatch"
    SUITE_IDENTITY_CONFLICT = "suite_identity_conflict"
    CASE_SET_DIFFERENCE = "case_set_difference"
    CASE_DEFINITION_MISMATCH = "case_definition_mismatch"
    CASE_WEIGHT_MISMATCH = "case_weight_mismatch"
    CASE_CATEGORY_MISMATCH = "case_category_mismatch"
    CASE_TAGS_MISMATCH = "case_tags_mismatch"
    CASE_FIXTURE_MISMATCH = "case_fixture_mismatch"
    SUITE_NAMESPACE_DIFFERENCE = "suite_namespace_difference"
    SUITE_VERSION_DIFFERENCE = "suite_version_difference"
    VERIFIED_INTERSECTION_COMPARISON = "verified_intersection_comparison"
    NO_VERIFIED_CASE_INTERSECTION = "no_verified_case_intersection"
    EMPTY_MATCHED_SCORED_POPULATION = "empty_matched_scored_population"
    AGGREGATION_SEMANTICS_MISMATCH = "aggregation_semantics_mismatch"
    EVALUATOR_SPECIFICATION_MISMATCH = "evaluator_specification_mismatch"
    EVALUATOR_UNAVAILABLE = "evaluator_unavailable"
    MODEL_DIGEST_UNKNOWN = "model_digest_unknown"
    MODEL_DIGEST_DIFFERENCE = "model_digest_difference"
    MODEL_ALIAS_DIFFERENCE = "model_alias_difference"
    MODEL_METADATA_CONFLICT = "model_metadata_conflict"
    MODEL_LINEAGE_UNVERIFIED = "model_lineage_unverified"
    QUANTIZATION_UNKNOWN = "quantization_unknown"
    QUANTIZATION_DIFFERENCE = "quantization_difference"
    TOKENIZER_UNKNOWN = "tokenizer_unknown"
    TOKENIZER_DIFFERENCE = "tokenizer_difference"
    TEMPLATE_HASH_DIFFERENCE = "template_hash_difference"
    CHAT_TEMPLATE_DIFFERENCE = "chat_template_difference"
    CHAT_TEMPLATE_UNKNOWN = "chat_template_unknown"
    PARAMETERS_HASH_DIFFERENCE = "parameters_hash_difference"
    MODEL_ARCHITECTURE_DIFFERENCE = "model_architecture_difference"
    MODEL_FORMAT_DIFFERENCE = "model_format_difference"
    MODEL_FAMILY_DIFFERENCE = "model_family_difference"
    PARAMETER_SIZE_DIFFERENCE = "parameter_size_difference"
    PROVIDER_DIFFERENCE = "provider_difference"
    ADAPTER_VERSION_DIFFERENCE = "adapter_version_difference"
    BACKEND_DIFFERENCE = "backend_difference"
    BACKEND_VERSION_UNKNOWN = "backend_version_unknown"
    BACKEND_VERSION_DIFFERENCE = "backend_version_difference"
    ENDPOINT_DIFFERENCE = "endpoint_difference"
    SOURCE_IDENTITY_DIFFERENCE = "source_identity_difference"
    THINKING_POLICY_DIFFERENCE = "thinking_policy_difference"
    THINKING_CONTROL_DIFFERENCE = "thinking_control_difference"
    THINKING_CONTROL_UNKNOWN = "thinking_control_unknown"
    PROVIDER_DEFAULT_THINKING = "provider_default_thinking"
    TEMPERATURE_DIFFERENCE = "temperature_difference"
    TOP_P_DIFFERENCE = "top_p_difference"
    TOP_K_DIFFERENCE = "top_k_difference"
    MAX_TOKENS_DIFFERENCE = "max_tokens_difference"
    STOP_SEQUENCE_DIFFERENCE = "stop_sequence_difference"
    SEED_DIFFERENCE = "seed_difference"
    SEED_CONTROL_DIFFERENCE = "seed_control_difference"
    REPEATS_DIFFERENCE = "repeats_difference"
    TIMEOUT_DIFFERENCE = "timeout_difference"
    RETRY_POLICY_DIFFERENCE = "retry_policy_difference"
    ATTEMPT_COUNT_DIFFERENCE = "attempt_count_difference"
    COVERAGE_INSUFFICIENT = "coverage_insufficient"
    COVERAGE_THRESHOLD_DIFFERENCE = "coverage_threshold_difference"
    INCOMPLETE_SAMPLE_POPULATION = "incomplete_sample_population"
    SCORED_CASE_SET_DIFFERENCE = "scored_case_set_difference"
    SAMPLE_STATUS_DIFFERENCE = "sample_status_difference"
    LEGACY_IDENTITY_GAP = "legacy_identity_gap"
    ENVIRONMENT_GPU_DIFFERENCE = "environment_gpu_difference"
    ENVIRONMENT_CPU_DIFFERENCE = "environment_cpu_difference"
    ENVIRONMENT_ARCHITECTURE_DIFFERENCE = "environment_architecture_difference"
    ENVIRONMENT_OS_DIFFERENCE = "environment_os_difference"
    ENVIRONMENT_PYTHON_DIFFERENCE = "environment_python_difference"
    ENVIRONMENT_DRIVER_DIFFERENCE = "environment_driver_difference"
    ENVIRONMENT_RUNTIME_DIFFERENCE = "environment_runtime_difference"
    PERFORMANCE_METRIC_MISSING = "performance_metric_missing"
    ZERO_BASELINE_RELATIVE_DELTA_UNAVAILABLE = (
        "zero_baseline_relative_delta_unavailable"
    )
    RELATIVE_DELTA_UNREPRESENTABLE = "relative_delta_unrepresentable"
    WARM_STATE_UNKNOWN = "warm_state_unknown"
    INTENDED_VARIABLE_NOT_DIFFERENT = "intended_variable_not_different"


class ModelIdentityConfidence(StrEnum):
    DIGEST_IDENTIFIED = "digest_identified"
    METADATA_IDENTIFIED = "metadata_identified"
    ALIAS_ONLY = "alias_only"
    INSUFFICIENT = "insufficient"


class CaseDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    UNCHANGED = "unchanged"
    UNAVAILABLE = "unavailable"


class MetricAvailability(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class PerformanceMetricName(StrEnum):
    CLIENT_REQUEST_DURATION_SECONDS = "client_request_duration_seconds"
    PROVIDER_TOTAL_DURATION_SECONDS = "provider_total_duration_seconds"
    PROVIDER_LOAD_DURATION_SECONDS = "provider_load_duration_seconds"
    PROVIDER_PROMPT_EVAL_DURATION_SECONDS = "provider_prompt_eval_duration_seconds"
    PROVIDER_GENERATION_DURATION_SECONDS = "provider_generation_duration_seconds"
    TERMINAL_ATTEMPT_ACTIVE_DURATION_SECONDS = (
        "terminal_attempt_active_duration_seconds"
    )
    PROMPT_TOKENS = "prompt_tokens"
    GENERATED_TOKENS = "generated_tokens"
    TOTAL_TOKENS = "total_tokens"
    GENERATION_TOKENS_PER_SECOND = "generation_tokens_per_second"
    ATTEMPT_COUNT = "attempt_count"
    FAILED_ATTEMPT_COUNT = "failed_attempt_count"
    ALL_ATTEMPTS_ACTIVE_DURATION_SECONDS = "all_attempts_active_duration_seconds"


class PerformanceMetricUnit(StrEnum):
    SECONDS = "seconds"
    TOKENS = "tokens"
    TOKENS_PER_SECOND = "tokens_per_second"
    COUNT = "count"


class MetricDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    UNCHANGED = "unchanged"


class CaseEvidenceStatus(StrEnum):
    SCORED = "scored"
    INVALID = "invalid"
    ERROR = "error"
    PENDING_REVIEW = "pending_review"
    MIXED = "mixed"
    INCOMPLETE = "incomplete"
    MISSING = "missing"


class CasePopulationMode(StrEnum):
    FULL_SUITE = "full_suite"
    MATCHED_CASE_PARTIAL = "matched_case_partial"
    VERIFIED_INTERSECTION = "verified_intersection"
    VERIFIED_INTERSECTION_MATCHED_PARTIAL = "verified_intersection_matched_partial"
    NONE = "none"


class FieldEvidence(DomainModel):
    field_path: str
    dimension: ComparabilityDimension
    impact: EvidenceImpact
    state: EvidenceState
    baseline_value: JsonValue | None = None
    candidate_value: JsonValue | None = None
    reason_code: ComparisonReasonCode | None = None


class ComparabilityAssessment(DomainModel):
    classification: ComparabilityClassification
    reason_codes: tuple[ComparisonReasonCode, ...] = ()


class MetricSummary(DomainModel):
    count: int = Field(ge=1)
    mean: float = Field(allow_inf_nan=False)
    median: float = Field(allow_inf_nan=False)
    minimum: float = Field(allow_inf_nan=False)
    maximum: float = Field(allow_inf_nan=False)


class MetricMissingness(DomainModel):
    expected_paired_sample_count: int = Field(ge=0)
    baseline_available_count: int = Field(ge=0)
    candidate_available_count: int = Field(ge=0)
    paired_available_count: int = Field(ge=0)
    baseline_missing_count: int = Field(ge=0)
    candidate_missing_count: int = Field(ge=0)
    unpaired_available_count: int = Field(ge=0)


class PerformanceMetricComparison(DomainModel):
    metric_name: PerformanceMetricName
    unit: PerformanceMetricUnit
    selected_sample_identities: tuple[SampleIdentity, ...] = ()
    availability: MetricAvailability
    baseline_summary: MetricSummary | None = None
    candidate_summary: MetricSummary | None = None
    missingness: MetricMissingness
    comparability: ComparabilityAssessment
    absolute_delta: float | None = Field(default=None, allow_inf_nan=False)
    relative_delta: float | None = Field(default=None, allow_inf_nan=False)
    direction: MetricDirection | None = None
    semantic_version: Literal["performance_metrics_v1"] = "performance_metrics_v1"


class FinishReasonCount(DomainModel):
    finish_reason: str | None
    count: int = Field(ge=1)


class FinishReasonDiagnostics(DomainModel):
    baseline_counts: tuple[FinishReasonCount, ...] = ()
    candidate_counts: tuple[FinishReasonCount, ...] = ()


class PerformanceAnalysis(DomainModel):
    semantic_version: Literal["performance_metrics_v1"] = "performance_metrics_v1"
    aggregation_semantic: Literal[
        "paired_sample_median_v1"
    ] = "paired_sample_median_v1"
    baseline_evidence_hash: Sha256Digest
    candidate_evidence_hash: Sha256Digest
    selected_sample_identities: tuple[SampleIdentity, ...]
    execution_cost_sample_identities: tuple[SampleIdentity, ...] = ()
    metrics: tuple[PerformanceMetricComparison, ...]
    finish_reasons: FinishReasonDiagnostics


class BenchmarkIdentityEvidence(DomainModel):
    suite_id: str
    version: str
    content_hash: Sha256Digest
    snapshot_hash: Sha256Digest


class RunComparisonReference(DomainModel):
    run_id: str
    run_fingerprint: Sha256Digest
    result_schema_version: Literal[2, 3]
    evidence_hash: Sha256Digest
    benchmark: BenchmarkIdentityEvidence


class ModelIdentityAssessment(DomainModel):
    baseline_confidence: ModelIdentityConfidence
    candidate_confidence: ModelIdentityConfidence
    relationship: Literal["same", "different", "unknown", "conflict"]
    lineage: Literal["unverified"] = "unverified"


class EvaluatorProvenance(DomainModel):
    run_role: Literal["baseline", "candidate"]
    case_id: str
    evaluator_name: str
    evaluator_version: str
    configuration_hash: Sha256Digest
    source_result_schema_version: Literal[2, 3]


class CoverageComparison(DomainModel):
    baseline_expected_samples: int = Field(ge=1)
    candidate_expected_samples: int = Field(ge=1)
    baseline_scored_samples: int = Field(ge=0)
    candidate_scored_samples: int = Field(ge=0)
    baseline_ratio: Score
    candidate_ratio: Score
    matched_case_count: int = Field(ge=0)
    benchmark_case_count: int = Field(ge=1)


class ScoreComparison(DomainModel):
    baseline_score: Score
    candidate_score: Score
    delta: float
    percentage_points: float
    case_count: int = Field(ge=1)


class BreakdownComparison(ScoreComparison):
    name: str
    population_mode: CasePopulationMode | None = None
    baseline_total_case_count: int | None = Field(default=None, ge=1)
    candidate_total_case_count: int | None = Field(default=None, ge=1)


class SourceRunScore(DomainModel):
    score: Score
    case_count: int = Field(ge=1)


class VerifiedCaseIdentity(DomainModel):
    case_id: str
    canonical_case_hash: Sha256Digest
    weight: float = Field(gt=0.0)


class CaseDefinitionMismatch(DomainModel):
    case_id: str
    baseline_case_hash: Sha256Digest
    candidate_case_hash: Sha256Digest
    reason_codes: tuple[ComparisonReasonCode, ...]


class IntersectionCoverage(DomainModel):
    baseline_expected_samples: int = Field(ge=0)
    candidate_expected_samples: int = Field(ge=0)
    baseline_scored_samples: int = Field(ge=0)
    candidate_scored_samples: int = Field(ge=0)
    baseline_ratio: Score | None = None
    candidate_ratio: Score | None = None
    baseline_minimum_required: Score
    candidate_minimum_required: Score
    sufficient: bool


class VerifiedIntersectionEvidence(DomainModel):
    baseline_total_case_count: int = Field(ge=1)
    candidate_total_case_count: int = Field(ge=1)
    ordered_shared_case_ids: tuple[str, ...]
    verified_cases: tuple[VerifiedCaseIdentity, ...]
    definition_mismatches: tuple[CaseDefinitionMismatch, ...]
    baseline_only_case_ids: tuple[str, ...]
    candidate_only_case_ids: tuple[str, ...]
    evaluator_unavailable_case_ids: tuple[str, ...]
    incomplete_scoring_case_ids: tuple[str, ...]
    baseline_expected_repeats: int = Field(ge=1)
    candidate_expected_repeats: int = Field(ge=1)
    selected_scoring_case_ids: tuple[str, ...]
    selected_total_case_weight: float = Field(ge=0.0)
    weighting_semantic: Literal[
        "weighted_macro_over_selected_verified_cases_v1"
    ] = "weighted_macro_over_selected_verified_cases_v1"
    coverage: IntersectionCoverage


class CaseComparison(DomainModel):
    case_id: str
    category: str
    tags: tuple[str, ...]
    baseline_status: CaseEvidenceStatus
    candidate_status: CaseEvidenceStatus
    baseline_score: Score | None = None
    candidate_score: Score | None = None
    baseline_scored_repeats: int = Field(ge=0)
    candidate_scored_repeats: int = Field(ge=0)
    baseline_expected_repeats: int = Field(ge=1)
    candidate_expected_repeats: int = Field(ge=1)
    delta: float | None = None
    direction: CaseDirection


class EvaluatorResolutionEvidence(DomainModel):
    run_role: Literal["baseline", "candidate"]
    case_id: str
    evaluator_name: str
    evaluator_version: str | None = None
    configuration_hash: Sha256Digest
    status: Literal["available", "unavailable"]
    reason_code: ComparisonReasonCode | None = None


class ComparisonResult(DomainModel):
    schema_version: Literal[1] = 1
    comparison_policy_version: SemanticVersion = "1.3.0"
    comparison_fingerprint: Sha256Digest
    generated_at: datetime
    evaluation_mode: Literal["current_in_memory"] = "current_in_memory"
    intent: ComparisonIntent
    baseline: RunComparisonReference
    candidate: RunComparisonReference
    model_identity: ModelIdentityAssessment
    quality_comparability: ComparabilityAssessment
    performance_comparability: ComparabilityAssessment
    performance_analysis: PerformanceAnalysis | None = None
    refusal_compliance_analysis: RefusalComplianceAnalysis | None = None
    evidence: tuple[FieldEvidence, ...]
    evaluator_resolution: tuple[EvaluatorResolutionEvidence, ...]
    evaluator_provenance: tuple[EvaluatorProvenance, ...]
    coverage: CoverageComparison
    case_population_mode: CasePopulationMode
    baseline_source_run_score: SourceRunScore | None = None
    candidate_source_run_score: SourceRunScore | None = None
    verified_intersection: VerifiedIntersectionEvidence | None = None
    full_suite_score_comparison: ScoreComparison | None = None
    matched_case_score_comparison: ScoreComparison | None = None
    verified_intersection_score_comparison: ScoreComparison | None = None
    categories: tuple[BreakdownComparison, ...] = ()
    tags: tuple[BreakdownComparison, ...] = ()
    cases: tuple[CaseComparison, ...] = ()
