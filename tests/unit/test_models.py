"""Domain model validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from elarabench.models import (
    BenchmarkCase,
    ChatMessage,
    EvaluationResult,
    EvaluationSpecification,
    EvaluationStatus,
    GenerationParameters,
    GenerationRequest,
    ResponseFormatConstraint,
    SampleIdentity,
)


def test_generation_request_preserves_message_order() -> None:
    messages = (
        ChatMessage(role="system", content="first"),
        ChatMessage(role="user", content="second"),
    )
    request = GenerationRequest(messages=messages, seed=7, timeout_seconds=3)

    assert request.messages == messages
    assert [message.content for message in request.messages] == ["first", "second"]


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (ChatMessage, {"role": "invalid", "content": "x"}),
        (GenerationRequest, {"messages": []}),
        (GenerationParameters, {"top_p": 1.1}),
        (GenerationParameters, {"max_tokens": 0}),
        (SampleIdentity, {"case_id": "../escape", "repeat_index": 0}),
    ],
)
def test_invalid_domain_values_are_rejected(
    model: type[object],
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model(**arguments)  # type: ignore[operator]


def test_response_format_requires_schema_for_json_schema_type() -> None:
    with pytest.raises(ValidationError, match="json_schema is required"):
        ResponseFormatConstraint(type="json_schema")


def test_evaluation_result_enforces_status_and_score() -> None:
    result = EvaluationResult(
        status=EvaluationStatus.SCORED,
        score=1.0,
        explanation="passed",
        evaluator_name="test",
        evaluator_version="1.0.0",
        configuration_hash="a" * 64,
    )

    assert result.status is EvaluationStatus.SCORED
    invalid_data = result.model_dump()
    invalid_data["score"] = None
    with pytest.raises(ValidationError, match="require a score"):
        EvaluationResult.model_validate(invalid_data)


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_evaluation_score_bounds(score: float) -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(
            status="scored",
            score=score,
            explanation="bad",
            evaluator_name="test",
            evaluator_version="1.0.0",
            configuration_hash="a" * 64,
        )


def test_non_scored_result_rejects_score() -> None:
    with pytest.raises(ValidationError, match="only scored"):
        EvaluationResult(
            status="invalid",
            score=0.0,
            explanation="invalid",
            evaluator_name="test",
            evaluator_version="1.0.0",
            configuration_hash="a" * 64,
        )


def test_sample_repeat_identity_is_stable() -> None:
    assert SampleIdentity(case_id="case-1", repeat_index=7).repeat_id == "repeat-007"


def test_case_rejects_duplicate_tags_and_nonpositive_weight() -> None:
    data = {
        "id": "case",
        "category": "reasoning",
        "tags": ["same", "same"],
        "messages": [{"role": "user", "content": "prompt"}],
        "evaluation": EvaluationSpecification(type="exact_match", config={"expected": "x"}),
    }
    with pytest.raises(ValidationError, match="tags must be unique"):
        BenchmarkCase.model_validate(data)
    data["tags"] = []
    data["weight"] = 0
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(data)
