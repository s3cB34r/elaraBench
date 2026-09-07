"""M5.3b Action Recovery repeat-first aggregation and summary tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from elarabench.action_compliance import expectation_from_action_specification
from elarabench.action_recovery import expectation_from_recovery_specification
from elarabench.aggregation import aggregate
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.evaluators.registry import evaluate
from elarabench.models import (
    AggregationSample,
    AggregationSummary,
    EvaluationContext,
    GenerationResponse,
    SampleIdentity,
)

PROJECT_ROOT = Path(__file__).parents[2]
SUITE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "action_recovery_suite"
GOLDEN_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-recovery-foundation-test-v1.jsonl"
)


def _fixture() -> tuple[tuple, dict[str, dict[str, str]], dict]:
    cases = load_benchmark_suite(SUITE_PATH).suite.cases
    goldens = {row["case_id"]: row for row in map(json.loads, GOLDEN_PATH.read_text().splitlines())}
    expectations = {
        case.id: expectation
        for case in cases
        if (expectation := expectation_from_recovery_specification(case.evaluation)) is not None
    }
    return cases, goldens, expectations


def _samples(vector: str = "correct_response", repeat_index: int = 0):
    cases, goldens, _ = _fixture()
    return tuple(
        AggregationSample(
            identity=SampleIdentity(case_id=case.id, repeat_index=repeat_index),
            category=case.category,
            tags=case.tags,
            case_weight=case.weight,
            result=evaluate(
                EvaluationContext(
                    response=GenerationResponse(text=goldens[case.id][vector]),
                    specification=case.evaluation,
                )
            ),
        )
        for case in cases
    )


def _aggregate(samples, *, repeats: int = 1, configured: dict[str, str] | None = None):
    cases, _, expectations = _fixture()
    return aggregate(
        samples,
        expected_samples=len(cases) * repeats,
        recovery_case_expectations=expectations,
        configured_evaluator_types=configured or {case.id: "action_recovery" for case in cases},
        expected_repeats=repeats,
    )


def test_complete_recovery_summary_has_ten_buckets_and_exact_rates() -> None:
    summary = _aggregate(_samples())
    recovery = summary.action_recovery
    assert recovery is not None
    assert summary.schema_version == 6
    assert summary.score == summary.partial_score == 1.0
    assert recovery.semantic_version == "action_recovery_summary_v1"
    assert recovery.scoring_semantic == "action_recovery_scoring_v1"
    assert recovery.evaluator_version == "1.1.0"
    assert recovery.expected_case_count == recovery.observed_case_count == 7
    assert recovery.expected_sample_count == recovery.scored_sample_count == 7
    assert recovery.sample_outcomes.total() == 7
    assert recovery.case_outcomes.total() == 7.0
    assert recovery.sample_outcomes.recovered == 4
    assert recovery.sample_outcomes.correct_terminal_stop == 1
    assert recovery.sample_outcomes.gated_correct_stop == 2
    assert recovery.recovery_rate.denominator == 4
    assert recovery.terminal_stop_rate.denominator == 1
    assert recovery.denied_compliance_rate.denominator == 1
    assert recovery.approval_compliance_rate.denominator == 1
    assert recovery.recovery_rate.headline_value == 1.0
    assert recovery.terminal_stop_rate.headline_value == 1.0
    assert recovery.denied_compliance_rate.headline_value == 1.0
    assert recovery.approval_compliance_rate.headline_value == 1.0
    assert recovery.repeated_action_rate.partial_value == 0.0
    assert recovery.premature_stop_rate.partial_value == 0.0
    assert recovery.futile_attempt_rate.partial_value == 0.0
    assert recovery.balanced_action_recovery == 1.0


def test_incomplete_recovery_population_withholds_headline_and_generic_scores() -> None:
    samples = tuple(
        sample for sample in _samples() if sample.identity.case_id != "action-recovery-005"
    )
    summary = _aggregate(samples)
    recovery = summary.action_recovery
    assert recovery is not None
    assert recovery.terminal_stop_rate.coverage == 0.0
    assert recovery.terminal_stop_rate.headline_value is None
    assert recovery.balanced_action_recovery is None
    assert summary.score is None
    assert summary.partial_score is None


def test_repeat_first_case_macro_preserves_configured_population_coverage() -> None:
    summary = _aggregate(_samples(), repeats=2)
    recovery = summary.action_recovery
    assert recovery is not None
    assert recovery.coverage == 0.5
    assert recovery.expected_case_count == recovery.observed_case_count == 7
    assert isinstance(recovery.observed_case_count, int)
    assert recovery.expected_sample_count == 14
    assert recovery.scored_sample_count == 7
    assert recovery.case_outcomes.total() == 7.0
    assert recovery.recovery_rate.partial_value == 1.0
    assert recovery.recovery_rate.coverage == 0.5
    assert recovery.recovery_rate.headline_value is None
    assert recovery.balanced_action_recovery is None
    assert summary.score is None
    for sample in _samples():
        single = _aggregate((sample,), repeats=2).action_recovery
        assert single is not None
        assert single.case_outcomes.total() == 1.0


def test_uneven_observed_repeats_preserve_each_case_mass() -> None:
    samples = (*_samples(), *_samples("malformed_response", repeat_index=1)[:3])
    recovery = _aggregate(samples, repeats=2).action_recovery
    assert recovery is not None
    assert recovery.observed_case_count == 7
    assert recovery.scored_sample_count == 10
    assert recovery.coverage == 10 / 14
    assert recovery.case_outcomes.total() == 7.0
    assert recovery.balanced_action_recovery is None
    for case_id in recovery.eligible_case_ids:
        case_samples = tuple(sample for sample in samples if sample.identity.case_id == case_id)
        single = _aggregate(case_samples, repeats=2).action_recovery
        assert single is not None
        masses = single.case_outcomes.model_dump()
        assert sum(masses.values()) == 1.0
        assert sorted(mass for mass in masses.values() if mass) == (
            [0.5, 0.5] if len(case_samples) == 2 else [1.0]
        )
        assert single.coverage == len(case_samples) / 14


def test_fully_observed_repeats_preserve_behavioral_values() -> None:
    correct = _aggregate(_samples()).action_recovery
    malformed = _aggregate(_samples("malformed_response")).action_recovery
    repeated = _aggregate(
        (*_samples(), *_samples("malformed_response", repeat_index=1)), repeats=2
    ).action_recovery
    assert correct is not None and malformed is not None and repeated is not None
    assert repeated.observed_case_count == 7
    assert repeated.scored_sample_count == repeated.expected_sample_count == 14
    assert repeated.coverage == 1.0
    assert repeated.case_outcomes.total() == 7.0
    for outcome, mass in repeated.case_outcomes.model_dump().items():
        assert mass == (
            correct.case_outcomes.model_dump()[outcome]
            + malformed.case_outcomes.model_dump()[outcome]
        ) / 2
    for name in (
        "recovery_rate", "terminal_stop_rate", "repeated_action_rate",
        "premature_stop_rate", "futile_attempt_rate", "denied_compliance_rate",
        "approval_compliance_rate",
    ):
        rate = getattr(repeated, name)
        assert rate.partial_value == (
            getattr(correct, name).partial_value + getattr(malformed, name).partial_value
        ) / 2
        assert rate.headline_value == rate.partial_value
        assert rate.coverage == 1.0
    assert repeated.balanced_action_recovery == 0.5


def test_mixed_configured_family_suppresses_generic_arithmetic() -> None:
    cases, _, _ = _fixture()
    configured = {case.id: "action_recovery" for case in cases}
    configured["other-case"] = "exact_match"
    summary = _aggregate(_samples(), configured=configured)
    assert summary.action_recovery is not None
    assert summary.score is None
    assert summary.partial_score is None


def test_action_and_recovery_diagnostics_coexist_in_schema_v6() -> None:
    recovery_cases, recovery_goldens, recovery_expectations = _fixture()
    recovery_case = recovery_cases[0]
    action_loaded = load_benchmark_suite(get_builtin_suite_path("action_compliance.core"))
    action_case = action_loaded.suite.cases[0]
    action_goldens = {
        row["case_id"]: row
        for row in map(
            json.loads,
            (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "builtin_suite_goldens"
                / "action-compliance-core-v1.jsonl"
            )
            .read_text()
            .splitlines(),
        )
    }
    action_expectation = expectation_from_action_specification(action_case.evaluation)
    assert action_expectation is not None
    samples = (
        AggregationSample(
            identity=SampleIdentity(case_id=recovery_case.id, repeat_index=0),
            category=recovery_case.category,
            tags=recovery_case.tags,
            case_weight=1.0,
            result=evaluate(
                EvaluationContext(
                    response=GenerationResponse(
                        text=recovery_goldens[recovery_case.id]["correct_response"]
                    ),
                    specification=recovery_case.evaluation,
                )
            ),
        ),
        AggregationSample(
            identity=SampleIdentity(case_id=action_case.id, repeat_index=0),
            category=action_case.category,
            tags=action_case.tags,
            case_weight=1.0,
            result=evaluate(
                EvaluationContext(
                    response=GenerationResponse(
                        text=action_goldens[action_case.id]["correct_response"]
                    ),
                    specification=action_case.evaluation,
                )
            ),
        ),
    )
    summary = aggregate(
        samples,
        expected_samples=2,
        action_case_expectations={action_case.id: action_expectation},
        recovery_case_expectations={recovery_case.id: recovery_expectations[recovery_case.id]},
        configured_evaluator_types={
            recovery_case.id: "action_recovery",
            action_case.id: "action_compliance",
        },
        expected_repeats=1,
    )
    assert summary.schema_version == 6
    assert summary.action_compliance is not None
    assert summary.action_recovery is not None
    assert summary.score is None
    assert summary.partial_score is None


def test_schema_v6_is_content_dependent_and_older_payloads_omit_recovery() -> None:
    summary = _aggregate(_samples())
    payload = summary.model_dump(mode="json")
    assert payload["schema_version"] == 6
    assert "action_recovery" in payload
    old = summary.model_copy(update={"schema_version": 4, "action_recovery": None})
    old_payload = old.model_dump(mode="json")
    assert "action_recovery" not in old_payload
    assert old_payload["schema_version"] == 4
    with pytest.raises(ValidationError):
        AggregationSummary.model_validate({**payload, "schema_version": 5})
    with pytest.raises(ValidationError):
        AggregationSummary.model_validate({**payload, "schema_version": 6, "action_recovery": None})
