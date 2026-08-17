"""Tests for the ElaraBench M0 package and CLI foundation."""

from __future__ import annotations

import subprocess
import sys

import elarabench


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI module in the active test interpreter."""
    return subprocess.run(
        [sys.executable, "-m", "elarabench.cli", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_package_imports_and_exposes_version() -> None:
    assert elarabench.__version__ == "0.0.0"


def test_cli_help_succeeds() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "usage: elarabench" in result.stdout
    assert "--version" in result.stdout
    assert result.stderr == ""


def test_cli_version_succeeds() -> None:
    result = run_cli("--version")

    assert result.returncode == 0
    assert result.stdout.strip() == f"elarabench {elarabench.__version__}"
    assert result.stderr == ""
