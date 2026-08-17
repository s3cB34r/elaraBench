"""Filesystem artifact store safety and round-trip tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from elarabench.aggregation import aggregate
from elarabench.models import (
    AggregationSample,
    EndpointMetadata,
    EnvironmentMetadata,
    EvaluationResult,
    EvaluationStatus,
    FrameworkMetadata,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
    ProviderMetadata,
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
from elarabench.storage import ArtifactExistsError, ArtifactStore, ArtifactStoreError


def manifest(run_id: str) -> RunManifest:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return RunManifest(
        run_id=run_id,
        run_fingerprint="f" * 64,
        framework=FrameworkMetadata(version="0.0.0"),
        suite_id="synthetic.tiny",
        suite_version="1.0.0",
        suite_hash="a" * 64,
        benchmark_snapshot_hash="c" * 64,
        configuration=RunConfiguration(suite_path="tests/fixtures/tiny_suite"),
        provider=ProviderMetadata(
            type="fake",
            adapter_version="1.0.0",
            endpoint=EndpointMetadata(
                scheme="fake", host="local", path="/", is_local=True
            ),
            capabilities=ProviderCapabilities(seed=True),
        ),
        model=ModelIdentity(provider="fake", backend="fake", model="elarabench-fake-v1"),
        seed_control=SeedControlMetadata(requested=False, supported=True, applied=False),
        thinking_control=ThinkingControlMetadata(
            requested_policy=ThinkingPolicy.DISABLED,
            model_advertises_thinking=True,
            control_kind=ThinkingControlKind.BOOLEAN,
            explicit_control_planned=True,
        ),
        environment=EnvironmentMetadata(
            python_version="3.12",
            python_implementation="CPython",
            operating_system="Linux",
            os_release="test",
            architecture="x86_64",
        ),
        request_plan=(),
        lifecycle=RunLifecycle(
            status=RunLifecycleStatus.INITIALIZING,
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


def request() -> GenerationRequest:
    return GenerationRequest.model_validate(
        {"messages": [{"role": "user", "content": "Return café"}], "seed": 3}
    )


def evaluation(score: float) -> EvaluationResult:
    return EvaluationResult(
        status=EvaluationStatus.SCORED,
        score=score,
        passed=score == 1.0,
        explanation="synthetic",
        evaluator_name="exact_match",
        evaluator_version="1.0.0",
        configuration_hash="b" * 64,
        source_result_schema_version=3,
    )


def test_canonical_and_derived_artifacts_round_trip(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path / "runs").run("run-001")
    identity = SampleIdentity(case_id="case-1", repeat_index=0)
    response = GenerationResponse(
        text="café",
        finish_reason="stop",
        raw_payload={"provider_value": "préservé"},
    )
    result = evaluation(1.0)
    summary = aggregate(
        [
            AggregationSample(
                identity=identity,
                category="reasoning",
                result=result,
            )
        ]
    )

    run.write_manifest(manifest("run-001"))
    run.write_request(identity, request())
    run.write_response(identity, response)
    run.write_evaluation(identity, result)
    run.write_summary(summary)

    assert run.read_manifest() == manifest("run-001")
    assert run.read_request(identity) == request()
    assert run.read_response(identity) == response
    assert run.read_evaluation(identity, source_result_schema_version=3) == result
    assert run.read_summary() == summary
    assert "café" in (run.path / "samples/case-1/repeat-000/request.json").read_text()


def test_canonical_artifacts_cannot_silently_overwrite(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path).run("run")
    identity = SampleIdentity(case_id="case", repeat_index=0)
    original = request()
    run.write_request(identity, original)

    with pytest.raises(ArtifactExistsError):
        run.write_request(
            identity,
            GenerationRequest.model_validate(
                {"messages": [{"role": "user", "content": "different"}]}
            ),
        )

    assert run.read_request(identity) == original


def test_manifest_and_response_are_canonical(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path).run("run")
    identity = SampleIdentity(case_id="case", repeat_index=0)
    run.write_manifest(manifest("run"))
    run.write_response(identity, GenerationResponse(text="first"))

    with pytest.raises(ArtifactExistsError):
        run.write_manifest(manifest("run"))
    with pytest.raises(ArtifactExistsError):
        run.write_response(identity, GenerationResponse(text="second"))


def test_derived_evaluation_requires_explicit_replace(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path).run("run")
    identity = SampleIdentity(case_id="case", repeat_index=0)
    run.write_evaluation(identity, evaluation(0.0))

    with pytest.raises(ArtifactExistsError):
        run.write_evaluation(identity, evaluation(1.0))
    run.write_evaluation(identity, evaluation(1.0), replace=True)

    assert run.read_evaluation(identity, source_result_schema_version=3).score == 1.0


@pytest.mark.parametrize(
    ("provenance", "expected_message"),
    [
        ("absent", "missing required source_result_schema_version"),
        (None, "evaluation source schema mismatch"),
        (4, "evaluation source schema mismatch"),
    ],
)
def test_schema_v3_evaluation_requires_valid_explicit_provenance(
    tmp_path: Path,
    provenance: object,
    expected_message: str,
) -> None:
    run = ArtifactStore(tmp_path).run("run")
    identity = SampleIdentity(case_id="case", repeat_index=0)
    run.write_evaluation(identity, evaluation(1.0))
    path = run.path / "samples/case/repeat-000/evaluation.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if provenance == "absent":
        del value["source_result_schema_version"]
    else:
        value["source_result_schema_version"] = provenance
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ArtifactStoreError, match=expected_message):
        run.read_evaluation(identity, source_result_schema_version=3)


def test_summary_is_atomically_regenerable(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path).run("run")
    identity = SampleIdentity(case_id="case", repeat_index=0)
    first = aggregate(
        [AggregationSample(identity=identity, category="reasoning", result=evaluation(0.0))]
    )
    second = aggregate(
        [AggregationSample(identity=identity, category="reasoning", result=evaluation(1.0))]
    )

    run.write_summary(first)
    run.write_summary(second)

    assert run.read_summary().score == 1.0
    assert not list(run.path.rglob(".elarabench-*"))


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", "bad/name"])
def test_run_path_traversal_is_rejected(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ArtifactStoreError, match="unsafe run ID"):
        ArtifactStore(tmp_path).run(run_id)


def test_event_and_artifacts_paths_are_deterministic(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path).run("run")
    identity = SampleIdentity(case_id="case", repeat_index=2)
    event = run.record_event(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        invocation_id="test-invocation",
        event_type=RunEventType.RUN_CREATED,
    )

    assert run.artifacts_directory(identity) == run.path / "samples/case/repeat-002/artifacts"
    assert event.sequence == 0
    assert run.read_events() == (event,)


def test_incomplete_event_tail_can_be_repaired_without_losing_valid_events(
    tmp_path: Path,
) -> None:
    run = ArtifactStore(tmp_path).run("run")
    event = run.record_event(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        invocation_id="test-invocation",
        event_type=RunEventType.RUN_CREATED,
    )
    with (run.path / "events.jsonl").open("ab") as handle:
        handle.write(b'{"incomplete"')

    assert run.repair_event_log_tail() is True
    assert run.read_events() == (event,)
    assert run.repair_event_log_tail() is False


def test_run_symlink_redirection_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "run").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactStoreError, match="redirected by a symlink"):
        ArtifactStore(root).run("run")


def test_sample_symlink_redirection_is_rejected(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path / "runs").run("run")
    outside = tmp_path / "outside"
    outside.mkdir()
    samples = run.path / "samples"
    samples.mkdir()
    (samples / "case").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactStoreError, match="redirected by a symlink"):
        run.write_request(SampleIdentity(case_id="case", repeat_index=0), request())
