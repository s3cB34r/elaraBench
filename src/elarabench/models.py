"""Validated domain models for the deterministic ElaraBench core."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
SemanticVersion = Annotated[
    str,
    Field(
        pattern=(
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
        )
    ),
]
Score = Annotated[float, Field(ge=0.0, le=1.0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DomainModel(BaseModel):
    """Base configuration shared by immutable public domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ChatRole(StrEnum):
    """Provider-neutral chat roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(DomainModel):
    """One ordered message in a generation request."""

    role: ChatRole
    content: str


class GenerationParameters(DomainModel):
    """Common provider-neutral generation controls."""

    temperature: Annotated[float, Field(ge=0.0)] | None = None
    top_p: Annotated[float, Field(gt=0.0, le=1.0)] | None = None
    top_k: Annotated[int, Field(gt=0)] | None = None
    max_tokens: Annotated[int, Field(gt=0)] | None = None
    stop: tuple[str, ...] = ()


class ResponseFormatType(StrEnum):
    """Supported provider-neutral response constraints."""

    TEXT = "text"
    JSON = "json"
    JSON_SCHEMA = "json_schema"


class ResponseFormatConstraint(DomainModel):
    """An optional response shape requested from a capable provider."""

    type: ResponseFormatType
    json_schema: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_schema_presence(self) -> Self:
        if self.type is ResponseFormatType.JSON_SCHEMA and self.json_schema is None:
            raise ValueError("json_schema is required when response format type is json_schema")
        if self.type is not ResponseFormatType.JSON_SCHEMA and self.json_schema is not None:
            raise ValueError("json_schema is only valid for the json_schema response format")
        return self


class GenerationRequest(DomainModel):
    """A complete provider-neutral model request."""

    messages: Annotated[tuple[ChatMessage, ...], Field(min_length=1)]
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    seed: int | None = None
    timeout_seconds: Annotated[float, Field(gt=0.0)] = 120.0
    response_format: ResponseFormatConstraint | None = None


class UsageInformation(DomainModel):
    """Normalized token usage when reported by a provider."""

    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None


class TimingMetadata(DomainModel):
    """Normalized provider timing data."""

    latency_seconds: Annotated[float, Field(ge=0.0)] | None = None
    time_to_first_token_seconds: Annotated[float, Field(ge=0.0)] | None = None
    provider_total_seconds: Annotated[float, Field(ge=0.0)] | None = None
    provider_load_seconds: Annotated[float, Field(ge=0.0)] | None = None
    provider_prompt_eval_seconds: Annotated[float, Field(ge=0.0)] | None = None
    provider_eval_seconds: Annotated[float, Field(ge=0.0)] | None = None


class GenerationErrorKind(StrEnum):
    """Normalized generation failure categories."""

    CONFIGURATION = "configuration"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    HTTP = "http"
    PROVIDER = "provider"
    MALFORMED_RESPONSE = "malformed_response"
    INTERRUPTED = "interrupted"
    INTERNAL = "internal"


class GenerationError(DomainModel):
    """Provider-neutral generation failure information."""

    code: str
    message: str
    kind: GenerationErrorKind = GenerationErrorKind.PROVIDER
    retryable: bool = False
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    provider_code: str | None = None
    provider_message: str | None = None


class GenerationResponse(DomainModel):
    """Normalized output plus optional untouched JSON provider metadata."""

    text: str = ""
    finish_reason: str | None = None
    usage: UsageInformation | None = None
    timing: TimingMetadata | None = None
    error: GenerationError | None = None
    raw_request_payload: JsonValue | None = None
    raw_payload: JsonValue | None = None


class ModelIdentity(DomainModel):
    """Stable identity information for a model and serving backend."""

    provider: Identifier
    backend: Identifier
    model: str
    model_digest: str | None = None
    quantization: str | None = None
    backend_version: str | None = None
    tokenizer: str | None = None
    chat_template: str | None = None
    format: str | None = None
    family: str | None = None
    parameter_size: str | None = None
    capabilities: tuple[str, ...] = ()
    parameters_hash: Sha256Digest | None = None
    template_hash: Sha256Digest | None = None


class ProviderCapabilities(DomainModel):
    """Features a provider adapter can support without silent translation."""

    seed: bool = False
    structured_output: bool = False
    tools: bool = False
    usage_metrics: bool = False


class EvaluationSpecification(DomainModel):
    """The explicit evaluator type, configuration, and optional components."""

    type: Identifier
    config: dict[str, JsonValue] = Field(default_factory=dict)
    components: tuple[EvaluationComponent, ...] = ()


class EvaluationComponent(DomainModel):
    """A weighted child specification for composite evaluation."""

    specification: EvaluationSpecification
    weight: Annotated[float, Field(gt=0.0)] = 1.0


EvaluationSpecification.model_rebuild()


class EvaluationStatus(StrEnum):
    """Outcome classes kept separate during scoring and aggregation."""

    SCORED = "scored"
    INVALID = "invalid"
    ERROR = "error"
    PENDING_REVIEW = "pending_review"


class EvaluationResult(DomainModel):
    """A deterministic evaluator result with complete evaluator provenance."""

    status: EvaluationStatus
    score: Score | None = None
    passed: bool | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    explanation: str
    evaluator_name: str
    evaluator_version: str
    configuration_hash: Sha256Digest
    artifacts: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_score_status(self) -> Self:
        if self.status is EvaluationStatus.SCORED and self.score is None:
            raise ValueError("scored evaluation results require a score")
        if self.status is not EvaluationStatus.SCORED and self.score is not None:
            raise ValueError("only scored evaluation results may contain a score")
        return self


class SuiteDefaults(DomainModel):
    """Suite-level defaults resolved by future run orchestration."""

    repeats: Annotated[int, Field(gt=0)] = 1
    timeout_seconds: Annotated[float, Field(gt=0.0)] = 120.0


class AggregationConfiguration(DomainModel):
    """M1 score aggregation policy."""

    method: Literal["weighted_macro"] = "weighted_macro"
    unscored_policy: Literal["exclude"] = "exclude"
    minimum_scored_coverage: Score = 0.95


class BenchmarkCase(DomainModel):
    """One fully specified, provider-neutral benchmark task."""

    id: Identifier
    category: Identifier
    tags: tuple[Identifier, ...] = ()
    difficulty: str | None = None
    messages: Annotated[tuple[ChatMessage, ...], Field(min_length=1)]
    response_format: ResponseFormatConstraint | None = None
    evaluation: EvaluationSpecification
    weight: Annotated[float, Field(gt=0.0)] = 1.0
    seed: int | None = None
    provenance: str | None = None
    license: str | None = None
    fixtures: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_tags(self) -> Self:
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("case tags must be unique")
        return self


class CaseFileReference(DomainModel):
    """Manifest pointer to the ordered case JSONL file."""

    path: str = "cases.jsonl"


class BenchmarkSuiteManifest(DomainModel):
    """Validated contents of suite.yaml before cases are loaded."""

    schema_version: Literal[1]
    id: Identifier
    version: SemanticVersion
    title: str
    description: str | None = None
    license: str | None = None
    provenance: str | None = None
    tags: tuple[Identifier, ...] = ()
    case_source: CaseFileReference = Field(default_factory=CaseFileReference, alias="cases")
    defaults: SuiteDefaults = Field(default_factory=SuiteDefaults)
    aggregation: AggregationConfiguration = Field(default_factory=AggregationConfiguration)


class BenchmarkSuite(DomainModel):
    """A complete validated suite with cases in source order."""

    schema_version: Literal[1]
    id: Identifier
    version: SemanticVersion
    title: str
    description: str | None = None
    license: str | None = None
    provenance: str | None = None
    tags: tuple[Identifier, ...] = ()
    cases: tuple[BenchmarkCase, ...]
    defaults: SuiteDefaults = Field(default_factory=SuiteDefaults)
    aggregation: AggregationConfiguration = Field(default_factory=AggregationConfiguration)


class SampleIdentity(DomainModel):
    """Stable identity of one repeated benchmark sample."""

    case_id: Identifier
    repeat_index: Annotated[int, Field(ge=0)]

    @property
    def repeat_id(self) -> str:
        return f"repeat-{self.repeat_index:03d}"


class RunConfiguration(DomainModel):
    """Resolved provider-neutral execution configuration."""

    suite_path: str
    provider: Identifier = "fake"
    model: str = "elarabench-fake-v1"
    endpoint: str | None = None
    repeats: Annotated[int, Field(gt=0)] = 1
    generation_parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    seed: int | None = None
    timeout_seconds: Annotated[float, Field(gt=0.0)] = 120.0
    retry_policy: RetryPolicy = Field(default_factory=lambda: RetryPolicy())
    concurrency: Literal[1] = 1
    minimum_scored_coverage: Score = 0.95


class RetryPolicy(DomainModel):
    """Conservative deterministic retry policy."""

    max_retries: Annotated[int, Field(ge=0)] = 2
    initial_backoff_seconds: Annotated[float, Field(ge=0.0)] = 0.5
    backoff_multiplier: Annotated[float, Field(ge=1.0)] = 2.0
    maximum_backoff_seconds: Annotated[float, Field(ge=0.0)] = 5.0


RunConfiguration.model_rebuild()


class EndpointMetadata(DomainModel):
    """Credential-free endpoint identity."""

    scheme: str
    host: str
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    path: str
    is_local: bool


class ProviderMetadata(DomainModel):
    """Provider adapter identity and declared capabilities."""

    type: Identifier
    adapter_version: SemanticVersion
    endpoint: EndpointMetadata
    capabilities: ProviderCapabilities


class SourceIdentity(DomainModel):
    """ElaraBench source revision identity."""

    git_commit: str | None = None
    git_dirty: bool | None = None
    source_state_hash: Sha256Digest | None = None


class FrameworkMetadata(DomainModel):
    """Framework version and source identity."""

    version: str
    source: SourceIdentity = Field(default_factory=SourceIdentity)


class GPUInfo(DomainModel):
    """Best-effort non-personal GPU runtime identity."""

    name: str
    driver_version: str | None = None


class EnvironmentMetadata(DomainModel):
    """Execution environment metadata kept separate from run fingerprint identity."""

    python_version: str
    python_implementation: str
    operating_system: str
    os_release: str
    architecture: str
    cpu: str | None = None
    gpus: tuple[GPUInfo, ...] = ()
    gpu_driver: str | None = None
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()


class SeedControlMetadata(DomainModel):
    """Honest seed request/support/application status."""

    requested: bool
    supported: bool
    applied: bool
    deterministic_output_guaranteed: Literal[False] = False


class RunLifecycleStatus(StrEnum):
    """Guarded run lifecycle states."""

    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class RunLifecycle(DomainModel):
    """The only mutable section of a run manifest."""

    status: RunLifecycleStatus
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None
    resume_count: Annotated[int, Field(ge=0)] = 0


class RequestPlanEntry(DomainModel):
    """Expected sample request identity in stable execution order."""

    identity: SampleIdentity
    request_hash: Sha256Digest


class RunManifest(DomainModel):
    """Result schema v2 identity/configuration plus guarded lifecycle state."""

    schema_version: Literal[2] = 2
    run_id: Identifier
    run_fingerprint: Sha256Digest
    framework: FrameworkMetadata
    suite_id: Identifier
    suite_version: SemanticVersion
    suite_hash: Sha256Digest
    benchmark_snapshot_hash: Sha256Digest
    configuration: RunConfiguration
    provider: ProviderMetadata
    model: ModelIdentity
    seed_control: SeedControlMetadata
    environment: EnvironmentMetadata
    request_plan: tuple[RequestPlanEntry, ...]
    lifecycle: RunLifecycle


class SnapshotFixture(DomainModel):
    """Immutable fixture bytes embedded as base64 for later offline evaluation."""

    path: str
    content_hash: Sha256Digest
    content_base64: str


class BenchmarkSnapshot(DomainModel):
    """Complete self-contained benchmark and evaluation snapshot."""

    schema_version: Literal[1] = 1
    suite: BenchmarkSuite
    fixtures: tuple[SnapshotFixture, ...] = ()
    minimum_scored_coverage: Score = 0.95
    benchmark_content_hash: Sha256Digest
    snapshot_hash: Sha256Digest


class AttemptOutcome(StrEnum):
    """Durable provider attempt outcome."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AttemptRecord(DomainModel):
    """One immutable provider invocation, including failed retry history."""

    identity: SampleIdentity
    attempt_index: Annotated[int, Field(ge=0)]
    retry_number: Annotated[int, Field(ge=0)]
    request_hash: Sha256Digest
    started_at: datetime
    completed_at: datetime
    duration_seconds: Annotated[float, Field(ge=0.0)]
    outcome: AttemptOutcome
    response: GenerationResponse


class RunEventType(StrEnum):
    """Small fixed vocabulary for diagnostic lifecycle events."""

    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    REQUEST_STORED = "request_stored"
    ATTEMPT_STARTED = "attempt_started"
    ATTEMPT_FAILED = "attempt_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    ATTEMPT_SUCCEEDED = "attempt_succeeded"
    ATTEMPT_INTERRUPTED = "attempt_interrupted"
    RESPONSE_STORED = "response_stored"
    SAMPLE_EVALUATED = "sample_evaluated"
    SUMMARY_WRITTEN = "summary_written"
    RUN_INTERRUPTED = "run_interrupted"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    SCORING_STARTED = "scoring_started"
    SCORING_COMPLETED = "scoring_completed"
    SUMMARIZATION_COMPLETED = "summarization_completed"


class RunEvent(DomainModel):
    """Validated append-only diagnostic event."""

    schema_version: Literal[1] = 1
    sequence: Annotated[int, Field(ge=0)]
    timestamp: datetime
    run_id: Identifier
    invocation_id: Identifier
    type: RunEventType
    sample: SampleIdentity | None = None
    attempt_index: Annotated[int, Field(ge=0)] | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)


class EvaluationContext(DomainModel):
    """Stored response evidence supplied to an evaluator."""

    response: GenerationResponse
    specification: EvaluationSpecification


class AggregationSample(DomainModel):
    """One evaluation result plus stable benchmark grouping metadata."""

    identity: SampleIdentity
    category: Identifier
    tags: tuple[Identifier, ...] = ()
    case_weight: Annotated[float, Field(gt=0.0)] = 1.0
    result: EvaluationResult


class ScoreStatistics(DomainModel):
    """Descriptive statistics without manufactured confidence intervals."""

    count: Annotated[int, Field(ge=0)]
    mean: Score | None = None
    minimum: Score | None = None
    maximum: Score | None = None
    population_variance: Annotated[float, Field(ge=0.0)] | None = None
    population_standard_deviation: Annotated[float, Field(ge=0.0)] | None = None


class StatusCounts(DomainModel):
    """Counts of samples in each evaluation state."""

    scored: Annotated[int, Field(ge=0)] = 0
    invalid: Annotated[int, Field(ge=0)] = 0
    error: Annotated[int, Field(ge=0)] = 0
    pending_review: Annotated[int, Field(ge=0)] = 0


class CaseSummary(DomainModel):
    """Repeat-level statistics and status counts for one case."""

    case_id: Identifier
    category: Identifier
    tags: tuple[Identifier, ...]
    weight: float
    score: Score | None
    repeat_statistics: ScoreStatistics
    status_counts: StatusCounts


class BreakdownSummary(DomainModel):
    """Weighted macro score for a category or tag."""

    case_count: Annotated[int, Field(ge=0)]
    scored_case_count: Annotated[int, Field(ge=0)]
    score: Score | None


class CoverageSummary(DomainModel):
    """Expected-sample scored coverage and headline validity."""

    expected_samples: Annotated[int, Field(gt=0)]
    scored_samples: Annotated[int, Field(ge=0)]
    ratio: Score
    minimum_required: Score
    sufficient: bool


class AggregationSummary(DomainModel):
    """Derived deterministic score summary."""

    schema_version: Literal[2] = 2
    score: Score | None
    partial_score: Score | None
    coverage: CoverageSummary
    case_count: Annotated[int, Field(ge=0)]
    scored_case_count: Annotated[int, Field(ge=0)]
    sample_status_counts: StatusCounts
    cases: tuple[CaseSummary, ...]
    categories: dict[str, BreakdownSummary]
    tags: dict[str, BreakdownSummary]
