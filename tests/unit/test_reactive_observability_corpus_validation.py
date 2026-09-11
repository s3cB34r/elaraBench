"""Independent mutations for all M5.6 first-party gates, using test-only cases."""

from dataclasses import replace

import pytest

from elarabench.hashing import hash_suite
from elarabench.models import BenchmarkCase, EvaluationSpecification
from elarabench.reactive_execution import ReactiveExecutionConfig, render_reactive_task
from elarabench.reactive_execution_corpus import PRODUCTION_CATEGORIES
from elarabench.reactive_observability_corpus import (
    STRATEGIES,
    TRUST_NAMES,
    strategy_summary,
    validate_reactive_observability_corpus,
)


def fixture_corpus(reactive):
    loaded = reactive.suite()
    cases = []
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {n: {"type": "string"} for n in sorted(TRUST_NAMES)},
    }
    for category_index, category in enumerate(PRODUCTION_CATEGORIES):
        for index in range(4):
            required = index < 2
            tools = {
                "a_inspect": {
                    "arguments_schema": schema,
                    "requires": {"stage": "open"},
                    "effects": {"stage": "open"},
                },
                "b_finish": {
                    "arguments_schema": schema,
                    "requires": {"stage": "normalized"},
                    "effects": {"stage": "closed", "done": True},
                },
            }
            if required:
                for name, branch in [("c_left", "left"), ("d_right", "right")]:
                    tools[name] = {
                        "arguments_schema": schema,
                        "requires": {"route": branch},
                        "effects": {"route": "aligned", "stage": "normalized"},
                    }
            else:
                tools["e_start"] = {
                    "arguments_schema": schema,
                    "requires": {"stage": "open"},
                    "effects": {"stage": "normalized"},
                }
            config = reactive.config(
                capability="information_required" if required else "information_sufficient",
                objective=f"{category}: set done=true and stage=closed to complete the workflow.",
                tools=tools,
                initial_state={
                    "stage": "open",
                    "done": False,
                    "route": ("left" if index == 0 else "right") if required else "parked",
                },
                expected_state={
                    "stage": "closed",
                    "done": True,
                    "route": "aligned" if required else "parked",
                },
                observability={
                    "observable_keys": ["done", "stage"],
                    "reveals": {"a_inspect": ["route"]},
                },
                observation_semantic="reactive_observation_v3",
                rendering_semantic="reactive_observation_rendering_v3",
                max_total_actions=3 if required else 2,
                max_model_turns=3,
                max_plan_length=2,
            )
            difficulty = ["easy", "medium", "hard"][category_index // 2]
            tags = [f"difficulty-{difficulty}"]
            if required:
                tags += [
                    f"contrastive-group-ro-pair-{category_index + 1:02}",
                    "contrastive-variant-branch-" + ("a" if index == 0 else "b"),
                ]
            cases.append(
                BenchmarkCase.model_validate(
                    {
                        "id": f"ro-{category_index:02}-{index:02}",
                        "category": category,
                        "difficulty": difficulty,
                        "tags": tags,
                        "messages": [
                            {"role": "system", "content": "Follow the synthetic workflow."},
                            {
                                "role": "user",
                                "content": render_reactive_task(config, config.objective),
                            },
                        ],
                        "evaluation": {
                            "type": "reactive_execution",
                            "config": config.model_dump(mode="json"),
                        },
                    }
                )
            )
    suite = loaded.suite.model_copy(
        update={"id": "reactive_observability.core", "version": "1.0.0", "cases": tuple(cases)}
    )
    return replace(loaded, suite=suite, content_hash=hash_suite(suite, {}))


@pytest.fixture
def corpus(reactive):
    return fixture_corpus(reactive)


def mutate(corpus, index, *, raw=None, **changes):
    cases = list(corpus.suite.cases)
    case = cases[index]
    if raw is not None:
        changes["evaluation"] = EvaluationSpecification(type="reactive_execution", config=raw)
        # Mutations target semantics independently of renderer equality.
        try:
            config = ReactiveExecutionConfig.model_validate(raw)
            changes["messages"] = (
                case.messages[0],
                case.messages[1].model_copy(
                    update={"content": render_reactive_task(config, config.objective)}
                ),
            )
        except ValueError:
            pass
    cases[index] = case.model_copy(update=changes)
    return replace(corpus, suite=corpus.suite.model_copy(update={"cases": tuple(cases)}))


def test_all_gates_and_fourteen_probes(corpus):
    result = validate_reactive_observability_corpus(corpus)
    assert result.valid, result.errors
    assert len(STRATEGIES) == 14
    for name, strategy in STRATEGIES.items():
        summary = strategy_summary(corpus.suite.cases, strategy)
        assert summary.coverage == 1
        if name == "perfect":
            assert summary.balanced_observability == 1
        else:
            assert summary.balanced_observability <= 1 / 3
            assert summary.observability_discrimination_rate.headline_value == 0


@pytest.mark.parametrize("name", ["left_then_right", "right_then_left"])
def test_lucky_blind_enumeration_has_acquisition_but_no_discrimination(corpus, name):
    summary = strategy_summary(corpus.suite.cases, STRATEGIES[name])
    assert summary.information_acquisition_rate.headline_value == 0.5
    assert summary.information_restraint_rate.headline_value == 0
    assert summary.observability_discrimination_rate.headline_value == 0
    assert summary.balanced_observability == 1 / 6


@pytest.mark.parametrize("status", ["missing", "error", "invalid", "pending_review"])
def test_probe_rejects_incomplete_or_nonbehavioral_coverage(corpus, monkeypatch, status):
    from elarabench import reactive_observability_corpus as module
    from elarabench.models import EvaluationStatus

    samples = module.witness_samples(corpus.suite.cases, STRATEGIES["perfect"])
    if status == "missing":
        samples = samples[1:]
    else:
        result = samples[0].result
        updates = {"status": EvaluationStatus(status), "score": None, "passed": None,
                   "artifacts": {}}
        if status == "pending_review":
            updates.update(evaluator_version="1.0.0", artifacts={
                "reactive_execution": result.artifacts["reactive_execution"] | {
                    "evaluator_version": "1.0.0"}})
        samples[0] = samples[0].model_copy(update={"result": result.model_copy(update=updates)})
    monkeypatch.setattr(module, "witness_samples", lambda *_: samples)
    with pytest.raises(ValueError, match="complete valid scored headline coverage"):
        module.strategy_summary(corpus.suite.cases, STRATEGIES["perfect"])


@pytest.mark.parametrize(
    ("field", "value", "gate"),
    [
        ("id", "ro-hidden-01", 23),
        ("category", "other", 3),
        ("difficulty", "hard", 4),
        ("tags", (), 6),
        ("seed", 99, 7),
    ],
)
def test_case_mutations(corpus, field, value, gate):
    errors = validate_reactive_observability_corpus(mutate(corpus, 0, **{field: value})).errors
    assert any(f"gate {gate}:" in f.message for f in errors), errors


@pytest.mark.parametrize(
    ("mutation", "gate"),
    [
        ("count", 1),
        ("gated", 2),
        ("s1", 10),
        ("s2", 10),
        ("s3", 10),
        ("shorter", 11),
        ("unreachable", 11),
        ("capacity", 11),
        ("inertness", 12),
        ("no_hidden", 13),
        ("overlap", 14),
        ("third", 15),
        ("catalog", 16),
        ("schedule", 16),
        ("undecidable", 17),
        ("pair_state", 8),
        ("pair_config", 9),
        ("required_unreachable", 19),
        ("renderer", 24),
        ("trust", 26),
        ("group", 5),
    ],
)
def test_config_mutations(corpus, mutation, gate):
    index = 2
    raw = dict(corpus.suite.cases[index].evaluation.config)
    import copy

    raw = copy.deepcopy(raw)
    if mutation == "count":
        corpus = replace(
            corpus, suite=corpus.suite.model_copy(update={"cases": corpus.suite.cases[:-1]})
        )
    elif mutation == "group":
        corpus = mutate(corpus, 0, difficulty="hard")
    elif mutation == "renderer":
        case = corpus.suite.cases[0]
        corpus = mutate(
            corpus,
            0,
            messages=(
                case.messages[0],
                case.messages[1].model_copy(update={"content": "stale task"}),
            ),
        )
    elif mutation == "trust":
        for i, case in enumerate(corpus.suite.cases):
            changed = copy.deepcopy(case.evaluation.config)
            for tool in changed["tools"].values():
                tool["arguments_schema"]["properties"] = {}
            corpus = mutate(corpus, i, raw=changed)
    else:
        if mutation == "gated":
            raw.update(authorization="DENIED", capability=None, expected_state=None)
        elif mutation == "s1":
            raw["expected_state"]["route"] = "different"
        elif mutation == "s2":
            raw["tools"]["e_start"]["requires"]["route"] = "parked"
        elif mutation == "s3":
            raw["tools"]["e_start"]["effects"]["route"] = "parked"
        elif mutation == "shorter":
            raw["max_total_actions"] = 3
        elif mutation == "unreachable":
            raw["expected_state"]["done"] = "impossible"
        elif mutation == "capacity":
            raw.update(max_model_turns=1, max_plan_length=1)
        elif mutation == "inertness":
            raw["tools"]["a_inspect"]["effects"]["extra"] = True
        elif mutation == "no_hidden":
            raw["observability"]["observable_keys"] += ["route"]
        elif mutation == "overlap":
            raw["observability"]["reveals"]["a_inspect"] += ["stage"]
        elif mutation == "third":
            raw["observability"]["reveals"].update(b_finish=["route"], e_start=["route"])
        elif mutation in {"catalog", "schedule"}:
            raw["failure_catalog"] = {"busy": "retryable"}
            if mutation == "schedule":
                raw["failure_schedule"] = {"a_inspect": {"code": "busy", "transient_failures": 1}}
                raw["outcome_semantic"] = "reactive_execution_outcomes_v2"
        elif mutation == "undecidable":
            raw["tools"]["e_start"]["arguments_schema"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"type": "string", "pattern": "^unlikely$"}},
                "required": ["x"],
            }
        elif mutation in {"pair_state", "pair_config", "required_unreachable"}:
            index = 0
            raw = copy.deepcopy(corpus.suite.cases[index].evaluation.config)
            if mutation == "pair_state":
                raw["initial_state"]["stage"] = "different"
            elif mutation == "pair_config":
                raw["max_total_actions"] += 1
            else:
                raw["expected_state"]["done"] = "impossible"
        corpus = mutate(corpus, index, raw=raw)
    errors = validate_reactive_observability_corpus(corpus).errors
    assert any(f"gate {gate}:" in f.message for f in errors), errors


def test_hidden_mode_counterexample_fails_epistemic_not_restraint(corpus):
    import copy

    raw = copy.deepcopy(corpus.suite.cases[2].evaluation.config)
    raw["max_total_actions"] = 1
    raw["tools"]["e_start"]["requires"]["route"] = "parked"
    raw["tools"]["e_start"]["effects"] = {"stage": "closed", "done": True}
    errors = validate_reactive_observability_corpus(mutate(corpus, 2, raw=raw)).errors
    assert any("S2 hidden applicability" in f.message for f in errors)
    assert not any("gate 11:" in f.message for f in errors)


def test_invalid_probe_cannot_pass_gate(corpus, monkeypatch):
    from elarabench import reactive_observability_corpus as module
    from elarabench.reactive_execution_corpus import BlindPolicyProof

    valid = strategy_summary(corpus.suite.cases, STRATEGIES["perfect"])
    monkeypatch.setattr(module, "analyze_blind_policy_group", lambda *_: BlindPolicyProof(None, 1))
    monkeypatch.setattr(
        module,
        "strategy_summary",
        lambda *_: valid.model_copy(update={"balanced_observability": None}),
    )
    errors = module.validate_reactive_observability_corpus(corpus).errors
    assert any("gate 25:" in f.message for f in errors)
