"""Offline rescoring and deterministic summary regeneration services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from elarabench.action_compliance import (
    ActionComplianceEvidenceError,
    expectation_from_action_specification,
    validate_action_compliance_result,
)
from elarabench.aggregation import AggregationError, aggregate
from elarabench.evaluators.registry import evaluate
from elarabench.evidence import RunIntegrityError as RunIntegrityError
from elarabench.evidence import (
    StoredManifest,
    StoredSnapshot,
    plan_by_identity,
    verify_attempt_request_hashes,
    verify_sample_artifact_dependencies,
    verify_stored_request,
    verify_terminal_attempt_response,
)
from elarabench.evidence import open_run_path as open_run_path
from elarabench.evidence import validate_stored_run as validate_stored_run
from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    EvaluationContext,
    RunEventType,
    SampleIdentity,
)
from elarabench.refusal_compliance import expectation_from_specification
from elarabench.storage import ArtifactStoreError, RunArtifactStore

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def regenerate_summary(
    store: RunArtifactStore,
    *,
    manifest: StoredManifest,
    snapshot: StoredSnapshot,
    invocation_id: str,
    clock: Clock = utc_now,
) -> AggregationSummary:
    """Aggregate stored evaluations only; never evaluate or generate."""
    samples: list[AggregationSample] = []
    plan = plan_by_identity(manifest)
    action_expectations = {
        case.id: action_expectation
        for case in snapshot.suite.cases
        if (
            action_expectation := expectation_from_action_specification(
                case.evaluation
            )
        )
        is not None
    }
    for case in snapshot.suite.cases:
        for repeat_index in range(manifest.configuration.repeats):
            identity = SampleIdentity(case_id=case.id, repeat_index=repeat_index)
            if not store.evaluation_exists(identity):
                continue
            try:
                attempts = store.read_attempts(identity)
            except ArtifactStoreError as error:
                raise RunIntegrityError(str(error)) from error
            verify_sample_artifact_dependencies(
                store,
                identity,
                attempts,
            )
            verify_stored_request(
                store,
                plan[(case.id, repeat_index)],
                result_schema_version=manifest.schema_version,
            )
            try:
                response = store.read_response(identity)
                verify_attempt_request_hashes(attempts, plan[(case.id, repeat_index)])
                verify_terminal_attempt_response(attempts, response, identity)
                result = store.read_evaluation(
                    identity,
                    source_result_schema_version=manifest.schema_version,
                )
            except ArtifactStoreError as error:
                raise RunIntegrityError(str(error)) from error
            action_expectation = action_expectations.get(case.id)
            if action_expectation is not None:
                try:
                    validate_action_compliance_result(
                        result,
                        action_expectation,
                        response=response,
                    )
                except ActionComplianceEvidenceError as error:
                    raise RunIntegrityError(
                        f"invalid derived evaluation evidence: {error}"
                    ) from error
            samples.append(
                AggregationSample(
                    identity=identity,
                    category=case.category,
                    tags=case.tags,
                    case_weight=case.weight,
                    result=result,
                )
            )
    expected = len(snapshot.suite.cases) * manifest.configuration.repeats
    try:
        summary = aggregate(
            samples,
            expected_samples=expected,
            minimum_scored_coverage=manifest.configuration.minimum_scored_coverage,
            refusal_case_expectations={
                case.id: expectation
                for case in snapshot.suite.cases
                if (expectation := expectation_from_specification(case.evaluation))
                is not None
            },
            action_case_expectations=action_expectations,
            expected_repeats=manifest.configuration.repeats,
            source_result_schema_version=manifest.schema_version,
        )
    except AggregationError as error:
        raise RunIntegrityError(f"invalid derived evaluation evidence: {error}") from error
    store.replace_summary(summary)
    store.record_event(
        timestamp=clock(),
        invocation_id=invocation_id,
        event_type=RunEventType.SUMMARY_WRITTEN,
        data={
            "coverage": summary.coverage.ratio,
            "coverage_sufficient": summary.coverage.sufficient,
        },
    )
    return summary


def score_run(path: str | Path, *, clock: Clock = utc_now) -> AggregationSummary:
    """Re-evaluate canonical responses and rebuild the summary without a provider."""
    store = open_run_path(path)
    manifest, snapshot = validate_stored_run(store)
    invocation_id = f"score-{uuid4().hex}"
    store.record_event(
        timestamp=clock(),
        invocation_id=invocation_id,
        event_type=RunEventType.SCORING_STARTED,
    )
    plan = plan_by_identity(manifest)
    action_expectations = {
        case.id: action_expectation
        for case in snapshot.suite.cases
        if (
            action_expectation := expectation_from_action_specification(
                case.evaluation
            )
        )
        is not None
    }
    for case in snapshot.suite.cases:
        for repeat_index in range(manifest.configuration.repeats):
            identity = SampleIdentity(case_id=case.id, repeat_index=repeat_index)
            if not store.response_exists(identity):
                continue
            entry = plan[(case.id, repeat_index)]
            verify_stored_request(
                store,
                entry,
                result_schema_version=manifest.schema_version,
            )
            try:
                attempts = store.read_attempts(identity)
                response = store.read_response(identity)
                verify_sample_artifact_dependencies(store, identity, attempts)
                verify_attempt_request_hashes(attempts, entry)
                verify_terminal_attempt_response(attempts, response, identity)
                action_expectation = action_expectations.get(case.id)
                result = evaluate(
                    EvaluationContext(
                        response=response,
                        specification=case.evaluation,
                        source_result_schema_version=manifest.schema_version,
                    )
                )
                if action_expectation is not None:
                    validate_action_compliance_result(
                        result,
                        action_expectation,
                        response=response,
                    )
                store.write_evaluation(
                    identity,
                    result,
                    replace=store.evaluation_exists(identity),
                )
            except (ActionComplianceEvidenceError, ArtifactStoreError) as error:
                raise RunIntegrityError(str(error)) from error
    summary = regenerate_summary(
        store,
        manifest=manifest,
        snapshot=snapshot,
        invocation_id=invocation_id,
        clock=clock,
    )
    store.record_event(
        timestamp=clock(),
        invocation_id=invocation_id,
        event_type=RunEventType.SCORING_COMPLETED,
        data={"coverage": summary.coverage.ratio},
    )
    return summary


def summarize_run(path: str | Path, *, clock: Clock = utc_now) -> AggregationSummary:
    """Rebuild a summary exclusively from stored derived evaluations."""
    store = open_run_path(path)
    manifest, snapshot = validate_stored_run(store)
    invocation_id = f"summary-{uuid4().hex}"
    summary = regenerate_summary(
        store,
        manifest=manifest,
        snapshot=snapshot,
        invocation_id=invocation_id,
        clock=clock,
    )
    store.record_event(
        timestamp=clock(),
        invocation_id=invocation_id,
        event_type=RunEventType.SUMMARIZATION_COMPLETED,
        data={"coverage": summary.coverage.ratio},
    )
    return summary
