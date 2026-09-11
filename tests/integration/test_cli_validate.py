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
        ("refusal_compliance.core", 54),
        ("action_compliance.core", 36),
        ("action_recovery.core", 36),
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


@pytest.mark.parametrize("reference", ["reasonign.core", "missing/custom/path"])
def test_missing_reference_adds_discovery_guidance(reference: str) -> None:
    result = run_cli("validate", reference)
    assert result.returncode == 2
    assert "suite manifest not found" in result.stderr
    assert reference in result.stderr
    assert "elarabench list" in result.stderr


def test_existing_builtin_looking_custom_directory_is_preserved(tmp_path: Path) -> None:
    import shutil

    suite = tmp_path / "custom.core"
    shutil.copytree(TINY_SUITE, suite)
    result = run_cli("validate", str(suite))
    assert result.returncode == 0
    assert "synthetic.tiny" in result.stdout


def test_existing_malformed_custom_suite_keeps_validation_error(tmp_path: Path) -> None:
    suite = tmp_path / "malformed.core"
    suite.mkdir()
    (suite / "suite.yaml").write_text("schema_version: 99\n")
    result = run_cli("validate", str(suite))
    assert result.returncode == 2
    assert "unsupported benchmark schema_version" in result.stderr
    assert "elarabench list" not in result.stderr
