"""E5-only product projection, conditional history, and information-safe witnesses."""

from dataclasses import replace

import pytest
from test_reactive_observability import response, route_config
from test_reactive_observability_corpus_validation import fixture_corpus, mutate

from elarabench.hashing import canonical_json_bytes
from elarabench.reactive_execution import ReactiveCapability, ReactiveRuntime, step_reactive
from elarabench.reactive_execution_corpus import (
    CorpusFindingCode,
    ProductState,
    ProductStatus,
    advance_product,
    analyze_blind_policy_group,
)
from elarabench.reactive_observability_corpus import (
    perfect,
    validate_reactive_observability_corpus,
    witness_samples,
)
from elarabench.synthetic_reachability import ReachabilityStatus


@pytest.mark.parametrize(
    "cap", ["information_required", "information_sufficient", "first_pass", "retryable_failure"]
)
def test_history_collision_is_conditional(reactive, cap):
    config = route_config(reactive, capability=cap)
    start = ProductState(
        canonical_json_bytes(config.initial_state), canonical_json_bytes(config.initial_state)
    )
    clean = advance_product(config, config, start, response("inspect").text)
    failed = advance_product(config, config, start, response("right").text)
    assert clean.state_A == failed.state_A
    assert clean.actions_used_A == failed.actions_used_A == 1
    assert clean.turns_used == failed.turns_used == 1
    if cap.startswith("information_"):
        assert clean != failed
        assert failed.precondition_failure_seen_A and failed.precondition_failure_seen_B
        assert (
            replace(failed, precondition_failure_seen_A=False, precondition_failure_seen_B=False)
            == clean
        )
        for text in (response("left").text, response("finish").text):
            clean = advance_product(config, config, clean, text)
            failed = advance_product(config, config, failed, text)
        assert clean.status_A is clean.status_B is ProductStatus.DONE
        assert failed.status_A is failed.status_B is ProductStatus.DEAD
    else:
        assert clean == failed
        assert not clean.precondition_failure_seen_A


@pytest.mark.parametrize("order", [("left", "right"), ("right", "left")])
def test_enumeration_and_exact_binary_proof(reactive, order):
    a = route_config(reactive, capability="information_required", max_total_actions=4)
    b = a.model_copy(update={"initial_state": a.initial_state | {"route": "right"}})
    assert analyze_blind_policy_group(a, b).error is None
    for config in (a, b):
        runtime = step_reactive(
            config, ReactiveRuntime(config.initial_state), response(*order, "finish")
        )
        if runtime.status is None:
            runtime = step_reactive(config, runtime, response(order[1], "finish"))
        while runtime.status is None:
            runtime = step_reactive(config, runtime, response("finish"))
        assert runtime.precondition_failures > 0
        assert runtime.outcome.value != "completed_without_execution_failure"


def test_safe_witness_and_malicious_closure(reactive):
    loaded = fixture_corpus(reactive)
    pair = loaded.suite.cases[:2]
    assert all(s.result.score == 1 for s in witness_samples(pair, perfect))
    hidden_routes = iter(c.evaluation.config["initial_state"]["route"] for c in pair)

    def malicious(request, turn):
        if turn == 0:
            return response("c_left" if next(hidden_routes) == "left" else "d_right").text
        return response("b_finish").text

    with pytest.raises(
        ValueError, match="identical Request received conflicting strategy Responses"
    ):
        witness_samples(pair, malicious)
    errors = validate_reactive_observability_corpus(loaded, witness=lambda *_: "{}").errors
    assert any("gate 21:" in f.message for f in errors)


def test_unprovable_and_node_cap_fail_closed(reactive, monkeypatch):
    from elarabench import reactive_observability_corpus as corpus

    loaded = fixture_corpus(reactive)
    original = corpus.analyze_bounded_reachability

    def unprovable(*args, **kwargs):
        return replace(original(*args, **kwargs), status=ReachabilityStatus.UNPROVABLE)

    with monkeypatch.context() as patch:
        patch.setattr(corpus, "analyze_bounded_reachability", unprovable)
        assert any(
            "gate 18:" in f.message
            for f in corpus.validate_reactive_observability_corpus(loaded).errors
        )
    a = route_config(reactive, capability=ReactiveCapability.INFORMATION_REQUIRED)
    b = a.model_copy(update={"initial_state": a.initial_state | {"route": "right"}})
    proof = analyze_blind_policy_group(a, b, node_limit=1)
    assert proof.error is CorpusFindingCode.ENUMERATION and proof.expanded_nodes == 1


def test_pair_with_blind_completion_is_rejected(reactive):
    import copy

    loaded = fixture_corpus(reactive)
    for index in (0, 1):
        raw = copy.deepcopy(loaded.suite.cases[index].evaluation.config)
        raw["tools"]["c_left"]["requires"] = {"stage": "open"}
        loaded = mutate(loaded, index, raw=raw)
    errors = validate_reactive_observability_corpus(loaded).errors
    assert any(f.code is CorpusFindingCode.BLIND_COMPLETES for f in errors)
