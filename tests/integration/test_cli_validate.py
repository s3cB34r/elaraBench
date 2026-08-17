"""Installed CLI validation behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TINY_SUITE = Path(__file__).parents[1] / "fixtures" / "tiny_suite"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "elarabench.cli", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_validate_succeeds_for_valid_suite() -> None:
    result = run_cli("validate", str(TINY_SUITE))

    assert result.returncode == 0
    assert "Suite: synthetic.tiny" in result.stdout
    assert "Version: 1.0.0" in result.stdout
    assert "Cases: 5" in result.stdout
    assert "Content hash:" in result.stdout
    assert result.stderr == ""


def test_validate_fails_cleanly_for_invalid_suite(tmp_path: Path) -> None:
    suite = tmp_path / "invalid"
    suite.mkdir()
    (suite / "suite.yaml").write_text("schema_version: 99\n", encoding="utf-8")
    result = run_cli("validate", str(suite))

    assert result.returncode != 0
    assert result.stdout == ""
    assert "unsupported benchmark schema_version 99" in result.stderr


@pytest.mark.parametrize(
    ("suite_id", "case_count"),
    (
        ("reasoning.core", 18),
        ("instruction_following.core", 18),
        ("coding.core", 12),
        ("cybersecurity.core", 12),
    ),
)
def test_validate_resolves_bundled_suite_id(suite_id: str, case_count: int) -> None:
    result = run_cli("validate", suite_id)

    assert result.returncode == 0
    assert f"Suite: {suite_id}" in result.stdout
    assert "Version: 1.0.0" in result.stdout
    assert f"Cases: {case_count}" in result.stdout
    assert "Content hash:" in result.stdout
    assert result.stderr == ""
