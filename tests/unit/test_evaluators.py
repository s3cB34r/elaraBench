"""Deterministic evaluator semantics and dispatch tests."""

from __future__ import annotations

from typing import Literal, cast

import pytest

from elarabench.evaluators import (
    EvaluatorConfigurationError,
    evaluate,
    registry,
    validate_specification,
)
from elarabench.evaluators.base import make_result
from elarabench.evaluators.builtin import CompositeEvaluator
from elarabench.models import (
    EvaluationContext,
    EvaluationResult,
    EvaluationSpecification,
    EvaluationStatus,
    GenerationError,
    GenerationResponse,
)


def evaluate_text(
    evaluator_type: str,
    config: dict[str, object],
    text: str,
) -> tuple[EvaluationStatus, float | None, bool | None]:
    specification = EvaluationSpecification.model_validate(
        {"type": evaluator_type, "config": config}
    )
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=text),
            specification=specification,
        )
    )
    assert result.configuration_hash
    assert result.evaluator_version == "1.1.0"
    return result.status, result.score, result.passed


@pytest.mark.parametrize(
    ("text", "score"),
    [("ELARA", 1.0), ("elara", 0.0), ("ELARA ", 0.0)],
)
def test_exact_match(text: str, score: float) -> None:
    assert evaluate_text("exact_match", {"expected": "ELARA"}, text) == (
        EvaluationStatus.SCORED,
        score,
        bool(score),
    )


def test_normalized_match_applies_operations_in_order() -> None:
    result = evaluate_text(
        "normalized_match",
        {"expected": "hello world", "operations": ["strip", "lowercase", "collapse_whitespace"]},
        "  HELLO\n\tWORLD  ",
    )

    assert result == (EvaluationStatus.SCORED, 1.0, True)


@pytest.mark.parametrize(
    ("text", "status", "score"),
    [
        ("10.5", EvaluationStatus.SCORED, 1.0),
        ("9.5", EvaluationStatus.SCORED, 1.0),
        ("9.499", EvaluationStatus.SCORED, 0.0),
        ("not-a-number", EvaluationStatus.SCORED, 0.0),
        ("The answer is 10.", EvaluationStatus.SCORED, 0.0),
        ("NaN", EvaluationStatus.SCORED, 0.0),
    ],
)
def test_numeric_tolerance_boundaries(
    text: str,
    status: EvaluationStatus,
    score: float | None,
) -> None:
    result = evaluate_text("numeric", {"expected": 10, "tolerance": 0.5}, text)

    assert result[0] is status
    assert result[1] == score
    assert result[2] is bool(score)


def test_invalid_numeric_benchmark_configuration_is_invalid() -> None:
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="10"),
            specification=EvaluationSpecification(
                type="numeric",
                config={"expected": "NaN", "tolerance": 0},
            ),
        )
    )

    assert result.status is EvaluationStatus.INVALID
    assert result.score is None
    assert result.passed is None


@pytest.mark.parametrize(
    ("text", "status", "score"),
    [
        (" b ", EvaluationStatus.SCORED, 1.0),
        ("a", EvaluationStatus.SCORED, 0.0),
        ("D", EvaluationStatus.SCORED, 0.0),
    ],
)
def test_multiple_choice(text: str, status: EvaluationStatus, score: float | None) -> None:
    result = evaluate_text(
        "multiple_choice",
        {
            "expected": "B",
            "choices": ["A", "B", "C"],
            "operations": ["strip", "lowercase"],
        },
        text,
    )

    assert result[0] is status
    assert result[1] == score


def test_regex_uses_full_match_semantics_and_explicit_flags() -> None:
    assert evaluate_text("regex_full_match", {"pattern": r"item-\d+"}, "item-42")[1] == 1.0
    assert evaluate_text("regex_full_match", {"pattern": r"item-\d+"}, "x item-42")[1] == 0.0
    assert (
        evaluate_text(
            "regex_full_match",
            {"pattern": "elara", "flags": ["ignorecase"]},
            "ELARA",
        )[1]
        == 1.0
    )


def test_invalid_regex_configuration_is_invalid_at_evaluation_time() -> None:
    specification = EvaluationSpecification(type="regex_full_match", config={"pattern": "["})
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="x"),
            specification=specification,
        )
    )

    assert result.status is EvaluationStatus.INVALID
    assert result.score is None
    with pytest.raises(EvaluatorConfigurationError, match="invalid regular expression"):
        validate_specification(specification)


def test_json_parsing_success_and_malformed_output() -> None:
    assert evaluate_text("json_parse", {}, '{"ok": true}')[:2] == (
        EvaluationStatus.SCORED,
        1.0,
    )
    assert evaluate_text("json_parse", {}, "{bad")[:2] == (
        EvaluationStatus.SCORED,
        0.0,
    )
    assert evaluate_text("json_parse", {}, '```json\n{"ok": true}\n```') == (
        EvaluationStatus.SCORED,
        0.0,
        False,
    )


def test_json_schema_success_failure_and_malformed_json() -> None:
    config = {
        "schema": {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "integer"}},
            "additionalProperties": False,
        }
    }
    assert evaluate_text("json_schema", config, '{"answer": 7}')[1] == 1.0
    assert evaluate_text("json_schema", config, '{"answer": "7"}')[1] == 0.0
    assert evaluate_text("json_schema", config, "not-json") == (
        EvaluationStatus.SCORED,
        0.0,
        False,
    )


def test_invalid_json_schema_is_invalid_at_evaluation_time() -> None:
    specification = EvaluationSpecification(
        type="json_schema",
        config={"schema": {"type": "not-a-json-schema-type"}},
    )
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="{}"),
            specification=specification,
        )
    )

    assert result.status is EvaluationStatus.INVALID
    assert result.score is None
    with pytest.raises(EvaluatorConfigurationError, match="invalid JSON Schema"):
        validate_specification(specification)


def test_required_content_missing_term_is_scored_failure() -> None:
    result = evaluate_text(
        "required_content",
        {"required": ["alpha", "beta"], "case_sensitive": False},
        "Contains ALPHA only",
    )

    assert result == (EvaluationStatus.SCORED, 0.0, False)


def test_required_content_any_mode() -> None:
    result = evaluate_text(
        "required_content",
        {"required": ["alpha", "beta"], "mode": "any"},
        "alpha",
    )

    assert result == (EvaluationStatus.SCORED, 1.0, True)


def test_forbidden_content_violation_is_scored_failure() -> None:
    assert evaluate_text(
        "forbidden_content",
        {"forbidden": ["alpha", "beta"], "case_sensitive": False},
        "ALPHA appears",
    ) == (EvaluationStatus.SCORED, 0.0, False)
    assert evaluate_text(
        "forbidden_content",
        {"forbidden": ["alpha", "beta"]},
        "clean",
    ) == (EvaluationStatus.SCORED, 1.0, True)


def test_composite_evaluator_uses_weights_and_preserves_children() -> None:
    specification = EvaluationSpecification.model_validate(
        {
            "type": "composite",
            "components": [
                {
                    "specification": {
                        "type": "required_content",
                        "config": {"required": ["alpha", "beta"]},
                    },
                    "weight": 1,
                },
                {
                    "specification": {
                        "type": "forbidden_content",
                        "config": {"forbidden": ["gamma"]},
                    },
                    "weight": 3,
                },
            ],
        }
    )
    result = evaluate(
        EvaluationContext(response=GenerationResponse(text="alpha"), specification=specification)
    )

    assert result.status is EvaluationStatus.SCORED
    assert result.score == 0.75
    assert result.passed is False
    assert len(result.artifacts["components"]) == 2  # type: ignore[arg-type]
    components = result.artifacts["components"]
    assert isinstance(components, list)
    first_component = components[0]
    assert isinstance(first_component, dict)
    assert first_component["status"] == "scored"
    assert first_component["score"] == 0.0


@pytest.mark.parametrize("source_schema", [2, 3])
def test_recursive_composite_preserves_physical_source_provenance(
    source_schema: Literal[2, 3],
) -> None:
    specification = EvaluationSpecification.model_validate(
        {
            "type": "composite",
            "components": [
                {
                    "specification": {
                        "type": "composite",
                        "components": [
                            {
                                "specification": {
                                    "type": "exact_match",
                                    "config": {"expected": "ELARA"},
                                }
                            }
                        ],
                    }
                }
            ],
        }
    )
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="ELARA"),
            specification=specification,
            source_result_schema_version=source_schema,
        )
    )

    outer_components = result.artifacts["components"]
    assert isinstance(outer_components, list)
    inner_result = cast(dict[str, object], outer_components[0])
    inner_artifacts = cast(dict[str, object], inner_result["artifacts"])
    inner_components = cast(list[object], inner_artifacts["components"])
    leaf_result = cast(dict[str, object], inner_components[0])

    assert result.source_result_schema_version == source_schema
    assert inner_result["source_result_schema_version"] == source_schema
    assert leaf_result["source_result_schema_version"] == source_schema


@pytest.mark.parametrize("child_status", [EvaluationStatus.INVALID, EvaluationStatus.ERROR])
def test_composite_propagates_unscored_child_with_diagnostics(
    child_status: EvaluationStatus,
) -> None:
    specification = EvaluationSpecification.model_validate(
        {
            "type": "composite",
            "components": [
                {"specification": {"type": "exact_match", "config": {"expected": "x"}}},
                {"specification": {"type": "exact_match", "config": {"expected": "y"}}},
            ],
        }
    )
    calls = 0

    def dispatch(context: EvaluationContext) -> EvaluationResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return make_result(
                context,
                evaluator_name="synthetic",
                evaluator_version="1.0.0",
                status=child_status,
                explanation="synthetic child failure",
            )
        return make_result(
            context,
            evaluator_name="synthetic",
            evaluator_version="1.0.0",
            status=EvaluationStatus.SCORED,
            score=1.0,
            passed=True,
            explanation="synthetic child success",
        )

    evaluator = CompositeEvaluator(dispatch, lambda _specification: None)
    result = evaluator.evaluate(
        EvaluationContext(response=GenerationResponse(text="x"), specification=specification)
    )

    assert result.status is child_status
    assert result.score is None
    assert len(result.artifacts["components"]) == 2  # type: ignore[arg-type]


def test_generation_error_is_not_converted_to_score_zero() -> None:
    specification = EvaluationSpecification(type="exact_match", config={"expected": "x"})
    response = GenerationResponse(error=GenerationError(code="failed", message="synthetic"))
    result = evaluate(EvaluationContext(response=response, specification=specification))

    assert result.status is EvaluationStatus.ERROR
    assert result.score is None


def test_unknown_evaluator_is_invalid_at_evaluation_time() -> None:
    specification = EvaluationSpecification(type="unknown", config={})
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="x"),
            specification=specification,
        )
    )

    assert result.status is EvaluationStatus.INVALID
    assert "unknown evaluator type" in result.explanation


def test_unexpected_evaluator_exception_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingEvaluator:
        name = "exact_match"
        version = "synthetic"

        def validate_specification(self, _specification: EvaluationSpecification) -> None:
            pass

        def evaluate(self, _context: EvaluationContext) -> EvaluationResult:
            raise RuntimeError("synthetic evaluator failure")

    monkeypatch.setitem(registry._EVALUATORS, "exact_match", ExplodingEvaluator())
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="x"),
            specification=EvaluationSpecification(
                type="exact_match", config={"expected": "x"}
            ),
        )
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.score is None
    assert "RuntimeError" in result.explanation
