"""Canonical serialization and identity tests."""

from __future__ import annotations

import pytest

from elarabench.hashing import (
    hash_canonical,
    hash_case,
    hash_case_with_fixture_hashes,
    hash_evaluation_specification,
    hash_generation_request,
)
from elarabench.models import (
    BenchmarkCase,
    EvaluationSpecification,
    GenerationRequest,
    ThinkingPolicy,
)


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


def test_snapshot_fixture_hash_participates_in_canonical_case_identity() -> None:
    fixture_case = case().model_copy(update={"fixtures": ("fixtures/context.txt",)})

    baseline = hash_case_with_fixture_hashes(
        fixture_case, {"fixtures/context.txt": "a" * 64}
    )
    candidate = hash_case_with_fixture_hashes(
        fixture_case, {"fixtures/context.txt": "b" * 64}
    )

    assert baseline != candidate
    assert baseline == hash_case_with_fixture_hashes(
        fixture_case,
        {"fixtures/context.txt": "a" * 64, "fixtures/unrelated.txt": "c" * 64},
    )


def test_fixture_aware_case_hash_requires_snapshot_identity() -> None:
    fixture_case = case().model_copy(update={"fixtures": ("fixtures/context.txt",)})

    with pytest.raises(ValueError, match="snapshot fixture identity is unavailable"):
        hash_case_with_fixture_hashes(fixture_case, {})


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


def test_generation_request_hash_includes_thinking_policy() -> None:
    base = GenerationRequest.model_validate(
        {"messages": [{"role": "user", "content": "prompt"}]}
    )
    enabled = base.model_copy(update={"thinking": ThinkingPolicy.ENABLED})
    provider_default = base.model_copy(update={"thinking": ThinkingPolicy.PROVIDER_DEFAULT})

    assert hash_generation_request(base) != hash_generation_request(enabled)
    assert hash_generation_request(base) != hash_generation_request(provider_default)
    assert hash_generation_request(enabled) != hash_generation_request(provider_default)
