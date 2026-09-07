"""Provider-neutral one-turn Runner integration for Action Recovery."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from elarabench.action_recovery import ActionRecoveryEvaluationArtifact
from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.hashing import hash_generation_request
from elarabench.models import (
    ChatRole,
    EnvironmentMetadata,
    EvaluationStatus,
    FrameworkMetadata,
    GenerationParameters,
    GenerationRequest,
    GenerationResponse,
    RetryPolicy,
    RunConfiguration,
    SampleIdentity,
    SourceIdentity,
    ThinkingPolicy,
)
from elarabench.providers import FakeProvider
from elarabench.runner import Runner
from elarabench.scoring import RunIntegrityError, score_run, summarize_run
from elarabench.storage import ArtifactStore

PROJECT_ROOT = Path(__file__).parents[2]
SUITE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "action_recovery_suite"
GOLDEN_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-recovery-foundation-test-v1.jsonl"
)


class RecordingProvider(FakeProvider):
    def __init__(self, *, responses: Mapping[str, str]) -> None:
        super().__init__(responses=responses)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        return super().generate(request)


def make_runner(provider: FakeProvider, runs_dir: Path) -> Runner:
    return Runner(
        provider,
        runs_dir=runs_dir,
        framework=FrameworkMetadata(
            version="0.2.1",
            source=SourceIdentity(git_commit="a" * 40, git_dirty=False),
        ),
        environment=EnvironmentMetadata(
            python_version="3.12.3",
            python_implementation="CPython",
            operating_system="Linux",
            os_release="test",
            architecture="x86_64",
            cpu="synthetic CPU",
        ),
        sleeper=lambda _: None,
    )


def request_for_case(loaded: LoadedBenchmarkSuite, case_id: str) -> GenerationRequest:
    selected = next(item for item in loaded.suite.cases if item.id == case_id)
    return GenerationRequest(
        messages=selected.messages,
        parameters=GenerationParameters(temperature=0, max_tokens=192),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=30,
        response_format=None,
    )


def configured_responses(loaded: LoadedBenchmarkSuite) -> dict[str, str]:
    outputs = {
        entry["case_id"]: entry["correct_response"]
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        for entry in [json.loads(line)]
    }
    return {
        hash_generation_request(request_for_case(loaded, item.id)): outputs[item.id]
        for item in loaded.suite.cases
    }


def test_runner_keeps_action_recovery_one_turn_and_provider_neutral(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    provider = RecordingProvider(responses=configured_responses(loaded))
    runs_dir = tmp_path / "runs"
    runner = make_runner(provider, runs_dir)
    configuration = RunConfiguration(
        suite_path=str(loaded.suite_dir),
        provider="fake",
        model="elarabench-fake-v1",
        generation_parameters=GenerationParameters(temperature=0, max_tokens=192),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_retries=0, initial_backoff_seconds=0),
    )

    run = runner.run(loaded, configuration, run_id="action-recovery-foundation")

    assert len(provider.requests) == 7
    assert provider.capabilities().tools is False
    assert all(len(request.messages) == 3 for request in provider.requests)
    assert all(
        tuple(message.role for message in request.messages)
        == (ChatRole.SYSTEM, ChatRole.USER, ChatRole.USER)
        for request in provider.requests
    )
    assert all(request.response_format is None for request in provider.requests)
    assert run.summary.schema_version == 6
    assert run.summary.score == 1.0
    assert run.summary.partial_score == 1.0
    assert run.summary.coverage.scored_samples == 7
    assert run.summary.sample_status_counts.scored == 7
    assert run.summary.action_recovery is not None
    assert run.summary.action_recovery.balanced_action_recovery == 1.0

    store = ArtifactStore(runs_dir).open_run("action-recovery-foundation")
    for item in loaded.suite.cases:
        identity = SampleIdentity(case_id=item.id, repeat_index=0)
        assert store.read_request(identity) == request_for_case(loaded, item.id)
        assert len(store.read_attempts(identity)) == 1
        response = store.read_response(identity)
        evaluation = store.read_evaluation(identity, source_result_schema_version=3)
        artifact = ActionRecoveryEvaluationArtifact.model_validate(evaluation.artifacts)
        assert response.error is None
        assert evaluation.status is EvaluationStatus.SCORED
        assert evaluation.score == 1.0
        assert evaluation.passed is True
        assert artifact.evaluator_version == "1.1.0"


def test_historical_m5_3a_requires_explicit_offline_upgrade(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    provider = RecordingProvider(responses=configured_responses(loaded))
    runs_dir = tmp_path / "runs"
    run = make_runner(provider, runs_dir).run(
        loaded,
        RunConfiguration(
            suite_path=str(loaded.suite_dir),
            provider="fake",
            model="elarabench-fake-v1",
            generation_parameters=GenerationParameters(temperature=0, max_tokens=192),
            thinking=ThinkingPolicy.DISABLED,
            seed=42,
            timeout_seconds=30,
            retry_policy=RetryPolicy(max_retries=0, initial_backoff_seconds=0),
        ),
        run_id="historical-m5-3a-upgrade",
    )
    for item in loaded.suite.cases:
        path = run.path / "samples" / item.id / "repeat-000" / "evaluation.json"
        payload = json.loads(path.read_text())
        payload["status"] = "pending_review"
        payload["score"] = None
        payload["passed"] = None
        payload["evaluator_version"] = "1.0.0"
        payload["artifacts"]["evaluator_version"] = "1.0.0"
        path.write_text(json.dumps(payload))

    canonical_paths = [run.path / "manifest.json", run.path / "benchmark.json"]
    for item in loaded.suite.cases:
        sample = run.path / "samples" / item.id / "repeat-000"
        canonical_paths.extend(
            [
                sample / "request.json",
                sample / "response.json",
                *sorted((sample / "attempts").glob("*.json")),
            ]
        )
    before = {str(path.relative_to(run.path)): path.read_bytes() for path in canonical_paths}

    with pytest.raises(RunIntegrityError, match="unsupported action_recovery evaluator"):
        summarize_run(run.path)
    resume_provider = RecordingProvider(responses=configured_responses(loaded))
    with pytest.raises(RunIntegrityError, match="unsupported action_recovery evaluator"):
        make_runner(resume_provider, runs_dir).resume(run.path)
    assert resume_provider.requests == []

    upgraded = score_run(run.path)
    assert upgraded.schema_version == 6
    assert upgraded.score == 1.0
    assert upgraded.action_recovery is not None
    assert upgraded.action_recovery.evaluator_version == "1.1.0"
    assert len(provider.requests) == 7
    store = ArtifactStore(runs_dir).open_run(run.manifest.run_id)
    for item in loaded.suite.cases:
        evaluation = store.read_evaluation(
            SampleIdentity(case_id=item.id, repeat_index=0),
            source_result_schema_version=3,
        )
        assert evaluation.status is EvaluationStatus.SCORED
        assert evaluation.evaluator_version == "1.1.0"
    assert before == {
        str(path.relative_to(run.path)): path.read_bytes() for path in canonical_paths
    }


def test_resume_rejects_wrong_recovery_score_before_provider_contact(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    run = make_runner(RecordingProvider(responses=configured_responses(loaded)), runs_dir).run(
        loaded,
        RunConfiguration(
            suite_path=str(loaded.suite_dir),
            provider="fake",
            model="elarabench-fake-v1",
            generation_parameters=GenerationParameters(temperature=0, max_tokens=192),
            thinking=ThinkingPolicy.DISABLED,
            seed=42,
            timeout_seconds=30,
            retry_policy=RetryPolicy(max_retries=0, initial_backoff_seconds=0),
        ),
        run_id="wrong-recovery-score",
    )
    item = loaded.suite.cases[0]
    path = run.path / "samples" / item.id / "repeat-000" / "evaluation.json"
    payload = json.loads(path.read_text())
    payload["score"] = 0.0
    payload["passed"] = False
    path.write_text(json.dumps(payload))
    resume_provider = RecordingProvider(responses=configured_responses(loaded))
    with pytest.raises(RunIntegrityError, match="score/pass"):
        make_runner(resume_provider, runs_dir).resume(run.path)
    assert resume_provider.requests == []
