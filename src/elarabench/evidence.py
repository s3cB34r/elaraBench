"""Read-only, version-aware access to validated physical run evidence."""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from pydantic import ValidationError

from elarabench.benchmark import validate_benchmark_snapshot
from elarabench.evaluators.registry import validate_specification
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
    AttemptOutcome,
    AttemptRecord,
    BenchmarkSnapshot,
    GenerationErrorKind,
    GenerationResponse,
    RequestPlanEntry,
    RetryPolicy,
    RunManifest,
    SampleIdentity,
)
from elarabench.run_identity import compute_run_fingerprint
from elarabench.storage import ArtifactStore, ArtifactStoreError, RunArtifactStore

StoredManifest: TypeAlias = RunManifest | LegacyV2RunManifest
StoredSnapshot: TypeAlias = BenchmarkSnapshot | LegacyV2BenchmarkSnapshot


class RunIntegrityError(ValueError):
    """Stored run artifacts are incomplete, corrupt, or incompatible."""


def open_run_path(path: str | Path) -> RunArtifactStore:
    """Open an exact existing run path without creating or modifying it."""
    run_path = Path(path).resolve()
    if not run_path.is_dir():
        raise RunIntegrityError(f"run directory not found: {run_path}")
    return ArtifactStore(run_path.parent).open_run(run_path.name)


def validate_stored_run(
    store: RunArtifactStore,
    *,
    validate_evaluators: bool = True,
    validate_samples: bool = True,
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
    if snapshot.minimum_scored_coverage != manifest.configuration.minimum_scored_coverage:
        raise RunIntegrityError("snapshot and run coverage policies do not match")

    expected_identities = tuple(
        (case.id, repeat_index)
        for case in snapshot.suite.cases
        for repeat_index in range(manifest.configuration.repeats)
    )
    actual_identities = tuple(
        (entry.identity.case_id, entry.identity.repeat_index) for entry in manifest.request_plan
    )
    if actual_identities != expected_identities:
        raise RunIntegrityError("manifest request plan is incomplete or out of order")
    if validate_evaluators:
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
    if validate_samples:
        validate_physical_run_evidence(store, manifest)
    return manifest, snapshot


def plan_by_identity(
    manifest: StoredManifest,
) -> dict[tuple[str, int], RequestPlanEntry]:
    """Index the immutable request plan by case ID and repeat index."""
    return {
        (entry.identity.case_id, entry.identity.repeat_index): entry
        for entry in manifest.request_plan
    }


def verify_stored_request(
    store: RunArtifactStore,
    entry: RequestPlanEntry,
    *,
    result_schema_version: int,
) -> None:
    """Verify one canonical request using its physical result-schema semantics."""
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


def verify_sample_artifact_dependencies(
    store: RunArtifactStore,
    identity: SampleIdentity,
    attempts: tuple[AttemptRecord, ...],
) -> None:
    """Reject impossible sample dependency states without materializing missing evidence."""
    request_exists = store.request_exists(identity)
    response_exists = store.response_exists(identity)
    evaluation_exists = store.evaluation_exists(identity)
    if not request_exists and (response_exists or evaluation_exists or attempts):
        raise RunIntegrityError(
            f"sample evidence exists without canonical request for {identity}"
        )
    if evaluation_exists and not response_exists:
        raise RunIntegrityError(f"evaluation exists without response for {identity}")


def verify_attempt_request_hashes(
    attempts: tuple[AttemptRecord, ...], entry: RequestPlanEntry
) -> None:
    """Verify that every materialized attempt belongs to its canonical request."""
    for attempt in attempts:
        if attempt.request_hash != entry.request_hash:
            raise RunIntegrityError(
                f"attempt request hash mismatch for {entry.identity} "
                f"at attempt {attempt.attempt_index}"
            )


def validate_attempt_history(
    attempts: tuple[AttemptRecord, ...],
    entry: RequestPlanEntry,
    *,
    retry_policy: RetryPolicy,
    result_schema_version: int,
) -> None:
    """Validate every v3 attempt and the retry sequence Runner can persist."""
    verify_attempt_request_hashes(attempts, entry)
    if result_schema_version != 3:
        return

    prior_failures = 0
    last_index = len(attempts) - 1
    for position, attempt in enumerate(attempts):
        if attempt.attempt_index != position:
            raise RunIntegrityError(
                f"attempt sequence is not contiguous for {entry.identity}"
            )
        if attempt.retry_number != prior_failures:
            raise RunIntegrityError(
                f"attempt retry number disagrees with prior failures for {entry.identity} "
                f"at attempt {attempt.attempt_index}"
            )
        if attempt.retry_number > retry_policy.max_retries:
            raise RunIntegrityError(
                f"attempt retry budget exceeded for {entry.identity} "
                f"at attempt {attempt.attempt_index}"
            )

        error = attempt.response.error
        if attempt.outcome is AttemptOutcome.SUCCEEDED:
            if error is not None:
                label = "terminal attempt" if position == last_index else "attempt"
                raise RunIntegrityError(
                    f"{label} outcome disagrees with its response for {entry.identity} "
                    f"at attempt {attempt.attempt_index}"
                )
            if position != last_index:
                raise RunIntegrityError(
                    f"successful attempt does not terminate retry history for "
                    f"{entry.identity} at attempt {attempt.attempt_index}"
                )
        elif attempt.outcome is AttemptOutcome.FAILED:
            if error is None:
                label = "terminal attempt" if position == last_index else "attempt"
                raise RunIntegrityError(
                    f"{label} outcome disagrees with its response for {entry.identity} "
                    f"at attempt {attempt.attempt_index}"
                )
            if position != last_index:
                if not error.retryable:
                    raise RunIntegrityError(
                        f"non-retryable failed attempt is followed by another attempt for "
                        f"{entry.identity} at attempt {attempt.attempt_index}"
                    )
                if attempt.retry_number >= retry_policy.max_retries:
                    raise RunIntegrityError(
                        f"retry-budget-exhausting failed attempt is followed by another "
                        f"attempt for {entry.identity} at attempt {attempt.attempt_index}"
                    )
            prior_failures += 1
        else:
            if (
                error is None
                or error.kind is not GenerationErrorKind.INTERRUPTED
                or error.retryable
            ):
                raise RunIntegrityError(
                    f"interrupted attempt lacks consistent interruption evidence for "
                    f"{entry.identity} at attempt {attempt.attempt_index}"
                )

    if prior_failures > retry_policy.max_retries + 1:
        raise RunIntegrityError(f"attempt retry budget exceeded for {entry.identity}")


def validate_physical_run_evidence(
    store: RunArtifactStore,
    manifest: StoredManifest,
) -> None:
    """Read and validate all materialized sample evidence without modifying it."""
    for entry in manifest.request_plan:
        identity = entry.identity
        try:
            attempts = store.read_attempts(identity)
        except ArtifactStoreError as error:
            raise RunIntegrityError(str(error)) from error
        verify_sample_artifact_dependencies(store, identity, attempts)
        if not store.request_exists(identity):
            continue
        verify_stored_request(
            store,
            entry,
            result_schema_version=manifest.schema_version,
        )
        validate_attempt_history(
            attempts,
            entry,
            retry_policy=manifest.configuration.retry_policy,
            result_schema_version=manifest.schema_version,
        )
        if not store.response_exists(identity):
            continue
        try:
            response = store.read_response(identity)
        except ArtifactStoreError as error:
            raise RunIntegrityError(str(error)) from error
        verify_terminal_attempt_response(attempts, response, identity)
        if manifest.schema_version == 3:
            terminal = attempts[-1]
            response_error = terminal.response.error
            if (
                terminal.outcome is AttemptOutcome.FAILED
                and response_error is not None
                and response_error.retryable
                and terminal.retry_number < manifest.configuration.retry_policy.max_retries
            ):
                raise RunIntegrityError(
                    f"canonical response prematurely terminates retryable failure for "
                    f"{identity} at attempt {terminal.attempt_index}"
                )


def verify_terminal_attempt_response(
    attempts: tuple[AttemptRecord, ...],
    response: GenerationResponse,
    identity: SampleIdentity,
) -> None:
    """Verify a canonical response is exactly the terminal attempt's response."""
    if not attempts:
        raise RunIntegrityError(f"canonical response exists without an attempt for {identity}")
    terminal = attempts[-1]
    if terminal.outcome is AttemptOutcome.INTERRUPTED:
        raise RunIntegrityError(
            f"canonical response cannot originate from an interrupted attempt for {identity}"
        )
    expected_outcome = (
        AttemptOutcome.FAILED
        if terminal.response.error is not None
        else AttemptOutcome.SUCCEEDED
    )
    if terminal.outcome is not expected_outcome:
        raise RunIntegrityError(
            f"terminal attempt outcome disagrees with its response for {identity}"
        )
    canonical_outcome = (
        AttemptOutcome.FAILED if response.error is not None else AttemptOutcome.SUCCEEDED
    )
    if canonical_outcome is not terminal.outcome:
        raise RunIntegrityError(
            f"canonical response outcome disagrees with terminal attempt for {identity}"
        )
    if response != terminal.response:
        raise RunIntegrityError(
            f"canonical response does not match terminal attempt response for {identity}"
        )
