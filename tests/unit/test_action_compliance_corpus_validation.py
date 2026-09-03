"""Focused tests for deterministic Action Compliance corpus validation."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from elarabench.action_compliance_corpus import (
    CorpusFindingCode,
    build_first_tool_response,
    validate_action_compliance_corpus,
)
from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.hashing import hash_suite
from elarabench.models import BenchmarkCase, EvaluationSpecification


def core() -> LoadedBenchmarkSuite:
    return load_benchmark_suite(get_builtin_suite_path("action_compliance.core"))


def with_cases(
    loaded: LoadedBenchmarkSuite,
    cases: tuple[BenchmarkCase, ...],
    *,
    suite_id: str | None = None,
) -> LoadedBenchmarkSuite:
    suite = loaded.suite.model_copy(
        update={"cases": cases, "id": suite_id or loaded.suite.id}
    )
    return replace(loaded, suite=suite)


def replace_case(
    loaded: LoadedBenchmarkSuite,
    case_id: str,
    changed: BenchmarkCase,
) -> LoadedBenchmarkSuite:
    return with_cases(
        loaded,
        tuple(changed if case.id == case_id else case for case in loaded.suite.cases),
    )


def finding_codes(loaded: LoadedBenchmarkSuite) -> set[CorpusFindingCode]:
    return {finding.code for finding in validate_action_compliance_corpus(loaded).errors}


def test_production_validator_is_clean_and_deterministic() -> None:
    loaded = core()

    first = validate_action_compliance_corpus(loaded)
    second = validate_action_compliance_corpus(loaded)

    assert first == second
    assert first.valid
    assert first.errors == ()
    assert first.warnings == ()


@pytest.mark.parametrize(
    ("replacement", "expected"),
    (
        (
            "contrastive-group-ac-triplet-one",
            CorpusFindingCode.MALFORMED_RESERVED_TAG,
        ),
        (
            "contrastive-variant-unknown",
            CorpusFindingCode.MALFORMED_RESERVED_TAG,
        ),
    ),
)
def test_malformed_reserved_tags_are_errors(
    replacement: str,
    expected: CorpusFindingCode,
) -> None:
    loaded = core()
    case = loaded.suite.cases[0]
    changed = case.model_copy(update={"tags": (*case.tags, replacement)})

    assert expected in finding_codes(replace_case(loaded, case.id, changed))


def test_multiple_and_unpaired_contrastive_tags_are_errors() -> None:
    loaded = core()
    first = loaded.suite.cases[0]
    second = loaded.suite.cases[3]
    first_changed = first.model_copy(
        update={
            "tags": (
                *first.tags,
                "contrastive-group-ac-triplet-02",
                "contrastive-variant-denied",
            )
        }
    )
    second_changed = second.model_copy(
        update={"tags": (*second.tags, "contrastive-variant-authorized")}
    )
    changed = replace_case(loaded, first.id, first_changed)
    changed = replace_case(changed, second.id, second_changed)
    codes = finding_codes(changed)

    assert CorpusFindingCode.MULTIPLE_GROUP_TAGS in codes
    assert CorpusFindingCode.MULTIPLE_VARIANT_TAGS in codes
    assert CorpusFindingCode.VARIANT_WITHOUT_GROUP in codes


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        ("category", CorpusFindingCode.TRIPLET_CATEGORY_MISMATCH),
        ("difficulty", CorpusFindingCode.TRIPLET_DIFFICULTY_MISMATCH),
        ("configuration", CorpusFindingCode.TRIPLET_CONFIGURATION_MISMATCH),
        ("prompt", CorpusFindingCode.TRIPLET_PROMPT_MISMATCH),
    ),
)
def test_triplet_mismatches_are_errors(kind: str, expected: CorpusFindingCode) -> None:
    loaded = core()
    case = loaded.suite.cases[1]
    if kind == "category":
        changed = case.model_copy(update={"category": "different-family"})
    elif kind == "difficulty":
        changed = case.model_copy(update={"difficulty": "easy"})
    elif kind == "configuration":
        config = dict(case.evaluation.config)
        config["max_plan_length"] = 2
        changed = case.model_copy(
            update={
                "evaluation": EvaluationSpecification(
                    type="action_compliance",
                    config=config,
                )
            }
        )
    else:
        messages = list(case.messages)
        messages[1] = messages[1].model_copy(
            update={"content": messages[1].content.replace("report-r1", "report-other", 1)}
        )
        changed = case.model_copy(update={"messages": tuple(messages)})

    assert expected in finding_codes(replace_case(loaded, case.id, changed))


def test_variant_must_agree_with_trusted_authorization() -> None:
    loaded = core()
    case = loaded.suite.cases[0]
    config = dict(case.evaluation.config)
    config["authorization"] = "DENIED"
    config.pop("expected_state")
    changed = case.model_copy(
        update={
            "evaluation": EvaluationSpecification(
                type="action_compliance",
                config=config,
            )
        }
    )

    assert CorpusFindingCode.VARIANT_AUTHORIZATION_MISMATCH in finding_codes(
        replace_case(loaded, case.id, changed)
    )


def test_custom_suite_leakage_is_warning_not_error() -> None:
    foundation = load_benchmark_suite("tests/fixtures/action_compliance_suite")
    custom = with_cases(
        foundation,
        foundation.suite.cases,
        suite_id="custom.action_compliance",
    )
    findings = validate_action_compliance_corpus(custom)
    warning_codes = {finding.code for finding in findings.warnings}

    assert findings.errors == ()
    assert CorpusFindingCode.STATE_EXCLUSIVE_CATEGORY in warning_codes
    assert CorpusFindingCode.STATE_EXCLUSIVE_TOOL in warning_codes
    assert CorpusFindingCode.NO_CONTRASTIVE_COVERAGE in warning_codes
    assert CorpusFindingCode.SMALL_STATE_POPULATION in warning_codes
    assert CorpusFindingCode.NONCONTRASTIVE_GATE_PROOF in warning_codes


def test_legal_role_and_token_properties_are_not_blacklisted() -> None:
    loaded = core()
    findings = validate_action_compliance_corpus(loaded)

    assert findings.errors == ()
    assert any(
        "role" in tool["arguments_schema"]["properties"]
        for case in loaded.suite.cases
        for tool in case.evaluation.config["tools"].values()
    )
    assert any(
        "token" in tool["arguments_schema"]["properties"]
        for case in loaded.suite.cases
        for tool in case.evaluation.config["tools"].values()
    )


def first_tool_case(schema: dict[str, object]) -> BenchmarkCase:
    original = core().suite.cases[0]
    config = dict(original.evaluation.config)
    config["tools"] = {
        "z_tool": {
            "arguments_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "effects": {"done": True},
        },
        "a_tool": {
            "arguments_schema": schema,
            "effects": {"done": True},
        },
    }
    config["initial_state"] = {"done": False}
    config["expected_state"] = {"done": True}
    return original.model_copy(
        update={
            "evaluation": EvaluationSpecification(
                type="action_compliance",
                config=config,
            )
        }
    )


def test_first_tool_generation_is_canonical_and_schema_driven() -> None:
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "integer": {"type": "integer"},
            "number": {"type": "number"},
            "flag": {"type": "boolean"},
            "nothing": {"type": "null"},
            "constant": {"type": "string", "const": "fixed"},
            "choice": {"type": "string", "enum": ["first", "second"]},
            "items": {"type": "array", "items": {"type": "string"}, "minItems": 2},
            "nested": {
                "type": "object",
                "properties": {"required": {"type": "integer"}},
                "required": ["required"],
                "additionalProperties": False,
            },
            "optional": {"type": "string"},
        },
        "required": [
            "text",
            "integer",
            "number",
            "flag",
            "nothing",
            "constant",
            "choice",
            "items",
            "nested",
        ],
        "additionalProperties": False,
    }
    case = first_tool_case(schema)
    first = build_first_tool_response(case)
    second = build_first_tool_response(case)

    assert first == second
    assert first is not None
    parsed = json.loads(first)
    assert parsed["actions"] == [
        {
            "tool": "a_tool",
            "arguments": {
                "choice": "first",
                "constant": "fixed",
                "flag": False,
                "integer": 0,
                "items": ["x", "x"],
                "nested": {"required": 0},
                "nothing": None,
                "number": 0.0,
                "text": "x",
            },
        }
    ]


def test_first_tool_ambiguous_union_is_ineligible() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": ["string", "null"]}},
        "required": ["value"],
        "additionalProperties": False,
    }

    assert build_first_tool_response(first_tool_case(schema)) is None


def test_all_production_cases_are_first_tool_eligible() -> None:
    loaded = core()
    responses = [build_first_tool_response(case) for case in loaded.suite.cases]

    assert all(response is not None for response in responses)
    assert responses == [build_first_tool_response(case) for case in loaded.suite.cases]


@pytest.mark.parametrize("field", ("category", "tags", "prompt", "config"))
def test_existing_suite_identity_covers_corpus_validity_inputs(field: str) -> None:
    loaded = core()
    case = loaded.suite.cases[0]
    if field == "category":
        changed = case.model_copy(update={"category": "changed-family"})
    elif field == "tags":
        changed = case.model_copy(update={"tags": (*case.tags, "changed-tag")})
    elif field == "prompt":
        messages = list(case.messages)
        messages[1] = messages[1].model_copy(
            update={"content": messages[1].content.replace("report-r1", "report-r9", 1)}
        )
        changed = case.model_copy(update={"messages": tuple(messages)})
    else:
        config = dict(case.evaluation.config)
        config["max_plan_length"] = 2
        changed = case.model_copy(
            update={
                "evaluation": EvaluationSpecification(
                    type="action_compliance",
                    config=config,
                )
            }
        )
    suite = loaded.suite.model_copy(
        update={
            "cases": tuple(
                changed if candidate.id == case.id else candidate
                for candidate in loaded.suite.cases
            )
        }
    )

    assert hash_suite(suite, loaded.fixture_files) != loaded.content_hash
