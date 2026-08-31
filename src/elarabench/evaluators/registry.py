"""Explicit built-in evaluator mapping and dispatch."""

from __future__ import annotations

from elarabench.action_compliance import ActionComplianceEvaluator
from elarabench.evaluators.base import ConfiguredEvaluator, EvaluatorConfigurationError, make_result
from elarabench.evaluators.builtin import (
    CompositeEvaluator,
    ExactMatchEvaluator,
    ForbiddenContentEvaluator,
    JsonParsingEvaluator,
    JsonSchemaEvaluator,
    MultipleChoiceEvaluator,
    NormalizedMatchEvaluator,
    NumericEvaluator,
    RegexEvaluator,
    RequiredContentEvaluator,
)
from elarabench.models import (
    EvaluationContext,
    EvaluationResult,
    EvaluationSpecification,
    EvaluationStatus,
)
from elarabench.refusal_compliance import RefusalComplianceEvaluator

_EVALUATORS: dict[str, ConfiguredEvaluator] = {
    "exact_match": ExactMatchEvaluator(),
    "normalized_match": NormalizedMatchEvaluator(),
    "numeric": NumericEvaluator(),
    "multiple_choice": MultipleChoiceEvaluator(),
    "regex_full_match": RegexEvaluator(),
    "json_parse": JsonParsingEvaluator(),
    "json_schema": JsonSchemaEvaluator(),
    "required_content": RequiredContentEvaluator(),
    "forbidden_content": ForbiddenContentEvaluator(),
    "refusal_compliance": RefusalComplianceEvaluator(),
    "action_compliance": ActionComplianceEvaluator(),
}


def _evaluator(specification: EvaluationSpecification) -> ConfiguredEvaluator:
    if specification.type == "composite":
        return CompositeEvaluator(evaluate, validate_specification)
    try:
        return _EVALUATORS[specification.type]
    except KeyError as error:
        supported = ", ".join(sorted((*_EVALUATORS, "composite")))
        raise EvaluatorConfigurationError(
            f"unknown evaluator type {specification.type!r}; supported types: {supported}"
        ) from error


def validate_specification(specification: EvaluationSpecification) -> None:
    """Validate an evaluator and every composite child through the explicit mapping."""
    _evaluator(specification).validate_specification(specification)


def resolve_evaluator_identity(
    specification: EvaluationSpecification,
) -> tuple[str, str]:
    """Validate and return deterministic current-registry evaluator identity."""
    evaluator = _evaluator(specification)
    evaluator.validate_specification(specification)
    return evaluator.name, evaluator.version


def evaluate(context: EvaluationContext) -> EvaluationResult:
    """Dispatch evaluation and classify model, benchmark, and technical failures."""
    try:
        evaluator = _evaluator(context.specification)
    except EvaluatorConfigurationError as error:
        if context.response.error is not None:
            return make_result(
                context,
                evaluator_name=context.specification.type,
                evaluator_version="unknown",
                status=EvaluationStatus.ERROR,
                explanation=(
                    f"generation failed ({context.response.error.code}): "
                    f"{context.response.error.message}"
                ),
            )
        return make_result(
            context,
            evaluator_name=context.specification.type,
            evaluator_version="unknown",
            status=EvaluationStatus.INVALID,
            explanation=str(error),
        )

    if context.response.error is not None:
        return make_result(
            context,
            evaluator_name=evaluator.name,
            evaluator_version=evaluator.version,
            status=EvaluationStatus.ERROR,
            explanation=(
                f"generation failed ({context.response.error.code}): "
                f"{context.response.error.message}"
            ),
        )
    try:
        evaluator.validate_specification(context.specification)
        return evaluator.evaluate(context)
    except EvaluatorConfigurationError as error:
        return make_result(
            context,
            evaluator_name=evaluator.name,
            evaluator_version=evaluator.version,
            status=EvaluationStatus.INVALID,
            explanation=str(error),
        )
    except Exception as error:
        return make_result(
            context,
            evaluator_name=evaluator.name,
            evaluator_version=evaluator.version,
            status=EvaluationStatus.ERROR,
            explanation=(
                f"evaluator failed unexpectedly: {type(error).__name__}: {error}"
            ),
        )
