"""Runner, retry, resume, offline scoring, and comparison invariants for M5.2a."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Never

import pytest

from elarabench.action_compliance import ActionComplianceEvaluationArtifact
from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.comparison import compare_runs
from elarabench.evaluators import evaluate
from elarabench.hashing import hash_generation_request
from elarabench.models import (
    EnvironmentMetadata,
    EvaluationContext,
    EvaluationStatus,
    FrameworkMetadata,
    GenerationError,
    GenerationErrorKind,
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
from elarabench.runner import RunInterrupted, Runner
from elarabench.scoring import RunIntegrityError, score_run, summarize_run
from elarabench.storage import ArtifactStore, RunArtifactStore

SUITE_PATH = Path(__file__).parents[1] / "fixtures" / "action_compliance_suite"
GOLDEN_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-compliance-core-v1.jsonl"
)


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
    loaded: LoadedBenchmarkSuite, *, max_retries: int = 0
) -> RunConfiguration:
    return RunConfiguration(
        suite_path=str(loaded.suite_dir),
        provider="fake",
        model="elarabench-fake-v1",
        generation_parameters=GenerationParameters(temperature=0, max_tokens=192),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=30,
        retry_policy=RetryPolicy(
            max_retries=max_retries,
            initial_backoff_seconds=0,
        ),
    )


def request_for_case(
    loaded: LoadedBenchmarkSuite, case_id: str
) -> GenerationRequest:
    case = next(case for case in loaded.suite.cases if case.id == case_id)
    return GenerationRequest(
        messages=case.messages,
        parameters=GenerationParameters(temperature=0, max_tokens=192),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=30,
        response_format=None,
    )


def correct_outputs() -> dict[str, str]:
    return {
        entry["case_id"]: entry["correct_response"]
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line
        for entry in [json.loads(line)]
    }


def responses(loaded: LoadedBenchmarkSuite) -> Mapping[str, str]:
    outputs = correct_outputs()
    return {
        hash_generation_request(request_for_case(loaded, case.id)): outputs[case.id]
        for case in loaded.suite.cases
    }


def runner(provider: FakeProvider, runs_dir: Path) -> Runner:
    return Runner(
        provider,
        runs_dir=runs_dir,
        environment=environment(),
        framework=framework(),
        sleeper=lambda _: None,
    )


def interrupted_after_first_action_sample(
    loaded: LoadedBenchmarkSuite,
    runs_dir: Path,
    *,
    run_id: str,
) -> RunArtifactStore:
    second_case = loaded.suite.cases[1]
    second_hash = hash_generation_request(request_for_case(loaded, second_case.id))
    provider = FakeProvider(
        responses=responses(loaded),
        scripts={second_hash: [KeyboardInterrupt()]},
    )
    with pytest.raises(RunInterrupted):
        runner(provider, runs_dir).run(
            loaded,
            configuration(loaded),
            run_id=run_id,
        )
    return ArtifactStore(runs_dir).open_run(run_id)


class CountingProvider(FakeProvider):
    def __init__(
        self,
        *,
        responses: Mapping[str, str] | None = None,
        scripts: Mapping[str, list[GenerationResponse | BaseException]] | None = None,
    ) -> None:
        super().__init__(responses=responses, scripts=scripts)
        self.calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        return super().generate(request)


class FailIfInspectedProvider(CountingProvider):
    def describe(self) -> Never:
        raise AssertionError("resume inspected provider before evidence validation")


def file_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def test_one_response_runner_offline_score_resume_and_compare_are_nonmutating(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    provider = CountingProvider(responses=responses(loaded))
    first = runner(provider, runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="action-first",
    )

    assert provider.calls == 4
    assert first.summary.score == 1.0
    assert first.summary.partial_score == 1.0
    assert first.summary.coverage.scored_samples == 4
    assert first.summary.sample_status_counts.scored == 4
    assert first.summary.schema_version == 5
    assert first.summary.action_compliance is not None
    assert first.summary.action_compliance.balanced_action_compliance == 1.0
    store = ArtifactStore(runs_dir).open_run("action-first")
    for case in loaded.suite.cases:
        identity = SampleIdentity(case_id=case.id, repeat_index=0)
        assert len(store.read_attempts(identity)) == 1
        assert store.read_response(identity).text == correct_outputs()[case.id]
        assert (
            store.read_evaluation(identity, source_result_schema_version=3).status
            is EvaluationStatus.SCORED
        )

    multi_identity = SampleIdentity(case_id="action-compliance-002", repeat_index=0)
    multi_artifact = ActionComplianceEvaluationArtifact.model_validate(
        store.read_evaluation(
            multi_identity, source_result_schema_version=3
        ).artifacts
    )
    assert multi_artifact.action_count == 2
    assert len(multi_artifact.simulation.observations) == 2

    response_bytes = {
        case.id: (
            first.path / "samples" / case.id / "repeat-000" / "response.json"
        ).read_bytes()
        for case in loaded.suite.cases
    }
    assert score_run(first.path).score == 1.0
    assert summarize_run(first.path).score == 1.0
    assert response_bytes == {
        case.id: (
            first.path / "samples" / case.id / "repeat-000" / "response.json"
        ).read_bytes()
        for case in loaded.suite.cases
    }

    resume_provider = CountingProvider(responses=responses(loaded))
    resumed = runner(resume_provider, runs_dir).resume(first.path)
    assert resumed.summary.score == 1.0
    assert resumed.summary.sample_status_counts.scored == 4
    assert resume_provider.calls == 0

    second = runner(
        CountingProvider(responses=responses(loaded)), runs_dir
    ).run(loaded, configuration(loaded), run_id="action-second")
    first_before = file_bytes(first.path)
    second_before = file_bytes(second.path)
    comparison = compare_runs(first.path, second.path)
    assert comparison.baseline_source_run_score is not None
    assert comparison.baseline_source_run_score.score == 1.0
    assert comparison.candidate_source_run_score is not None
    assert comparison.candidate_source_run_score.score == 1.0
    assert comparison.full_suite_score_comparison is not None
    assert file_bytes(first.path) == first_before
    assert file_bytes(second.path) == second_before


def test_provider_retry_remains_generation_only_and_preserves_one_final_response(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    first_case = loaded.suite.cases[0]
    first_hash = hash_generation_request(request_for_case(loaded, first_case.id))
    retry_error = GenerationResponse(
        error=GenerationError(
            code="temporary",
            message="temporary provider failure",
            kind=GenerationErrorKind.PROVIDER,
            retryable=True,
        )
    )
    provider = CountingProvider(
        responses=responses(loaded),
        scripts={
            first_hash: [
                retry_error,
                GenerationResponse(text=correct_outputs()[first_case.id]),
            ]
        },
    )
    result = runner(provider, tmp_path / "runs").run(
        loaded,
        configuration(loaded, max_retries=1),
        run_id="action-retry",
    )
    store = ArtifactStore(tmp_path / "runs").open_run("action-retry")
    identity = SampleIdentity(case_id=first_case.id, repeat_index=0)

    assert provider.calls == 5
    assert len(store.read_attempts(identity)) == 2
    assert store.read_response(identity).text == correct_outputs()[first_case.id]
    assert result.summary.score == 1.0
    assert result.summary.sample_status_counts.scored == 4


def test_noncanonical_response_values_persist_as_protocol_invalid_evidence(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    scripted = dict(responses(loaded))

    def proposal(argument_fragment: str) -> str:
        return (
            '{"type":"action","actions":[{"tool":"mark_ready","arguments":'
            f'{{"target":{argument_fragment}}}'
            "}]}"
        )

    affected = loaded.suite.cases[:2]
    scripted[hash_generation_request(request_for_case(loaded, affected[0].id))] = (
        proposal("1e400")
    )
    scripted[hash_generation_request(request_for_case(loaded, affected[1].id))] = (
        proposal(r'"\ud800"')
    )
    run = runner(FakeProvider(responses=scripted), tmp_path / "runs").run(
        loaded,
        configuration(loaded),
        run_id="noncanonical-proposals",
    )
    store = ArtifactStore(tmp_path / "runs").open_run(run.manifest.run_id)
    original_evaluations = {}
    for case in affected:
        identity = SampleIdentity(case_id=case.id, repeat_index=0)
        evaluation = store.read_evaluation(
            identity,
            source_result_schema_version=3,
        )
        artifact = ActionComplianceEvaluationArtifact.model_validate(
            evaluation.artifacts
        )
        assert artifact.outcome.value == "protocol_invalid"
        assert artifact.protocol_failure_reason == "noncanonical_value"
        original_evaluations[identity] = evaluation

    assert summarize_run(run.path).sample_status_counts.scored == 4
    assert score_run(run.path).sample_status_counts.scored == 4
    for identity, original in original_evaluations.items():
        assert (
            store.read_evaluation(identity, source_result_schema_version=3)
            == original
        )


def test_provider_failure_is_error_not_action_outcome(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    first_case = loaded.suite.cases[0]
    first_hash = hash_generation_request(request_for_case(loaded, first_case.id))
    provider = FakeProvider(
        responses=responses(loaded),
        errors={
            first_hash: GenerationError(
                code="provider_unavailable",
                message="provider unavailable",
                kind=GenerationErrorKind.PROVIDER,
            )
        },
    )
    result = runner(provider, tmp_path / "runs").run(
        loaded,
        configuration(loaded),
        run_id="action-provider-error",
    )
    evaluation = ArtifactStore(tmp_path / "runs").open_run(
        "action-provider-error"
    ).read_evaluation(
        SampleIdentity(case_id=first_case.id, repeat_index=0),
        source_result_schema_version=3,
    )

    assert evaluation.status is EvaluationStatus.ERROR
    assert evaluation.artifacts == {}
    assert result.summary.coverage.scored_samples == 3
    assert result.summary.sample_status_counts.scored == 3
    assert result.summary.sample_status_counts.error == 1
    assert result.summary.score is None
    rescored = score_run(result.path)
    repaired = ArtifactStore(tmp_path / "runs").open_run(
        "action-provider-error"
    ).read_evaluation(
        SampleIdentity(case_id=first_case.id, repeat_index=0),
        source_result_schema_version=3,
    )
    assert rescored.sample_status_counts.error == 1
    assert repaired.status is EvaluationStatus.ERROR
    assert repaired.artifacts == {}


def test_tampered_action_artifact_cannot_reclassify_canonical_provider_error(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    first_case = loaded.suite.cases[0]
    first_hash = hash_generation_request(request_for_case(loaded, first_case.id))
    runs_dir = tmp_path / "runs"
    run = runner(
        FakeProvider(
            responses=responses(loaded),
            errors={
                first_hash: GenerationError(
                    code="provider_unavailable",
                    message="provider unavailable",
                    kind=GenerationErrorKind.PROVIDER,
                )
            },
        ),
        runs_dir,
    ).run(
        loaded,
        configuration(loaded),
        run_id="tampered-provider-error",
    )
    identity = SampleIdentity(case_id=first_case.id, repeat_index=0)
    store = ArtifactStore(runs_dir).open_run(run.manifest.run_id)
    tampered = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=correct_outputs()[first_case.id]),
            specification=first_case.evaluation,
        )
    )
    assert tampered.status is EvaluationStatus.SCORED
    store.write_evaluation(identity, tampered, replace=True)

    with pytest.raises(RunIntegrityError, match="provider failure"):
        summarize_run(run.path)

    resume_provider = CountingProvider(responses=responses(loaded))
    with pytest.raises(RunIntegrityError, match="provider failure"):
        runner(resume_provider, runs_dir).resume(run.path)

    assert resume_provider.calls == 0


def test_trusted_spec_changes_run_fingerprint(
    tmp_path: Path,
) -> None:
    original = load_benchmark_suite(SUITE_PATH)
    copied_suite = tmp_path / "changed-suite"
    shutil.copytree(SUITE_PATH, copied_suite)
    cases_path = copied_suite / "cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text().splitlines()]
    cases[0]["evaluation"]["config"]["max_plan_length"] = 3
    cases_path.write_text(
        "\n".join(json.dumps(case, separators=(",", ":")) for case in cases) + "\n",
        encoding="utf-8",
    )
    changed = load_benchmark_suite(copied_suite)
    runs_dir = tmp_path / "runs"
    original_run = runner(
        FakeProvider(responses=responses(original)), runs_dir
    ).run(original, configuration(original), run_id="identity-original")
    changed_run = runner(
        FakeProvider(responses=responses(changed)), runs_dir
    ).run(changed, configuration(changed), run_id="identity-changed")

    assert original_run.manifest.run_fingerprint != changed_run.manifest.run_fingerprint


def test_canonical_schema_diagnostics_survive_summarize_and_offline_rescore(
    tmp_path: Path,
) -> None:
    copied_suite = tmp_path / "diagnostic-suite"
    shutil.copytree(SUITE_PATH, copied_suite)
    cases_path = copied_suite / "cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text().splitlines()]
    target_schema = cases[0]["evaluation"]["config"]["tools"]["mark_ready"][
        "arguments_schema"
    ]["properties"]["target"]
    target_schema.clear()
    target_schema.update({"pattern": "^z", "minLength": 3, "type": "string"})
    cases_path.write_text(
        "\n".join(json.dumps(case, separators=(",", ":")) for case in cases) + "\n",
        encoding="utf-8",
    )
    loaded = load_benchmark_suite(copied_suite)
    first_case = loaded.suite.cases[0]
    scripted = dict(responses(loaded))
    scripted[hash_generation_request(request_for_case(loaded, first_case.id))] = json.dumps(
        {
            "type": "action",
            "actions": [
                {"tool": "mark_ready", "arguments": {"target": "a"}}
            ],
        },
        separators=(",", ":"),
    )
    run = runner(FakeProvider(responses=scripted), tmp_path / "runs").run(
        loaded,
        configuration(loaded),
        run_id="canonical-diagnostics",
    )
    identity = SampleIdentity(case_id=first_case.id, repeat_index=0)
    store = ArtifactStore(tmp_path / "runs").open_run(run.manifest.run_id)
    original = store.read_evaluation(identity, source_result_schema_version=3)
    artifact = ActionComplianceEvaluationArtifact.model_validate(original.artifacts)

    assert [
        item.validator_keyword
        for item in artifact.plan_validation.validation_diagnostics
    ] == ["minLength", "pattern"]
    assert summarize_run(run.path).sample_status_counts.scored == 4
    assert score_run(run.path).sample_status_counts.scored == 4
    assert store.read_evaluation(identity, source_result_schema_version=3) == original


@pytest.mark.parametrize(
    "corruption",
    [
        "artifact_semantic",
        "evaluator_version",
        "outcome_semantic",
        "outcome",
        "coercible_boolean",
        "invalid_result",
        "score_mismatch",
    ],
)
def test_score_repairs_corrupt_derived_action_evidence_but_summarize_rejects_it(
    tmp_path: Path,
    corruption: str,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    provider = CountingProvider(responses=responses(loaded))
    run = runner(provider, tmp_path / "runs").run(
        loaded,
        configuration(loaded),
        run_id=f"repair-{corruption.replace('_', '-')}",
    )
    store = ArtifactStore(tmp_path / "runs").open_run(run.manifest.run_id)
    identity = SampleIdentity(case_id="action-compliance-001", repeat_index=0)
    original_evaluation = store.read_evaluation(
        identity,
        source_result_schema_version=3,
    )
    sample_path = run.path / "samples" / identity.case_id / identity.repeat_id
    canonical_paths = (
        sample_path / "request.json",
        sample_path / "response.json",
        *sorted((sample_path / "attempts").glob("*.json")),
    )
    canonical_bytes = {
        str(path.relative_to(sample_path)): path.read_bytes()
        for path in canonical_paths
    }

    evaluation_path = (
        run.path
        / "samples"
        / "action-compliance-001"
        / "repeat-000"
        / "evaluation.json"
    )
    payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if corruption == "evaluator_version":
        payload["evaluator_version"] = "9.0.0"
    elif corruption == "artifact_semantic":
        payload["artifacts"]["artifact_semantic"] = "future_artifact_v9"
    elif corruption == "outcome_semantic":
        payload["artifacts"]["outcome_semantic"] = "future_outcomes_v9"
    elif corruption == "outcome":
        payload["artifacts"]["outcome"] = "authorized_unsuccessful_plan"
    elif corruption == "coercible_boolean":
        payload["artifacts"]["simulation"]["observations"][0][
            "preconditions_satisfied"
        ] = 1
    elif corruption == "invalid_result":
        payload["status"] = "invalid"
        payload["score"] = None
        payload["passed"] = None
        payload["artifacts"] = {}
    else:
        payload["status"] = "scored"
        payload["score"] = 0.0
        payload["passed"] = False
    evaluation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="invalid derived evaluation evidence"):
        summarize_run(run.path)

    repaired_summary = score_run(run.path)
    repaired = store.read_evaluation(identity, source_result_schema_version=3)

    assert repaired_summary.score == 1.0
    assert repaired == original_evaluation
    assert provider.calls == 4
    assert canonical_bytes == {
        str(path.relative_to(sample_path)): path.read_bytes() for path in canonical_paths
    }


def test_historical_m5_2a_evidence_requires_explicit_offline_score_upgrade(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    provider = CountingProvider(responses=responses(loaded))
    run = runner(provider, runs_dir).run(
        loaded,
        configuration(loaded),
        run_id="historical-m5-2a-upgrade",
    )
    store = ArtifactStore(runs_dir).open_run(run.manifest.run_id)
    for case in loaded.suite.cases:
        identity = SampleIdentity(case_id=case.id, repeat_index=0)
        evaluation_path = (
            run.path
            / "samples"
            / identity.case_id
            / identity.repeat_id
            / "evaluation.json"
        )
        payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
        payload["status"] = "pending_review"
        payload["score"] = None
        payload["passed"] = None
        payload["evaluator_version"] = "1.0.0"
        payload["artifacts"]["evaluator_version"] = "1.0.0"
        evaluation_path.write_text(json.dumps(payload), encoding="utf-8")

    canonical_paths = [run.path / "manifest.json", run.path / "benchmark.json"]
    for case in loaded.suite.cases:
        sample_path = run.path / "samples" / case.id / "repeat-000"
        canonical_paths.extend(
            [
                sample_path / "request.json",
                sample_path / "response.json",
                *sorted((sample_path / "attempts").glob("*.json")),
            ]
        )
    canonical_bytes = {
        str(path.relative_to(run.path)): path.read_bytes() for path in canonical_paths
    }

    with pytest.raises(RunIntegrityError, match="unsupported action_compliance evaluator"):
        summarize_run(run.path)

    resume_provider = CountingProvider(responses=responses(loaded))
    with pytest.raises(RunIntegrityError, match="unsupported action_compliance evaluator"):
        runner(resume_provider, runs_dir).resume(run.path)
    assert resume_provider.calls == 0

    upgraded = score_run(run.path)
    assert upgraded.schema_version == 5
    assert upgraded.score == 1.0
    assert upgraded.action_compliance is not None
    assert upgraded.action_compliance.evaluator_version == "1.1.0"
    assert provider.calls == 4
    for case in loaded.suite.cases:
        evaluation = store.read_evaluation(
            SampleIdentity(case_id=case.id, repeat_index=0),
            source_result_schema_version=3,
        )
        assert evaluation.status is EvaluationStatus.SCORED
        assert evaluation.evaluator_version == "1.1.0"
    assert canonical_bytes == {
        str(path.relative_to(run.path)): path.read_bytes() for path in canonical_paths
    }
    assert score_run(run.path) == upgraded


@pytest.mark.parametrize("corruption", ["outcome", "invalid_result"])
def test_resume_rejects_corrupt_action_artifact_without_provider_generation(
    tmp_path: Path,
    corruption: str,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    run = runner(
        FakeProvider(responses=responses(loaded)), tmp_path / "runs"
    ).run(
        loaded,
        configuration(loaded),
        run_id=f"resume-corrupt-action-{corruption.replace('_', '-')}",
    )
    evaluation_path = (
        run.path
        / "samples"
        / "action-compliance-001"
        / "repeat-000"
        / "evaluation.json"
    )
    payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if corruption == "outcome":
        payload["artifacts"]["outcome"] = "authorized_unsuccessful_plan"
    else:
        payload["status"] = "invalid"
        payload["score"] = None
        payload["passed"] = None
        payload["artifacts"] = {}
    evaluation_path.write_text(json.dumps(payload), encoding="utf-8")
    provider = CountingProvider(responses=responses(loaded))

    with pytest.raises(RunIntegrityError, match="invalid derived evaluation evidence"):
        runner(provider, tmp_path / "runs").resume(run.path)

    assert provider.calls == 0


def test_resume_rejects_corrupt_reused_action_evidence_before_missing_work(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    store = interrupted_after_first_action_sample(
        loaded,
        runs_dir,
        run_id="resume-preflight-corrupt",
    )
    first_identity = SampleIdentity(case_id=loaded.suite.cases[0].id, repeat_index=0)
    missing_identity = SampleIdentity(case_id=loaded.suite.cases[1].id, repeat_index=0)
    evaluation_path = (
        store.path
        / "samples"
        / first_identity.case_id
        / first_identity.repeat_id
        / "evaluation.json"
    )
    payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    payload["artifacts"]["simulation"]["observations"][0][
        "preconditions_satisfied"
    ] = 1
    evaluation_path.write_text(json.dumps(payload), encoding="utf-8")
    original_missing_attempts = store.read_attempts(missing_identity)
    provider = FailIfInspectedProvider(responses=responses(loaded))

    with pytest.raises(RunIntegrityError, match="changed during typed validation"):
        runner(provider, runs_dir).resume(store.path)

    assert provider.calls == 0
    assert store.read_attempts(missing_identity) == original_missing_attempts
    assert not store.response_exists(missing_identity)
    assert not store.evaluation_exists(missing_identity)
    assert store.read_manifest().lifecycle.resume_count == 0


def test_resume_validates_reused_action_evidence_then_generates_missing_work(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    runs_dir = tmp_path / "runs"
    store = interrupted_after_first_action_sample(
        loaded,
        runs_dir,
        run_id="resume-preflight-valid",
    )
    first_identity = SampleIdentity(case_id=loaded.suite.cases[0].id, repeat_index=0)
    missing_identity = SampleIdentity(case_id=loaded.suite.cases[1].id, repeat_index=0)
    first_evaluation = store.read_evaluation(
        first_identity,
        source_result_schema_version=3,
    )
    provider = CountingProvider(responses=responses(loaded))

    resumed = runner(provider, runs_dir).resume(store.path)

    assert provider.calls == 3
    assert resumed.manifest.lifecycle.resume_count == 1
    assert (
        store.read_evaluation(first_identity, source_result_schema_version=3)
        == first_evaluation
    )
    assert store.response_exists(missing_identity)
    assert store.evaluation_exists(missing_identity)
    assert len(store.read_attempts(missing_identity)) == 2


def test_summarize_wraps_out_of_range_observation_index_as_integrity_error(
    tmp_path: Path,
) -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    run = runner(
        FakeProvider(responses=responses(loaded)), tmp_path / "runs"
    ).run(loaded, configuration(loaded), run_id="invalid-observation-index")
    evaluation_path = (
        run.path
        / "samples"
        / "action-compliance-002"
        / "repeat-000"
        / "evaluation.json"
    )
    payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    payload["artifacts"]["simulation"]["observations"][1]["action_index"] = 2
    evaluation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="invalid derived evaluation evidence"):
        summarize_run(run.path)
