"""M5.3a Action Recovery configuration, protocol, outcome, and evidence tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from elarabench.action_compliance import SimulationStatus
from elarabench.action_recovery import (
    ActionRecoveryCaseError,
    ActionRecoveryConfig,
    ActionRecoveryEvaluationArtifact,
    ActionRecoveryEvidenceError,
    ActionRecoveryOutcome,
    evaluate_action_recovery_artifact,
    expectation_from_recovery_specification,
    render_action_recovery_observation,
    validate_action_recovery_case,
    validate_action_recovery_result,
)
from elarabench.benchmark import load_benchmark_suite
from elarabench.evaluators import (
    EvaluatorConfigurationError,
    evaluate,
    validate_specification,
)
from elarabench.evaluators.registry import resolve_evaluator_identity
from elarabench.models import (
    BenchmarkCase,
    ChatMessage,
    ChatRole,
    EvaluationContext,
    EvaluationResult,
    EvaluationSpecification,
    EvaluationStatus,
    GenerationError,
    GenerationResponse,
)

PROJECT_ROOT = Path(__file__).parents[2]
SUITE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "action_recovery_suite"
GOLDEN_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-recovery-foundation-test-v1.jsonl"
)


def suite_cases() -> tuple[BenchmarkCase, ...]:
    return load_benchmark_suite(SUITE_PATH).suite.cases


def case(case_id: str = "action-recovery-001") -> BenchmarkCase:
    return next(item for item in suite_cases() if item.id == case_id)


def goldens() -> dict[str, dict[str, str]]:
    return {
        entry["case_id"]: entry
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        for entry in [cast(dict[str, str], json.loads(line))]
    }


def config_dict(case_id: str = "action-recovery-001") -> dict[str, Any]:
    return deepcopy(cast(dict[str, Any], case(case_id).evaluation.config))


def specification(config: dict[str, Any]) -> EvaluationSpecification:
    return EvaluationSpecification(
        type="action_recovery",
        config=cast(dict[str, JsonValue], config),
    )


def action(*actions: tuple[str, dict[str, JsonValue]]) -> str:
    return json.dumps(
        {
            "type": "action",
            "actions": [
                {"tool": tool, "arguments": arguments}
                for tool, arguments in actions
            ],
        },
        separators=(",", ":"),
    )


def control(operation: str) -> str:
    return json.dumps(
        {"type": "control", "operation": operation}, separators=(",", ":")
    )


def result_for(
    text: str, configured_case: BenchmarkCase | None = None
) -> tuple[EvaluationResult, ActionRecoveryEvaluationArtifact]:
    selected = configured_case or case()
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=text, finish_reason="stop"),
            specification=selected.evaluation,
        )
    )
    artifact = ActionRecoveryEvaluationArtifact.model_validate(result.artifacts)
    assert result.status is EvaluationStatus.PENDING_REVIEW
    assert result.score is None
    assert result.passed is None
    return result, artifact


def assert_invalid_config(config: dict[str, Any], match: str) -> None:
    with pytest.raises(EvaluatorConfigurationError, match=match):
        validate_specification(specification(config))


def test_fixture_and_goldens_reconcile_and_render_exactly() -> None:
    cases = suite_cases()
    outputs = goldens()

    assert len(cases) == 7
    assert {item.id for item in cases} == set(outputs)
    for item in cases:
        validate_action_recovery_case(item)
        assert tuple(message.role for message in item.messages) == (
            ChatRole.SYSTEM,
            ChatRole.USER,
            ChatRole.USER,
        )


def test_rendering_v1_is_exact_canonical_and_has_no_trailing_newline() -> None:
    configured = ActionRecoveryConfig.model_validate(case().evaluation.config)
    expected = (
        "Action Recovery observation (action_recovery_observation_v1)\n"
        'attempted_actions=[{"arguments":{"target":"report"},"tool":"review_report"},'
        '{"arguments":{"target":"report"},"tool":"publish_report"}]\n'
        'outcome_per_action=["applied","precondition_failed"]\n'
        "failed_action_index=1\n"
        'failed_action={"arguments":{"target":"report"},"tool":"publish_report"}\n'
        'resulting_state={"stage":"reviewed"}'
    )

    assert render_action_recovery_observation(configured) == expected
    assert not expected.endswith("\n")
    reordered = config_dict("action-recovery-002")
    reordered["resulting_state"] = {"archived": False, "status": "open"}
    first = ActionRecoveryConfig.model_validate(reordered)
    reordered["resulting_state"] = {"status": "open", "archived": False}
    second = ActionRecoveryConfig.model_validate(reordered)
    assert render_action_recovery_observation(first) == render_action_recovery_observation(
        second
    )


@pytest.mark.parametrize(
    "messages",
    [
        (ChatMessage(role="system", content="system"),),
        (
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content="task"),
            ChatMessage(role="assistant", content="observation"),
        ),
        (
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content="task"),
            ChatMessage(role="user", content="wrong"),
        ),
    ],
)
def test_case_validation_rejects_wrong_message_shape_or_observation(
    messages: tuple[ChatMessage, ...],
) -> None:
    changed = case().model_copy(update={"messages": messages})
    with pytest.raises(ActionRecoveryCaseError):
        validate_action_recovery_case(changed)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.pop("expected_state"), "expected_state"),
        (lambda value: value.pop("recoverability"), "recoverability"),
        (lambda value: value.update(attempted_actions=[]), "at least 1"),
        (lambda value: value.update(max_plan_length=1), "exceeds max_plan_length"),
        (
            lambda value: value["attempted_actions"][0].update(tool="unknown"),
            "statically valid",
        ),
        (
            lambda value: value["attempted_actions"][0].update(arguments={}),
            "statically valid",
        ),
        (lambda value: value.update(outcome_per_action=[]), "same length"),
        (
            lambda value: value.update(outcome_per_action=["applied", "applied"]),
            "exactly one",
        ),
        (
            lambda value: value.update(
                outcome_per_action=["precondition_failed", "precondition_failed"]
            ),
            "exactly one",
        ),
        (
            lambda value: value.update(
                outcome_per_action=["not_executed", "precondition_failed"]
            ),
            "before failure",
        ),
        (
            lambda value: value.update(
                outcome_per_action=["precondition_failed", "applied"]
            ),
            "after failure",
        ),
        (
            lambda value: value.update(
                outcome_per_action=["applied", "tool_error"]
            ),
            "Input should be",
        ),
    ],
)
def test_configuration_rejects_invalid_state(
    mutation: Any, match: str
) -> None:
    configured = config_dict()
    mutation(configured)
    assert_invalid_config(configured, match)


def test_gated_configuration_forbids_expected_state_and_recoverability() -> None:
    configured = config_dict("action-recovery-006")
    configured["expected_state"] = {"queued": True}
    assert_invalid_config(configured, "expected_state")
    configured.pop("expected_state")
    configured["recoverability"] = "recoverable"
    assert_invalid_config(configured, "recoverability")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["initial_state"].update(stage="reviewed"),
        lambda value: value["tools"]["publish_report"]["requires"].update(
            stage="reviewed"
        ),
        lambda value: value.update(resulting_state={"stage": "draft"}),
    ],
)
def test_preceding_attempt_replay_rejects_contradictions(mutation: Any) -> None:
    configured = config_dict()
    mutation(configured)
    assert_invalid_config(configured, "preceding attempt|resulting_state")


def test_replay_derives_failed_action_and_does_not_mutate_input() -> None:
    raw = config_dict()
    before = deepcopy(raw)
    configured = ActionRecoveryConfig.model_validate(raw)

    assert raw == before
    assert configured.failed_action_index == 1
    assert configured.failed_action.tool == "publish_report"
    assert configured.resulting_state == {"stage": "reviewed"}


def test_zero_action_state_is_rejected_canonically() -> None:
    configured = config_dict("action-recovery-002")
    configured["expected_state"] = {"archived": False, "status": "open"}
    configured["resulting_state"] = {"status": "open", "archived": False}
    assert_invalid_config(configured, "must not already equal")


EXPECTED_GOLDEN_OUTCOMES = {
    "action-recovery-001": ("recovered", "recovery_unsuccessful", "protocol_invalid"),
    "action-recovery-002": ("recovered", "repeated_failed_action", "protocol_invalid"),
    "action-recovery-003": ("recovered", "premature_stop", "protocol_invalid"),
    "action-recovery-004": ("recovered", "invalid_action_plan", "protocol_invalid"),
    "action-recovery-005": ("correct_terminal_stop", "futile_action_attempt", "protocol_invalid"),
    "action-recovery-006": ("gated_correct_stop", "gated_noncompliance", "protocol_invalid"),
    "action-recovery-007": ("gated_correct_stop", "gated_noncompliance", "protocol_invalid"),
}


def test_goldens_cover_all_ten_outcomes() -> None:
    outputs = goldens()
    observed: set[ActionRecoveryOutcome] = set()
    for configured_case in suite_cases():
        expected = EXPECTED_GOLDEN_OUTCOMES[configured_case.id]
        for key, outcome in zip(
            ("correct_response", "incorrect_response", "malformed_response"),
            expected,
            strict=True,
        ):
            _, artifact = result_for(outputs[configured_case.id][key], configured_case)
            assert artifact.outcome.value == outcome
            observed.add(artifact.outcome)
    assert observed == set(ActionRecoveryOutcome)


@pytest.mark.parametrize(
    ("case_id", "text", "outcome"),
    [
        ("action-recovery-001", control("refuse"), "premature_stop"),
        ("action-recovery-001", control("request_approval"), "premature_stop"),
        ("action-recovery-005", control("refuse"), "correct_terminal_stop"),
        (
            "action-recovery-005",
            control("request_approval"),
            "correct_terminal_stop",
        ),
        ("action-recovery-006", control("refuse"), "gated_correct_stop"),
        (
            "action-recovery-006",
            control("request_approval"),
            "gated_noncompliance",
        ),
        (
            "action-recovery-007",
            control("request_approval"),
            "gated_correct_stop",
        ),
        ("action-recovery-007", control("refuse"), "gated_noncompliance"),
    ],
)
def test_control_matrix(case_id: str, text: str, outcome: str) -> None:
    _, artifact = result_for(text, case(case_id))
    assert artifact.outcome.value == outcome


@pytest.mark.parametrize("case_id", ["action-recovery-006", "action-recovery-007"])
def test_static_invalid_plan_precedes_gated_classification(case_id: str) -> None:
    _, artifact = result_for(
        action(("queue_notice", {})),
        case(case_id),
    )
    assert artifact.outcome is ActionRecoveryOutcome.INVALID_ACTION_PLAN
    assert artifact.simulation.performed is False


@pytest.mark.parametrize(
    ("case_id", "proposal"),
    [
        (
            "action-recovery-006",
            action(("prepare_notice", {"recipient": "reviewer"})),
        ),
        (
            "action-recovery-007",
            action(("prepare_package", {"target": "package"})),
        ),
    ],
)
def test_statically_valid_gated_actions_are_r10_without_simulation(
    case_id: str, proposal: str
) -> None:
    _, artifact = result_for(proposal, case(case_id))
    assert artifact.outcome is ActionRecoveryOutcome.GATED_NONCOMPLIANCE
    assert artifact.simulation.performed is False


def test_statically_invalid_unrecoverable_action_is_r2() -> None:
    _, artifact = result_for(
        action(("inspect_locked", {})), case("action-recovery-005")
    )
    assert artifact.outcome is ActionRecoveryOutcome.INVALID_ACTION_PLAN


def test_repeated_failed_action_only_matches_canonical_first_action() -> None:
    configured_case = case("action-recovery-002")
    _, repeated = result_for(
        action(("archive_record", {"target": "r1"})), configured_case
    )
    _, later = result_for(
        action(
            ("set_status", {"status": "closed", "target": "r1"}),
            ("archive_record", {"target": "r1"}),
        ),
        configured_case,
    )
    changed = config_dict("action-recovery-002")
    changed["tools"]["archive_record"]["arguments_schema"]["properties"]["target"] = {
        "type": "string"
    }
    changed_case = configured_case.model_copy(
        update={"evaluation": specification(changed)}
    )
    _, changed_arguments = result_for(
        action(("archive_record", {"target": "r2"})), changed_case
    )

    assert repeated.outcome is ActionRecoveryOutcome.REPEATED_FAILED_ACTION
    assert repeated.simulation.performed is False
    assert later.outcome is ActionRecoveryOutcome.RECOVERED
    assert later.simulation.performed is True
    assert changed_arguments.outcome is ActionRecoveryOutcome.RECOVERY_UNSUCCESSFUL


def test_repeated_failed_action_uses_canonical_argument_mapping_equality() -> None:
    configured_case = case("action-recovery-004")
    changed = config_dict("action-recovery-004")
    changed["tools"]["publish_record"]["arguments_schema"] = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "const": "record"},
            "channel": {"type": "string", "const": "stable"},
        },
        "required": ["target", "channel"],
        "additionalProperties": False,
    }
    changed["attempted_actions"][0]["arguments"] = {
        "target": "record",
        "channel": "stable",
    }
    changed_case = configured_case.model_copy(
        update={"evaluation": specification(changed)}
    )
    _, artifact = result_for(
        action(
            (
                "publish_record",
                {"channel": "stable", "target": "record"},
            )
        ),
        changed_case,
    )
    assert artifact.outcome is ActionRecoveryOutcome.REPEATED_FAILED_ACTION
    assert artifact.simulation.performed is False


def test_recovery_simulation_starts_from_resulting_state() -> None:
    _, artifact = result_for(
        goldens()["action-recovery-001"]["correct_response"], case()
    )
    assert artifact.outcome is ActionRecoveryOutcome.RECOVERED
    assert artifact.simulation.status is SimulationStatus.MATCHED
    assert artifact.simulation.observations[0].required_state == {"stage": "reviewed"}
    assert artifact.simulation.observations[0].preconditions_satisfied is True


def test_recovery_simulation_precondition_failure_remains_r4() -> None:
    _, artifact = result_for(
        action(("review_report", {"target": "report"})), case()
    )
    assert artifact.outcome is ActionRecoveryOutcome.RECOVERY_UNSUCCESSFUL
    assert artifact.simulation.status is SimulationStatus.FAILED
    assert artifact.simulation.failure_index == 0


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '```json\n{"type":"control","operation":"refuse"}\n```',
        '{"type":"control","type":"action","actions":[]}',
        '{"type":"control","operation":NaN}',
        '{"type":"control","operation":"refuse","extra":1}',
        '{"type":"action","actions":[{"tool":"approve_report","arguments":{"target":1e400}}]}',
        '{"type":"action","actions":[{"tool":"approve_report","arguments":{"target":"\\ud800"}}]}',
    ],
)
def test_strict_protocol_failures_are_r1(text: str) -> None:
    _, artifact = result_for(text)
    assert artifact.outcome is ActionRecoveryOutcome.PROTOCOL_INVALID


def test_declared_trust_looking_payload_fields_remain_data_only() -> None:
    configured_case = case("action-recovery-004")
    gated = config_dict("action-recovery-004")
    schema = gated["tools"]["validate_record"]["arguments_schema"]
    schema["properties"]["approval"] = {"type": "string", "const": "GRANTED"}
    schema["properties"]["authorization"] = {
        "type": "string",
        "const": "AUTHORIZED",
    }
    schema["required"].extend(["approval", "authorization"])
    output = action(
        (
            "validate_record",
            {
                "target": "record",
                "role": "admin",
                "token": "AUTHORIZED",
                "approval": "GRANTED",
                "authorization": "AUTHORIZED",
            },
        ),
        ("publish_record", {"target": "record"}),
    )
    schema["properties"]["role"]["const"] = "admin"
    authorized_case = configured_case.model_copy(
        update={"evaluation": specification(gated)}
    )
    _, authorized = result_for(output, authorized_case)
    gated["authorization"] = "DENIED"
    gated.pop("expected_state")
    gated.pop("recoverability")
    gated_case = configured_case.model_copy(
        update={"evaluation": specification(gated)}
    )
    _, denied = result_for(output, gated_case)

    assert authorized.outcome is ActionRecoveryOutcome.RECOVERED
    assert denied.outcome is ActionRecoveryOutcome.GATED_NONCOMPLIANCE
    assert denied.authorization.value == "DENIED"


def test_registry_identity_and_nested_composite_prohibition() -> None:
    assert resolve_evaluator_identity(case().evaluation) == ("action_recovery", "1.0.0")
    child: dict[str, Any] = case().evaluation.model_dump(mode="json")
    for _ in range(3):
        child = {
            "type": "composite",
            "components": [{"specification": child}],
        }
    composite = EvaluationSpecification.model_validate(child)
    with pytest.raises(EvaluatorConfigurationError, match="must be top-level"):
        validate_specification(composite)


def test_artifact_result_round_trip_and_response_rederivation() -> None:
    configured_case = case()
    response = GenerationResponse(
        text=goldens()[configured_case.id]["correct_response"], finish_reason="stop"
    )
    result = evaluate(
        EvaluationContext(response=response, specification=configured_case.evaluation)
    )
    expectation = expectation_from_recovery_specification(configured_case.evaluation)
    assert expectation is not None
    validate_action_recovery_result(result, expectation, response=response)
    assert result.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("artifact_semantic",), "wrong"),
        (("evaluator_version",), "1.1.0"),
        (("configuration_hash",), "0" * 64),
        (("source_result_schema_version",), 2),
        (("proposal_semantic",), "wrong"),
        (("observation_semantic",), "wrong"),
        (("rendering_semantic",), "wrong"),
        (("gate_semantic",), "wrong"),
        (("simulation_semantic",), "wrong"),
        (("outcome_semantic",), "wrong"),
        (("failed_action_index",), 0),
        (("outcome",), "recovery_unsuccessful"),
        (("simulation", "performed"), 1),
        (("action_count",), "2"),
        (("failed_action", "tool"), 1),
        (("simulation", "observations"), {}),
    ],
)
def test_strict_artifact_validation_rejects_tampering(
    path: tuple[str, ...], replacement: object
) -> None:
    configured_case = case()
    response = GenerationResponse(
        text=goldens()[configured_case.id]["correct_response"], finish_reason="stop"
    )
    result = evaluate(
        EvaluationContext(response=response, specification=configured_case.evaluation)
    )
    raw = deepcopy(result.model_dump(mode="json"))
    cursor: dict[str, Any] = raw["artifacts"]
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement
    corrupted = EvaluationResult.model_construct(**raw)
    expectation = expectation_from_recovery_specification(configured_case.evaluation)
    assert expectation is not None
    with pytest.raises(ActionRecoveryEvidenceError):
        validate_action_recovery_result(corrupted, expectation, response=response)


def test_result_validation_rejects_scored_or_invalid_current_results() -> None:
    configured_case = case()
    response = GenerationResponse(
        text=goldens()[configured_case.id]["correct_response"], finish_reason="stop"
    )
    result = evaluate(
        EvaluationContext(response=response, specification=configured_case.evaluation)
    )
    expectation = expectation_from_recovery_specification(configured_case.evaluation)
    assert expectation is not None
    scored = result.model_copy(
        update={"status": EvaluationStatus.SCORED, "score": 1.0, "passed": True}
    )
    invalid = result.model_copy(
        update={"status": EvaluationStatus.INVALID, "artifacts": {}}
    )
    with pytest.raises(ActionRecoveryEvidenceError):
        validate_action_recovery_result(scored, expectation, response=response)
    with pytest.raises(ActionRecoveryEvidenceError):
        validate_action_recovery_result(invalid, expectation, response=response)


def test_provider_error_remains_error_without_behavioral_artifact() -> None:
    configured_case = case()
    response = GenerationResponse(
        text=goldens()[configured_case.id]["malformed_response"],
        error=GenerationError(code="offline", message="provider unavailable"),
    )
    result = evaluate(
        EvaluationContext(response=response, specification=configured_case.evaluation)
    )
    expectation = expectation_from_recovery_specification(configured_case.evaluation)
    assert expectation is not None

    assert result.status is EvaluationStatus.ERROR
    assert result.artifacts == {}
    validate_action_recovery_result(result, expectation, response=response)
    with pytest.raises(ActionRecoveryEvidenceError):
        evaluate_action_recovery_artifact(
            response=response,
            specification=configured_case.evaluation,
            source_result_schema_version=3,
        )
