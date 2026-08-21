"""Typed, versioned values produced by trustworthy run comparison."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from elarabench.models import DomainModel, Score, SemanticVersion, Sha256Digest


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


class ComparisonReasonCode(StrEnum):
    BENCHMARK_CONTENT_MISMATCH = "benchmark_content_mismatch"
    SUITE_IDENTITY_CONFLICT = "suite_identity_conflict"
    CASE_SET_DIFFERENCE = "case_set_difference"
    CASE_DEFINITION_MISMATCH = "case_definition_mismatch"
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
    comparison_policy_version: SemanticVersion = "1.0.0"
    comparison_fingerprint: Sha256Digest
    generated_at: datetime
    evaluation_mode: Literal["current_in_memory"] = "current_in_memory"
    intent: ComparisonIntent
    baseline: RunComparisonReference
    candidate: RunComparisonReference
    model_identity: ModelIdentityAssessment
    quality_comparability: ComparabilityAssessment
    performance_comparability: ComparabilityAssessment
    evidence: tuple[FieldEvidence, ...]
    evaluator_resolution: tuple[EvaluatorResolutionEvidence, ...]
    evaluator_provenance: tuple[EvaluatorProvenance, ...]
    coverage: CoverageComparison
    case_population_mode: CasePopulationMode
    full_suite_score_comparison: ScoreComparison | None = None
    matched_case_score_comparison: ScoreComparison | None = None
    categories: tuple[BreakdownComparison, ...] = ()
    tags: tuple[BreakdownComparison, ...] = ()
    cases: tuple[CaseComparison, ...] = ()
