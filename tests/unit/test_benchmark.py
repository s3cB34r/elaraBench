"""Benchmark loader, validation, fixture safety, and suite identity tests."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from elarabench.benchmark import (
    BenchmarkLoadError,
    create_benchmark_snapshot,
    load_benchmark_suite,
    validate_benchmark_snapshot,
)
from elarabench.hashing import hash_benchmark_snapshot
from elarabench.models import ThinkingPolicy

TINY_SUITE = Path(__file__).parents[1] / "fixtures" / "tiny_suite"


def copy_suite(tmp_path: Path) -> Path:
    destination = tmp_path / "suite"
    shutil.copytree(TINY_SUITE, destination)
    return destination


def read_cases(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (path / "cases.jsonl").read_text().splitlines()]


def write_cases(path: Path, cases: list[dict[str, object]]) -> None:
    content = "\n".join(json.dumps(case, separators=(",", ":")) for case in cases) + "\n"
    (path / "cases.jsonl").write_text(content, encoding="utf-8")


def test_valid_suite_loads_and_preserves_case_order() -> None:
    loaded = load_benchmark_suite(TINY_SUITE)

    assert loaded.suite.id == "synthetic.tiny"
    assert loaded.suite.version == "1.0.0"
    assert [case.id for case in loaded.suite.cases] == [
        "exact-001",
        "numeric-001",
        "choice-001",
        "json-001",
        "content-001",
    ]
    assert len(loaded.content_hash) == 64
    assert list(loaded.fixture_files) == ["fixtures/context.txt"]


def test_suite_may_define_provider_neutral_thinking_default(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    manifest_path = suite / "suite.yaml"
    manifest = manifest_path.read_text(encoding="utf-8").replace(
        "  timeout_seconds: 120",
        "  timeout_seconds: 120\n  thinking: enabled",
    )
    manifest_path.write_text(manifest, encoding="utf-8")

    loaded = load_benchmark_suite(suite)

    assert loaded.suite.defaults.thinking is ThinkingPolicy.ENABLED


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    cases = read_cases(suite)
    cases[1]["id"] = cases[0]["id"]
    write_cases(suite, cases)

    with pytest.raises(BenchmarkLoadError, match="duplicate case ID"):
        load_benchmark_suite(suite)


def test_malformed_jsonl_is_rejected_with_line_number(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    with (suite / "cases.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    with pytest.raises(BenchmarkLoadError, match=r"line 6, column"):
        load_benchmark_suite(suite)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    manifest = (suite / "suite.yaml").read_text().replace("schema_version: 1", "schema_version: 2")
    (suite / "suite.yaml").write_text(manifest, encoding="utf-8")

    with pytest.raises(BenchmarkLoadError, match="unsupported benchmark schema_version 2"):
        load_benchmark_suite(suite)


def test_missing_fixture_is_rejected(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "fixtures" / "context.txt").unlink()

    with pytest.raises(BenchmarkLoadError, match="missing fixture file"):
        load_benchmark_suite(suite)


@pytest.mark.parametrize("fixture", ["../outside.txt", "/tmp/outside.txt", "other/file.txt"])
def test_fixture_escape_or_wrong_directory_is_rejected(tmp_path: Path, fixture: str) -> None:
    suite = copy_suite(tmp_path)
    cases = read_cases(suite)
    cases[0]["fixtures"] = [fixture]
    write_cases(suite, cases)

    with pytest.raises(BenchmarkLoadError, match="fixture path"):
        load_benchmark_suite(suite)


def test_fixture_symlink_escape_is_rejected(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    fixture = suite / "fixtures" / "context.txt"
    fixture.unlink()
    fixture.symlink_to(outside)

    with pytest.raises(BenchmarkLoadError, match="outside suite directory"):
        load_benchmark_suite(suite)


def test_unknown_evaluator_is_rejected_during_suite_validation(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    cases = read_cases(suite)
    evaluation = cases[0]["evaluation"]
    assert isinstance(evaluation, dict)
    evaluation["type"] = "does_not_exist"
    write_cases(suite, cases)

    with pytest.raises(BenchmarkLoadError, match="unknown evaluator type"):
        load_benchmark_suite(suite)


def test_fixture_content_changes_hash_but_mtime_does_not(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    fixture = suite / "fixtures" / "context.txt"
    first = load_benchmark_suite(suite).content_hash
    stat = fixture.stat()
    os.utime(fixture, (stat.st_atime + 100, stat.st_mtime + 100))
    assert load_benchmark_suite(suite).content_hash == first

    fixture.write_text("changed fixture bytes\n", encoding="utf-8")
    assert load_benchmark_suite(suite).content_hash != first


def test_snapshot_is_self_contained_and_fixture_corruption_is_rejected() -> None:
    snapshot = create_benchmark_snapshot(load_benchmark_suite(TINY_SUITE))

    validate_benchmark_snapshot(snapshot)
    assert snapshot.benchmark_content_hash
    assert snapshot.snapshot_hash
    assert snapshot.fixtures[0].content_base64

    changed_fixture = snapshot.fixtures[0].model_copy(update={"content_base64": "Y2hhbmdlZA=="})
    provisional = snapshot.model_copy(
        update={"fixtures": (changed_fixture,), "snapshot_hash": "0" * 64}
    )
    corrupted = provisional.model_copy(
        update={"snapshot_hash": hash_benchmark_snapshot(provisional)}
    )
    with pytest.raises(BenchmarkLoadError, match="fixture content hash mismatch"):
        validate_benchmark_snapshot(corrupted)
