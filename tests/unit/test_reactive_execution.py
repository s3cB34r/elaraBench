"""Focused M5.4a deterministic Reactive outcome and observation coverage."""

import json
from pathlib import Path

import pytest

from elarabench.action_compliance import SyntheticToolDefinition
from elarabench.models import EvaluationStatus, GenerationResponse
from elarabench.reactive_execution import (
    ReactiveExecutionConfig,
    ReactiveOutcome,
    ReactiveRuntime,
    render_reactive_observation,
    step_reactive,
)


def _config(**updates: object) -> ReactiveExecutionConfig:
    values: dict[str, object] = {
        "authorization": "AUTHORIZED",
        "tools": {
            "open": SyntheticToolDefinition(
                arguments_schema={"type": "object", "additionalProperties": False},
                effects={"open": True},
            ),
            "finish": SyntheticToolDefinition(
                arguments_schema={"type": "object", "additionalProperties": False},
                requires={"open": True},
                effects={"done": True},
            ),
        },
        "initial_state": {},
        "expected_state": {"done": True},
        "max_plan_length": 2,
        "max_model_turns": 2,
        "max_total_actions": 2,
        "proposal_semantic": "action_control_envelope_v1",
        "gate_semantic": "static_authorization_gate_v1",
        "simulation_semantic": "synthetic_transition_v1",
        "outcome_semantic": "reactive_execution_outcomes_v1",
        "transcript_semantic": "reactive_transcript_v1",
        "observation_semantic": "reactive_observation_v1",
        "rendering_semantic": "reactive_observation_rendering_v1",
    }
    values.update(updates)
    return ReactiveExecutionConfig.model_validate(values)


def _response(text: str) -> GenerationResponse:
    return GenerationResponse(text=text)


def test_protocol_and_static_plan_outcomes() -> None:
    config = _config(max_total_actions=3)
    runtime = ReactiveRuntime(current_state={})
    assert (
        step_reactive(config, runtime, _response("not json")).outcome
        is ReactiveOutcome.PROTOCOL_INVALID
    )
    invalid = '{"type":"action","actions":[{"tool":"missing","arguments":{}}]}'
    assert (
        step_reactive(config, runtime, _response(invalid)).outcome
        is ReactiveOutcome.INVALID_ACTION_PLAN
    )


def test_recovery_observation_and_completion() -> None:
    config = _config(max_total_actions=3, expected_state={"open": True, "done": True})
    first = '{"type":"action","actions":[{"tool":"finish","arguments":{}}]}'
    runtime = step_reactive(config, ReactiveRuntime(current_state={}), _response(first))
    assert runtime.status is None
    assert runtime.steps[-1].observation is not None
    assert runtime.steps[-1].observation.outcome_per_action == ("precondition_failed",)
    assert "failed_action" in render_reactive_observation(runtime.steps[-1].observation)
    second = (
        '{"type":"action","actions":[{"tool":"open","arguments":{}},'
        '{"tool":"finish","arguments":{}}]}'
    )
    runtime = step_reactive(config, runtime, _response(second))
    assert runtime.status is EvaluationStatus.PENDING_REVIEW
    assert runtime.outcome is ReactiveOutcome.COMPLETED_AFTER_RECOVERY


def test_control_final_turn_uses_counterfactual_capacity() -> None:
    config = _config(
        max_model_turns=1,
        max_total_actions=1,
        max_plan_length=1,
        expected_state={"open": True},
    )
    control = _response('{"type":"control","operation":"refuse"}')
    runtime = step_reactive(config, ReactiveRuntime(current_state={}), control)
    assert runtime.status is EvaluationStatus.PENDING_REVIEW
    assert runtime.outcome is ReactiveOutcome.PREMATURE_STOP


GOLDENS = json.loads(Path("tests/fixtures/reactive_execution/golden.json").read_text())


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda row: row["id"])
def test_reactive_golden(reactive, golden):
    config = reactive.config(**golden["overrides"])
    runtime = ReactiveRuntime(current_state=config.initial_state)
    for text in golden["responses"]:
        assert runtime.status is None
        runtime = step_reactive(config, runtime, GenerationResponse(text=text))
    assert runtime.outcome.value == golden["outcome"]
    assert runtime.status.value == golden["status"]
    assert runtime.invoked_actions == golden["invoked_actions"]
    assert runtime.futile_occurrences == golden["futile_occurrences"]
    assert runtime.current_state == golden["final_state"]
    if golden["id"] == "e10_oversize":
        assert runtime.steps[-1].observation is None
    if golden["id"] == "e6":
        assert not runtime.steps[-1].futile_repeat


def test_unprovable_control_is_invalid(reactive):
    config = reactive.config(
        tools={"ambiguous": {
            "arguments_schema": {"type": "object", "properties": {
                "choice": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": ["choice"], "additionalProperties": False},
            "effects": {"done": True}}},
        expected_state={"done": True},
    )
    runtime = step_reactive(config, ReactiveRuntime(current_state={}),
                            _response('{"type":"control","operation":"refuse"}'))
    assert runtime.status is EvaluationStatus.INVALID
    assert runtime.outcome is None


def test_fixture_renderer_and_observation_golden(reactive):
    from elarabench.reactive_execution import render_reactive_task

    config = reactive.config()
    task = render_reactive_task(config, reactive.fixture["objective"])
    assert task == render_reactive_task(config, reactive.fixture["objective"])
    for field in ("Behavioral objective:", "open", "finish", "arguments_schema", "requires",
                  "max_plan_length", "max_model_turns", "max_total_actions"):
        assert field in task
    for hidden in ("expected_state", "initial_state", "reachability", "termination_reason"):
        assert hidden not in task
    first = GOLDENS[5]["responses"][0]
    runtime = step_reactive(config, ReactiveRuntime(current_state={}), _response(first))
    observation = runtime.steps[-1].observation
    assert json.loads(render_reactive_observation(observation)) == {
        "turn_index": 0, "attempted_actions": [{"tool": "open", "arguments": {}}],
        "outcome_per_action": ["applied"], "resulting_state": {"open": True},
        "turns_remaining": 3, "actions_remaining": 5,
    }
