"""Atomic filesystem storage primitives for canonical and derived run artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from elarabench.models import (
    AggregationSummary,
    EvaluationResult,
    GenerationRequest,
    GenerationResponse,
    Identifier,
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
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ArtifactStoreError(f"invalid artifact {path}: {error}") from error


class ArtifactStore:
    """Factory for deterministic run directories beneath one configured root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, run_id: str) -> RunArtifactStore:
        """Open a run after validating its identifier through the domain model."""
        try:
            safe_run_id = _IDENTIFIER_ADAPTER.validate_python(run_id)
        except ValidationError as error:
            raise ArtifactStoreError(f"unsafe run ID {run_id!r}") from error
        return RunArtifactStore(self.root, safe_run_id)


class RunArtifactStore:
    """Read and atomically write one run's canonical and derived artifacts."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = root / run_id
        self.path.mkdir(parents=True, exist_ok=True)
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
        """Atomically replace the reproducible derived summary."""
        _atomic_write(self.path / "summary.json", _json_bytes(summary), replace=True)

    def read_summary(self) -> AggregationSummary:
        return _read_model(self.path / "summary.json", AggregationSummary)

    def append_event(self, event: dict[str, JsonValue]) -> None:
        """Append a structured lifecycle event for future runner use."""
        path = self.path / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def artifacts_directory(self, identity: SampleIdentity) -> Path:
        """Return the deterministic per-sample derived-artifact directory."""
        path = self._sample_path(identity) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path
