"""Synchronous, sequential, interruption-safe benchmark orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from elarabench.action_compliance import (
    ActionComplianceEvidenceError,
    expectation_from_action_specification,
    validate_action_compliance_result,
)
from elarabench.benchmark import (
    LoadedBenchmarkSuite,
    create_benchmark_snapshot,
    validate_benchmark_snapshot,
)
from elarabench.environment import discover_environment, discover_framework_metadata
from elarabench.evaluators.registry import evaluate
from elarabench.evidence import validate_physical_run_evidence
from elarabench.hashing import hash_evaluation_specification, hash_generation_request
from elarabench.legacy_v2 import LegacyV2RunManifest
from elarabench.models import (
    AggregationSummary,
    AttemptOutcome,
    AttemptRecord,
    BenchmarkCase,
    BenchmarkSnapshot,
    ChatRole,
    EnvironmentMetadata,
    EvaluationContext,
    FrameworkMetadata,
    GenerationError,
    GenerationErrorKind,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderMetadata,
    RequestPlanEntry,
    RunConfiguration,
    RunEventType,
    RunLifecycle,
    RunLifecycleStatus,
    RunManifest,
    SampleIdentity,
    SeedControlMetadata,
    ThinkingControlKind,
    ThinkingControlMetadata,
    ThinkingPolicy,
)
from elarabench.providers.base import ModelProvider, ProviderConfigurationError
from elarabench.run_identity import compute_run_fingerprint, generate_run_id
from elarabench.scoring import (
    RunIntegrityError,
    open_run_path,
    regenerate_summary,
    validate_stored_run,
)
from elarabench.storage import ArtifactStore, ArtifactStoreError, RunArtifactStore

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RunResult:
    """Completed physical execution and its derived summary."""

    path: Path
    manifest: RunManifest
    summary: AggregationSummary


class RunnerError(RuntimeError):
    """A run cannot be started or resumed safely."""


class RunInterrupted(RunnerError):
    """A handled interruption left a resumable run on disk."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"run interrupted: {path}")
        self.path = path


def _validate_thinking_policy(
    policy: ThinkingPolicy,
    support: ThinkingControlKind,
) -> None:
    if policy is ThinkingPolicy.PROVIDER_DEFAULT:
        return
    if support is ThinkingControlKind.BOOLEAN:
        return
    if (
        support is ThinkingControlKind.NONE
        and policy is ThinkingPolicy.DISABLED
    ):
        return
    if support is ThinkingControlKind.NONE:
        raise ProviderConfigurationError(
            "selected model does not advertise thinking capability; "
            "thinking cannot be enabled"
        )
    if support is ThinkingControlKind.UNKNOWN:
        raise ProviderConfigurationError(
            f"provider cannot verify explicit thinking={policy.value!r} control; "
            "use provider_default only if provider/model defaults are intentional"
        )
    raise ProviderConfigurationError(
        f"provider exposes level-valued thinking control, which cannot safely represent "
        f"thinking={policy.value!r}; use provider_default or choose a boolean-controllable "
        "model"
    )


def _thinking_control_metadata(
    policy: ThinkingPolicy,
    control_kind: ThinkingControlKind,
    model: ModelIdentity,
) -> ThinkingControlMetadata:
    advertised = any(
        capability.strip().lower() == "thinking" for capability in model.capabilities
    )
    if control_kind in {ThinkingControlKind.BOOLEAN, ThinkingControlKind.LEVELS}:
        advertised_value: bool | None = True
    elif control_kind is ThinkingControlKind.NONE:
        advertised_value = False
    else:
        advertised_value = True if advertised else None
    return ThinkingControlMetadata(
        requested_policy=policy,
        model_advertises_thinking=advertised_value,
        control_kind=control_kind,
        explicit_control_planned=(
            control_kind is ThinkingControlKind.BOOLEAN
            and policy is not ThinkingPolicy.PROVIDER_DEFAULT
        ),
    )


class Runner:
    """Execute a benchmark through only the narrow ModelProvider protocol."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        runs_dir: str | Path = "runs",
        clock: Clock = _utc_now,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        environment: EnvironmentMetadata | None = None,
        framework: FrameworkMetadata | None = None,
    ) -> None:
        self._provider = provider
        self._artifact_store = ArtifactStore(runs_dir)
        self._clock = clock
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._environment = environment
        self._framework = framework

    def run(
        self,
        loaded: LoadedBenchmarkSuite,
        configuration: RunConfiguration,
        *,
        run_id: str | None = None,
    ) -> RunResult:
        """Create and execute a new physical run."""
        store: RunArtifactStore | None = None
        invocation_id = f"run-{uuid4().hex}"
        try:
            snapshot = create_benchmark_snapshot(
                loaded,
                minimum_scored_coverage=configuration.minimum_scored_coverage,
            )
            validate_benchmark_snapshot(snapshot)
            provider_metadata, model, seed_control, thinking_control = self._preflight(
                configuration, snapshot
            )
            requests = self._resolve_requests(snapshot, configuration)
            request_plan = tuple(
                RequestPlanEntry(identity=identity, request_hash=hash_generation_request(request))
                for identity, request in requests
            )
            framework = self._framework or discover_framework_metadata()
            environment = self._environment or discover_environment()
            fingerprint = compute_run_fingerprint(
                snapshot=snapshot,
                configuration=configuration,
                provider=provider_metadata,
                model=model,
                seed_control=seed_control,
                thinking_control=thinking_control,
                framework=framework,
                request_plan=request_plan,
            )
            created_at = self._clock()
            physical_id = run_id or generate_run_id(fingerprint, created_at)
            store = self._artifact_store.create_run(physical_id)
            lifecycle = RunLifecycle(
                status=RunLifecycleStatus.INITIALIZING,
                created_at=created_at,
                updated_at=created_at,
            )
            manifest = RunManifest(
                run_id=physical_id,
                run_fingerprint=fingerprint,
                framework=framework,
                suite_id=snapshot.suite.id,
                suite_version=snapshot.suite.version,
                suite_hash=snapshot.benchmark_content_hash,
                benchmark_snapshot_hash=snapshot.snapshot_hash,
                configuration=configuration,
                provider=provider_metadata,
                model=model,
                seed_control=seed_control,
                thinking_control=thinking_control,
                environment=environment,
                request_plan=request_plan,
                lifecycle=lifecycle,
            )
            store.write_manifest(manifest)
            store.write_benchmark(snapshot)
            self._event(store, invocation_id, RunEventType.RUN_CREATED)
            started = self._clock()
            manifest = store.update_manifest_lifecycle(
                lifecycle.model_copy(
                    update={
                        "status": RunLifecycleStatus.RUNNING,
                        "started_at": started,
                        "updated_at": started,
                    }
                )
            )
            self._event(store, invocation_id, RunEventType.RUN_STARTED)
            return self._execute(
                store,
                manifest,
                snapshot,
                requests,
                invocation_id=invocation_id,
            )
        except KeyboardInterrupt as error:
            if store is not None:
                self._interrupt(store, invocation_id)
                raise RunInterrupted(store.path) from error
            raise
        except Exception:
            if store is not None:
                self._fail_run(store, invocation_id)
            raise
        finally:
            self._provider.close()

    def resume(self, path: str | Path) -> RunResult:
        """Continue an incomplete schema-v3 run after strict identity verification."""
        store = open_run_path(path)
        invocation_id = f"resume-{uuid4().hex}"
        execution_started = False
        try:
            manifest, snapshot = validate_stored_run(store, validate_samples=False)
            if isinstance(manifest, LegacyV2RunManifest):
                raise RunnerError(
                    "result schema v2 predates explicit Thinking-policy identity; "
                    "it may be scored or summarized but cannot be resumed under schema v3. "
                    "Start a new run instead."
                )
            if not isinstance(snapshot, BenchmarkSnapshot):
                raise RunnerError("schema-v3 manifest has an incompatible benchmark snapshot")
            repaired_event_tail = store.repair_event_log_tail()
            removed = store.cleanup_temporary_files()
            requests = self._resolve_requests(snapshot, manifest.configuration)
            expected_plan = tuple(
                RequestPlanEntry(identity=identity, request_hash=hash_generation_request(request))
                for identity, request in requests
            )
            if expected_plan != manifest.request_plan:
                raise RunnerError("resolved request identity changed; resume rejected")
            self._validate_existing_samples(store, requests, snapshot)
            validate_physical_run_evidence(store, manifest)
            provider_metadata, model, seed_control, thinking_control = self._preflight(
                manifest.configuration, snapshot
            )
            if provider_metadata != manifest.provider:
                raise RunnerError("provider or adapter identity changed; resume rejected")
            if model != manifest.model:
                raise RunnerError("model identity changed; resume rejected")
            if seed_control != manifest.seed_control:
                raise RunnerError("seed capability/application identity changed; resume rejected")
            if thinking_control != manifest.thinking_control:
                raise RunnerError(
                    "thinking capability/control interpretation changed; resume rejected"
                )
            framework = self._framework or discover_framework_metadata()
            if framework != manifest.framework:
                raise RunnerError("ElaraBench source identity changed; resume rejected")
            fingerprint = compute_run_fingerprint(
                snapshot=snapshot,
                configuration=manifest.configuration,
                provider=provider_metadata,
                model=model,
                seed_control=seed_control,
                thinking_control=thinking_control,
                framework=framework,
                request_plan=expected_plan,
            )
            if fingerprint != manifest.run_fingerprint:
                raise RunnerError("run fingerprint incompatibility; resume rejected")
            now = self._clock()
            manifest = store.update_manifest_lifecycle(
                manifest.lifecycle.model_copy(
                    update={
                        "status": RunLifecycleStatus.RUNNING,
                        "updated_at": now,
                        "completed_at": None,
                        "resume_count": manifest.lifecycle.resume_count + 1,
                    }
                )
            )
            execution_started = True
            self._event(
                store,
                invocation_id,
                RunEventType.RUN_RESUMED,
                data={
                    "removed_temporary_files": list(removed),
                    "repaired_incomplete_event_tail": repaired_event_tail,
                },
            )
            return self._execute(
                store,
                manifest,
                snapshot,
                requests,
                invocation_id=invocation_id,
            )
        except KeyboardInterrupt as error:
            if execution_started:
                self._interrupt(store, invocation_id)
            raise RunInterrupted(store.path) from error
        except Exception:
            if execution_started:
                self._fail_run(store, invocation_id)
            raise
        finally:
            self._provider.close()

    def _preflight(
        self,
        configuration: RunConfiguration,
        snapshot: BenchmarkSnapshot,
    ) -> tuple[
        ProviderMetadata,
        ModelIdentity,
        SeedControlMetadata,
        ThinkingControlMetadata,
    ]:
        model = self._provider.describe()
        capabilities = self._provider.capabilities()
        if configuration.provider != model.provider:
            raise ProviderConfigurationError(
                f"configured provider {configuration.provider!r} does not match "
                f"provider identity {model.provider!r}"
            )
        if configuration.model != model.model:
            raise ProviderConfigurationError(
                f"configured model {configuration.model!r} does not match discovered "
                f"model {model.model!r}"
            )
        requested_seed = configuration.seed is not None or any(
            case.seed is not None for case in snapshot.suite.cases
        )
        if requested_seed and not capabilities.seed:
            raise ProviderConfigurationError("provider does not support requested seed control")
        _validate_thinking_policy(
            configuration.thinking,
            capabilities.thinking_control,
        )
        if any(
            case.response_format is not None for case in snapshot.suite.cases
        ) and not capabilities.structured_output:
            raise ProviderConfigurationError(
                "provider does not support requested structured output"
            )
        if any(
            message.role is ChatRole.TOOL
            for case in snapshot.suite.cases
            for message in case.messages
        ) and not capabilities.tools:
            raise ProviderConfigurationError("provider does not support requested tool messages")
        metadata = ProviderMetadata(
            type=configuration.provider,
            adapter_version=self._provider.adapter_version,
            endpoint=self._provider.endpoint_metadata(),
            capabilities=capabilities,
        )
        seed_control = SeedControlMetadata(
            requested=requested_seed,
            supported=capabilities.seed,
            applied=requested_seed and capabilities.seed,
        )
        thinking_control = _thinking_control_metadata(
            configuration.thinking,
            capabilities.thinking_control,
            model,
        )
        return metadata, model, seed_control, thinking_control

    def _resolve_requests(
        self,
        snapshot: BenchmarkSnapshot,
        configuration: RunConfiguration,
    ) -> tuple[tuple[SampleIdentity, GenerationRequest], ...]:
        resolved: list[tuple[SampleIdentity, GenerationRequest]] = []
        for case in snapshot.suite.cases:
            for repeat_index in range(configuration.repeats):
                identity = SampleIdentity(case_id=case.id, repeat_index=repeat_index)
                request = GenerationRequest(
                    messages=case.messages,
                    parameters=configuration.generation_parameters,
                    thinking=configuration.thinking,
                    seed=case.seed if case.seed is not None else configuration.seed,
                    timeout_seconds=configuration.timeout_seconds,
                    response_format=case.response_format,
                )
                resolved.append((identity, request))
        return tuple(resolved)

    def _execute(
        self,
        store: RunArtifactStore,
        manifest: RunManifest,
        snapshot: BenchmarkSnapshot,
        requests: tuple[tuple[SampleIdentity, GenerationRequest], ...],
        *,
        invocation_id: str,
    ) -> RunResult:
        cases = {case.id: case for case in snapshot.suite.cases}
        for identity, request in requests:
            self._ensure_request(store, identity, request, invocation_id)
            if not store.response_exists(identity):
                recovered = self._recover_terminal_attempt(store, identity, manifest)
                if recovered is not None:
                    store.write_response(identity, recovered)
                    self._event(
                        store, invocation_id, RunEventType.RESPONSE_STORED, sample=identity
                    )
                else:
                    response = self._generate(
                        store,
                        identity,
                        request,
                        manifest,
                        invocation_id=invocation_id,
                    )
                    store.write_response(identity, response)
                    self._event(
                        store, invocation_id, RunEventType.RESPONSE_STORED, sample=identity
                    )
            stored_response = store.read_response(identity)
            if stored_response.error is not None and (
                stored_response.error.kind
                in {GenerationErrorKind.CONFIGURATION, GenerationErrorKind.INTERNAL}
                or stored_response.error.http_status in {401, 403}
            ):
                raise RunnerError(
                    f"fatal provider failure for {identity}: "
                    f"{stored_response.error.message}"
                )
            self._ensure_evaluation(
                store,
                identity,
                cases[identity.case_id],
                invocation_id,
            )

        summary = regenerate_summary(
            store,
            manifest=manifest,
            snapshot=snapshot,
            invocation_id=invocation_id,
            clock=self._clock,
        )
        has_provider_errors = any(
            store.read_response(identity).error is not None for identity, _ in requests
        )
        completed_at = self._clock()
        status = (
            RunLifecycleStatus.COMPLETED_WITH_ERRORS
            if has_provider_errors
            else RunLifecycleStatus.COMPLETED
        )
        final_manifest = store.update_manifest_lifecycle(
            manifest.lifecycle.model_copy(
                update={
                    "status": status,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
        )
        self._event(
            store,
            invocation_id,
            RunEventType.RUN_COMPLETED,
            data={
                "status": status.value,
                "coverage": summary.coverage.ratio,
                "coverage_sufficient": summary.coverage.sufficient,
            },
        )
        return RunResult(path=store.path, manifest=final_manifest, summary=summary)

    def _ensure_request(
        self,
        store: RunArtifactStore,
        identity: SampleIdentity,
        expected: GenerationRequest,
        invocation_id: str,
    ) -> None:
        expected_hash = hash_generation_request(expected)
        if store.request_exists(identity):
            actual = store.read_request(identity)
            if hash_generation_request(actual) != expected_hash:
                raise RunnerError(f"stored request mismatch for {identity}")
            return
        store.write_request(identity, expected)
        self._event(
            store,
            invocation_id,
            RunEventType.REQUEST_STORED,
            sample=identity,
            data={"request_hash": expected_hash},
        )

    def _validate_existing_samples(
        self,
        store: RunArtifactStore,
        requests: tuple[tuple[SampleIdentity, GenerationRequest], ...],
        snapshot: BenchmarkSnapshot,
    ) -> None:
        """Read every finalized artifact before mutating lifecycle state on resume."""
        cases = {case.id: case for case in snapshot.suite.cases}
        for identity, expected in requests:
            if store.request_exists(identity):
                actual = store.read_request(identity)
                if hash_generation_request(actual) != hash_generation_request(expected):
                    raise RunnerError(f"stored request mismatch for {identity}")
            response = (
                store.read_response(identity)
                if store.response_exists(identity)
                else None
            )
            store.read_attempts(identity)
            if store.evaluation_exists(identity):
                result = store.read_evaluation(
                    identity,
                    source_result_schema_version=3,
                )
                expectation = expectation_from_action_specification(
                    cases[identity.case_id].evaluation
                )
                if expectation is not None and response is not None:
                    try:
                        validate_action_compliance_result(
                            result,
                            expectation,
                            response=response,
                        )
                    except ActionComplianceEvidenceError as error:
                        raise RunIntegrityError(
                            f"invalid derived evaluation evidence: {error}"
                        ) from error
        if store.summary_exists():
            store.read_summary()

    def _generate(
        self,
        store: RunArtifactStore,
        identity: SampleIdentity,
        request: GenerationRequest,
        manifest: RunManifest,
        *,
        invocation_id: str,
    ) -> GenerationResponse:
        attempts = store.read_attempts(identity)
        attempt_index = len(attempts)
        failed_retries = sum(
            attempt.outcome is AttemptOutcome.FAILED for attempt in attempts
        )
        retry_number = failed_retries
        request_hash = hash_generation_request(request)
        policy = manifest.configuration.retry_policy
        while True:
            started_at = self._clock()
            started = self._monotonic()
            self._event(
                store,
                invocation_id,
                RunEventType.ATTEMPT_STARTED,
                sample=identity,
                attempt_index=attempt_index,
                data={"retry_number": retry_number},
            )
            try:
                response = self._provider.generate(request)
            except KeyboardInterrupt:
                interrupted = GenerationResponse(
                    error=GenerationError(
                        code="interrupted",
                        message="provider execution interrupted by user",
                        kind=GenerationErrorKind.INTERRUPTED,
                    )
                )
                self._persist_attempt(
                    store,
                    identity,
                    request_hash,
                    attempt_index,
                    retry_number,
                    started_at,
                    started,
                    AttemptOutcome.INTERRUPTED,
                    interrupted,
                )
                self._event(
                    store,
                    invocation_id,
                    RunEventType.ATTEMPT_INTERRUPTED,
                    sample=identity,
                    attempt_index=attempt_index,
                )
                raise
            except Exception as error:
                response = GenerationResponse(
                    error=GenerationError(
                        code="provider_exception",
                        message=f"provider raised {type(error).__name__}: {error}",
                        kind=GenerationErrorKind.INTERNAL,
                    )
                )

            outcome = (
                AttemptOutcome.FAILED if response.error is not None else AttemptOutcome.SUCCEEDED
            )
            self._persist_attempt(
                store,
                identity,
                request_hash,
                attempt_index,
                retry_number,
                started_at,
                started,
                outcome,
                response,
            )
            event_type = (
                RunEventType.ATTEMPT_FAILED
                if response.error is not None
                else RunEventType.ATTEMPT_SUCCEEDED
            )
            self._event(
                store,
                invocation_id,
                event_type,
                sample=identity,
                attempt_index=attempt_index,
                data={"error_kind": response.error.kind.value if response.error else None},
            )
            if response.error is None:
                return response
            if not response.error.retryable or retry_number >= policy.max_retries:
                return response
            delay = min(
                policy.maximum_backoff_seconds,
                policy.initial_backoff_seconds
                * (policy.backoff_multiplier**retry_number),
            )
            self._event(
                store,
                invocation_id,
                RunEventType.RETRY_SCHEDULED,
                sample=identity,
                attempt_index=attempt_index,
                data={"delay_seconds": delay, "next_retry_number": retry_number + 1},
            )
            self._sleeper(delay)
            attempt_index += 1
            retry_number += 1

    def _persist_attempt(
        self,
        store: RunArtifactStore,
        identity: SampleIdentity,
        request_hash: str,
        attempt_index: int,
        retry_number: int,
        started_at: datetime,
        started: float,
        outcome: AttemptOutcome,
        response: GenerationResponse,
    ) -> None:
        store.write_attempt(
            AttemptRecord(
                identity=identity,
                attempt_index=attempt_index,
                retry_number=retry_number,
                request_hash=request_hash,
                started_at=started_at,
                completed_at=self._clock(),
                duration_seconds=max(0.0, self._monotonic() - started),
                outcome=outcome,
                response=response,
            )
        )

    def _recover_terminal_attempt(
        self,
        store: RunArtifactStore,
        identity: SampleIdentity,
        manifest: RunManifest,
    ) -> GenerationResponse | None:
        attempts = store.read_attempts(identity)
        if not attempts:
            return None
        last = attempts[-1]
        if last.outcome is AttemptOutcome.SUCCEEDED:
            return last.response
        if last.outcome is AttemptOutcome.INTERRUPTED:
            return None
        error = last.response.error
        if error is None:
            raise RunnerError(f"failed attempt lacks an error for {identity}")
        failed_count = sum(
            attempt.outcome is AttemptOutcome.FAILED for attempt in attempts
        )
        retries_used = max(0, failed_count - 1)
        if not error.retryable or retries_used >= manifest.configuration.retry_policy.max_retries:
            return last.response
        return None

    def _ensure_evaluation(
        self,
        store: RunArtifactStore,
        identity: SampleIdentity,
        case: BenchmarkCase,
        invocation_id: str,
    ) -> None:
        expected_hash = hash_evaluation_specification(case.evaluation)
        if store.evaluation_exists(identity):
            existing = store.read_evaluation(identity, source_result_schema_version=3)
            if existing.configuration_hash == expected_hash:
                return
        response = store.read_response(identity)
        result = evaluate(EvaluationContext(response=response, specification=case.evaluation))
        store.write_evaluation(
            identity,
            result,
            replace=store.evaluation_exists(identity),
        )
        self._event(
            store,
            invocation_id,
            RunEventType.SAMPLE_EVALUATED,
            sample=identity,
            data={"status": result.status.value},
        )

    def _interrupt(self, store: RunArtifactStore, invocation_id: str) -> None:
        try:
            manifest, snapshot = validate_stored_run(store)
            regenerate_summary(
                store,
                manifest=manifest,
                snapshot=snapshot,
                invocation_id=invocation_id,
                clock=self._clock,
            )
            now = self._clock()
            store.update_manifest_lifecycle(
                manifest.lifecycle.model_copy(
                    update={
                        "status": RunLifecycleStatus.INTERRUPTED,
                        "updated_at": now,
                        "completed_at": now,
                    }
                )
            )
            self._event(store, invocation_id, RunEventType.RUN_INTERRUPTED)
        except (ArtifactStoreError, RunIntegrityError, ValueError):
            return

    def _fail_run(self, store: RunArtifactStore, invocation_id: str) -> None:
        try:
            manifest = store.read_manifest()
            now = self._clock()
            store.update_manifest_lifecycle(
                manifest.lifecycle.model_copy(
                    update={
                        "status": RunLifecycleStatus.FAILED,
                        "updated_at": now,
                        "completed_at": now,
                    }
                )
            )
            self._event(store, invocation_id, RunEventType.RUN_FAILED)
        except ArtifactStoreError:
            return

    def _event(
        self,
        store: RunArtifactStore,
        invocation_id: str,
        event_type: RunEventType,
        *,
        sample: SampleIdentity | None = None,
        attempt_index: int | None = None,
        data: dict[str, JsonValue] | None = None,
    ) -> None:
        store.record_event(
            timestamp=self._clock(),
            invocation_id=invocation_id,
            event_type=event_type,
            sample=sample,
            attempt_index=attempt_index,
            data=data,
        )
