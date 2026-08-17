"""Canonical serialization and identity tests."""

from __future__ import annotations

from elarabench.hashing import (
    hash_canonical,
    hash_case,
    hash_evaluation_specification,
    hash_generation_request,
)
from elarabench.models import BenchmarkCase, EvaluationSpecification, GenerationRequest


def case(prompt: str = "prompt", expected: str = "answer") -> BenchmarkCase:
    return BenchmarkCase.model_validate(
        {
            "id": "case-1",
            "category": "reasoning",
            "messages": [{"role": "user", "content": prompt}],
            "evaluation": {"type": "exact_match", "config": {"expected": expected}},
        }
    )


def test_dictionary_key_order_does_not_change_hash() -> None:
    assert hash_canonical({"a": 1, "b": 2}) == hash_canonical({"b": 2, "a": 1})


def test_meaningful_list_order_changes_hash() -> None:
    assert hash_canonical(["a", "b"]) != hash_canonical(["b", "a"])


def test_identical_models_hash_identically() -> None:
    assert hash_case(case()) == hash_case(case())


def test_changed_prompt_or_evaluator_changes_case_hash() -> None:
    baseline = hash_case(case())
    assert hash_case(case(prompt="different")) != baseline
    assert hash_case(case(expected="different")) != baseline


def test_generation_request_hash_preserves_message_order() -> None:
    first = GenerationRequest.model_validate(
        {"messages": [{"role": "system", "content": "a"}, {"role": "user", "content": "b"}]}
    )
    second = GenerationRequest.model_validate(
        {"messages": [{"role": "user", "content": "b"}, {"role": "system", "content": "a"}]}
    )

    assert hash_generation_request(first) != hash_generation_request(second)
    assert hash_generation_request(first) == hash_generation_request(first.model_copy())


def test_evaluator_hash_includes_configuration() -> None:
    first = EvaluationSpecification(type="exact_match", config={"expected": "a"})
    second = EvaluationSpecification(type="exact_match", config={"expected": "b"})

    assert hash_evaluation_specification(first) != hash_evaluation_specification(second)
