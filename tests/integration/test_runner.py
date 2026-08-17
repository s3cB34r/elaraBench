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
    FrameworkMetadata,
    GenerationError,
    GenerationErrorKind,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    RunConfiguration,
    RunLifecycleStatus,
    SampleIdentity,
    SourceIdentity,
)
from elarabench.providers import FakeProvider
from elarabench.runner import RunInterrupted, Runner, RunnerError
from elarabench.scoring import score_run, summarize_run
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
        version="0.0.0",
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
) -> RunConfiguration:
    return RunConfiguration.model_validate(
        {
            "suite_path": str(loaded.suite_dir),
            "provider": "fake",
            "model": "elarabench-fake-v1",
            "repeats": repeats,
            "timeout_seconds": 30,
            "retry_policy": {
                "max_retries": max_retries,
                "initial_backoff_seconds": 0.5,
            },
        }
    )


def resolved_request(case_id: str, loaded: LoadedBenchmarkSuite) -> GenerationRequest:
    case = next(case for case in loaded.suite.cases if case.id == case_id)
    return GenerationRequest(
        messages=case.messages,
        seed=case.seed,
        timeout_seconds=30,
        response_format=case.response_format,
    )


def passing_responses(loaded: LoadedBenchmarkSuite) -> Mapping[str, str]:
    return {
        hash_generation_request(resolved_request(case.id, loaded)): EXPECTED_OUTPUTS[case.id]
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
    assert result.manifest.run_id == "ordered-run"
    assert result.manifest.run_fingerprint not in result.manifest.run_id
    assert result.manifest.environment.cpu == "synthetic CPU"
    store = ArtifactStore(runs_dir).open_run("ordered-run")
    assert store.read_benchmark().suite == loaded.suite
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
