"""First-party M5.5 gate mutations on a small complete synthetic corpus."""

from dataclasses import replace

import pytest

from elarabench.models import EvaluationSpecification
from elarabench.reactive_execution import ReactiveExecutionConfig, render_reactive_task
from elarabench.reactive_execution_corpus import PRODUCTION_CATEGORIES, CorpusFindingCode
from elarabench.reactive_failure_corpus import (
    STRATEGIES,
    TRUST_NAMES,
    strategy_summary,
    validate_reactive_failure_corpus,
)


@pytest.fixture
def failure_corpus(reactive):
    base = reactive.suite()
    original = base.suite.cases[0]
    cases = []
    for i, category in enumerate(PRODUCTION_CATEGORIES):
        for j in range(4):
            permanent = j % 2 == 1
            raw = dict(original.evaluation.config)
            raw.update(
                {
                    "capability": "terminal_failure_stop" if permanent else "retryable_failure",
                    "max_model_turns": 2,
                    "max_total_actions": 4,
                    "failure_catalog": {"busy": "retryable", "closed": "permanent"},
                    "failure_schedule": {
                        "finish": {"code": "closed"}
                        if permanent
                        else {"code": "busy", "transient_failures": 1}
                    },
                    "outcome_semantic": "reactive_execution_outcomes_v2",
                    "observation_semantic": "reactive_observation_v2",
                    "rendering_semantic": "reactive_observation_rendering_v2",
                }
            )
            raw["tools"] = {name: dict(tool) for name, tool in raw["tools"].items()}
            raw["tools"]["finish"]["arguments_schema"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: {"type": "string"} for name in TRUST_NAMES},
            }
            config = ReactiveExecutionConfig.model_validate(raw)
            cases.append(
                original.model_copy(
                    update={
                        "id": f"rf-{i * 4 + j + 1:03}",
                        "category": category,
                        "difficulty": ("easy", "medium", "hard")[(i // 2 + j // 2) % 3],
                        "tags": (
                            f"contrastive-group-rf-pair-{i + 1:02}",
                            f"contrastive-variant-branch-{'b' if permanent else 'a'}",
                        )
                        if j < 2
                        else (),
                        "evaluation": EvaluationSpecification(
                            type="reactive_execution", config=raw
                        ),
                        "messages": (
                            original.messages[0],
                            original.messages[1].model_copy(
                                update={"content": render_reactive_task(config, config.objective)}
                            ),
                        ),
                    }
                )
            )
    return replace(
        base,
        suite=base.suite.model_copy(
            update={"id": "reactive_failure.core", "version": "1.0.0", "cases": tuple(cases)}
        ),
    )


def change(source, index, **updates):
    cases = list(source.suite.cases)
    cases[index] = cases[index].model_copy(update=updates)
    return replace(source, suite=source.suite.model_copy(update={"cases": tuple(cases)}))


def test_all_strategies_and_gates(failure_corpus):
    result = validate_reactive_failure_corpus(failure_corpus)
    assert result.valid, result.errors
    assert len(STRATEGIES) == 12
    for name, strategy in STRATEGIES.items():
        summary = strategy_summary(failure_corpus.suite.cases, strategy)
        assert summary.coverage == 1
        if name == "perfect":
            assert summary.balanced_failure_recovery == 1
        else:
            assert summary.balanced_failure_recovery <= 1 / 3
            assert summary.failure_discrimination_rate.headline_value == 0


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("difficulty", "impossible", CorpusFindingCode.DIFFICULTY),
        ("category", "elsewhere", CorpusFindingCode.CATEGORY),
        ("tags", (), CorpusFindingCode.GROUP),
        ("seed", 999, CorpusFindingCode.PAIR),
        ("id", "retryable-hidden", CorpusFindingCode.LEAKAGE),
    ],
)
def test_profile_mutations(failure_corpus, field, value, code):
    result = validate_reactive_failure_corpus(change(failure_corpus, 0, **{field: value}))
    assert code in {f.code for f in result.errors}


@pytest.mark.parametrize("mutation", ["avoidable", "unreachable", "counter", "catalog", "trigger"])
def test_reachability_schedule_mutations(failure_corpus, mutation):
    case = failure_corpus.suite.cases[2]
    raw = dict(case.evaluation.config)
    expected = CorpusFindingCode.REACHABILITY
    if mutation == "avoidable":
        raw["failure_schedule"] = {
            "finish": {"code": "busy", "transient_failures": 1, "trigger": {"never": True}}
        }
    elif mutation == "unreachable":
        raw["expected_state"] = {"never": True}
    elif mutation == "trigger":
        raw["failure_schedule"] = {"finish": {"code": "closed", "trigger": {"never": True}}}
        raw["capability"] = "terminal_failure_stop"
    elif mutation == "counter":
        raw["failure_schedule"] = {"finish": {"code": "busy", "transient_failures": 2}}
        expected = CorpusFindingCode.METADATA
    else:
        raw["failure_catalog"] = {"busy": "retryable"}
        expected = CorpusFindingCode.METADATA
    result = validate_reactive_failure_corpus(
        change(
            failure_corpus,
            2,
            evaluation=EvaluationSpecification(type="reactive_execution", config=raw),
        )
    )
    assert expected in {f.code for f in result.errors}


def test_unavailable_strategies_fail_closed(failure_corpus, monkeypatch):
    import elarabench.reactive_failure_corpus as corpus

    def unavailable(*args):
        raise ValueError("incomplete coverage")

    monkeypatch.setattr(corpus, "strategy_summary", unavailable)
    result = corpus.validate_reactive_failure_corpus(failure_corpus)
    assert sum(f.code is CorpusFindingCode.STRATEGY for f in result.errors) == 12


def test_loose_pair_mutation_rejected(failure_corpus):
    source = failure_corpus
    for i in (0, 1):
        case = source.suite.cases[i]
        raw = case.evaluation.config | {"max_model_turns": 3}
        config = ReactiveExecutionConfig.model_validate(raw)
        source = change(
            source,
            i,
            evaluation=EvaluationSpecification(type="reactive_execution", config=raw),
            messages=(
                case.messages[0],
                case.messages[1].model_copy(
                    update={"content": render_reactive_task(config, config.objective)}
                ),
            ),
        )
    result = validate_reactive_failure_corpus(source)
    assert CorpusFindingCode.BLIND_COMPLETES in {f.code for f in result.errors}


def test_missing_trust_and_resource_exhaustion_fail_closed(failure_corpus, monkeypatch):
    import elarabench.reactive_failure_corpus as corpus
    from elarabench.reactive_execution_corpus import BlindPolicyProof

    monkeypatch.setattr(corpus, "trust_payload_probe", lambda c: False)
    monkeypatch.setattr(
        corpus,
        "analyze_blind_policy_group",
        lambda a, b: BlindPolicyProof(CorpusFindingCode.ENUMERATION, 250000),
    )
    result = corpus.validate_reactive_failure_corpus(failure_corpus)
    codes = {f.code for f in result.errors}
    assert {CorpusFindingCode.TRUST, CorpusFindingCode.ENUMERATION} <= codes
