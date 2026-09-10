"""All mandatory M5.5 strategies through real Runner, offline derivation, and comparison."""

import json
from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import available_builtin_suites, get_builtin_suite_path
from elarabench.comparison import compare_runs
from elarabench.hashing import hash_generation_request
from elarabench.models import GenerationParameters, RunConfiguration, SampleIdentity
from elarabench.providers import FakeProvider
from elarabench.reactive_execution_corpus import strategy_context
from elarabench.reactive_failure_corpus import STRATEGIES, validate_reactive_failure_corpus
from elarabench.scoring import score_run, summarize_run
from elarabench.storage import RunArtifactStore


@pytest.mark.parametrize("name", STRATEGIES)
def test_live_offline_mandatory_strategy(reactive, name):
    loaded = load_benchmark_suite(get_builtin_suite_path("reactive_failure.core"))
    responses = {}
    contexts = {}
    for case in loaded.suite.cases:
        context = strategy_context(case, STRATEGIES[name])
        contexts[case.id] = context
        for turn in context.turns:
            key = hash_generation_request(turn.request)
            if key in responses:
                assert responses[key] == turn.response.text
            responses[key] = turn.response.text
    provider = FakeProvider(responses=responses)
    result = reactive.runner(provider).run(
        loaded,
        RunConfiguration(
            suite_path=str(loaded.suite_dir),
            provider="fake",
            model="elarabench-fake-v1",
            generation_parameters=GenerationParameters(temperature=0, max_tokens=512),
            seed=42,
        ),
        run_id=f"failure-{name}",
    )
    summary = result.summary
    assert result.manifest.schema_version == 4
    assert summary.schema_version == 8 and summary.reactive_execution is None
    assert summary.coverage.ratio == summary.reactive_failure.coverage == 1
    assert summary.sample_status_counts.scored == 24
    if name == "perfect":
        assert summary.score == summary.partial_score == 1
    else:
        assert summary.score <= 1 / 3
        assert summary.reactive_failure.failure_discrimination_rate.headline_value == 0
    if name in {"always_refuse", "always_request_approval", "malformed"}:
        assert summary.score == 0
    canonical = {
        p: p.read_bytes() for p in result.path.glob("samples/**/turns/**/*") if p.is_file()
    }
    assert score_run(result.path) == summary
    assert summarize_run(result.path) == summary
    assert all(p.read_bytes() == data for p, data in canonical.items())
    assert sum(provider._calls.values()) == sum(len(c.turns) for c in contexts.values())
    if name != "perfect":
        return
    store = RunArtifactStore(result.path.parent, result.path.name)
    for case in loaded.suite.cases:
        evaluation = store.read_evaluation(
            SampleIdentity(case_id=case.id, repeat_index=0), source_result_schema_version=4
        )
        artifact = evaluation.artifacts["reactive_execution"]
        retryable = case.evaluation.config["capability"] == "retryable_failure"
        length = len(case.evaluation.config["tools"])
        # Independent simple-chain oracle: one failed final invocation, then retry or Control.
        assert evaluation.score == 1 and evaluation.evaluator_version == "1.2.0"
        assert artifact["outcome"] == (
            "completed_after_execution_failure" if retryable else "correct_terminal_stop"
        )
        assert artifact["invoked_actions"] == length + int(retryable)
        assert artifact["durable_model_responses"] == 2
        if retryable:
            assert artifact["final_state"] == case.evaluation.config["expected_state"]
        else:
            assert len(artifact["final_state"]) == length - 1
    before = {p: p.read_bytes() for p in result.path.rglob("*") if p.is_file()}
    comparison = compare_runs(result.path, result.path)
    assert all(
        r.status == "available" and r.evaluator_version == "1.2.0"
        for r in comparison.evaluator_resolution
    )
    assert all(p.read_bytes() == data for p, data in before.items())


def test_production_gates_and_all_historical_hashes():
    pins = json.loads(Path("tests/fixtures/builtin_suite_hashes.json").read_text())
    assert len(pins) == len(available_builtin_suites()) == 9
    count = 0
    for name in available_builtin_suites():
        loaded = load_benchmark_suite(get_builtin_suite_path(name))
        count += len(loaded.suite.cases)
        assert loaded.content_hash == pins[name]["content_hash"]
        assert len(loaded.suite.cases) == pins[name]["case_count"]
        if name == "reactive_failure.core":
            result = validate_reactive_failure_corpus(loaded)
            assert result.valid, result.errors
    assert count == 258
