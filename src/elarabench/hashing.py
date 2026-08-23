"""Canonical serialization and SHA-256 identity helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from elarabench.models import (
    BenchmarkCase,
    BenchmarkSnapshot,
    BenchmarkSuite,
    EvaluationSpecification,
    GenerationRequest,
)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON-compatible value with stable keys and meaningful list order."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=False)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    """Return a lowercase SHA-256 hexadecimal digest."""
    return hashlib.sha256(content).hexdigest()


def hash_canonical(value: object) -> str:
    """Hash canonical JSON data."""
    return sha256_bytes(canonical_json_bytes(value))


def hash_case(case: BenchmarkCase) -> str:
    """Hash every validated, benchmark-defining case field."""
    return hash_canonical(case)


def hash_case_with_fixture_hashes(
    case: BenchmarkCase,
    fixture_hashes: Mapping[str, str],
) -> str:
    """Hash one complete case plus its immutable snapshot fixture identities.

    ``fixture_hashes`` must come from an already validated benchmark snapshot. Live suite
    files are deliberately not accepted here: comparison identity must describe the physical
    run evidence, not the current checkout.
    """
    missing = [reference for reference in case.fixtures if reference not in fixture_hashes]
    if missing:
        raise ValueError(
            f"snapshot fixture identity is unavailable for case {case.id!r}: {missing!r}"
        )
    fixtures = [
        {"path": reference, "sha256": fixture_hashes[reference]}
        for reference in case.fixtures
    ]
    return hash_canonical({"case": case.model_dump(mode="json"), "fixtures": fixtures})


def hash_generation_request(request: GenerationRequest) -> str:
    """Hash the complete provider-neutral request, preserving message order."""
    return hash_canonical(request)


def hash_evaluation_specification(specification: EvaluationSpecification) -> str:
    """Hash evaluator type, configuration, component order, and weights."""
    return hash_canonical(specification)


def hash_benchmark_snapshot(snapshot: BenchmarkSnapshot) -> str:
    """Hash every snapshot field except the self-referential snapshot hash."""
    return hash_canonical(snapshot.model_dump(mode="json", exclude={"snapshot_hash"}))


def hash_run_fingerprint(payload: object) -> str:
    """Hash a versioned logical execution identity payload."""
    return hash_canonical(payload)


def hash_suite(
    suite: BenchmarkSuite,
    fixture_files: Mapping[str, Path],
) -> str:
    """Hash validated suite semantics and referenced fixture bytes.

    Fixture keys are suite-relative POSIX paths. Absolute paths and filesystem metadata never
    participate in the identity.
    """
    hashes = {
        relative_path: sha256_bytes(path.read_bytes())
        for relative_path, path in fixture_files.items()
    }
    return hash_suite_from_fixture_hashes(suite, hashes)


def hash_suite_from_fixture_hashes(
    suite: BenchmarkSuite,
    fixture_hashes: Mapping[str, str],
) -> str:
    """Hash suite semantics and already-computed relative fixture identities."""
    fixtures: list[dict[str, Any]] = [
        {"path": path, "sha256": fixture_hashes[path]} for path in sorted(fixture_hashes)
    ]
    return hash_canonical({"suite": suite.model_dump(mode="json"), "fixtures": fixtures})
