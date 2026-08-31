"""Built-in deterministic evaluator implementations."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import ClassVar, Literal, TypeVar, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from elarabench.evaluators.base import EvaluatorConfigurationError, make_result
from elarabench.models import (
    EvaluationContext,
    EvaluationResult,
    EvaluationSpecification,
    EvaluationStatus,
)

ConfigT = TypeVar("ConfigT", bound=BaseModel)


class EvaluatorConfig(BaseModel):
    """Strict immutable evaluator configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizationOperation(StrEnum):
    """Allowed deterministic text transformations, applied in listed order."""

    STRIP = "strip"
    LOWERCASE = "lowercase"
    COLLAPSE_WHITESPACE = "collapse_whitespace"


class ExactMatchConfig(EvaluatorConfig):
    expected: str


class NormalizedMatchConfig(EvaluatorConfig):
    expected: str
    operations: tuple[NormalizationOperation, ...] = (
        NormalizationOperation.STRIP,
        NormalizationOperation.COLLAPSE_WHITESPACE,
    )


class NumericConfig(EvaluatorConfig):
    expected: Decimal
    tolerance: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_finite_values(self) -> NumericConfig:
        if not self.expected.is_finite():
            raise ValueError("expected must be finite")
        if not self.tolerance.is_finite():
            raise ValueError("tolerance must be finite")
        return self


class MultipleChoiceConfig(EvaluatorConfig):
    expected: str
    choices: tuple[str, ...] = Field(min_length=2)
    operations: tuple[NormalizationOperation, ...] = (NormalizationOperation.STRIP,)

    @model_validator(mode="after")
    def validate_expected_choice(self) -> MultipleChoiceConfig:
        normalized = [normalize_text(choice, self.operations) for choice in self.choices]
        if len(normalized) != len(set(normalized)):
            raise ValueError("choices must remain unique after normalization")
        if normalize_text(self.expected, self.operations) not in normalized:
            raise ValueError("expected must be one of choices after normalization")
        return self


class RegexFlag(StrEnum):
    IGNORECASE = "ignorecase"
    MULTILINE = "multiline"
    DOTALL = "dotall"


class RegexConfig(EvaluatorConfig):
    pattern: str
    flags: tuple[RegexFlag, ...] = ()

    @model_validator(mode="after")
    def validate_pattern(self) -> RegexConfig:
        try:
            re.compile(self.pattern, regex_flags(self.flags))
        except re.error as error:
            raise ValueError(f"invalid regular expression: {error}") from error
        return self


class JsonParsingConfig(EvaluatorConfig):
    pass


class JsonSchemaConfig(EvaluatorConfig):
    schema_definition: dict[str, JsonValue] = Field(alias="schema")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def validate_schema(self) -> JsonSchemaConfig:
        validate_json_schema_definition(self.schema_definition)
        return self


class RequiredContentConfig(EvaluatorConfig):
    required: tuple[str, ...] = Field(min_length=1)
    case_sensitive: bool = True
    mode: Literal["all", "any"] = "all"

    @model_validator(mode="after")
    def validate_terms(self) -> RequiredContentConfig:
        if any(term == "" for term in self.required):
            raise ValueError("required content terms must not be empty")
        return self


class ForbiddenContentConfig(EvaluatorConfig):
    forbidden: tuple[str, ...] = Field(min_length=1)
    case_sensitive: bool = True

    @model_validator(mode="after")
    def validate_terms(self) -> ForbiddenContentConfig:
        if any(term == "" for term in self.forbidden):
            raise ValueError("forbidden content terms must not be empty")
        return self


def normalize_text(value: str, operations: tuple[NormalizationOperation, ...]) -> str:
    """Apply explicit deterministic normalization operations in order."""
    for operation in operations:
        if operation is NormalizationOperation.STRIP:
            value = value.strip()
        elif operation is NormalizationOperation.LOWERCASE:
            value = value.lower()
        elif operation is NormalizationOperation.COLLAPSE_WHITESPACE:
            value = " ".join(value.split())
    return value


def regex_flags(flags: tuple[RegexFlag, ...]) -> re.RegexFlag:
    """Convert explicit portable names to Python regular-expression flags."""
    result = re.RegexFlag(0)
    mapping = {
        RegexFlag.IGNORECASE: re.IGNORECASE,
        RegexFlag.MULTILINE: re.MULTILINE,
        RegexFlag.DOTALL: re.DOTALL,
    }
    for flag in flags:
        result |= mapping[flag]
    return result


def validate_json_schema_definition(
    schema_definition: dict[str, JsonValue], *, label: str = "JSON Schema"
) -> None:
    """Validate a Draft 2020-12 schema for deterministic evaluator reuse."""
    try:
        Draft202012Validator.check_schema(schema_definition)
    except SchemaError as error:
        raise ValueError(f"invalid {label}: {error.message}") from error


def json_schema_validation_errors(
    instance: JsonValue, schema_definition: dict[str, JsonValue]
) -> list[str]:
    """Return deterministically ordered Draft 2020-12 validation messages."""
    errors = sorted(
        Draft202012Validator(schema_definition).iter_errors(instance),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    return [error.message for error in errors]


def _configuration(
    specification: EvaluationSpecification,
    model: type[ConfigT],
) -> ConfigT:
    try:
        return model.model_validate(specification.config)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_url=False)
        )
        raise EvaluatorConfigurationError(
            f"invalid {specification.type} configuration: {details}"
        ) from error


class SimpleEvaluator:
    """Shared validation rules for non-composite deterministic evaluators."""

    name: str
    version: str = "1.1.0"
    config_model: ClassVar[type[EvaluatorConfig]]

    def validate_specification(self, specification: EvaluationSpecification) -> None:
        if specification.components:
            raise EvaluatorConfigurationError(
                f"{specification.type} does not accept composite components"
            )
        _configuration(specification, self.config_model)


class ExactMatchEvaluator(SimpleEvaluator):
    name = "exact_match"
    config_model = ExactMatchConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, ExactMatchConfig)
        passed = context.response.text == config.expected
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=float(passed),
            passed=passed,
            explanation=(
                "output exactly matched expected text" if passed else "output did not exactly match"
            ),
        )


class NormalizedMatchEvaluator(SimpleEvaluator):
    name = "normalized_match"
    config_model = NormalizedMatchConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, NormalizedMatchConfig)
        actual = normalize_text(context.response.text, config.operations)
        expected = normalize_text(config.expected, config.operations)
        passed = actual == expected
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=float(passed),
            passed=passed,
            explanation=(
                "normalized output matched" if passed else "normalized output did not match"
            ),
        )


class NumericEvaluator(SimpleEvaluator):
    name = "numeric"
    config_model = NumericConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, NumericConfig)
        try:
            actual = Decimal(context.response.text.strip())
        except InvalidOperation:
            return self._format_failure(
                context,
                "output does not satisfy the required numeric-only format",
            )
        if not actual.is_finite():
            return self._format_failure(
                context,
                "output does not satisfy the required finite numeric format",
            )
        difference = abs(actual - config.expected)
        passed = difference <= config.tolerance
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=float(passed),
            passed=passed,
            metrics={"absolute_error": float(difference)},
            explanation=(
                "numeric output is within tolerance"
                if passed
                else "numeric output is outside tolerance"
            ),
        )
    def _format_failure(
        self, context: EvaluationContext, explanation: str
    ) -> EvaluationResult:
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=0.0,
            passed=False,
            explanation=explanation,
        )


class MultipleChoiceEvaluator(SimpleEvaluator):
    name = "multiple_choice"
    config_model = MultipleChoiceConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, MultipleChoiceConfig)
        actual = normalize_text(context.response.text, config.operations)
        choices = tuple(normalize_text(choice, config.operations) for choice in config.choices)
        if actual not in choices:
            return make_result(
                context,
                evaluator_name=self.name,
                evaluator_version=self.version,
                status=EvaluationStatus.SCORED,
                score=0.0,
                passed=False,
                explanation="output is not one of the configured choices",
            )
        expected = normalize_text(config.expected, config.operations)
        passed = actual == expected
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=float(passed),
            passed=passed,
            explanation=(
                "selected expected choice" if passed else "selected a different valid choice"
            ),
        )


class RegexEvaluator(SimpleEvaluator):
    name = "regex_full_match"
    config_model = RegexConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, RegexConfig)
        passed = re.fullmatch(
            config.pattern,
            context.response.text,
            regex_flags(config.flags),
        ) is not None
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=float(passed),
            passed=passed,
            explanation=(
                "output fully matched regex" if passed else "output did not fully match regex"
            ),
        )


class JsonParsingEvaluator(SimpleEvaluator):
    name = "json_parse"
    config_model = JsonParsingConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        _configuration(context.specification, self.config_model)
        try:
            json.loads(context.response.text)
        except json.JSONDecodeError as error:
            return make_result(
                context,
                evaluator_name=self.name,
                evaluator_version=self.version,
                status=EvaluationStatus.SCORED,
                score=0.0,
                passed=False,
                explanation=f"output is not valid JSON: {error.msg}",
            )
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=1.0,
            passed=True,
            explanation="output is valid JSON",
        )


class JsonSchemaEvaluator(SimpleEvaluator):
    name = "json_schema"
    config_model = JsonSchemaConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, JsonSchemaConfig)
        try:
            instance = json.loads(context.response.text)
        except json.JSONDecodeError as error:
            return make_result(
                context,
                evaluator_name=self.name,
                evaluator_version=self.version,
                status=EvaluationStatus.SCORED,
                score=0.0,
                passed=False,
                explanation=f"output is not valid JSON: {error.msg}",
            )
        messages = json_schema_validation_errors(instance, config.schema_definition)
        passed = not messages
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=float(passed),
            passed=passed,
            metrics={"validation_error_count": float(len(messages))},
            explanation="JSON satisfies schema" if passed else "JSON does not satisfy schema",
            artifacts={"validation_errors": cast(JsonValue, messages)},
        )


class RequiredContentEvaluator(SimpleEvaluator):
    name = "required_content"
    config_model = RequiredContentConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, RequiredContentConfig)
        output = context.response.text if config.case_sensitive else context.response.text.lower()
        terms = (
            config.required
            if config.case_sensitive
            else tuple(term.lower() for term in config.required)
        )
        matches = sum(term in output for term in terms)
        ratio = matches / len(terms)
        passed = matches == len(terms) if config.mode == "all" else matches > 0
        score = float(passed)
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=score,
            passed=passed,
            metrics={
                "matched": float(matches),
                "required": float(len(terms)),
                "match_ratio": ratio,
            },
            explanation=f"matched {matches} of {len(terms)} required content terms",
        )


class ForbiddenContentEvaluator(SimpleEvaluator):
    name = "forbidden_content"
    config_model = ForbiddenContentConfig

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        config = _configuration(context.specification, ForbiddenContentConfig)
        output = context.response.text if config.case_sensitive else context.response.text.lower()
        terms = (
            config.forbidden
            if config.case_sensitive
            else tuple(term.lower() for term in config.forbidden)
        )
        matches = sum(term in output for term in terms)
        passed = matches == 0
        score = float(passed)
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=score,
            passed=passed,
            metrics={
                "matched": float(matches),
                "forbidden": float(len(terms)),
                "violation_ratio": matches / len(terms),
            },
            explanation=f"matched {matches} of {len(terms)} forbidden content terms",
        )


class CompositeEvaluator:
    """Combine fully scored child evaluators using explicit positive weights."""

    name = "composite"
    version = "1.1.0"

    def __init__(
        self,
        dispatch: Callable[[EvaluationContext], EvaluationResult],
        validate: Callable[[EvaluationSpecification], None],
    ) -> None:
        self._dispatch = dispatch
        self._validate = validate

    def validate_specification(self, specification: EvaluationSpecification) -> None:
        if specification.config:
            raise EvaluatorConfigurationError("composite config must be empty; use components")
        if not specification.components:
            raise EvaluatorConfigurationError("composite requires at least one component")
        for component in specification.components:
            if component.specification.type in {
                "action_compliance",
                "refusal_compliance",
            }:
                raise EvaluatorConfigurationError(
                    f"{component.specification.type} evaluator must be top-level and "
                    "cannot be nested inside composite evaluators"
                )
            self._validate(component.specification)

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        self.validate_specification(context.specification)
        child_results = [
            self._dispatch(
                EvaluationContext(
                    response=context.response,
                    specification=component.specification,
                    source_result_schema_version=context.source_result_schema_version,
                )
            )
            for component in context.specification.components
        ]
        for status in (
            EvaluationStatus.ERROR,
            EvaluationStatus.INVALID,
            EvaluationStatus.PENDING_REVIEW,
        ):
            if any(result.status is status for result in child_results):
                return make_result(
                    context,
                    evaluator_name=self.name,
                    evaluator_version=self.version,
                    status=status,
                    explanation=f"composite contains a {status.value} component",
                    artifacts={
                        "components": [
                            cast(JsonValue, result.model_dump(mode="json"))
                            for result in child_results
                        ]
                    },
                )

        weights = [component.weight for component in context.specification.components]
        scores = [cast(float, result.score) for result in child_results]
        score = math.fsum(
            value * weight for value, weight in zip(scores, weights, strict=True)
        ) / math.fsum(weights)
        passed_values = [result.passed for result in child_results]
        passed: bool | None
        if any(value is False for value in passed_values):
            passed = False
        elif all(value is True for value in passed_values):
            passed = True
        else:
            passed = None
        metrics = {f"component_{index}_score": value for index, value in enumerate(scores)}
        return make_result(
            context,
            evaluator_name=self.name,
            evaluator_version=self.version,
            status=EvaluationStatus.SCORED,
            score=score,
            passed=passed,
            metrics=metrics,
            explanation=f"combined {len(child_results)} scored components",
            artifacts={
                "components": [
                    cast(JsonValue, result.model_dump(mode="json")) for result in child_results
                ]
            },
        )
