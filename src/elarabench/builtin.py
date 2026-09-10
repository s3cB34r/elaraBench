"""Stable lookup for first-party benchmark suites bundled with ElaraBench."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Final


class BuiltinSuiteError(ValueError):
    """A requested built-in suite is unknown or missing from the installation."""


_BUILTIN_SUITE_PATHS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "reasoning.core": ("reasoning", "core-v1"),
        "instruction_following.core": ("instruction_following", "core-v1"),
        "coding.core": ("coding", "core-v1"),
        "cybersecurity.core": ("cybersecurity", "core-v1"),
        "refusal_compliance.core": ("refusal_compliance", "core-v1"),
        "action_compliance.core": ("action_compliance", "core-v1"),
        "action_recovery.core": ("action_recovery", "core-v1"),
        "reactive_execution.core": ("reactive_execution", "core-v1"),
        "reactive_failure.core": ("reactive_failure", "core-v1"),
    }
)


def available_builtin_suites() -> tuple[str, ...]:
    """Return built-in suite IDs in stable display order."""
    return tuple(_BUILTIN_SUITE_PATHS)


def get_builtin_suite_path(suite_id: str) -> Path:
    """Return the filesystem path for a bundled first-party suite.

    Normal source and wheel installations expose package resources as real files. A clear error
    is raised for an unknown ID, missing distribution data, or a non-filesystem import loader.
    """
    try:
        relative = _BUILTIN_SUITE_PATHS[suite_id]
    except KeyError as error:
        available = ", ".join(available_builtin_suites())
        raise BuiltinSuiteError(
            f"unknown built-in suite {suite_id!r}; available suites: {available}"
        ) from error

    resource = files("elarabench").joinpath("builtin_benchmarks", *relative)
    if not isinstance(resource, Path):
        raise BuiltinSuiteError(
            "built-in suite resources are not filesystem-backed; install ElaraBench from its "
            "wheel rather than importing directly from a compressed archive"
        )
    if not resource.joinpath("suite.yaml").is_file() or not resource.joinpath(
        "cases.jsonl"
    ).is_file():
        raise BuiltinSuiteError(
            f"built-in suite {suite_id!r} is missing from the ElaraBench installation"
        )
    return resource


def resolve_suite_path(reference: str | Path) -> Path:
    """Resolve a built-in suite ID, otherwise preserve a user-supplied filesystem path."""
    suite_id = str(reference)
    if suite_id in _BUILTIN_SUITE_PATHS:
        return get_builtin_suite_path(suite_id)
    return Path(reference)
