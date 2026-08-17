"""Offline sequential runner, retry, interruption, resume, and rescoring tests."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.hashing import hash_generation_request
from elarabench.models import (
    EnvironmentMetadata,
    EvaluationStatus,
    FrameworkMetadata,
    GenerationError,
    GenerationErrorKind,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
    RunConfiguration,
    RunLifecycleStatus,
    SampleIdentity,
    SourceIdentity,
    ThinkingControlKind,
    ThinkingPolicy,
)
from elarabench.providers import FakeProvider, ProviderConfigurationError
from elarabench.runner import RunInterrupted, Runner, RunnerError
from elarabench.scoring import RunIntegrityError, score_run, summarize_run
from elarabench.storage import ArtifactStore, ArtifactStoreError

SUITE_PATH = Path("tests/fixtures/tiny_suite")
EXPECTED_OUTPUTS = {
    "exact-001": "ELARA",
    "numeric-001": "2.5",
    "choice-001": "B",
    "json-001": '{"answer": 7}',
    "content-001": "alpha and beta",
}


def framework() -> FrameworkMetadata:
    return FrameworkMetadata(
        version="0.2.1",
        source=SourceIdentity(git_commit="a" * 40, git_dirty=False),
    )


def environment() -> EnvironmentMetadata:
    return EnvironmentMetadata(
        python_version="3.12.3",
        python_implementation="CPython",
        operating_system="Linux",
        os_release="test",
        architecture="x86_64",
        cpu="synthetic CPU",
    )


def configuration(
    loaded: LoadedBenchmarkSuite,
    *,
    repeats: int = 1,
    max_retries: int = 2,
    thinking: ThinkingPolicy = ThinkingPolicy.DISABLED,
) -> RunConfiguration:
    return RunConfiguration.model_validate(
        {
            "suite_path": str(loaded.suite_dir),
            "provider": "fake",
            "model": "elarabench-fake-v1",
            "repeats": repeats,
            "thinking": thinking,
            "timeout_seconds": 30,
            "retry_policy": {
                "max_retries": max_retries,
                "initial_backoff_seconds": 0.5,
            },
        }
    )


def resolved_request(
    case_id: str,
    loaded: LoadedBenchmarkSuite,
    *,
    thinking: ThinkingPolicy = ThinkingPolicy.DISABLED,
) -> GenerationRequest:
    case = next(case for case in loaded.suite.cases if case.id == case_id)
    return GenerationRequest(
        messages=case.messages,
        thinking=thinking,
        seed=case.seed,
        timeout_seconds=30,
        response_format=case.response_format,
    )


def passing_responses(
    loaded: LoadedBenchmarkSuite,
    *,
    thinking: ThinkingPolicy = ThinkingPolicy.DISABLED,
) -> Mapping[str, str]:
    return {
        hash_generation_request(
            resolved_request(case.id, loaded, thinking=thinking)
        ): EXPECTED_OUTPUTS[case.id]
        for case in loaded.suite.cases
    }


def runner(
    provider: FakeProvider,
    runs_dir: Path,
    *,
    sleeper: object | None = None,
) -> Runner:
    kwargs: dict[str, object] = {
        "runs_dir": runs_dir,
        "environment": environment(),
        "framework": framework(),
    }
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    return Runner(provider, **kwargs)  # type: ignore[arg-type]


class InspectingProvider(FakeProvider):
    """Assert canonical run evidence exists before each provider call."""

    def __init__(self, runs_dir: Path, responses: Mapping[str, str]) -> None:
        super().__init__(responses=responses)
        self._runs_dir = runs_dir

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        run_paths = list(self._runs_dir.iterdir())
        assert len(run_paths) == 1
        run_path = run_paths[0]
        assert (run_path / "manifest.json").is_file()
        assert (run_path / "benchmark.json").is_file()
        request_hash = hash_generation_request(request)
        stored_requests = list((run_path / "samples").glob("*/repeat-*/request.json"))
        assert any(
            hash_generation_request(GenerationRequest.model_validate_json(path.read_text()))
            == request_hash
            for path in stored_requests
        )
        return super().generate(request)


class CountingProvider(FakeProvider):
    def __init__(self, responses: Mapping[str, str]) -> None:
        super().__init__(responses=responses)
        self.generate_calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.generate_calls += 1
        return super().generate(request)


class NoThinkingControlProvider(FakeProvider):
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(seed=True, structured_output=True)


class PolicyCapabilityProvider(FakeProvider):
    def __init__(
        self,
        support: ThinkingControlKind,
        *,
        responses: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(responses=responses)
        self._support = support
        self.generate_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            seed=True,
            thinking_control=self._support,
            structured_output=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.generate_calls += 1
        return super().generate(request)


def test_successful_run_is_ordered_complete_and_self_contained(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    provider = InspectingProvider(runs_dir, passing_responses(loaded))
    result = runner(provider, runs_dir).run(
        loaded,
        configuration(loaded, repeats=2),
        run_id="ordered-run",
    )

    assert result.manifest.lifecycle.status is RunLifecycleStatus.COMPLETED
    assert result.summary.score == 1.0
    assert result.summary.coverage.ratio == 1.0
    assert result.summary.schema_version == 3
    assert result.summary.source_result_schema_version == 3
    assert result.manifest.run_id == "ordered-run"
    assert result.manifest.schema_version == 3
    assert result.manifest.thinking_control.requested_policy is ThinkingPolicy.DISABLED
    assert result.manifest.thinking_control.control_kind is ThinkingControlKind.BOOLEAN
    assert result.manifest.thinking_control.explicit_control_planned is True
    assert result.manifest.run_fingerprint not in result.manifest.run_id
    assert result.manifest.environment.cpu == "synthetic CPU"
    store = ArtifactStore(runs_dir).open_run("ordered-run")
    assert store.read_benchmark().suite == loaded.suite
    evaluation_data = json.loads(
        (
            result.path
            / "samples"
            / "exact-001"
            / "repeat-000"
            / "evaluation.json"
        ).read_text(encoding="utf-8")
    )
    assert evaluation_data["source_result_schema_version"] == 3
    request_events = [
        event.sample
        for event in store.read_events()
        if event.type.value == "request_stored"
    ]
    assert request_events == [
        SampleIdentity(case_id=case.id, repeat_index=repeat_index)
        for case in loaded.suite.cases
        for repeat_index in range(2)
    ]
    for identity in request_events:
        assert identity is not None
        assert len(store.read_attempts(identity)) == 1
        assert store.response_exists(identity)
        assert store.evaluation_exists(identity)


def test_live_smoke_response_shape_scores_failures_without_losing_coverage(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    outputs = {
        "exact-001": "ELARA",
        "numeric-001": "The decimal result of 10 divided by 4 is **2.5**.",
        "choice-001": "B",
        "json-001": '```json\n{\n  "answer": 7\n}\n```',
        "content-001": "alpha and beta are present",
    }
    responses = {
        hash_generation_request(resolved_request(case.id, loaded)): outputs[case.id]
        for case in loaded.suite.cases
    }

    result = runner(FakeProvider(responses=responses), tmp_path / "runs").run(
        loaded,
        configuration(loaded),
        run_id="live-smoke-shape",
    )
    store = ArtifactStore(tmp_path / "runs").open_run("live-smoke-shape")
    scores = {
        case.id: store.read_evaluation(
            SampleIdentity(case_id=case.id, repeat_index=0),
            source_result_schema_version=3,
        )
        for case in loaded.suite.cases
    }

    assert all(item.status is EvaluationStatus.SCORED for item in scores.values())
    assert {case_id: item.score for case_id, item in scores.items()} == {
        "exact-001": 1.0,
        "numeric-001": 0.0,
        "choice-001": 1.0,
        "json-001": 0.0,
        "content-001": 1.0,
    }
    assert result.summary.coverage.ratio == 1.0
    assert result.summary.coverage.scored_samples == 5
    assert result.summary.score == pytest.approx(0.6)


def test_retryable_error_preserves_attempt_history_and_backoff(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    first_request = resolved_request("exact-001", loaded)
    request_hash = hash_generation_request(first_request)
    retryable = GenerationResponse(
        error=GenerationError(
            code="connection",
            message="temporary connection failure",
            kind=GenerationErrorKind.CONNECTION,
            retryable=True,
        )
    )
    provider = FakeProvider(
        responses=passing_responses(loaded),
        scripts={request_hash: [retryable, GenerationResponse(text="ELARA")]},
    )
    delays: list[float] = []
    result = runner(provider, tmp_path / "runs", sleeper=delays.append).run(
        loaded,
        configuration(loaded),
        run_id="retry-run",
    )

    store = ArtifactStore(tmp_path / "runs").open_run("retry-run")
    attempts = store.read_attempts(SampleIdentity(case_id="exact-001", repeat_index=0))
    assert [attempt.outcome.value for attempt in attempts] == ["failed", "succeeded"]
    assert attempts[0].response.error is not None
    assert delays == [0.5]
    assert result.summary.score == 1.0


def test_non_retryable_error_is_final_and_not_zero_scored(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    request_hash = hash_generation_request(resolved_request("exact-001", loaded))
    provider = FakeProvider(
        responses=passing_responses(loaded),
        errors={
            request_hash: GenerationError(
                code="model_not_found",
                message="missing",
                kind=GenerationErrorKind.PROVIDER,
            )
        },
    )
    result = runner(provider, tmp_path / "runs").run(
        loaded,
        configuration(loaded),
        run_id="error-run",
    )

    assert result.manifest.lifecycle.status is RunLifecycleStatus.COMPLETED_WITH_ERRORS
    assert result.summary.score is None
    assert result.summary.partial_score == 1.0
    assert result.summary.coverage.ratio == 0.8
    assert result.summary.sample_status_counts.error == 1


def test_configuration_failure_is_persisted_and_terminates_run(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    request_hash = hash_generation_request(resolved_request("exact-001", loaded))
    provider = FakeProvider(
        errors={
            request_hash: GenerationError(
                code="unsupported_request",
                message="unsupported setting",
                kind=GenerationErrorKind.CONFIGURATION,
            )
        }
    )
    runs_dir = tmp_path / "runs"

    with pytest.raises(RunnerError, match="fatal provider failure"):
        runner(provider, runs_dir).run(
            loaded,
            configuration(loaded),
            run_id="configuration-failure-run",
        )

    store = ArtifactStore(runs_dir).open_run("configuration-failure-run")
    identity = SampleIdentity(case_id="exact-001", repeat_index=0)
    assert store.read_manifest().lifecycle.status is RunLifecycleStatus.FAILED
    assert store.read_response(identity).error is not None
    assert len(store.read_attempts(identity)) == 1


def test_interrupted_attempt_is_resumable_without_final_response(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    first_hash = hash_generation_request(resolved_request("exact-001", loaded))
    interrupted_provider = FakeProvider(scripts={first_hash: [KeyboardInterrupt()]})
    runs_dir = tmp_path / "runs"
    with pytest.raises(RunInterrupted):
        runner(interrupted_provider, runs_dir).run(
            loaded,
            configuration(loaded),
            run_id="interrupted-run",
        )

    store = ArtifactStore(runs_dir).open_run("interrupted-run")
    identity = SampleIdentity(case_id="exact-001", repeat_index=0)
    assert store.read_manifest().lifecycle.status is RunLifecycleStatus.INTERRUPTED
    assert not store.response_exists(identity)
    assert store.read_attempts(identity)[0].outcome.value == "interrupted"

    resumed = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).resume(
        store.path
    )
    assert resumed.manifest.lifecycle.status is RunLifecycleStatus.COMPLETED
    assert resumed.manifest.lifecycle.resume_count == 1
    assert len(store.read_attempts(identity)) == 2


def test_completed_and_final_error_samples_are_skipped_on_resume(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    first_hash = hash_generation_request(resolved_request("exact-001", loaded))
    initial = FakeProvider(
        responses=passing_responses(loaded),
        errors={
            first_hash: GenerationError(
                code="provider",
                message="final failure",
                kind=GenerationErrorKind.PROVIDER,
            )
        },
    )
    runs_dir = tmp_path / "runs"
    original = runner(initial, runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="completed-run",
    )
    replacement = CountingProvider(passing_responses(loaded))

    resumed = runner(replacement, runs_dir).resume(original.path)

    assert replacement.generate_calls == 0
    assert resumed.manifest.lifecycle.status is RunLifecycleStatus.COMPLETED_WITH_ERRORS


def test_resume_rejects_changed_request_and_corrupt_response_before_transition(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    result = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="integrity-run",
    )
    store = ArtifactStore(runs_dir).open_run("integrity-run")
    original_status = store.read_manifest().lifecycle.status
    request_path = result.path / "samples/exact-001/repeat-000/request.json"
    request_data = json.loads(request_path.read_text())
    request_data["messages"][0]["content"] = "changed"
    request_path.write_text(json.dumps(request_data), encoding="utf-8")

    with pytest.raises(RunnerError, match="stored request mismatch"):
        runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).resume(result.path)
    assert store.read_manifest().lifecycle.status is original_status

    request_path.write_text(
        resolved_request("exact-001", loaded).model_dump_json(), encoding="utf-8"
    )
    response_path = result.path / "samples/exact-001/repeat-000/response.json"
    response_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ArtifactStoreError, match="invalid artifact"):
        runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).resume(result.path)
    assert store.read_manifest().lifecycle.status is original_status


def test_score_and_summarize_work_without_benchmark_source_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_copy = tmp_path / "source-suite"
    shutil.copytree(SUITE_PATH, suite_copy)
    loaded = load_benchmark_suite(suite_copy)
    runs_dir = tmp_path / "runs"
    result = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="offline-score-run",
    )
    suite_copy.rename(tmp_path / "moved-source-suite")
    for evaluation_path in result.path.glob("samples/*/repeat-000/evaluation.json"):
        evaluation_path.unlink()
    scored = score_run(result.path)
    assert scored.score == 1.0

    def forbidden_evaluation(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("summarize must not invoke an evaluator")

    monkeypatch.setattr("elarabench.scoring.evaluate", forbidden_evaluation)
    (result.path / "summary.json").unlink()
    summarized = summarize_run(result.path)
    assert summarized == scored


def test_resume_and_summarize_reject_missing_schema_v3_evaluation_provenance(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    result = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="missing-evaluation-provenance",
    )
    evaluation_path = result.path / "samples/exact-001/repeat-000/evaluation.json"
    evaluation_data = json.loads(evaluation_path.read_text(encoding="utf-8"))
    del evaluation_data["source_result_schema_version"]
    evaluation_path.write_text(json.dumps(evaluation_data), encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="incomplete schema-v3 evaluation"):
        summarize_run(result.path)
    with pytest.raises(ArtifactStoreError, match="incomplete schema-v3 evaluation"):
        runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).resume(
            result.path
        )


def test_hard_kill_state_recovers_terminal_attempt_without_regeneration(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    result = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="hard-kill-run",
    )
    identity = SampleIdentity(case_id="exact-001", repeat_index=0)
    sample_path = result.path / "samples" / identity.case_id / identity.repeat_id
    (sample_path / "response.json").unlink()
    (sample_path / "evaluation.json").unlink()
    (result.path / "summary.json").unlink()
    manifest_path = result.path / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text())
    manifest_data["lifecycle"]["status"] = "running"
    manifest_data["lifecycle"]["completed_at"] = None
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
    provider = CountingProvider(passing_responses(loaded))

    resumed = runner(provider, runs_dir).resume(result.path)

    assert provider.generate_calls == 0
    assert resumed.manifest.lifecycle.status is RunLifecycleStatus.COMPLETED
    assert ArtifactStore(runs_dir).open_run(result.path.name).response_exists(identity)


def test_fingerprint_mismatch_is_rejected_without_lifecycle_mutation(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    result = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="fingerprint-run",
    )
    manifest_path = result.path / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["run_fingerprint"] = "0" * 64
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="fingerprint"):
        runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).resume(result.path)


def test_resume_rejects_changed_model_identity(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    result = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="model-identity-run",
    )
    changed_model = ModelIdentity(
        provider="fake",
        backend="deterministic",
        model="elarabench-fake-v1",
        model_digest="changed-digest",
        backend_version="1.0.0",
    )

    with pytest.raises(RunnerError, match="model identity changed"):
        runner(
            FakeProvider(responses=passing_responses(loaded), identity=changed_model),
            runs_dir,
        ).resume(result.path)


def test_run_fingerprint_excludes_physical_id_and_general_hardware(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    responses = passing_responses(loaded)
    first = runner(FakeProvider(responses=responses), tmp_path / "first").run(
        loaded,
        configuration(loaded),
        run_id="physical-one",
    )
    other_environment = environment().model_copy(
        update={"cpu": "different CPU", "architecture": "aarch64"}
    )
    second = Runner(
        FakeProvider(responses=responses),
        runs_dir=tmp_path / "second",
        environment=other_environment,
        framework=framework(),
    ).run(
        loaded,
        configuration(loaded),
        run_id="physical-two",
    )

    assert first.manifest.run_id != second.manifest.run_id
    assert first.manifest.environment != second.manifest.environment
    assert first.manifest.run_fingerprint == second.manifest.run_fingerprint


def test_thinking_policy_is_persisted_hashed_and_round_trips(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    fingerprints: set[str] = set()
    for policy in ThinkingPolicy:
        responses = passing_responses(loaded, thinking=policy)
        result = runner(
            FakeProvider(responses=responses),
            tmp_path / policy.value,
        ).run(
            loaded,
            configuration(loaded, thinking=policy),
            run_id=f"thinking-{policy.value}",
        )
        store = ArtifactStore(result.path.parent).open_run(result.path.name)
        stored_request = store.read_request(
            SampleIdentity(case_id="exact-001", repeat_index=0)
        )

        assert result.manifest.configuration.thinking is policy
        assert stored_request.thinking is policy
        fingerprints.add(result.manifest.run_fingerprint)

    assert len(fingerprints) == len(ThinkingPolicy)


def test_resume_rejects_thinking_request_mismatch(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    responses = passing_responses(loaded)
    result = runner(FakeProvider(responses=responses), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="thinking-resume-run",
    )
    request_path = result.path / "samples/exact-001/repeat-000/request.json"
    request_data = json.loads(request_path.read_text())
    request_data["thinking"] = ThinkingPolicy.ENABLED.value
    request_path.write_text(json.dumps(request_data), encoding="utf-8")

    with pytest.raises(RunnerError, match="stored request mismatch"):
        runner(FakeProvider(responses=responses), runs_dir).resume(result.path)


def test_explicit_thinking_requires_provider_control_support(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    with pytest.raises(ProviderConfigurationError, match="cannot verify explicit thinking"):
        runner(NoThinkingControlProvider(), tmp_path / "runs").run(
            loaded,
            configuration(loaded, thinking=ThinkingPolicy.ENABLED),
            run_id="unsupported-thinking-run",
        )


def test_provider_default_does_not_require_thinking_control_support(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    policy = ThinkingPolicy.PROVIDER_DEFAULT
    result = runner(
        NoThinkingControlProvider(responses=passing_responses(loaded, thinking=policy)),
        tmp_path / "runs",
    ).run(
        loaded,
        configuration(loaded, thinking=policy),
        run_id="provider-default-thinking-run",
    )

    assert result.manifest.configuration.thinking is policy


def test_non_thinking_model_satisfies_disabled_but_rejects_enabled(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    disabled = PolicyCapabilityProvider(
        ThinkingControlKind.NONE,
        responses=passing_responses(loaded),
    )
    result = runner(disabled, tmp_path / "disabled").run(
        loaded,
        configuration(loaded, thinking=ThinkingPolicy.DISABLED),
        run_id="non-thinking-disabled",
    )

    assert result.manifest.provider.capabilities.thinking_control is (
        ThinkingControlKind.NONE
    )
    assert disabled.generate_calls == len(loaded.suite.cases)

    enabled = PolicyCapabilityProvider(ThinkingControlKind.NONE)
    with pytest.raises(ProviderConfigurationError, match="does not advertise thinking"):
        runner(enabled, tmp_path / "enabled").run(
            loaded,
            configuration(loaded, thinking=ThinkingPolicy.ENABLED),
            run_id="non-thinking-enabled",
        )
    assert enabled.generate_calls == 0


@pytest.mark.parametrize(
    "support",
    [ThinkingControlKind.UNKNOWN, ThinkingControlKind.LEVELS],
)
def test_unverified_thinking_control_allows_only_provider_default(
    tmp_path: Path,
    support: ThinkingControlKind,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    policy = ThinkingPolicy.PROVIDER_DEFAULT
    allowed = PolicyCapabilityProvider(
        support,
        responses=passing_responses(loaded, thinking=policy),
    )
    result = runner(allowed, tmp_path / f"allowed-{support.value}").run(
        loaded,
        configuration(loaded, thinking=policy),
        run_id=f"provider-default-{support.value}",
    )

    assert result.manifest.provider.capabilities.thinking_control is support

    for explicit in (ThinkingPolicy.ENABLED, ThinkingPolicy.DISABLED):
        rejected = PolicyCapabilityProvider(support)
        with pytest.raises(ProviderConfigurationError, match="thinking"):
            runner(rejected, tmp_path / f"rejected-{support.value}-{explicit.value}").run(
                loaded,
                configuration(loaded, thinking=explicit),
                run_id=f"rejected-{support.value}-{explicit.value}",
            )
        assert rejected.generate_calls == 0


def test_resume_rejects_changed_thinking_control_capability(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    original = runner(FakeProvider(responses=passing_responses(loaded)), runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="capability-resume-run",
    )
    changed = PolicyCapabilityProvider(ThinkingControlKind.NONE)

    with pytest.raises(RunnerError, match="provider or adapter identity changed"):
        runner(changed, runs_dir).resume(original.path)
    assert changed.generate_calls == 0


def test_discovered_thinking_control_changes_run_fingerprint(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    responses = passing_responses(loaded)
    boolean_run = runner(
        PolicyCapabilityProvider(
            ThinkingControlKind.BOOLEAN,
            responses=responses,
        ),
        tmp_path / "boolean",
    ).run(loaded, configuration(loaded), run_id="boolean-control")
    non_thinking_run = runner(
        PolicyCapabilityProvider(
            ThinkingControlKind.NONE,
            responses=responses,
        ),
        tmp_path / "non-thinking",
    ).run(loaded, configuration(loaded), run_id="no-control-needed")

    assert boolean_run.manifest.configuration == non_thinking_run.manifest.configuration
    assert boolean_run.manifest.model == non_thinking_run.manifest.model
    assert boolean_run.manifest.run_fingerprint != non_thinking_run.manifest.run_fingerprint
