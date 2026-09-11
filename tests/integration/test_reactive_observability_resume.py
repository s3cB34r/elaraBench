"""A precondition-failed inspection cannot acquire visibility through crash replay."""

from dataclasses import replace

import pytest

from elarabench.hashing import hash_suite
from elarabench.models import (
    ChatMessage,
    ChatRole,
    EvaluationSpecification,
    GenerationResponse,
    SampleIdentity,
)
from elarabench.reactive_engine import ReactiveTurnEngine
from elarabench.reactive_execution import (
    ReactiveExecutionConfig,
    render_reactive_task,
    replay_reactive,
)
from elarabench.scoring import _reactive_context, score_run, summarize_run
from elarabench.storage import RunArtifactStore


def test_precondition_failed_reveal_crash_replay(reactive, monkeypatch):
    loaded = reactive.suite()
    case = loaded.suite.cases[0]
    raw = dict(case.evaluation.config)
    raw.update(
        capability="information_sufficient",
        initial_state={"route": "left"},
        expected_state={"route": "left", "open": True, "done": True},
        observability={"observable_keys": [], "reveals": {"inspect": ["route"]}},
        observation_semantic="reactive_observation_v3",
        rendering_semantic="reactive_observation_rendering_v3",
    )
    raw["tools"] = dict(raw["tools"]) | {
        "inspect": {
            "arguments_schema": {"type": "object", "additionalProperties": False},
            "requires": {"open": True},
            "effects": {"open": True},
        }
    }
    config = ReactiveExecutionConfig.model_validate(raw)
    case = case.model_copy(
        update={
            "evaluation": EvaluationSpecification(type="reactive_execution", config=raw),
            "messages": (
                case.messages[0],
                ChatMessage(
                    role=ChatRole.USER, content=render_reactive_task(config, config.objective)
                ),
            ),
        }
    )
    suite = loaded.suite.model_copy(update={"cases": (case,)})
    loaded = replace(loaded, suite=suite, content_hash=hash_suite(suite, {}))
    monkeypatch.setattr(reactive, "suite", lambda **_: loaded)
    provider = reactive.provider()
    seen = []

    def generate(request):
        seen.append(request)
        if len(request.messages) == 2:
            text = '{"type":"action","actions":[{"tool":"inspect","arguments":{}}]}'
        else:
            text = (
                '{"type":"action","actions":[{"tool":"open","arguments":{}},'
                '{"tool":"finish","arguments":{}}]}'
            )
        return GenerationResponse(text=text)

    provider.generate = generate

    class Crash(BaseException):
        pass

    def crash(*_):
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(ReactiveTurnEngine, "next_request", crash)
        with pytest.raises(Crash):
            reactive.run(provider)
    path = reactive.root / "reactive-test"
    before = {p: p.read_bytes() for p in path.glob("samples/**/turns/**/*") if p.is_file()}
    result = reactive.runner(provider).resume(path)
    assert len(seen) == 2
    assert '"resulting_state":{}' in seen[-1].messages[-1].content
    store = RunArtifactStore(path.parent, path.name)
    state, _ = replay_reactive(
        _reactive_context(store, SampleIdentity(case_id=case.id, repeat_index=0), case.evaluation)
    )
    assert state.revealed_keys == frozenset()
    assert state.outcome.value == "completed_after_recovery"
    assert result.summary.reactive_observability.information_restraint_rate.headline_value == 0
    assert all(p.read_bytes() == value for p, value in before.items())
    assert score_run(path) == summarize_run(path) == result.summary
