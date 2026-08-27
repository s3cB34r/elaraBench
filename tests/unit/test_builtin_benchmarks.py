"""Stable source and installed-resource lookup for first-party suites."""

from __future__ import annotations

from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import (
    BuiltinSuiteError,
    available_builtin_suites,
    get_builtin_suite_path,
    resolve_suite_path,
)

EXPECTED = {
    "reasoning.core": (
        18,
        "76e8699add4c94921e40215b85b2b8870abf25023dff02bff92b0a51e6021b3c",
    ),
    "instruction_following.core": (
        18,
        "2dc75a0d7fa60c0503e1430cb797d35f7310cff1e325b8468b2d92bdaac10b39",
    ),
    "coding.core": (
        12,
        "0f1c5d78434ed711d7759eff8c5e727553a65364d2006b788f5710f5a91d7d94",
    ),
    "cybersecurity.core": (
        12,
        "61f0ce35f487ed1ad9c7cf10f7feaa5bd233ad5ceb0885b2bd1f940eb46d1ba3",
    ),
    "refusal_compliance.core": (
        54,
        "efb6802abfb536629c82380568b8e7c5b74cac2d56314a7cdd0cec10760ee7a9",
    ),
}


def test_available_builtin_suites_have_stable_order() -> None:
    assert available_builtin_suites() == (
        "reasoning.core",
        "instruction_following.core",
        "coding.core",
        "cybersecurity.core",
        "refusal_compliance.core",
    )
    assert len(EXPECTED) == 5
    assert sum(case_count for case_count, _ in EXPECTED.values()) == 114


@pytest.mark.parametrize(("suite_id", "expected"), EXPECTED.items())
def test_builtin_suite_lookup_validates_independently_of_cwd(
    suite_id: str,
    expected: tuple[int, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    path = get_builtin_suite_path(suite_id)
    loaded = load_benchmark_suite(path)

    assert path.is_absolute()
    assert "tests" not in path.parts
    assert loaded.suite.id == suite_id
    assert len(loaded.suite.cases) == expected[0]
    assert loaded.content_hash == expected[1]


def test_unknown_builtin_suite_fails_clearly() -> None:
    with pytest.raises(BuiltinSuiteError, match=r"unknown built-in suite 'unknown\.core'"):
        get_builtin_suite_path("unknown.core")


def test_builtin_id_resolution_wins_over_cwd_name_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "reasoning.core").mkdir()
    monkeypatch.chdir(tmp_path)

    assert resolve_suite_path("reasoning.core") == get_builtin_suite_path("reasoning.core")


def test_builtin_corpus_carries_its_cc0_license() -> None:
    corpus_root = get_builtin_suite_path("reasoning.core").parents[1]

    assert (corpus_root / "LICENSE").is_file()
    assert "CC0 1.0 Universal" in (corpus_root / "LICENSE").read_text(encoding="utf-8")
