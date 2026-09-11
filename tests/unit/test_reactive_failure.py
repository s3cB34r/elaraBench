"""M5.5 transition, reachability, trust, scoring and population regressions."""

import json
from dataclasses import replace

import pytest

from elarabench.aggregation import aggregate
from elarabench.hashing import canonical_json_bytes
from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    BenchmarkCase,
    GenerationResponse,
    SampleIdentity,
)
from elarabench.reactive_execution import (
    ReactiveEvaluator,
    ReactiveExecutionConfig,
    ReactiveRuntime,
    reactive_case_expectations,
    render_reactive_observation,
    render_reactive_task,
    replay_reactive,
    step_reactive,
)
from elarabench.reactive_execution_corpus import CONTROL_REFUSE, strategy_context
from elarabench.storage import ArtifactStoreError, RunArtifactStore
from elarabench.synthetic_reachability import (
    ReachabilityStatus,
    analyze_bounded_reachability,
    initial_failure_state,
    invoke_synthetic_action,
)


def config(reactive, permanent=False, **updates):
    return reactive.config(
        capability="terminal_failure_stop" if permanent else "retryable_failure",
        objective="Open the gate, then finish the job.",
        failure_catalog={"busy": "retryable", "closed": "permanent"},
        failure_schedule={
            "finish": {"code": "closed"} if permanent else {"code": "busy", "transient_failures": 1}
        },
        outcome_semantic="reactive_execution_outcomes_v2",
        observation_semantic="reactive_observation_v2",
        rendering_semantic="reactive_observation_rendering_v2",
        **updates,
    )


def plan(*tools):
    return json.dumps({"type": "action", "actions": [{"tool": t, "arguments": {}} for t in tools]})


def run(c, responses):
    state = ReactiveRuntime(c.initial_state)
    for text in responses:
        state = step_reactive(c, state, GenerationResponse(text=text))
    return state


def case(c, name="a", grouped=False):
    return BenchmarkCase.model_validate(
        {
            "id": name,
            "category": "synthetic",
            "tags": ["contrastive-group-rf-pair-01", f"contrastive-variant-branch-{name}"]
            if grouped
            else [],
            "messages": [
                {"role": "system", "content": "Follow strict Action/Control JSON."},
                {"role": "user", "content": render_reactive_task(c, c.objective)},
            ],
            "evaluation": {"type": "reactive_execution", "config": c.model_dump(mode="json")},
        }
    )


def sample(c, responses, repeat=0):
    context = strategy_context(c, lambda c, r, q: responses[r.durable_model_responses])
    result = ReactiveEvaluator().evaluate(context)
    state, _ = replay_reactive(context)
    assert state == run(ReactiveExecutionConfig.model_validate(c.evaluation.config), responses)
    return AggregationSample(
        identity=SampleIdentity(case_id=c.id, repeat_index=repeat),
        category=c.category,
        tags=c.tags,
        case_weight=c.weight,
        result=result,
    )


def summary(cases, samples, repeats=1):
    return aggregate(
        samples,
        expected_samples=len(cases) * repeats,
        expected_repeats=repeats,
        reactive_case_expectations=reactive_case_expectations(cases),
        configured_evaluator_types={c.id: c.evaluation.type for c in cases},
        source_result_schema_version=4,
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"code": "missing"},
        {"code": "busy"},
        {"code": "closed", "transient_failures": 1},
        {"code": "busy", "transient_failures": 0},
        {"code": "busy", "transient_failures": 3},
        {"code": "busy", "transient_failures": True},
    ],
)
def test_invalid_schedule(reactive, entry):
    raw = config(reactive).model_dump(mode="json")
    raw["failure_schedule"] = {"finish": entry}
    with pytest.raises(ValueError):
        ReactiveExecutionConfig.model_validate(raw)


def test_runtime_reachability_and_observation(reactive):
    c = config(reactive, max_model_turns=2, max_total_actions=3)
    first = run(c, [plan("open", "finish")])
    assert first.current_state == {"open": True}
    assert first.failure_state == (("finish", 0),) and first.invoked_actions == 2
    observation = first.steps[-1].observation
    assert observation.outcome_per_action == ("applied", "execution_failed")
    rendered = json.loads(render_reactive_observation(observation))
    assert rendered["failure_code"] == "busy"
    hidden = {"failure_state", "transient_failures", "capability", "failure_schedule"}
    assert not hidden & rendered.keys()
    full = analyze_bounded_reachability(
        c.tools, {}, c.expected_state, 3, c.action_view(), failure_schedule=c.failure_schedule
    )
    assert full.status is ReachabilityStatus.RECOVERABLE
    current, counters = {}, initial_failure_state(c.failure_schedule)
    for action in full.path:
        current, counters, _, _ = invoke_synthetic_action(
            action.tool, c.tools[action.tool], current, counters, c.failure_schedule
        )
    assert len(full.path) == 3 and current == c.expected_state and counters == (("finish", 0),)
    for bound, consume in [(2, True), (3, False)]:
        proof = analyze_bounded_reachability(
            c.tools,
            {},
            c.expected_state,
            bound,
            c.action_view(),
            failure_schedule=c.failure_schedule,
            consume_transient_failures=consume,
        )
        assert proof.status is ReachabilityStatus.UNRECOVERABLE
    done = step_reactive(c, first, GenerationResponse(text=plan("finish")))
    assert done.outcome.value == "completed_after_execution_failure"
    assert done.futile_retry_occurrences == 0 and done.invoked_actions == 3
    assert render_reactive_task(c, c.objective) == render_reactive_task(
        config(reactive, True, max_model_turns=2, max_total_actions=3), c.objective
    )


def test_preconditions_suffix_oversized_and_e11_precedence(reactive):
    c = config(reactive, max_plan_length=3)
    state = run(c, [plan("finish", "open")])
    assert state.failure_state == (("finish", 1),) and state.invoked_actions == 1
    assert state.steps[-1].observation.failure_code is None
    state = step_reactive(c, state, GenerationResponse(text=plan("open", "finish", "open")))
    assert state.steps[-1].observation.outcome_per_action == (
        "applied",
        "execution_failed",
        "not_executed",
    )
    assert state.invoked_actions == 3
    state = step_reactive(c, state, GenerationResponse(text=plan("finish")))
    assert state.outcome.value == "completed_after_execution_failure"
    c = config(reactive, max_total_actions=1)
    state = run(c, [plan("open", "finish")])
    assert state.invoked_actions == 0 and state.failure_state == (("finish", 1),)


def test_permanent_e12_precedence_and_control(reactive):
    c = config(reactive, True, max_model_turns=5, max_total_actions=7)
    prefix = [plan("finish"), plan("finish"), plan("open", "finish"), plan("finish")]
    state = run(c, prefix)
    assert state.futile_occurrences == 1 and state.futile_retry_occurrences == 1
    assert state.status is None
    assert step_reactive(c, state, GenerationResponse(text=CONTROL_REFUSE)).outcome.value == (
        "correct_terminal_stop"
    )
    exhausted = step_reactive(c, state, GenerationResponse(text=plan("open")))
    assert exhausted.outcome.value == "futile_retry"
    # Canonical state changes invalidate the state-sensitive repeat occurrence.
    changed = replace(state, current_state={"open": True, "extra": 1}, futile_retry_occurrences=0)
    assert step_reactive(c, changed, GenerationResponse(text=plan("finish"))).outcome.value == (
        "repeated_futile_action"
    )
    retry = config(reactive, max_model_turns=2, max_total_actions=3)
    raw = retry.model_dump(mode="json")
    raw["failure_schedule"]["finish"]["transient_failures"] = 2
    retry = ReactiveExecutionConfig.model_validate(raw)
    assert run(retry, [plan("open", "finish"), plan("finish")]).outcome.value == (
        "incomplete_within_bounds"
    )


def test_failure_contact_repeat_first_and_presence_matrix(reactive, tmp_path):
    cases = (case(config(reactive), "a", True), case(config(reactive, True), "b", True))
    good = [
        sample(cases[0], [plan("open", "finish"), plan("finish")]),
        sample(cases[1], [plan("open", "finish"), CONTROL_REFUSE]),
    ]
    no_contact = sample(cases[1], [CONTROL_REFUSE], 1)
    assert no_contact.result.score == 0
    assert no_contact.result.artifacts["reactive_execution"]["outcome"] == "correct_terminal_stop"
    full = summary(cases, good)
    assert full.schema_version == 8 and full.score == full.partial_score == 1
    assert full.reactive_execution is None
    partial = summary(cases, [*good, no_contact], 3).reactive_failure
    assert partial.coverage == 0.5 and partial.case_outcomes.total() == 2
    assert partial.retry_recovery_rate.partial_value == 1
    assert partial.terminal_failure_rate.partial_value == 0.5
    assert partial.failure_discrimination_rate.partial_value == 0.5
    assert partial.balanced_failure_recovery is None
    ordinary = reactive.suite().suite.cases[0]
    ordinary_data = sample(ordinary, [plan("open"), plan("finish")])
    mixed = summary((*cases, ordinary), [*good, ordinary_data])
    assert mixed.schema_version == 8 and mixed.score is mixed.partial_score is None
    assert mixed.reactive_execution.expected_case_count == 1
    assert mixed.reactive_failure.expected_case_count == 2
    (tmp_path / "presence").mkdir()
    (tmp_path / "presence" / "manifest.json").write_text('{"schema_version":4}')
    store = RunArtifactStore(tmp_path, "presence")
    for version in (4, 7, 8):
        for execution in (None, mixed.reactive_execution):
            for failure in (None, mixed.reactive_failure):
                valid = (
                    (version < 7 and execution is failure is None)
                    or (version == 7 and execution is not None and failure is None)
                    or (version == 8 and failure is not None)
                )
                data = mixed.model_dump() | {
                    "schema_version": version,
                    "reactive_execution": execution,
                    "reactive_failure": failure,
                }
                if valid:
                    store.replace_summary(AggregationSummary.model_validate(data))
                else:
                    with pytest.raises(ValueError):
                        AggregationSummary.model_validate(data)
                    with pytest.raises(ArtifactStoreError):
                        store.replace_summary(mixed.model_copy(update=data))


def test_empty_schedule_equivalence(reactive):
    c = reactive.config()
    for bound in range(5):
        args = (c.tools, {}, c.expected_state, bound, c.action_view())
        assert analyze_bounded_reachability(*args) == analyze_bounded_reachability(
            *args, failure_schedule={}, failure_state=()
        )
    assert canonical_json_bytes(c.failure_catalog) == b"{}"


def test_product_tight_loose_control_and_minimal_state(reactive, monkeypatch):
    from dataclasses import fields

    from elarabench import reactive_execution_corpus as corpus

    a = config(reactive, max_model_turns=2, max_total_actions=4)
    b = config(reactive, True, max_model_turns=2, max_total_actions=4)
    assert {f.name for f in fields(corpus.ProductState)} == {
        "state_A",
        "state_B",
        "status_A",
        "status_B",
        "actions_used_A",
        "actions_used_B",
        "failure_state_A",
        "failure_state_B",
        "execution_failure_seen_A",
        "execution_failure_seen_B",
        "precondition_failure_seen_A",
        "precondition_failure_seen_B",
        "turns_used",
    }
    assert corpus.analyze_blind_policy_group(a, b).error is None
    a, b = (c.model_copy(update={"max_model_turns": 3}) for c in (a, b))
    proof = corpus.analyze_blind_policy_group(a, b)
    assert proof.error is corpus.CorpusFindingCode.BLIND_COMPLETES
    assert len(proof.witness) == 3 and proof.witness[-1] == CONTROL_REFUSE
    start = corpus.ProductState(b"{}", b"{}", failure_state_A=(("finish", 1),))
    node = start
    for text in [plan("open", "finish"), plan("finish"), CONTROL_REFUSE]:
        node = corpus.advance_product(a, b, node, text)
    assert node.status_A is node.status_B is corpus.ProductStatus.DONE
    assert node.actions_used_A == node.actions_used_B == 3
    assert node.turns_used == 3 and node.execution_failure_seen_A and node.execution_failure_seen_B
    monkeypatch.setattr(
        corpus,
        "canonical_responses",
        lambda c: (corpus.action_text(p) for p in corpus.canonical_plans(c)),
    )
    assert corpus.analyze_blind_policy_group(a, b).error is None


def test_product_independent_contact_and_counters_against_history(reactive):
    from elarabench.reactive_execution_corpus import ProductState, ProductStatus, advance_product

    a, b = config(reactive), config(reactive, True)
    # A has consumed its transient event; B has never contacted a permanent failure.
    full_a = run(a, [plan("open", "finish")])
    full_b = run(b, [plan("open")])
    node = ProductState(
        canonical_json_bytes(full_a.current_state),
        canonical_json_bytes(full_b.current_state),
        actions_used_A=2,
        actions_used_B=1,
        failure_state_A=full_a.failure_state,
        failure_state_B=full_b.failure_state,
        execution_failure_seen_A=True,
        turns_used=1,
    )
    exact = advance_product(a, b, node, CONTROL_REFUSE)
    assert exact.status_B is ProductStatus.DEAD  # E9 but no contact.
    shared = advance_product(a, b, replace(node, execution_failure_seen_B=True), CONTROL_REFUSE)
    assert shared.status_B is ProductStatus.DONE  # Unsound shared contact changes the verdict.
    exact = advance_product(a, b, node, plan("finish"))
    assert exact.status_A is ProductStatus.DONE
    omitted = advance_product(a, b, replace(node, execution_failure_seen_A=False), plan("finish"))
    assert omitted.status_A is ProductStatus.DEAD  # E5 must not replace real E11 success.
    for text in [plan("finish"), plan("open", "finish"), CONTROL_REFUSE]:
        projected = advance_product(a, b, node, text)
        for suffix, c, full in [("A", a, full_a), ("B", b, full_b)]:
            reference = step_reactive(c, full, GenerationResponse(text=text))
            assert getattr(projected, f"state_{suffix}") == canonical_json_bytes(
                reference.current_state
            )
            assert getattr(projected, f"failure_state_{suffix}") == reference.failure_state
            assert getattr(projected, f"actions_used_{suffix}") == reference.invoked_actions
            assert getattr(projected, f"execution_failure_seen_{suffix}") == (
                reference.execution_failures > 0
            )


def test_completion_in_wrong_population_is_diagnostic_not_credit(reactive):
    c = config(reactive).model_dump(mode="json") | {"capability": "terminal_failure_stop"}
    # A custom suite can attach a terminal intent to a recoverable graph. The first-party
    # validator rejects it, but runtime still needs to report the actual E11 without credit.
    c = ReactiveExecutionConfig.model_validate(c)
    terminal = case(c)
    data = sample(terminal, [plan("open", "finish"), plan("finish")])
    assert data.result.score == 0
    result = summary((terminal,), [data]).reactive_failure
    assert result.case_outcomes.completed_after_execution_failure == 1
    assert result.terminal_failure_rate.partial_value == 0


def test_product_search_contact_mutations_against_full_runtime_reference(reactive, monkeypatch):
    from collections import deque

    from elarabench import reactive_execution_corpus as corpus
    from elarabench.reactive_execution import reactive_outcome_passes

    def reference(a, b):
        # Deliberately retain all runtime histories and enumerate response sequences without
        # projecting/merging states. This is an independent bounded reference for the tiny pair.
        queue = deque([(ReactiveRuntime(a.initial_state), ReactiveRuntime(b.initial_state), ())])
        while queue:
            ra, rb, path = queue.popleft()
            for text in corpus.canonical_responses(a):
                states = tuple(
                    r
                    if r.status is not None
                    else step_reactive(c, r, GenerationResponse(text=text))
                    for c, r in ((a, ra), (b, rb))
                )
                done = [
                    reactive_outcome_passes(c, r.outcome, r.execution_failures > 0)
                    for c, r in zip((a, b), states, strict=True)
                ]
                if all(done):
                    return (*path, text)
                if any(
                    r.status is not None and not success
                    for r, success in zip(states, done, strict=True)
                ):
                    continue
                queue.append((*states, (*path, text)))
        return None

    a = config(reactive, max_model_turns=3, max_total_actions=4)
    b = config(reactive, True, max_model_turns=3, max_total_actions=4)
    assert reference(a, b) is not None
    assert corpus.analyze_blind_policy_group(a, b).error is corpus.CorpusFindingCode.BLIND_COMPLETES
    project = corpus.project_variant

    def omitted(c, state, status, actions, turns, response, failures, contact, precondition=False):
        return project(c, state, status, actions, turns, response, failures, False, precondition)

    with monkeypatch.context() as patch:
        patch.setattr(corpus, "project_variant", omitted)
        assert corpus.analyze_blind_policy_group(a, b).error is None
    # A can contact/retry finish from an open state. B's required open operation fails
    # permanently, but a blind finish/finish/Control policy never contacts that operation.
    a = a.model_copy(update={"initial_state": {"open": True}})
    raw = b.model_dump(mode="json")
    raw["failure_schedule"] = {"open": {"code": "closed"}}
    b = ReactiveExecutionConfig.model_validate(raw)
    # With enough blind exploration, a different policy may contact both; compare the
    # specific policy's reference verdict as well as exhaustive search under tight Actions.
    a, b = (c.model_copy(update={"max_plan_length": 1, "max_total_actions": 2}) for c in (a, b))
    assert reference(a, b) is None
    assert corpus.analyze_blind_policy_group(a, b).error is None
    advance = corpus.advance_product

    def shared(ca, cb, node, text):
        contact = node.execution_failure_seen_A or node.execution_failure_seen_B
        return advance(
            ca,
            cb,
            replace(node, execution_failure_seen_A=contact, execution_failure_seen_B=contact),
            text,
        )

    with monkeypatch.context() as patch:
        patch.setattr(corpus, "advance_product", shared)
        # B needs an unconsumed Action slot for terminal Control after two failed finishes.
        a, b = (c.model_copy(update={"max_total_actions": 3}) for c in (a, b))
        # Reference still cannot complete both in three turns with max_plan_length=1:
        # contacting open on B consumes a response that A needs for its own retry path.
        assert reference(a, b) is None
        assert corpus.analyze_blind_policy_group(a, b).error is (
            corpus.CorpusFindingCode.BLIND_COMPLETES
        )


def test_failure_group_metadata_rejects_same_population(reactive):
    cases = (case(config(reactive), "a", True), case(config(reactive), "b", True))
    with pytest.raises(ValueError, match="one case of each failure capability"):
        reactive_case_expectations(cases)


@pytest.mark.parametrize("rate_name", ["retry_recovery_rate", "futile_retry_rate"])
def test_summary_rejects_inconsistent_population_coverage(reactive, rate_name):
    from elarabench.models import ReactiveFailureSummary

    cases = (case(config(reactive), "a", True), case(config(reactive, True), "b", True))
    data = [
        sample(cases[0], [plan("open", "finish"), plan("finish")]),
        sample(cases[1], [plan("open", "finish"), CONTROL_REFUSE]),
    ]
    raw = summary(cases, data).reactive_failure.model_dump(mode="json")
    raw[rate_name]["coverage"] = 0.5
    raw[rate_name]["headline_value"] = None
    raw["balanced_failure_recovery"] = None
    with pytest.raises(ValueError, match=r"coverage|partition"):
        ReactiveFailureSummary.model_validate(raw)


@pytest.mark.parametrize(
    "capability", ["first_pass", "recovery_opportunity", "terminal_unreachable"]
)
def test_failure_schedule_requires_failure_scoring_population(reactive, capability):
    raw = config(reactive).model_dump(mode="json") | {"capability": capability}
    wrong_population = case(ReactiveExecutionConfig.model_validate(raw))
    with pytest.raises(ValueError, match="failure capability scoring"):
        reactive_case_expectations((wrong_population,))
