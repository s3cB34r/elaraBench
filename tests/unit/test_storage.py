"""Filesystem artifact store safety and round-trip tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from elarabench.aggregation import aggregate
from elarabench.models import (
    ActionComplianceCaseOutcomeMasses,
    ActionComplianceSampleOutcomeCounts,
    ActionComplianceSummary,
    AggregationSample,
    BehavioralRate,
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


def action_summary() -> ActionComplianceSummary:
    def rate(numerator: float, denominator: int) -> BehavioralRate:
        return BehavioralRate(
            numerator=numerator,
            denominator=denominator,
            eligible_count=denominator,
            coverage=1.0,
            partial_value=numerator / denominator,
            headline_value=numerator / denominator,
        )

    return ActionComplianceSummary(
        eligible_case_ids=("a", "d", "p"),
        expected_case_count=3,
        observed_case_count=3,
        expected_sample_count=3,
        scored_sample_count=3,
        coverage=1.0,
        sample_outcomes=ActionComplianceSampleOutcomeCounts(
            authorized_successful_plan=1,
            denied_correct_stop=1,
            requires_approval_correct_stop=1,
        ),
        case_outcomes=ActionComplianceCaseOutcomeMasses(
            authorized_successful_plan=1,
            denied_correct_stop=1,
            requires_approval_correct_stop=1,
        ),
        authorized_success_rate=rate(1, 1),
        authorized_unsuccessful_rate=rate(0, 1),
        unnecessary_stop_rate=rate(0, 1),
        denied_compliance_rate=rate(1, 1),
        approval_compliance_rate=rate(1, 1),
        boundary_violation_rate=rate(0, 2),
        protocol_invalid_rate=rate(0, 3),
        invalid_plan_rate=rate(0, 3),
        overall_compliance_rate=rate(3, 3),
        balanced_action_compliance=1.0,
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
    stored_summary = json.loads((run.path / "summary.json").read_text(encoding="utf-8"))
    assert stored_summary["schema_version"] == 4
    assert stored_summary["source_result_schema_version"] == 3
    assert "action_compliance" not in stored_summary
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
    run.write_manifest(manifest("run"))
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


@pytest.mark.parametrize("schema_version", [2, 3])
def test_current_summary_writer_rejects_historical_schema_objects(
    tmp_path: Path, schema_version: int
) -> None:
    run = ArtifactStore(tmp_path).run("run")
    run.write_manifest(manifest("run"))
    current = aggregate(
        [
            AggregationSample(
                identity=SampleIdentity(case_id="case", repeat_index=0),
                category="reasoning",
                result=evaluation(1.0),
            )
        ]
    )
    historical = current.model_copy(
        update={
            "schema_version": schema_version,
            "source_result_schema_version": 2 if schema_version == 2 else 3,
            "refusal_compliance": None,
        }
    )

    with pytest.raises(ArtifactStoreError, match="only accepts summary schema v4"):
        run.write_summary(historical)
    assert not (run.path / "summary.json").exists()


def test_action_summary_schema_v5_round_trips_and_v4_rejects_action_data(
    tmp_path: Path,
) -> None:
    run = ArtifactStore(tmp_path / "runs").run("action-summary")
    run.write_manifest(manifest("action-summary"))
    base = aggregate(
        [
            AggregationSample(
                identity=SampleIdentity(case_id="case", repeat_index=0),
                category="action",
                result=evaluation(1.0),
            )
        ]
    )
    action = action_summary()
    current = base.model_copy(
        update={
            "schema_version": 5,
            "score": 1.0,
            "partial_score": 1.0,
            "action_compliance": action,
        }
    )

    run.replace_summary(current)

    assert run.read_summary() == current
    payload = json.loads((run.path / "summary.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 5
    assert payload["action_compliance"]["scoring_semantic"] == (
        "action_compliance_scoring_v1"
    )

    invalid_v4 = current.model_copy(update={"schema_version": 4})
    with pytest.raises(ArtifactStoreError, match="v4 cannot contain action"):
        run.replace_summary(invalid_v4)

    invalid_v5 = base.model_copy(update={"schema_version": 5})
    with pytest.raises(ArtifactStoreError, match="v5 requires action"):
        run.replace_summary(invalid_v5)


@pytest.mark.parametrize("schema_version", [3, 4])
def test_physical_v3_rejects_current_summary_missing_provenance(
    tmp_path: Path, schema_version: int
) -> None:
    run = ArtifactStore(tmp_path).run("run")
    run.write_manifest(manifest("run"))
    summary = aggregate(
        [
            AggregationSample(
                identity=SampleIdentity(case_id="case", repeat_index=0),
                category="reasoning",
                result=evaluation(1.0),
            )
        ]
    )
    payload = summary.model_dump(mode="json")
    payload["schema_version"] = schema_version
    payload.pop("source_result_schema_version")
    if schema_version == 3:
        payload.pop("refusal_compliance")
    (run.path / "summary.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactStoreError, match="missing required source_result"):
        run.read_summary()


def test_physical_v3_reads_explicit_schema_v3_summary_provenance(tmp_path: Path) -> None:
    run = ArtifactStore(tmp_path).run("run")
    run.write_manifest(manifest("run"))
    summary = aggregate(
        [
            AggregationSample(
                identity=SampleIdentity(case_id="case", repeat_index=0),
                category="reasoning",
                result=evaluation(1.0),
            )
        ]
    )
    payload = summary.model_dump(mode="json")
    payload["schema_version"] = 3
    payload.pop("refusal_compliance")
    (run.path / "summary.json").write_text(json.dumps(payload), encoding="utf-8")

    stored = run.read_summary()
    assert stored.schema_version == 3
    assert stored.source_result_schema_version == 3


@pytest.mark.parametrize("explicit_source", [None, 2])
def test_physical_v3_rejects_legacy_or_mismatched_summary_provenance(
    tmp_path: Path, explicit_source: int | None
) -> None:
    run = ArtifactStore(tmp_path).run("run")
    run.write_manifest(manifest("run"))
    summary = aggregate(
        [
            AggregationSample(
                identity=SampleIdentity(case_id="case", repeat_index=0),
                category="reasoning",
                result=evaluation(1.0),
            )
        ]
    )
    payload = summary.model_dump(mode="json")
    if explicit_source is None:
        payload["schema_version"] = 2
        payload.pop("source_result_schema_version")
        payload.pop("refusal_compliance")
    else:
        payload["source_result_schema_version"] = explicit_source
    (run.path / "summary.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ArtifactStoreError,
        match=r"summary source schema mismatch|missing required source_result",
    ):
        run.read_summary()


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
