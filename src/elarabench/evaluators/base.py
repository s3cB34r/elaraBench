"""Narrow evaluator contract and shared deterministic result helpers."""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from elarabench.hashing import hash_evaluation_specification
from elarabench.models import (
    EvaluationContext,
    EvaluationResult,
    EvaluationSpecification,
    EvaluationStatus,
)


class EvaluatorConfigurationError(ValueError):
    """An evaluation specification is unknown or incorrectly configured."""


class Evaluator(Protocol):
    """Score stored response evidence without invoking a provider."""

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        """Evaluate a response using the context specification."""
        ...


class ConfiguredEvaluator(Evaluator, Protocol):
    """Evaluator behavior used by the explicit built-in registry."""

    name: str
    version: str

    def validate_specification(self, specification: EvaluationSpecification) -> None:
        """Raise a clear error for invalid configuration."""
        ...


def make_result(
    context: EvaluationContext,
    *,
    evaluator_name: str,
    evaluator_version: str,
    status: EvaluationStatus,
    explanation: str,
    score: float | None = None,
    passed: bool | None = None,
    metrics: dict[str, float] | None = None,
    artifacts: dict[str, JsonValue] | None = None,
) -> EvaluationResult:
    """Create a result with mandatory evaluator configuration provenance."""
    return EvaluationResult(
        status=status,
        score=score,
        passed=passed,
        metrics=metrics or {},
        explanation=explanation,
        evaluator_name=evaluator_name,
        evaluator_version=evaluator_version,
        configuration_hash=hash_evaluation_specification(context.specification),
        artifacts=artifacts or {},
        source_result_schema_version=context.source_result_schema_version,
    )
