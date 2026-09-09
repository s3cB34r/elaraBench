"""Capability scoring is distinct from unchanged behavioral derivation."""

import json
from pathlib import Path

import pytest

from elarabench.hashing import hash_case, hash_evaluation_specification
from elarabench.models import EvaluationSpecification, EvaluationStatus
from elarabench.reactive_execution import (
    ReactiveCapability,
    ReactiveEvaluator,
    ReactiveExecutionConfig,
    ReactiveOutcome,
    reactive_case_expectations,
    reactive_outcome_passes,
    validate_reactive_evaluation,
)
from elarabench.reactive_execution_corpus import strategy_context


@pytest.mark.parametrize("capability", list(ReactiveCapability))
@pytest.mark.parametrize("outcome", list(ReactiveOutcome))
def test_capability_conditioned_map(reactive, capability, outcome):
    config = reactive.config(capability=capability, objective="Finish the job.")
    expected = outcome.value in (
        {"correct_terminal_stop"}
        if capability.value == "terminal_unreachable"
        else {"completed_without_execution_failure", "completed_after_recovery"}
    )
    assert reactive_outcome_passes(config, outcome) is expected


@pytest.mark.parametrize("authorization", ["DENIED", "REQUIRES_APPROVAL"])
@pytest.mark.parametrize("outcome", list(ReactiveOutcome))
def test_gate_mapping(reactive, authorization, outcome):
    config = reactive.config(authorization=authorization, expected_state=None)
    assert reactive_outcome_passes(config, outcome) is (
        outcome is ReactiveOutcome.GATED_CORRECT_STOP
    )


def test_real_e6_and_historical_artifact_derivation(reactive):
    case = reactive.suite().suite.cases[0]
    rows = json.loads(Path("tests/fixtures/reactive_execution/golden.json").read_text())
    responses = next(row["responses"] for row in rows if row["id"] == "e6")
    context = strategy_context(case, lambda c, r, q: responses[r.durable_model_responses])
    evaluator = ReactiveEvaluator()
    current = evaluator.evaluate(context)
    assert current.score == 1 and current.passed is True
    assert current.status is EvaluationStatus.SCORED
    assert current.artifacts["reactive_execution"]["outcome"] == "completed_after_recovery"
    old = evaluator.derive(context, version="1.0.0")
    assert old.status is EvaluationStatus.PENDING_REVIEW and old.score is old.passed is None
    validate_reactive_evaluation(old, context)
    validate_reactive_evaluation(current, context)
    before = dict(old.artifacts["reactive_execution"])
    after = dict(current.artifacts["reactive_execution"])
    assert before.pop("evaluator_version") == "1.0.0"
    assert after.pop("evaluator_version") == "1.1.0"
    assert before == after  # No scoring fields added to the behavioral artifact.


def test_optional_defaults_do_not_rewrite_original_configuration(reactive):
    raw = reactive.fixture["config"]
    spec = EvaluationSpecification(type="reactive_execution", config=raw)
    original = hash_evaluation_specification(spec)
    parsed = ReactiveExecutionConfig.model_validate(spec.config)
    assert parsed.capability is parsed.objective is None
    assert "capability" not in spec.model_dump()["config"]
    assert "objective" not in spec.model_dump()["config"]
    assert hash_evaluation_specification(spec) == original
    case = reactive.suite().suite.cases[0]
    for field, value in [("capability", "terminal_unreachable"), ("objective", "Different task.")]:
        changed = case.model_copy(
            update={
                "evaluation": EvaluationSpecification(
                    type="reactive_execution", config=case.evaluation.config | {field: value}
                )
            }
        )
        assert hash_case(changed) != hash_case(case)


@pytest.mark.parametrize(
    "change",
    [
        {"objective": None},
        {"objective": " "},
        {"capability": None},
        {"authorization": "DENIED", "expected_state": None},
    ],
)
def test_strong_eligibility_is_separate_from_structural_parsing(reactive, change):
    case = reactive.suite().suite.cases[0]
    spec = EvaluationSpecification(
        type="reactive_execution", config=case.evaluation.config | change
    )
    ReactiveEvaluator().validate_specification(spec)
    with pytest.raises(ValueError, match=r"M5\.4b"):
        reactive_case_expectations((case.model_copy(update={"evaluation": spec}),))
