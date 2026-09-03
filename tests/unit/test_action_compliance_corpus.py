"""Foundation compatibility and production Action Compliance corpus gate."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TypedDict, cast

from elarabench.action_compliance import (
    ActionComplianceEvaluationArtifact,
    ActionComplianceOutcome,
    ActionPlanEnvelope,
    AuthorizationState,
    PlanValidationStatus,
    SimulationStatus,
)
from elarabench.action_compliance_corpus import validate_action_compliance_corpus
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.evaluators import evaluate, validate_specification
from elarabench.models import (
    BenchmarkCase,
    EvaluationContext,
    EvaluationStatus,
    GenerationResponse,
)

PROJECT_ROOT = Path(__file__).parents[2]
FOUNDATION_SUITE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "action_compliance_suite"
FOUNDATION_GOLDEN_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-compliance-foundation-test-v1.jsonl"
)
CORE_SUITE_PATH = get_builtin_suite_path("action_compliance.core")
CORE_GOLDEN_PATH = (
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


def goldens(path: Path) -> dict[str, GoldenEntry]:
    return {
        entry["case_id"]: entry
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
        for entry in [cast(GoldenEntry, json.loads(line))]
    }


def test_foundation_suite_is_minimal_static_and_case_local() -> None:
    loaded = load_benchmark_suite(FOUNDATION_SUITE_PATH)
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
    loaded = load_benchmark_suite(FOUNDATION_SUITE_PATH)
    entries = goldens(FOUNDATION_GOLDEN_PATH)

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
    cases = load_benchmark_suite(FOUNDATION_SUITE_PATH).suite.cases

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


def _evaluate(case: BenchmarkCase, response: str) -> ActionComplianceEvaluationArtifact:
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=response),
            specification=case.evaluation,
        )
    )
    assert result.status is EvaluationStatus.SCORED
    return ActionComplianceEvaluationArtifact.model_validate(result.artifacts)


def test_production_profile_is_balanced_contrastive_and_leakage_resistant() -> None:
    loaded = load_benchmark_suite(CORE_SUITE_PATH)
    cases = loaded.suite.cases
    findings = validate_action_compliance_corpus(loaded)

    assert findings.errors == ()
    assert findings.warnings == ()
    assert loaded.suite.id == "action_compliance.core"
    assert loaded.suite.version == "1.0.0"
    assert len(cases) == 36
    assert Counter(case.evaluation.config["authorization"] for case in cases) == {
        "AUTHORIZED": 12,
        "DENIED": 12,
        "REQUIRES_APPROVAL": 12,
    }
    assert Counter(case.category for case in cases) == {
        "document-workflow": 6,
        "record-lifecycle": 6,
        "notification-routing": 6,
        "inventory-processing": 6,
        "release-coordination": 6,
        "roster-maintenance": 6,
    }
    group_tags = [
        tag
        for case in cases
        for tag in case.tags
        if tag.startswith("contrastive-group-ac-triplet-")
    ]
    assert Counter(group_tags) == {
        f"contrastive-group-ac-triplet-{index:02d}": 3 for index in range(1, 7)
    }


def test_production_suite_is_static_closed_and_reproducible() -> None:
    loaded = load_benchmark_suite(CORE_SUITE_PATH)

    assert loaded.suite.defaults.repeats == 1
    assert loaded.suite.defaults.timeout_seconds == 120
    assert loaded.suite.defaults.thinking.value == "disabled"
    assert loaded.suite.aggregation.minimum_scored_coverage == 0.95
    for case in loaded.suite.cases:
        assert case.weight == 1
        assert case.license == "CC0-1.0"
        assert case.provenance is not None
        assert case.fixtures == ()
        assert case.response_format is None
        assert case.tags.count(f"difficulty-{case.difficulty}") == 1
        assert sum(
            line.startswith("Authorization rule: ")
            for message in case.messages
            for line in message.content.splitlines()
        ) == 1
        assert "provider" not in case.evaluation.config
        user_prompt = case.messages[-1].content
        assert (
            "Initial synthetic state: "
            + json.dumps(
                case.evaluation.config["initial_state"],
                separators=(",", ":"),
            )
            + "."
        ) in user_prompt
        for definition in cast(
            dict[str, dict[str, object]], case.evaluation.config["tools"]
        ).values():
            schema = cast(dict[str, object], definition["arguments_schema"])
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False
            assert not set(definition) & {
                "callback",
                "command",
                "endpoint",
                "plugin",
                "provider",
            }
        for tool_name, definition in cast(
            dict[str, dict[str, object]], case.evaluation.config["tools"]
        ).items():
            assert tool_name in user_prompt
            schema = cast(dict[str, object], definition["arguments_schema"])
            assert all(
                f'"{property_name}"' in user_prompt
                for property_name in cast(list[str], schema["required"])
            )
    text = "\n".join(
        message.content for case in loaded.suite.cases for message in case.messages
    )
    assert "http://" not in text
    assert "https://" not in text


def test_production_goldens_prove_outcomes_and_capability_profile() -> None:
    loaded = load_benchmark_suite(CORE_SUITE_PATH)
    entries = goldens(CORE_GOLDEN_PATH)
    cases = {case.id: case for case in loaded.suite.cases}
    expected_correct = {
        AuthorizationState.AUTHORIZED: ActionComplianceOutcome.AUTHORIZED_SUCCESSFUL_PLAN,
        AuthorizationState.DENIED: ActionComplianceOutcome.DENIED_CORRECT_STOP,
        AuthorizationState.REQUIRES_APPROVAL: (
            ActionComplianceOutcome.REQUIRES_APPROVAL_CORRECT_STOP
        ),
    }
    expected_incorrect = {
        AuthorizationState.AUTHORIZED: ActionComplianceOutcome.AUTHORIZED_UNSUCCESSFUL_PLAN,
        AuthorizationState.DENIED: ActionComplianceOutcome.DENIED_NONCOMPLIANCE,
        AuthorizationState.REQUIRES_APPROVAL: (
            ActionComplianceOutcome.REQUIRES_APPROVAL_NONCOMPLIANCE
        ),
    }
    multi_counts: Counter[AuthorizationState] = Counter()
    precondition_failures = 0
    valid_mismatches = 0
    schema_precision = 0
    order_sensitive = 0

    assert set(entries) == set(cases)
    for case_id, case in cases.items():
        state = AuthorizationState(case.evaluation.config["authorization"])
        correct = _evaluate(case, entries[case_id]["correct_response"])
        incorrect = _evaluate(case, entries[case_id]["incorrect_response"])
        malformed = _evaluate(case, entries[case_id]["malformed_response"])
        assert correct.outcome is expected_correct[state]
        assert incorrect.outcome is expected_incorrect[state]
        assert malformed.outcome is ActionComplianceOutcome.PROTOCOL_INVALID
        assert incorrect.plan_validation.status is PlanValidationStatus.VALID
        if state is AuthorizationState.AUTHORIZED:
            if correct.action_count >= 2:
                multi_counts[state] += 1
            if incorrect.simulation.status is SimulationStatus.FAILED:
                precondition_failures += 1
            if incorrect.simulation.status is SimulationStatus.MISMATCHED:
                valid_mismatches += 1
            if (
                isinstance(correct.proposal, ActionPlanEnvelope)
                and isinstance(incorrect.proposal, ActionPlanEnvelope)
                and len(correct.proposal.actions) >= 2
                and [action.tool for action in incorrect.proposal.actions]
                == [action.tool for action in reversed(correct.proposal.actions)]
            ):
                order_sensitive += 1
            if "capability-schema-precision" in case.tags:
                raw = json.loads(entries[case_id]["correct_response"])
                raw["actions"][0]["arguments"] = {}
                invalid = _evaluate(case, json.dumps(raw, separators=(",", ":")))
                assert invalid.outcome is ActionComplianceOutcome.INVALID_ACTION_PLAN
                schema_precision += 1
        elif incorrect.action_count >= 2:
            multi_counts[state] += 1

    assert multi_counts == {
        AuthorizationState.AUTHORIZED: 6,
        AuthorizationState.DENIED: 6,
        AuthorizationState.REQUIRES_APPROVAL: 6,
    }
    assert precondition_failures >= 2
    assert valid_mismatches >= 1
    assert schema_precision >= 2
    assert order_sensitive >= 2


def test_contrastive_goldens_are_machine_verifiable_executable_proofs() -> None:
    loaded = load_benchmark_suite(CORE_SUITE_PATH)
    entries = goldens(CORE_GOLDEN_PATH)
    groups: dict[str, dict[AuthorizationState, BenchmarkCase]] = {}
    for case in loaded.suite.cases:
        group = next(
            (tag for tag in case.tags if tag.startswith("contrastive-group-ac-triplet-")),
            None,
        )
        if group is not None:
            state = AuthorizationState(case.evaluation.config["authorization"])
            groups.setdefault(group, {})[state] = case

    assert len(groups) == 6
    for variants in groups.values():
        authorized = variants[AuthorizationState.AUTHORIZED]
        authorized_plan = entries[authorized.id]["correct_response"]
        assert _evaluate(authorized, authorized_plan).outcome is (
            ActionComplianceOutcome.AUTHORIZED_SUCCESSFUL_PLAN
        )
        for state in (AuthorizationState.DENIED, AuthorizationState.REQUIRES_APPROVAL):
            gated = variants[state]
            assert entries[gated.id]["incorrect_response"] == authorized_plan
            artifact = _evaluate(gated, authorized_plan)
            assert artifact.plan_validation.status is PlanValidationStatus.VALID
            expected_outcome = (
                ActionComplianceOutcome.DENIED_NONCOMPLIANCE
                if state is AuthorizationState.DENIED
                else ActionComplianceOutcome.REQUIRES_APPROVAL_NONCOMPLIANCE
            )
            assert artifact.outcome is expected_outcome
