"""Atomic filesystem storage primitives for canonical and derived run artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from elarabench.models import (
    AggregationSummary,
    AttemptRecord,
    BenchmarkSnapshot,
    EvaluationResult,
    GenerationRequest,
    GenerationResponse,
    Identifier,
    RunEvent,
    RunEventType,
    RunLifecycle,
    RunManifest,
    SampleIdentity,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
_IDENTIFIER_ADAPTER: TypeAdapter[str] = TypeAdapter(Identifier)


class ArtifactStoreError(RuntimeError):
    """Base class for safe artifact storage failures."""


class ArtifactExistsError(ArtifactStoreError):
    """A canonical artifact already exists and cannot be silently replaced."""


class ArtifactNotFoundError(ArtifactStoreError):
    """A requested artifact does not exist."""


def _json_bytes(value: BaseModel | dict[str, JsonValue]) -> bytes:
    serializable = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(
            serializable,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".elarabench-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError as error:
                raise ArtifactExistsError(f"canonical artifact already exists: {path}") from error
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_model(path: Path, model: type[ModelT]) -> ModelT:
    if not path.is_file():
        raise ArtifactNotFoundError(f"artifact not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise ArtifactStoreError(f"invalid artifact {path}: {error}") from error


class ArtifactStore:
    """Factory for deterministic run directories beneath one configured root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_run_id(self, run_id: str) -> str:
        try:
            return _IDENTIFIER_ADAPTER.validate_python(run_id)
        except ValidationError as error:
            raise ArtifactStoreError(f"unsafe run ID {run_id!r}") from error

    def create_run(self, run_id: str) -> RunArtifactStore:
        """Create a new run directory without reusing an existing execution."""
        safe_run_id = self._safe_run_id(run_id)
        path = self.root / safe_run_id
        try:
            path.mkdir()
        except FileExistsError as error:
            raise ArtifactExistsError(f"run already exists: {path}") from error
        return RunArtifactStore(self.root, safe_run_id)

    def open_run(self, run_id: str) -> RunArtifactStore:
        """Open an existing run without implicitly creating it."""
        safe_run_id = self._safe_run_id(run_id)
        path = self.root / safe_run_id
        if not path.is_dir():
            raise ArtifactNotFoundError(f"run not found: {path}")
        return RunArtifactStore(self.root, safe_run_id)

    def run(self, run_id: str) -> RunArtifactStore:
        """Compatibility helper: open an existing run or create a new one."""
        path = self.root / self._safe_run_id(run_id)
        return self.open_run(run_id) if path.exists() else self.create_run(run_id)


class RunArtifactStore:
    """Read and atomically write one run's canonical and derived artifacts."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = root / run_id
        if not self.path.is_dir():
            raise ArtifactNotFoundError(f"run not found: {self.path}")
        if self.path.resolve() != self.path:
            raise ArtifactStoreError(
                f"run directory must not be redirected by a symlink: {self.path}"
            )

    def _sample_path(self, identity: SampleIdentity) -> Path:
        candidate = self.path / "samples" / identity.case_id / identity.repeat_id
        if candidate.resolve() != candidate:
            raise ArtifactStoreError(
                f"sample directory must not be redirected by a symlink: {candidate}"
            )
        return candidate

    def write_manifest(self, manifest: RunManifest) -> None:
        """Write the canonical run manifest exactly once."""
        if manifest.run_id != self.run_id:
            raise ArtifactStoreError("manifest run_id does not match artifact store run_id")
        _atomic_write(self.path / "manifest.json", _json_bytes(manifest), replace=False)

    def read_manifest(self) -> RunManifest:
        return _read_model(self.path / "manifest.json", RunManifest)

    def update_manifest_lifecycle(self, lifecycle: RunLifecycle) -> RunManifest:
        """Atomically update only the guarded lifecycle section."""
        current = self.read_manifest()
        previous = current.lifecycle
        if lifecycle.created_at != previous.created_at:
            raise ArtifactStoreError("manifest lifecycle created_at cannot change")
        if lifecycle.updated_at < previous.updated_at:
            raise ArtifactStoreError("manifest lifecycle time cannot move backwards")
        if previous.started_at is not None and lifecycle.started_at != previous.started_at:
            raise ArtifactStoreError("manifest lifecycle started_at cannot change")
        resume_delta = lifecycle.resume_count - previous.resume_count
        if resume_delta not in {0, 1}:
            raise ArtifactStoreError("manifest resume count may increase only by one")
        if resume_delta == 1 and lifecycle.status.value != "running":
            raise ArtifactStoreError("a resume count increase requires running status")
        terminal = {"completed", "completed_with_errors", "interrupted", "failed"}
        allowed_transitions = {
            "initializing": {"running", "interrupted", "failed"},
            "running": {"running", "completed", "completed_with_errors", "interrupted", "failed"},
            "completed": {"running"},
            "completed_with_errors": {"running"},
            "interrupted": {"running"},
            "failed": {"running"},
        }
        if (
            lifecycle.status != previous.status
            and lifecycle.status.value not in allowed_transitions[previous.status.value]
        ):
            raise ArtifactStoreError(
                f"invalid lifecycle transition {previous.status.value} -> "
                f"{lifecycle.status.value}"
            )
        if (
            lifecycle.status.value == "running"
            and previous.status.value != "initializing"
            and resume_delta != 1
        ):
            raise ArtifactStoreError("resuming running state must increase resume count")
        if lifecycle.status.value in terminal and lifecycle.completed_at is None:
            raise ArtifactStoreError("terminal lifecycle states require completed_at")
        if lifecycle.status.value not in terminal and lifecycle.completed_at is not None:
            raise ArtifactStoreError("non-terminal lifecycle states cannot have completed_at")
        if (
            lifecycle.status.value in {"running", "completed", "completed_with_errors"}
            and lifecycle.started_at is None
        ):
            raise ArtifactStoreError("started lifecycle states require started_at")
        updated = current.model_copy(update={"lifecycle": lifecycle})
        current_identity = current.model_dump(mode="json", exclude={"lifecycle"})
        updated_identity = updated.model_dump(mode="json", exclude={"lifecycle"})
        if current_identity != updated_identity:
            raise ArtifactStoreError("manifest identity/configuration cannot change")
        _atomic_write(self.path / "manifest.json", _json_bytes(updated), replace=True)
        return updated

    def write_benchmark(self, snapshot: BenchmarkSnapshot) -> None:
        """Write the complete canonical benchmark snapshot exactly once."""
        _atomic_write(self.path / "benchmark.json", _json_bytes(snapshot), replace=False)

    def read_benchmark(self) -> BenchmarkSnapshot:
        return _read_model(self.path / "benchmark.json", BenchmarkSnapshot)

    def write_request(self, identity: SampleIdentity, request: GenerationRequest) -> None:
        """Write a canonical materialized request exactly once."""
        _atomic_write(
            self._sample_path(identity) / "request.json",
            _json_bytes(request),
            replace=False,
        )

    def read_request(self, identity: SampleIdentity) -> GenerationRequest:
        return _read_model(self._sample_path(identity) / "request.json", GenerationRequest)

    def write_response(self, identity: SampleIdentity, response: GenerationResponse) -> None:
        """Write a canonical raw/normalized response exactly once."""
        _atomic_write(
            self._sample_path(identity) / "response.json",
            _json_bytes(response),
            replace=False,
        )

    def read_response(self, identity: SampleIdentity) -> GenerationResponse:
        return _read_model(self._sample_path(identity) / "response.json", GenerationResponse)

    def response_exists(self, identity: SampleIdentity) -> bool:
        return (self._sample_path(identity) / "response.json").exists()

    def request_exists(self, identity: SampleIdentity) -> bool:
        return (self._sample_path(identity) / "request.json").exists()

    def evaluation_exists(self, identity: SampleIdentity) -> bool:
        return (self._sample_path(identity) / "evaluation.json").exists()

    def summary_exists(self) -> bool:
        return (self.path / "summary.json").exists()

    def write_attempt(self, attempt: AttemptRecord) -> None:
        """Write one immutable provider attempt."""
        path = (
            self._sample_path(attempt.identity)
            / "attempts"
            / f"attempt-{attempt.attempt_index:03d}.json"
        )
        _atomic_write(path, _json_bytes(attempt), replace=False)

    def read_attempts(self, identity: SampleIdentity) -> tuple[AttemptRecord, ...]:
        directory = self._sample_path(identity) / "attempts"
        if not directory.exists():
            return ()
        attempts: list[AttemptRecord] = []
        for path in sorted(directory.glob("attempt-*.json")):
            attempt = _read_model(path, AttemptRecord)
            if attempt.identity != identity:
                raise ArtifactStoreError(f"attempt sample identity mismatch: {path}")
            attempts.append(attempt)
        if [attempt.attempt_index for attempt in attempts] != list(range(len(attempts))):
            raise ArtifactStoreError(f"attempt sequence is not contiguous for {identity.case_id}")
        return tuple(attempts)

    def write_evaluation(
        self,
        identity: SampleIdentity,
        evaluation: EvaluationResult,
        *,
        replace: bool = False,
    ) -> None:
        """Write derived evaluation data; replacement must be requested explicitly."""
        _atomic_write(
            self._sample_path(identity) / "evaluation.json",
            _json_bytes(evaluation),
            replace=replace,
        )

    def read_evaluation(self, identity: SampleIdentity) -> EvaluationResult:
        return _read_model(self._sample_path(identity) / "evaluation.json", EvaluationResult)

    def write_summary(self, summary: AggregationSummary) -> None:
        """Compatibility API for atomically writing a derived summary."""
        self.replace_summary(summary)

    def replace_summary(self, summary: AggregationSummary) -> None:
        """Explicitly and atomically replace the reproducible derived summary."""
        _atomic_write(self.path / "summary.json", _json_bytes(summary), replace=True)

    def read_summary(self) -> AggregationSummary:
        return _read_model(self.path / "summary.json", AggregationSummary)

    def read_events(self) -> tuple[RunEvent, ...]:
        """Read and validate the diagnostic event log."""
        path = self.path / "events.jsonl"
        if not path.exists():
            return ()
        events: list[RunEvent] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line_number, line in enumerate(lines, start=1):
                event = RunEvent.model_validate_json(line)
                if event.run_id != self.run_id:
                    raise ArtifactStoreError(f"event run_id mismatch at line {line_number}")
                if event.sequence != len(events):
                    raise ArtifactStoreError(f"event sequence mismatch at line {line_number}")
                events.append(event)
        except (OSError, UnicodeError, ValidationError) as error:
            raise ArtifactStoreError(f"invalid event log {path}: {error}") from error
        return tuple(events)

    def next_event_sequence(self) -> int:
        return len(self.read_events())

    def append_event(self, event: RunEvent) -> None:
        """Append one validated event with a monotonic sequence."""
        if event.run_id != self.run_id:
            raise ArtifactStoreError("event run_id does not match artifact store run_id")
        expected = self.next_event_sequence()
        if event.sequence != expected:
            raise ArtifactStoreError(
                f"event sequence must be {expected}, received {event.sequence}"
            )
        path = self.path / "events.jsonl"
        line = event.model_dump_json() + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def repair_event_log_tail(self) -> bool:
        """Discard only an incomplete trailing event write left by a hard kill."""
        path = self.path / "events.jsonl"
        if not path.exists():
            return False
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ArtifactStoreError(f"cannot read event log {path}: {error}") from error
        if not content or content.endswith(b"\n"):
            self.read_events()
            return False
        complete_prefix = content.rpartition(b"\n")[0]
        repaired = complete_prefix + (b"\n" if complete_prefix else b"")
        _atomic_write(path, repaired, replace=True)
        self.read_events()
        return True

    def record_event(
        self,
        *,
        timestamp: datetime,
        invocation_id: str,
        event_type: RunEventType,
        sample: SampleIdentity | None = None,
        attempt_index: int | None = None,
        data: dict[str, JsonValue] | None = None,
    ) -> RunEvent:
        """Build, validate, and append the next event through the single-writer API."""
        event = RunEvent(
            sequence=self.next_event_sequence(),
            timestamp=timestamp,
            run_id=self.run_id,
            invocation_id=invocation_id,
            type=event_type,
            sample=sample,
            attempt_index=attempt_index,
            data=data or {},
        )
        self.append_event(event)
        return event

    def cleanup_temporary_files(self) -> tuple[str, ...]:
        """Remove only known atomic-write leftovers and report relative paths."""
        removed: list[str] = []
        for path in self.path.rglob(".elarabench-*"):
            if path.is_file() and path.resolve().is_relative_to(self.path):
                removed.append(path.relative_to(self.path).as_posix())
                path.unlink()
        return tuple(sorted(removed))

    def artifacts_directory(self, identity: SampleIdentity) -> Path:
        """Return the deterministic per-sample derived-artifact directory."""
        path = self._sample_path(identity) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path
