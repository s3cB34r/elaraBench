"""Explicit built-in evaluator mapping and dispatch."""

from __future__ import annotations

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


def evaluate(context: EvaluationContext) -> EvaluationResult:
    """Dispatch a validated specification and preserve provider failures as errors."""
    evaluator = _evaluator(context.specification)
    evaluator.validate_specification(context.specification)
    if context.response.error is not None:
        return make_result(
            context.specification,
            evaluator_name=evaluator.name,
            evaluator_version=evaluator.version,
            status=EvaluationStatus.ERROR,
            explanation=(
                f"generation failed ({context.response.error.code}): "
                f"{context.response.error.message}"
            ),
        )
    return evaluator.evaluate(context)
