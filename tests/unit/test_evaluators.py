"""Deterministic evaluator semantics and dispatch tests."""

from __future__ import annotations

import pytest

from elarabench.evaluators import EvaluatorConfigurationError, evaluate
from elarabench.models import (
    EvaluationContext,
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
    assert result.evaluator_version == "1.0.0"
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
        ("not-a-number", EvaluationStatus.INVALID, None),
        ("NaN", EvaluationStatus.INVALID, None),
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


@pytest.mark.parametrize(
    ("text", "status", "score"),
    [
        (" b ", EvaluationStatus.SCORED, 1.0),
        ("a", EvaluationStatus.SCORED, 0.0),
        ("D", EvaluationStatus.INVALID, None),
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


def test_invalid_regex_configuration_fails_clearly() -> None:
    specification = EvaluationSpecification(type="regex_full_match", config={"pattern": "["})
    with pytest.raises(EvaluatorConfigurationError, match="invalid regular expression"):
        evaluate(
            EvaluationContext(
                response=GenerationResponse(text="x"),
                specification=specification,
            )
        )


def test_json_parsing_success_and_malformed_output() -> None:
    assert evaluate_text("json_parse", {}, '{"ok": true}')[:2] == (
        EvaluationStatus.SCORED,
        1.0,
    )
    assert evaluate_text("json_parse", {}, "{bad")[:2] == (
        EvaluationStatus.INVALID,
        None,
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
    assert evaluate_text("json_schema", config, "not-json")[0] is EvaluationStatus.INVALID


def test_invalid_json_schema_is_rejected() -> None:
    specification = EvaluationSpecification(
        type="json_schema",
        config={"schema": {"type": "not-a-json-schema-type"}},
    )
    with pytest.raises(EvaluatorConfigurationError, match="invalid JSON Schema"):
        evaluate(
            EvaluationContext(
                response=GenerationResponse(text="{}"),
                specification=specification,
            )
        )


def test_required_content_reports_partial_deterministic_score() -> None:
    result = evaluate_text(
        "required_content",
        {"required": ["alpha", "beta"], "case_sensitive": False},
        "Contains ALPHA only",
    )

    assert result == (EvaluationStatus.SCORED, 0.5, False)


def test_required_content_any_mode() -> None:
    result = evaluate_text(
        "required_content",
        {"required": ["alpha", "beta"], "mode": "any"},
        "alpha",
    )

    assert result == (EvaluationStatus.SCORED, 1.0, True)


def test_forbidden_content_scores_fraction_absent() -> None:
    assert evaluate_text(
        "forbidden_content",
        {"forbidden": ["alpha", "beta"], "case_sensitive": False},
        "ALPHA appears",
    ) == (EvaluationStatus.SCORED, 0.5, False)
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
    assert result.score == 0.875
    assert result.passed is False
    assert len(result.artifacts["components"]) == 2  # type: ignore[arg-type]


def test_composite_propagates_invalid_without_partial_score() -> None:
    specification = EvaluationSpecification.model_validate(
        {
            "type": "composite",
            "components": [
                {
                    "specification": {
                        "type": "numeric",
                        "config": {"expected": 1, "tolerance": 0},
                    }
                },
                {"specification": {"type": "exact_match", "config": {"expected": "x"}}},
            ],
        }
    )
    result = evaluate(
        EvaluationContext(response=GenerationResponse(text="x"), specification=specification)
    )

    assert result.status is EvaluationStatus.INVALID
    assert result.score is None


def test_generation_error_is_not_converted_to_score_zero() -> None:
    specification = EvaluationSpecification(type="exact_match", config={"expected": "x"})
    response = GenerationResponse(error=GenerationError(code="failed", message="synthetic"))
    result = evaluate(EvaluationContext(response=response, specification=specification))

    assert result.status is EvaluationStatus.ERROR
    assert result.score is None


def test_unknown_evaluator_fails_clearly() -> None:
    specification = EvaluationSpecification(type="unknown", config={})
    with pytest.raises(EvaluatorConfigurationError, match="unknown evaluator type"):
        evaluate(
            EvaluationContext(
                response=GenerationResponse(text="x"),
                specification=specification,
            )
        )
