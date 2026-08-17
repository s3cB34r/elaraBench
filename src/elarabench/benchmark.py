"""Loading and validation for versioned ElaraBench benchmark suites."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import cast

import yaml
from pydantic import ValidationError

from elarabench.evaluators.base import EvaluatorConfigurationError
from elarabench.evaluators.registry import validate_specification
from elarabench.hashing import (
    hash_benchmark_snapshot,
    hash_suite,
    hash_suite_from_fixture_hashes,
    sha256_bytes,
)
from elarabench.models import (
    BenchmarkCase,
    BenchmarkSnapshot,
    BenchmarkSuite,
    BenchmarkSuiteManifest,
    SnapshotFixture,
)


class BenchmarkLoadError(ValueError):
    """A benchmark suite could not be safely loaded or validated."""


@dataclass(frozen=True)
class LoadedBenchmarkSuite:
    """A validated suite plus its stable identity and resolved fixture files."""

    suite_dir: Path
    suite: BenchmarkSuite
    content_hash: str
    fixture_files: MappingProxyType[str, Path]


def _format_validation_error(path: Path, error: ValidationError) -> BenchmarkLoadError:
    details = "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_url=False)
    )
    return BenchmarkLoadError(f"validation failed for {path}: {details}")


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise BenchmarkLoadError(f"cannot read suite manifest {path}: {error}") from error
    except yaml.YAMLError as error:
        raise BenchmarkLoadError(f"malformed YAML in {path}: {error}") from error
    if not isinstance(loaded, dict):
        raise BenchmarkLoadError(f"suite manifest {path} must contain a YAML mapping")
    return cast(dict[str, object], loaded)


def _safe_suite_file(
    suite_dir: Path,
    reference: str,
    *,
    kind: str,
    require_fixtures_directory: bool = False,
) -> tuple[str, Path]:
    if "\\" in reference:
        raise BenchmarkLoadError(f"{kind} path must use portable '/' separators: {reference!r}")
    relative = PurePosixPath(reference)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise BenchmarkLoadError(f"unsafe {kind} path outside suite directory: {reference!r}")
    if require_fixtures_directory and relative.parts[0] != "fixtures":
        raise BenchmarkLoadError(f"fixture path must be under fixtures/: {reference!r}")

    root = suite_dir.resolve()
    candidate = (root / Path(*relative.parts)).resolve()
    if not candidate.is_relative_to(root):
        raise BenchmarkLoadError(f"unsafe {kind} path outside suite directory: {reference!r}")
    if not candidate.is_file():
        raise BenchmarkLoadError(f"missing {kind} file: {reference!r}")
    return relative.as_posix(), candidate


def _load_cases(path: Path) -> tuple[BenchmarkCase, ...]:
    cases: list[BenchmarkCase] = []
    seen: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise BenchmarkLoadError(f"cannot read case file {path}: {error}") from error

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw_case = json.loads(line)
        except json.JSONDecodeError as error:
            raise BenchmarkLoadError(
                f"malformed JSON in {path} at line {line_number}, column {error.colno}: "
                f"{error.msg}"
            ) from error
        try:
            case = BenchmarkCase.model_validate(raw_case)
        except ValidationError as error:
            raise _format_validation_error(Path(f"{path}:{line_number}"), error) from error
        if case.id in seen:
            raise BenchmarkLoadError(
                f"duplicate case ID {case.id!r} in {path} at lines {seen[case.id]} and "
                f"{line_number}"
            )
        try:
            validate_specification(case.evaluation)
        except EvaluatorConfigurationError as error:
            raise BenchmarkLoadError(
                f"invalid evaluator for case {case.id!r} in {path}:{line_number}: {error}"
            ) from error
        seen[case.id] = line_number
        cases.append(case)
    if not cases:
        raise BenchmarkLoadError(f"case file {path} contains no benchmark cases")
    return tuple(cases)


def load_benchmark_suite(path: str | Path) -> LoadedBenchmarkSuite:
    """Load, validate, safely resolve, and canonically hash a benchmark suite."""
    supplied_path = Path(path)
    manifest_path = (
        supplied_path if supplied_path.name == "suite.yaml" else supplied_path / "suite.yaml"
    )
    suite_dir = manifest_path.parent.resolve()
    if not manifest_path.is_file():
        raise BenchmarkLoadError(f"suite manifest not found: {manifest_path}")

    raw_manifest = _load_yaml_mapping(manifest_path)
    schema_version = raw_manifest.get("schema_version")
    if schema_version != 1:
        raise BenchmarkLoadError(
            f"unsupported benchmark schema_version {schema_version!r} in {manifest_path}; "
            "supported versions: 1"
        )
    try:
        manifest = BenchmarkSuiteManifest.model_validate(raw_manifest)
    except ValidationError as error:
        raise _format_validation_error(manifest_path, error) from error

    _, case_path = _safe_suite_file(
        suite_dir,
        manifest.case_source.path,
        kind="case",
    )
    cases = _load_cases(case_path)
    suite_data = manifest.model_dump(mode="python", exclude={"case_source"})
    suite = BenchmarkSuite.model_validate({**suite_data, "cases": cases})

    fixtures: dict[str, Path] = {}
    for case in cases:
        for reference in case.fixtures:
            relative, fixture_path = _safe_suite_file(
                suite_dir,
                reference,
                kind="fixture",
                require_fixtures_directory=True,
            )
            fixtures[relative] = fixture_path

    fixture_files = MappingProxyType(fixtures)
    return LoadedBenchmarkSuite(
        suite_dir=suite_dir,
        suite=suite,
        content_hash=hash_suite(suite, fixture_files),
        fixture_files=fixture_files,
    )


def create_benchmark_snapshot(
    loaded: LoadedBenchmarkSuite,
    *,
    minimum_scored_coverage: float | None = None,
) -> BenchmarkSnapshot:
    """Embed complete validated benchmark definitions and immutable fixture bytes."""
    fixtures: list[SnapshotFixture] = []
    for relative_path, path in sorted(loaded.fixture_files.items()):
        try:
            content = path.read_bytes()
        except OSError as error:
            raise BenchmarkLoadError(
                f"cannot snapshot fixture {relative_path!r}: {error}"
            ) from error
        fixtures.append(
            SnapshotFixture(
                path=relative_path,
                content_hash=sha256_bytes(content),
                content_base64=base64.b64encode(content).decode("ascii"),
            )
        )
    provisional = BenchmarkSnapshot(
        suite=loaded.suite,
        fixtures=tuple(fixtures),
        minimum_scored_coverage=(
            loaded.suite.aggregation.minimum_scored_coverage
            if minimum_scored_coverage is None
            else minimum_scored_coverage
        ),
        benchmark_content_hash=loaded.content_hash,
        snapshot_hash="0" * 64,
    )
    return provisional.model_copy(update={"snapshot_hash": hash_benchmark_snapshot(provisional)})


def validate_benchmark_snapshot(snapshot: BenchmarkSnapshot) -> None:
    """Verify self-hash, fixture bytes, and benchmark content identity."""
    if hash_benchmark_snapshot(snapshot) != snapshot.snapshot_hash:
        raise BenchmarkLoadError("benchmark snapshot hash mismatch")
    fixture_hashes: dict[str, str] = {}
    for fixture in snapshot.fixtures:
        relative = PurePosixPath(fixture.path)
        if (
            "\\" in fixture.path
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0] != "fixtures"
        ):
            raise BenchmarkLoadError(f"unsafe snapshot fixture path {fixture.path!r}")
        if fixture.path in fixture_hashes:
            raise BenchmarkLoadError(f"duplicate snapshot fixture {fixture.path!r}")
        try:
            content = base64.b64decode(fixture.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise BenchmarkLoadError(f"invalid base64 fixture {fixture.path!r}") from error
        if sha256_bytes(content) != fixture.content_hash:
            raise BenchmarkLoadError(f"fixture content hash mismatch for {fixture.path!r}")
        fixture_hashes[fixture.path] = fixture.content_hash
    referenced = {reference for case in snapshot.suite.cases for reference in case.fixtures}
    if set(fixture_hashes) != referenced:
        raise BenchmarkLoadError("snapshot fixtures do not match benchmark references")
    content_hash = hash_suite_from_fixture_hashes(snapshot.suite, fixture_hashes)
    if content_hash != snapshot.benchmark_content_hash:
        raise BenchmarkLoadError("benchmark snapshot content hash mismatch")
