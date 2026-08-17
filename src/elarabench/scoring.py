"""Offline rescoring and deterministic summary regeneration services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

from pydantic import ValidationError

from elarabench.aggregation import aggregate
from elarabench.benchmark import validate_benchmark_snapshot
from elarabench.evaluators.registry import evaluate, validate_specification
from elarabench.hashing import hash_generation_request
from elarabench.legacy_v2 import (
    LegacyV2BenchmarkSnapshot,
    LegacyV2GenerationRequest,
    LegacyV2RunManifest,
    compute_legacy_v2_fingerprint,
    hash_legacy_v2_request,
    validate_legacy_v2_snapshot,
)
from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    BenchmarkSnapshot,
    EvaluationContext,
    RequestPlanEntry,
    RunEventType,
    RunManifest,
    SampleIdentity,
)
from elarabench.run_identity import compute_run_fingerprint
from elarabench.storage import ArtifactStore, ArtifactStoreError, RunArtifactStore

Clock = Callable[[], datetime]
StoredManifest: TypeAlias = RunManifest | LegacyV2RunManifest
StoredSnapshot: TypeAlias = BenchmarkSnapshot | LegacyV2BenchmarkSnapshot


class RunIntegrityError(ValueError):
    """Stored run artifacts are incomplete, corrupt, or incompatible."""


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def open_run_path(path: str | Path) -> RunArtifactStore:
    """Open an exact existing run path without creating missing directories."""
    run_path = Path(path).resolve()
    if not run_path.is_dir():
        raise RunIntegrityError(f"run directory not found: {run_path}")
    return ArtifactStore(run_path.parent).open_run(run_path.name)


def validate_stored_run(
    store: RunArtifactStore,
) -> tuple[StoredManifest, StoredSnapshot]:
    """Validate v3 or historical v2 identity with its original hash semantics."""
    try:
        manifest_data = store.read_manifest_data()
        snapshot_data = store.read_benchmark_data()
        schema_version = manifest_data.get("schema_version")
        if schema_version == 3:
            current_manifest = RunManifest.model_validate(manifest_data)
            current_snapshot = BenchmarkSnapshot.model_validate(snapshot_data)
            validate_benchmark_snapshot(current_snapshot)
            manifest: StoredManifest = current_manifest
            snapshot: StoredSnapshot = current_snapshot
        elif schema_version == 2:
            legacy_manifest = LegacyV2RunManifest.model_validate(manifest_data)
            legacy_snapshot = LegacyV2BenchmarkSnapshot.model_validate(snapshot_data)
            validate_legacy_v2_snapshot(legacy_snapshot)
            manifest = legacy_manifest
            snapshot = legacy_snapshot
        else:
            raise RunIntegrityError(
                f"unsupported result schema version {schema_version!r}; supported versions: 2, 3"
            )
    except (ArtifactStoreError, ValidationError, ValueError) as error:
        if isinstance(error, RunIntegrityError):
            raise
        raise RunIntegrityError(str(error)) from error
    if manifest.run_id != store.run_id:
        raise RunIntegrityError("manifest run ID does not match run directory")
    if manifest.suite_id != snapshot.suite.id or manifest.suite_version != snapshot.suite.version:
        raise RunIntegrityError("manifest suite identity does not match benchmark snapshot")
    if manifest.suite_hash != snapshot.benchmark_content_hash:
        raise RunIntegrityError("manifest suite hash does not match benchmark snapshot")
    if manifest.benchmark_snapshot_hash != snapshot.snapshot_hash:
        raise RunIntegrityError("manifest benchmark snapshot hash mismatch")
    if (
        snapshot.minimum_scored_coverage
        != manifest.configuration.minimum_scored_coverage
    ):
        raise RunIntegrityError("snapshot and run coverage policies do not match")

    expected_identities = tuple(
        SampleIdentity(case_id=case.id, repeat_index=repeat_index)
        for case in snapshot.suite.cases
        for repeat_index in range(manifest.configuration.repeats)
    )
    if tuple(entry.identity for entry in manifest.request_plan) != expected_identities:
        raise RunIntegrityError("manifest request plan is incomplete or out of order")
    try:
        for case in snapshot.suite.cases:
            validate_specification(case.evaluation)
    except ValueError as error:
        raise RunIntegrityError(f"invalid stored evaluator specification: {error}") from error
    if isinstance(manifest, LegacyV2RunManifest):
        if not isinstance(snapshot, LegacyV2BenchmarkSnapshot):
            raise RunIntegrityError("result schema and benchmark snapshot version mismatch")
        fingerprint = compute_legacy_v2_fingerprint(manifest, snapshot)
    else:
        if not isinstance(snapshot, BenchmarkSnapshot):
            raise RunIntegrityError("result schema and benchmark snapshot version mismatch")
        fingerprint = compute_run_fingerprint(
            snapshot=snapshot,
            configuration=manifest.configuration,
            provider=manifest.provider,
            model=manifest.model,
            seed_control=manifest.seed_control,
            thinking_control=manifest.thinking_control,
            framework=manifest.framework,
            request_plan=manifest.request_plan,
        )
    if fingerprint != manifest.run_fingerprint:
        raise RunIntegrityError("run fingerprint does not match stored canonical identity")
    return manifest, snapshot


def _plan_by_identity(manifest: StoredManifest) -> dict[tuple[str, int], RequestPlanEntry]:
    return {
        (entry.identity.case_id, entry.identity.repeat_index): entry
        for entry in manifest.request_plan
    }


def _verify_request(
    store: RunArtifactStore,
    entry: RequestPlanEntry,
    *,
    result_schema_version: int,
) -> None:
    if not store.request_exists(entry.identity):
        raise RunIntegrityError(f"missing canonical request for {entry.identity}")
    try:
        if result_schema_version == 2:
            request = LegacyV2GenerationRequest.model_validate(
                store.read_request_data(entry.identity)
            )
            actual_hash = hash_legacy_v2_request(request)
        else:
            actual_hash = hash_generation_request(store.read_request(entry.identity))
    except (ArtifactStoreError, ValidationError) as error:
        raise RunIntegrityError(str(error)) from error
    if actual_hash != entry.request_hash:
        raise RunIntegrityError(f"canonical request hash mismatch for {entry.identity}")


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
    plan = _plan_by_identity(manifest)
    for case in snapshot.suite.cases:
        for repeat_index in range(manifest.configuration.repeats):
            identity = SampleIdentity(case_id=case.id, repeat_index=repeat_index)
            if not store.evaluation_exists(identity):
                continue
            if not store.response_exists(identity):
                raise RunIntegrityError(f"evaluation exists without response for {identity}")
            _verify_request(
                store,
                plan[(case.id, repeat_index)],
                result_schema_version=manifest.schema_version,
            )
            try:
                store.read_response(identity)
                result = store.read_evaluation(
                    identity,
                    source_result_schema_version=manifest.schema_version,
                )
            except ArtifactStoreError as error:
                raise RunIntegrityError(str(error)) from error
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
    summary = aggregate(
        samples,
        expected_samples=expected,
        minimum_scored_coverage=manifest.configuration.minimum_scored_coverage,
    ).model_copy(update={"source_result_schema_version": manifest.schema_version})
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
    plan = _plan_by_identity(manifest)
    for case in snapshot.suite.cases:
        for repeat_index in range(manifest.configuration.repeats):
            identity = SampleIdentity(case_id=case.id, repeat_index=repeat_index)
            if not store.response_exists(identity):
                continue
            entry = plan[(case.id, repeat_index)]
            _verify_request(
                store,
                entry,
                result_schema_version=manifest.schema_version,
            )
            try:
                response = store.read_response(identity)
                result = evaluate(
                    EvaluationContext(
                        response=response,
                        specification=case.evaluation,
                        source_result_schema_version=manifest.schema_version,
                    )
                )
                store.write_evaluation(
                    identity,
                    result,
                    replace=store.evaluation_exists(identity),
                )
            except ArtifactStoreError as error:
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
