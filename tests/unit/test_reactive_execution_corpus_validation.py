"""First-party profile, pair identity and fail-closed proof/probe gates."""

from dataclasses import replace

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.models import EvaluationSpecification
from elarabench.reactive_execution_corpus import (
    CorpusFindingCode,
    validate_reactive_execution_corpus,
)


def loaded():
    return load_benchmark_suite(get_builtin_suite_path("reactive_execution.core"))


def test_production_validity_and_determinism():
    source = loaded()
    assert len(source.suite.cases) == 48
    result = validate_reactive_execution_corpus(source)
    assert result.valid and not result.warnings
    assert result == validate_reactive_execution_corpus(source)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("objective", "", CorpusFindingCode.METADATA),
        ("capability", None, CorpusFindingCode.METADATA),
        ("objective", "changed", CorpusFindingCode.RENDERER),
    ],
)
def test_metadata_and_renderer_rejection(field, value, expected):
    source = loaded()
    case = next(c for c in source.suite.cases if c.evaluation.config["capability"] == "first_pass")
    changed = case.model_copy(
        update={
            "evaluation": EvaluationSpecification(
                type="reactive_execution", config=case.evaluation.config | {field: value}
            )
        }
    )
    suite = source.suite.model_copy(
        update={"cases": tuple(changed if c.id == case.id else c for c in source.suite.cases)}
    )
    result = validate_reactive_execution_corpus(replace(source, suite=suite))
    assert expected in {f.code for f in result.errors}


@pytest.mark.parametrize(
    "field,value",
    [
        ("difficulty", "impossible"),
        ("category", "elsewhere"),
        ("seed", 123),
        ("response_format", {"type": "text"}),
    ],
)
def test_pair_identity_and_difficulty(field, value):
    source = loaded()
    case = next(
        c for c in source.suite.cases if c.evaluation.config["capability"] == "recovery_opportunity"
    )
    if field == "response_format":
        from elarabench.models import ResponseFormatConstraint

        value = ResponseFormatConstraint.model_validate(value)
    changed = case.model_copy(update={field: value})
    suite = source.suite.model_copy(
        update={"cases": tuple(changed if c.id == case.id else c for c in source.suite.cases)}
    )
    findings = validate_reactive_execution_corpus(replace(source, suite=suite))
    assert CorpusFindingCode.PAIR in {f.code for f in findings.errors}


def test_counts_gated_difficulty_and_missing_group():
    source = loaded()
    gate = next(c for c in source.suite.cases if c.evaluation.config["authorization"] == "DENIED")
    changed = gate.model_copy(update={"difficulty": "impossible"})
    suite = source.suite.model_copy(
        update={"cases": tuple(changed if c.id == gate.id else c for c in source.suite.cases)}
    )
    assert CorpusFindingCode.DIFFICULTY in {
        f.code for f in validate_reactive_execution_corpus(replace(source, suite=suite)).errors
    }


def test_probe_unavailable_headline_cannot_bypass_gate(monkeypatch):
    import elarabench.reactive_execution_corpus as corpus

    def unavailable(*args):
        raise ValueError("incomplete coverage")

    monkeypatch.setattr(corpus, "strategy_summary", unavailable)
    result = corpus.validate_reactive_execution_corpus(loaded())
    assert len([f for f in result.errors if f.code is CorpusFindingCode.STRATEGY]) == 11
