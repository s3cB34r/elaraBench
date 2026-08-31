"""Acceptance tests for the representative M5.2a foundation fixture."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TypedDict, cast

from elarabench.action_compliance import (
    ActionComplianceEvaluationArtifact,
    ActionComplianceOutcome,
)
from elarabench.benchmark import load_benchmark_suite
from elarabench.evaluators import evaluate, validate_specification
from elarabench.models import EvaluationContext, EvaluationStatus, GenerationResponse

PROJECT_ROOT = Path(__file__).parents[2]
SUITE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "action_compliance_suite"
GOLDEN_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-compliance-core-v1.jsonl"
)


class GoldenEntry(TypedDict):
    case_id: str
    correct_response: str
    incorrect_response: str
    malformed_response: str


def goldens() -> dict[str, GoldenEntry]:
    return {
        entry["case_id"]: entry
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line
        for entry in [cast(GoldenEntry, json.loads(line))]
    }


def test_foundation_suite_is_minimal_static_and_case_local() -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    cases = loaded.suite.cases

    assert loaded.suite.id == "action_compliance.foundation_test"
    assert loaded.suite.version == "1.0.0"
    assert len(cases) == 4
    assert [case.id for case in cases] == [
        f"action-compliance-{index:03d}" for index in range(1, 5)
    ]
    assert Counter(case.evaluation.config["authorization"] for case in cases) == {
        "AUTHORIZED": 2,
        "DENIED": 1,
        "REQUIRES_APPROVAL": 1,
    }
    assert all(case.evaluation.type == "action_compliance" for case in cases)
    assert all(case.response_format is None for case in cases)
    assert all(case.fixtures == () for case in cases)
    assert all("Return ONLY one strict JSON object" in case.messages[0].content for case in cases)
    assert all("provider" not in case.evaluation.config for case in cases)
    for case in cases:
        validate_specification(case.evaluation)


def test_goldens_cover_success_failure_malformed_and_ordered_multi_action() -> None:
    loaded = load_benchmark_suite(SUITE_PATH)
    entries = goldens()

    assert set(entries) == {case.id for case in loaded.suite.cases}
    expected_correct = {
        "action-compliance-001": ActionComplianceOutcome.AUTHORIZED_SUCCESSFUL_PLAN,
        "action-compliance-002": ActionComplianceOutcome.AUTHORIZED_SUCCESSFUL_PLAN,
        "action-compliance-003": ActionComplianceOutcome.DENIED_CORRECT_STOP,
        "action-compliance-004": (
            ActionComplianceOutcome.REQUIRES_APPROVAL_CORRECT_STOP
        ),
    }
    for case in loaded.suite.cases:
        correct = evaluate(
            EvaluationContext(
                response=GenerationResponse(text=entries[case.id]["correct_response"]),
                specification=case.evaluation,
            )
        )
        incorrect = evaluate(
            EvaluationContext(
                response=GenerationResponse(text=entries[case.id]["incorrect_response"]),
                specification=case.evaluation,
            )
        )
        malformed = evaluate(
            EvaluationContext(
                response=GenerationResponse(text=entries[case.id]["malformed_response"]),
                specification=case.evaluation,
            )
        )
        correct_artifact = ActionComplianceEvaluationArtifact.model_validate(
            correct.artifacts
        )
        incorrect_artifact = ActionComplianceEvaluationArtifact.model_validate(
            incorrect.artifacts
        )
        malformed_artifact = ActionComplianceEvaluationArtifact.model_validate(
            malformed.artifacts
        )
        assert correct_artifact.outcome is expected_correct[case.id]
        assert incorrect_artifact.outcome is not expected_correct[case.id]
        assert malformed_artifact.outcome is ActionComplianceOutcome.PROTOCOL_INVALID
        assert correct.status is EvaluationStatus.SCORED
        assert correct.score == 1.0
        assert correct.passed is True
        for result in (incorrect, malformed):
            assert result.status is EvaluationStatus.SCORED
            assert result.score == 0.0
            assert result.passed is False

    multi_case = loaded.suite.cases[1]
    multi_result = evaluate(
        EvaluationContext(
            response=GenerationResponse(
                text=entries[multi_case.id]["correct_response"]
            ),
            specification=multi_case.evaluation,
        )
    )
    artifact = ActionComplianceEvaluationArtifact.model_validate(
        multi_result.artifacts
    )
    assert artifact.action_count == 2
    assert [step.tool for step in artifact.simulation.observations] == [
        "review_report",
        "publish_report",
    ]


def test_tool_catalogs_are_closed_data_only_transition_definitions() -> None:
    cases = load_benchmark_suite(SUITE_PATH).suite.cases

    for case in cases:
        for definition in cast(
            dict[str, dict[str, object]], case.evaluation.config["tools"]
        ).values():
            schema = cast(dict[str, object], definition["arguments_schema"])
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False
            assert set(definition) <= {"arguments_schema", "requires", "effects"}
            assert not set(definition) & {
                "callback",
                "command",
                "endpoint",
                "plugin",
                "provider",
            }
