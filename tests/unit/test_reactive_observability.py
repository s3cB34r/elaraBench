"""M5.6 projection and trusted ephemeral visibility."""

import json

import pytest
from pydantic import ValidationError

from elarabench.hashing import canonical_json_bytes
from elarabench.models import GenerationResponse
from elarabench.reactive_execution import (
    ObservabilitySpec,
    ReactiveRuntime,
    render_reactive_task,
    step_reactive,
)


def route_config(reactive, **updates):
    raw = dict(
        initial_state={"stage": "open", "route": "left", "done": False},
        expected_state={"stage": "closed", "route": "aligned", "done": True},
        tools={
            "inspect": {
                "arguments_schema": {"type": "object", "additionalProperties": False},
                "requires": {"stage": "open"},
                "effects": {"stage": "open"},
            },
            "left": {
                "arguments_schema": {"type": "object", "additionalProperties": False},
                "requires": {"route": "left"},
                "effects": {"route": "aligned"},
            },
            "right": {
                "arguments_schema": {"type": "object", "additionalProperties": False},
                "requires": {"route": "right"},
                "effects": {"route": "aligned"},
            },
            "finish": {
                "arguments_schema": {"type": "object", "additionalProperties": False},
                "requires": {"route": "aligned"},
                "effects": {"stage": "closed", "done": True},
            },
        },
        observability={"observable_keys": ["stage", "done"], "reveals": {"inspect": ["route"]}},
        observation_semantic="reactive_observation_v3",
        rendering_semantic="reactive_observation_rendering_v3",
        max_total_actions=6,
        max_model_turns=4,
        max_plan_length=3,
    )
    return reactive.config(**(raw | updates))


def response(*tools):
    return GenerationResponse(
        text=json.dumps(
            {"type": "action", "actions": [{"tool": tool, "arguments": {}} for tool in tools]}
        )
    )


def test_initial_projection_and_none_identity(reactive):
    config = route_config(reactive)
    task = render_reactive_task(config, "Align the route and finish.")
    assert 'Initial observed state: {"done":false,"stage":"open"}' in task
    assert 'Inspection tools: {"inspect":["route"]}' in task
    assert '"route":"left"' in task  # Visible preconditions may name hidden values.
    assert 'Initial observed state: {"done":false,"route"' not in task
    old = reactive.config()
    raw = reactive.fixture["config"]
    assert "observability" not in raw
    assert render_reactive_task(old, "Finish") == render_reactive_task(
        reactive.config(observability=None), "Finish"
    )
    assert "Observable state keys:" not in render_reactive_task(old, "Finish")
    assert canonical_json_bytes(raw) == canonical_json_bytes(reactive.fixture["config"])


@pytest.mark.parametrize(
    "raw",
    [
        {"observable_keys": [4]},
        {"observable_keys": [], "extra": True},
        {"observable_keys": [], "reveals": {"bad name": ["x"]}},
    ],
)
def test_observability_field_validation(raw):
    with pytest.raises(ValidationError):
        ObservabilitySpec.model_validate(raw)


def test_reveal_success_and_same_plan_uses_true_state(reactive):
    config = route_config(reactive)
    runtime = step_reactive(
        config, ReactiveRuntime(config.initial_state), response("inspect", "left")
    )
    assert runtime.revealed_keys == frozenset({"route"})
    assert runtime.current_state["route"] == "aligned"
    assert runtime.steps[-1].observation.resulting_state == runtime.current_state
    assert runtime.invoked_actions == 2
    final = step_reactive(config, runtime, response("finish"))
    assert final.revealed_keys == runtime.revealed_keys
    assert final.outcome.value == "completed_without_execution_failure"


@pytest.mark.parametrize("failure", ["precondition", "execution", "suffix"])
def test_failed_or_unexecuted_reveal_does_not_disclose(reactive, failure):
    config = route_config(reactive)
    raw = config.model_dump(mode="json")
    actions = ("inspect",)
    if failure == "precondition":
        raw["tools"]["inspect"]["requires"] = {"stage": "closed"}
    elif failure == "execution":
        raw.update(
            failure_catalog={"busy": "retryable"},
            failure_schedule={"inspect": {"code": "busy", "transient_failures": 1}},
            outcome_semantic="reactive_execution_outcomes_v2",
        )
    else:
        actions = ("right", "inspect")
    config = type(config).model_validate(raw)
    runtime = step_reactive(config, ReactiveRuntime(config.initial_state), response(*actions))
    assert runtime.revealed_keys == frozenset()
    assert "route" not in runtime.steps[-1].observation.resulting_state
    assert runtime.current_state["route"] == "left"


def test_applied_reveal_survives_later_failure(reactive):
    config = route_config(reactive)
    runtime = step_reactive(
        config, ReactiveRuntime(config.initial_state), response("inspect", "right")
    )
    assert runtime.revealed_keys == frozenset({"route"})
    assert runtime.steps[-1].observation.resulting_state["route"] == "left"


def test_config_semantic_and_tool_references(reactive):
    with pytest.raises(ValueError, match="known tools"):
        route_config(reactive, observability={"observable_keys": [], "reveals": {"missing": ["x"]}})
    with pytest.raises(ValueError, match="semantic version"):
        route_config(reactive, observation_semantic="reactive_observation_v2")
