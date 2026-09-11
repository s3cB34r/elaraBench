"""Adversarial completion projection and bounded blind-policy proof tests."""

from dataclasses import replace

import pytest

from elarabench.hashing import canonical_json_bytes
from elarabench.models import GenerationResponse
from elarabench.reactive_execution import ReactiveRuntime, step_reactive
from elarabench.reactive_execution_corpus import (
    MAX_PRODUCT_NODES,
    CorpusFindingCode,
    ProductState,
    ProductStatus,
    advance_product,
    analyze_blind_policy_group,
    canonical_plans,
    project_variant,
)

OPEN = '{"tool":"open","arguments":{}}'
FINISH = '{"tool":"finish","arguments":{}}'


def plan(*actions):
    return '{"type":"action","actions":[' + ",".join(actions) + "]}"


def test_independent_action_budgets_and_oversized_variant(reactive):
    config = reactive.config(
        expected_state={"never": True}, max_total_actions=4, max_model_turns=4, max_plan_length=3
    )
    node = ProductState(canonical_json_bytes({"open": True}), canonical_json_bytes({}))
    first = advance_product(config, config, node, plan(FINISH, OPEN))
    assert (first.actions_used_A, first.actions_used_B) == (2, 1)
    assert first.status_A is first.status_B is ProductStatus.ALIVE
    second = advance_product(config, config, first, plan(FINISH, OPEN, OPEN))
    assert second.status_A is ProductStatus.DEAD  # 3 proposed > A's 2 remaining.
    assert second.state_A == first.state_A and second.actions_used_A == 2
    assert second.status_B is ProductStatus.ALIVE  # Failure consumed only one invocation.
    assert second.actions_used_B == 2 and second.turns_used == 2


def test_done_variant_freezes_while_other_continues(reactive):
    config = reactive.config(max_total_actions=3)
    node = ProductState(canonical_json_bytes({"open": True}), canonical_json_bytes({}))
    first = advance_product(config, config, node, plan(FINISH))
    assert first.status_A is ProductStatus.DONE and first.status_B is ProductStatus.ALIVE
    second = advance_product(config, config, first, plan(OPEN, FINISH))
    assert second.status_A is second.status_B is ProductStatus.DONE
    assert second.actions_used_A == 1 and second.state_A == first.state_A
    assert second.actions_used_B == 3  # Goal wins on final available Action.


def test_blind_witness_rejected_and_order_deterministic(reactive):
    a = reactive.config(initial_state={"open": True}, max_total_actions=3)
    b = reactive.config(max_total_actions=3)
    first = analyze_blind_policy_group(a, b)
    assert first.error is CorpusFindingCode.BLIND_COMPLETES
    assert first.witness
    assert analyze_blind_policy_group(a, b) == first
    sequences = [tuple(a.tool for a in p) for p in canonical_plans(a)]
    assert sequences == sorted(sequences)


def test_dead_pruning_and_node_cap_boundary(reactive):
    dead = reactive.config(
        max_model_turns=1, max_total_actions=1, max_plan_length=1, expected_state={"never": True}
    )
    result = analyze_blind_policy_group(dead, dead, node_limit=1)
    assert result.error is None and result.expanded_nodes == 1
    live = dead.model_copy(update={"max_model_turns": 4, "max_total_actions": 4})
    overflow = analyze_blind_policy_group(live, live, node_limit=2)
    assert overflow.error is CorpusFindingCode.ENUMERATION
    assert overflow.expanded_nodes == 2  # Expansion 3 is refused, not counted as proof.
    assert MAX_PRODUCT_NODES == 250000
    with pytest.raises(ValueError):
        analyze_blind_policy_group(live, live, node_limit=250001)


@pytest.mark.parametrize("text", [plan(OPEN), plan(FINISH), plan(OPEN, FINISH), plan(FINISH, OPEN)])
@pytest.mark.parametrize("actions_used", [0, 5])
@pytest.mark.parametrize("turns_used", [0, 3])
def test_projection_matches_full_runtime_with_history(reactive, text, actions_used, turns_used):
    config = reactive.config()
    failed = next(p[0] for p in canonical_plans(config) if p[0].tool == "finish")
    full = ReactiveRuntime(
        {},
        invoked_actions=actions_used,
        durable_model_responses=turns_used,
        precondition_failures=1,
        failed_action=failed,
        state_at_failure={},
        futile_occurrences=1,
    )
    full = step_reactive(config, full, GenerationResponse(text=text))
    state, status, actions, failures, contact, precondition = project_variant(
        config, b"{}", ProductStatus.ALIVE, actions_used, turns_used, text
    )
    assert failures == () and contact is False and precondition is False
    assert state == canonical_json_bytes(full.current_state)
    assert actions == full.invoked_actions
    assert (status is ProductStatus.DONE) == (
        full.outcome is not None
        and full.outcome.value
        in {"completed_without_execution_failure", "completed_after_recovery"}
    )
    assert (status is ProductStatus.ALIVE) == (full.status is None)


def test_canonical_arguments_do_not_change_transition(reactive):
    config = reactive.config(initial_state={"open": True})
    runtime = ReactiveRuntime(config.initial_state)
    canonical = step_reactive(config, runtime, GenerationResponse(text=plan(FINISH)))
    changed = step_reactive(
        config,
        runtime,
        GenerationResponse(text=plan('{"tool":"finish","arguments":{"label":"different"}}')),
    )
    assert replace(canonical, steps=()) == replace(changed, steps=())


def test_m54_production_verdict_node_and_hash_pins():
    from collections import defaultdict

    from elarabench.benchmark import load_benchmark_suite
    from elarabench.builtin import get_builtin_suite_path
    from elarabench.reactive_execution import ReactiveExecutionConfig

    loaded = load_benchmark_suite(get_builtin_suite_path("reactive_execution.core"))
    assert loaded.content_hash == (
        "6c0d74ddebe5f94e68498c4b31eb4cd494272f9ef79b52b8b0b094776890f498")
    groups = defaultdict(list)
    for case in loaded.suite.cases:
        for tag in case.tags:
            if tag.startswith("contrastive-group-"):
                groups[tag].append(ReactiveExecutionConfig.model_validate(case.evaluation.config))
    results = [analyze_blind_policy_group(*groups[g]) for g in sorted(groups)]
    assert all(r.error is None for r in results)
    assert [r.expanded_nodes for r in results] == [8, 8, 17, 17, 17, 17]
