"""First-invocation resume at each ratified durable crash boundary."""

import pytest

from elarabench.models import EvaluationStatus, SampleIdentity
from elarabench.reactive_engine import ReactiveTurnEngine
from elarabench.storage import RunArtifactStore


class Crash(BaseException):
    """Simulated abrupt process loss, not a provider or behavioral failure."""


@pytest.mark.parametrize(
    ("boundary", "owner", "method", "target", "after", "expected_calls"),
    [
        ("A", RunArtifactStore, "write_request", 0, True, [0, 1]),
        ("B", RunArtifactStore, "write_attempt", 1, True, [1]),
        ("C", RunArtifactStore, "write_response", 1, False, []),
        ("D", ReactiveTurnEngine, "consume", 0, False, [1]),
        ("E", ReactiveTurnEngine, "next_request", 1, False, [1]),
        ("F", RunArtifactStore, "write_request", 1, True, [1]),
        ("G", RunArtifactStore, "write_evaluation", 0, False, []),
    ],
    ids=list("ABCDEFG"),
)
@pytest.mark.parametrize("failure_enabled", [False, True])
@pytest.mark.parametrize("observability_enabled", [False, True])
def test_first_resume_crash_boundary(
    reactive, monkeypatch, boundary, owner, method, target, after, expected_calls, failure_enabled,
    observability_enabled,
):
    if failure_enabled:
        from dataclasses import replace

        from elarabench.hashing import hash_suite
        from elarabench.models import ChatMessage, ChatRole, EvaluationSpecification
        from elarabench.reactive_execution import ReactiveExecutionConfig, render_reactive_task

        loaded = reactive.suite()
        case = loaded.suite.cases[0]
        config = ReactiveExecutionConfig.model_validate(case.evaluation.config | {
            "capability": "retryable_failure",
            "failure_catalog": {"busy": "retryable", "closed": "permanent"},
            "failure_schedule": {"open": {"code": "busy", "transient_failures": 1}},
            "outcome_semantic": "reactive_execution_outcomes_v2",
            "observation_semantic": "reactive_observation_v2",
            "rendering_semantic": "reactive_observation_rendering_v2",
        })
        case = case.model_copy(update={
            "evaluation": EvaluationSpecification(type="reactive_execution",
                                                  config=config.model_dump(mode="json")),
            "messages": (case.messages[0], ChatMessage(
                role=ChatRole.USER, content=render_reactive_task(config, config.objective))),
        })
        suite = loaded.suite.model_copy(update={"cases": (case,)})
        loaded = replace(loaded, suite=suite, content_hash=hash_suite(suite, {}))
        monkeypatch.setattr(reactive, "suite", lambda **kwargs: loaded)
    if observability_enabled:
        from dataclasses import replace

        from elarabench.hashing import hash_suite
        from elarabench.models import ChatMessage, ChatRole, EvaluationSpecification
        from elarabench.reactive_execution import ReactiveExecutionConfig, render_reactive_task

        loaded = reactive.suite()
        case = loaded.suite.cases[0]
        config = ReactiveExecutionConfig.model_validate(case.evaluation.config | {
            "observability": {"observable_keys": [], "reveals": {"open": ["open"]}},
            "observation_semantic": "reactive_observation_v3",
            "rendering_semantic": "reactive_observation_rendering_v3",
        })
        case = case.model_copy(update={
            "evaluation": EvaluationSpecification(type="reactive_execution",
                                                  config=config.model_dump(mode="json")),
            "messages": (case.messages[0], ChatMessage(
                role=ChatRole.USER, content=render_reactive_task(config, config.objective))),
        })
        suite = loaded.suite.model_copy(update={"cases": (case,)})
        loaded = replace(loaded, suite=suite, content_hash=hash_suite(suite, {}))
        monkeypatch.setattr(reactive, "suite", lambda **kwargs: loaded)
    original = getattr(owner, method)

    def interrupted(self, *args, **kwargs):
        index = (self._turn_index if isinstance(self, RunArtifactStore)
                 else self.runtime.durable_model_responses)
        if index == target:
            if after:
                original(self, *args, **kwargs)
            raise Crash()
        return original(self, *args, **kwargs)

    provider = reactive.provider(retry_turn=1 if boundary == "B" else None)
    if failure_enabled:
        from elarabench.models import GenerationResponse

        generate = provider.generate

        def failure_response(request):
            response = generate(request)
            if response.error is None and len(request.messages) > 2:
                return GenerationResponse(text=(
                    '{"type":"action","actions":[{"tool":"open","arguments":{}},'
                    '{"tool":"finish","arguments":{}}]}'))
            return response

        provider.generate = failure_response
    with monkeypatch.context() as patch:
        patch.setattr(owner, method, interrupted)
        with pytest.raises(Crash):
            reactive.run(provider)
    path = reactive.root / "reactive-test"
    store = RunArtifactStore(reactive.root, "reactive-test")
    identity = SampleIdentity(case_id="reactive", repeat_index=0)
    view = store.for_turn(0 if boundary == "A" else 1)
    if boundary in ("A", "B", "C", "F"):
        assert view.request_exists(identity)
        assert not view.response_exists(identity)
        attempts = view.read_attempts(identity)
        assert len(attempts) == (1 if boundary in ("B", "C") else 0)
        if boundary == "B":
            assert attempts[-1].response.error.retryable
        if boundary == "C":
            assert attempts[-1].response.error is None
    if boundary in ("D", "E"):
        assert store.response_exists(identity)
        assert not view.request_exists(identity)
    if boundary == "G":
        assert view.response_exists(identity)
        assert not store.evaluation_exists(identity)
    immutable = {p: p.read_bytes() for p in path.glob("samples/**/turns/**/*")
                 if p.is_file()}
    before = list(provider.calls)
    # Exactly ONE resume invocation: no second-call self-healing is allowed.
    result = reactive.runner(provider).resume(path)
    assert provider.calls[len(before):] == expected_calls
    assert provider.calls[:len(before)] == before
    for file, content in immutable.items():
        assert file.read_bytes() == content
    assert store.turn_indices(identity, reactive=True) == (0, 1)
    assert store.read_evaluation(
        identity, source_result_schema_version=4).status is EvaluationStatus.SCORED
    if failure_enabled:
        evaluation = store.read_evaluation(identity, source_result_schema_version=4)
        assert evaluation.score == 1
        assert evaluation.artifacts["reactive_execution"]["outcome"] == (
            "completed_after_execution_failure")
    if observability_enabled:
        from elarabench.reactive_execution import replay_reactive
        from elarabench.scoring import _reactive_context

        case = reactive.suite().suite.cases[0]
        context = _reactive_context(store, identity, case.evaluation)
        state, _ = replay_reactive(context)
        assert state.revealed_keys == frozenset({"open"})
        initial = state.steps[0].observation.resulting_state
        assert initial == ({} if failure_enabled else {"open": True})
        assert "revealed_keys" not in str(store.read_evaluation(
            identity, source_result_schema_version=4).model_dump())
    assert result.manifest.schema_version == 4
    for index in (0, 1):
        view = store.for_turn(index)
        assert view.read_response(identity) == view.read_attempts(identity)[-1].response
    assert provider.calls.count(0) == 1
    assert provider.calls.count(1) == (2 if boundary == "B" else 1)


def test_request_only_resume_rejects_byte_only_tampering(reactive, monkeypatch):
    original = RunArtifactStore.write_request

    def crash_after_request(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise Crash()

    provider = reactive.provider()
    with monkeypatch.context() as patch:
        patch.setattr(RunArtifactStore, "write_request", crash_after_request)
        with pytest.raises(Crash):
            reactive.run(provider)
    path = reactive.root / "reactive-test"
    request = next(path.glob("samples/**/turns/000/request.json"))
    tampered = request.read_bytes() + b" "
    request.write_bytes(tampered)
    with pytest.raises(RuntimeError, match="canonical Request bytes mismatch"):
        reactive.runner(provider).resume(path)
    assert provider.calls == []
    assert request.read_bytes() == tampered
