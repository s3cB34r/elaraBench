"""E5-only capability mass, repeat normalization and Summary v9."""

from dataclasses import replace

import pytest
from test_reactive_observability import route_config

from elarabench.aggregation import aggregate
from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    BenchmarkCase,
    EvaluationSpecification,
    EvaluationStatus,
    SampleIdentity,
)
from elarabench.reactive_execution import (
    ReactiveEvaluator,
    ReactiveOutcome,
    reactive_case_expectations,
    reactive_outcome_passes,
    render_reactive_task,
)
from elarabench.reactive_execution_corpus import action_text, strategy_context, witnesses
from elarabench.storage import RunArtifactStore


def cases(reactive):
    result = []
    for index, cap in enumerate(
        ["information_required", "information_required", "information_sufficient"]
    ):
        config = route_config(reactive, capability=cap, objective="Align the route and finish.")
        if index == 1:
            config = config.model_copy(
                update={"initial_state": config.initial_state | {"route": "right"}}
            )
        tags = (
            ()
            if index == 2
            else (
                "contrastive-group-ro-pair-01",
                "contrastive-variant-branch-" + ("a" if index == 0 else "b"),
            )
        )
        result.append(
            BenchmarkCase.model_validate(
                {
                    "id": f"case-{index}",
                    "category": "synthetic",
                    "tags": tags,
                    "messages": [
                        {"role": "system", "content": "Follow instructions."},
                        {"role": "user", "content": render_reactive_task(config, config.objective)},
                    ],
                    "evaluation": {
                        "type": "reactive_execution",
                        "config": config.model_dump(mode="json"),
                    },
                }
            )
        )
    return tuple(result)


def successful(config, runtime, request):
    available = {a.tool: a for a in witnesses(config)}
    name = (
        "inspect"
        if runtime.durable_model_responses == 0
        else config.initial_state["route"]
        if runtime.durable_model_responses == 1
        else "finish"
    )
    return action_text((available[name],))


def sample(case, repeat=0, strategy=successful):
    context = strategy_context(case, strategy)
    context = replace(
        context,
        identity=SampleIdentity(case_id=case.id, repeat_index=repeat),
        turns=tuple(
            replace(
                t,
                attempts=tuple(
                    a.model_copy(
                        update={"identity": SampleIdentity(case_id=case.id, repeat_index=repeat)}
                    )
                    for a in t.attempts
                ),
            )
            for t in context.turns
        ),
    )
    return AggregationSample(
        identity=context.identity,
        category=case.category,
        tags=case.tags,
        case_weight=1,
        result=ReactiveEvaluator().evaluate(context),
    )


def summary(cases, samples, repeats=1):
    return aggregate(
        samples,
        expected_samples=len(cases) * repeats,
        expected_repeats=repeats,
        configured_evaluator_types={c.id: c.evaluation.type for c in cases},
        reactive_case_expectations=reactive_case_expectations(cases),
        source_result_schema_version=4,
    )


@pytest.mark.parametrize("cap", ["information_required", "information_sufficient"])
@pytest.mark.parametrize("outcome", list(ReactiveOutcome))
def test_exact_outcome_predicate(reactive, cap, outcome):
    config = route_config(reactive, capability=cap)
    assert reactive_outcome_passes(config, outcome, True) == (
        outcome is ReactiveOutcome.COMPLETED_WITHOUT_EXECUTION_FAILURE
    )


def test_repeat_normalization_and_missing_coverage(reactive):
    suite = cases(reactive)
    rows = [sample(c) for c in suite]
    rows.append(sample(suite[0], 1, lambda *_: "{}"))
    result = summary(suite, rows, 3)
    obs = result.reactive_observability
    assert result.schema_version == 9 and result.reactive_execution is None
    assert result.reactive_failure is None
    assert obs.observed_case_count == 3
    assert obs.information_acquisition_rate.partial_value == 0.75
    assert obs.information_restraint_rate.partial_value == 1
    assert obs.observability_discrimination_rate.partial_value == 0.5
    assert result.score is None and result.partial_score == 0.75
    assert obs.coverage == 4 / 9


def test_complete_and_configured_mixed_suppression(reactive):
    suite = cases(reactive)
    rows = [sample(c) for c in suite]
    result = summary(suite, rows)
    assert result.score == result.partial_score == 1
    old = reactive.suite().suite.cases[0]
    mixed = summary((*suite, old), rows)
    assert mixed.score is mixed.partial_score is None
    assert mixed.reactive_observability.balanced_observability == 1


@pytest.mark.parametrize("version", [6, 7, 8, 9])
@pytest.mark.parametrize("mask", range(8))
def test_presence_contract_model_and_writer(reactive, tmp_path, version, mask):
    from elarabench.models import validate_reactive_summary_presence

    present = tuple(bool(mask & (1 << i)) for i in range(3))
    valid = (
        (version == 6 and mask == 0)
        or (version == 7 and mask == 1)
        or (version == 8 and mask in {2, 3})
        or (version == 9 and mask >= 4)
    )
    if valid:
        validate_reactive_summary_presence(version, *present)
    else:
        with pytest.raises(ValueError):
            validate_reactive_summary_presence(version, *present)
    # Writer must reject even when normal Pydantic construction was bypassed.
    if not valid:
        obj = AggregationSummary.model_construct(
            schema_version=version,
            reactive_execution=object() if present[0] else None,
            reactive_failure=object() if present[1] else None,
            reactive_observability=object() if present[2] else None,
        )
        with pytest.raises(ValueError):
            obj.validate_summary_generation()
        (tmp_path / "matrix").mkdir()
        with pytest.raises(Exception, match="summary schema"):
            RunArtifactStore(tmp_path, "matrix").replace_summary(obj)


def test_e6_and_e11_completion_are_not_success(reactive):
    case = cases(reactive)[0]

    def recover(config, runtime, request):
        actions = witnesses(config)
        name = ("right", "left", "finish")[runtime.durable_model_responses]
        return action_text((next(a for a in actions if a.tool == name),))

    e6 = sample(case, strategy=recover).result
    assert e6.artifacts["reactive_execution"]["outcome"] == "completed_after_recovery"
    assert e6.score == 0
    raw = dict(case.evaluation.config)
    raw.update(
        failure_catalog={"busy": "retryable"},
        failure_schedule={"inspect": {"code": "busy", "transient_failures": 1}},
        outcome_semantic="reactive_execution_outcomes_v2",
    )
    case = case.model_copy(
        update={"evaluation": EvaluationSpecification(type="reactive_execution", config=raw)}
    )

    def retry(config, runtime, request):
        name = ("inspect", "inspect", "left", "finish")[runtime.durable_model_responses]
        return action_text((next(a for a in witnesses(config) if a.tool == name),))

    e11 = sample(case, strategy=retry).result
    assert e11.status is EvaluationStatus.SCORED and e11.score == 0
    assert e11.artifacts["reactive_execution"]["outcome"] == "completed_after_execution_failure"


@pytest.mark.parametrize("status", [EvaluationStatus.ERROR, EvaluationStatus.INVALID])
def test_nonbehavioral_results_affect_only_observability_coverage(reactive, status):
    suite = cases(reactive)
    rows = [sample(c) for c in suite]
    failed = rows[0].model_copy(update={"result": rows[0].result.model_copy(update={
        "status": status, "score": None, "passed": None, "artifacts": {},
    })})
    result = summary(suite, [failed, *rows[1:]])
    obs = result.reactive_observability
    assert obs.observed_case_count == obs.scored_sample_count == 2
    assert obs.case_outcomes.completed_without_execution_failure == 2
    assert obs.information_acquisition_rate.numerator == 1
    assert obs.coverage == 2 / 3 and obs.balanced_observability is None
    assert result.score is None


@pytest.mark.parametrize("execution", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_all_valid_v9_combinations_roundtrip(reactive, tmp_path, execution, failure):
    from test_reactive_failure import case as failure_case
    from test_reactive_failure import config as failure_config
    from test_reactive_failure import plan
    from test_reactive_failure import sample as old_sample

    suite = list(cases(reactive))
    rows = [sample(c) for c in suite]
    if execution:
        case = reactive.suite().suite.cases[0]
        suite.append(case)
        rows.append(old_sample(case, [plan("open", "finish")]))
    if failure:
        case = failure_case(failure_config(reactive), name="failure")
        suite.append(case)
        rows.append(old_sample(case, [plan("open", "finish"), plan("finish")]))
    result = summary(suite, rows)
    assert result.schema_version == 9
    assert (result.reactive_execution is not None) == execution
    assert (result.reactive_failure is not None) == failure
    if execution or failure:
        assert result.score is result.partial_score is None
    else:
        assert result.score == 1
    assert AggregationSummary.model_validate_json(result.model_dump_json()) == result
    (tmp_path / "matrix").mkdir()
    (tmp_path / "matrix" / "manifest.json").write_text('{"schema_version":4}')
    store = RunArtifactStore(tmp_path, "matrix")
    store.replace_summary(result)
    assert store.read_summary() == result
