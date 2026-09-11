"""Tests for the ElaraBench M0 package and CLI foundation."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

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
    assert elarabench.__version__ == "0.4.0"


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


def test_run_help_documents_thinking_and_timeout_policy() -> None:
    result = run_cli("run", "--help")

    assert result.returncode == 0
    assert "--think" in result.stdout
    assert "--no-think" in result.stdout
    assert "provider-default" in result.stdout
    assert "generation read timeout" in result.stdout
    assert "120" in result.stdout


def test_product_version_sources_agree() -> None:
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    assert project["project"]["version"] == elarabench.__version__
    assert importlib.metadata.version("elarabench") == elarabench.__version__


def test_all_user_arguments_have_help() -> None:
    import argparse

    from elarabench.cli import build_parser

    parser = build_parser()
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for command in commands.choices.values():
        for action in command._actions:
            assert action.help, (command.prog, action.dest)
    for name in commands.choices:
        assert run_cli(name, "--help").returncode == 0
