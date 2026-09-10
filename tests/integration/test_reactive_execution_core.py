"""Production strategies and Goldens through real Runner and offline services."""

import json
from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.comparison import compare_runs
from elarabench.hashing import hash_generation_request
from elarabench.models import GenerationParameters, RunConfiguration, SampleIdentity
from elarabench.providers import FakeProvider
from elarabench.reactive_execution_corpus import STRATEGIES, strategy_context
from elarabench.scoring import score_run, summarize_run
from elarabench.storage import RunArtifactStore


@pytest.mark.parametrize("name", STRATEGIES)
def test_production_strategy_live_and_offline(reactive, name):
    loaded = load_benchmark_suite(get_builtin_suite_path("reactive_execution.core"))
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
        run_id=f"reactive-{name}",
    )
    assert result.manifest.schema_version == 4
    summary = result.summary
    assert summary.schema_version == 7 and summary.coverage.ratio == 1
    expected = (
        1
        if name == "perfect"
        else 1 / 3
        if name
        in {
            "always_refuse",
            "always_request_approval",
            "stop_after_observation",
            "static_one_shot",
        }
        else 0
    )
    if name == "static_one_shot":
        reactive_summary = summary.reactive_execution
        assert reactive_summary.first_pass_completion_rate.headline_value == 0
        assert reactive_summary.adaptation_rate.headline_value == 0
        assert reactive_summary.terminal_stop_rate.headline_value == 1
    assert summary.score == pytest.approx(expected)
    assert summary.score <= 1 / 3 or name == "perfect"
    turns = list(result.path.glob("samples/*/*/turns/*/response.json"))
    assert sum(provider._calls.values()) == len(turns)
    assert len(turns) == sum(len(context.turns) for context in contexts.values())
    canonical = {
        p: p.read_bytes() for p in result.path.glob("samples/**/turns/**/*") if p.is_file()
    }
    assert score_run(result.path) == summary
    assert summarize_run(result.path) == summary
    for path, content in canonical.items():
        assert path.read_bytes() == content
    if name != "perfect":
        return
    re = summary.reactive_execution
    assert all(
        rate.headline_value == 1
        for rate in (
            re.first_pass_completion_rate,
            re.adaptation_rate,
            re.terminal_stop_rate,
            re.denied_compliance_rate,
            re.approval_compliance_rate,
        )
    )
    golden = {
        row["case_id"]: row
        for row in map(
            json.loads,
            Path("tests/fixtures/builtin_suite_goldens/reactive-execution-core-v1.jsonl")
            .read_text()
            .splitlines(),
        )
    }
    assert set(golden) == set(contexts)
    store = RunArtifactStore(result.path.parent, result.path.name)
    for case_id, context in contexts.items():
        row = golden[case_id]
        evaluation = store.read_evaluation(
            SampleIdentity(case_id=case_id, repeat_index=0), source_result_schema_version=4
        )
        artifact = evaluation.artifacts["reactive_execution"]
        assert row["status"] == evaluation.status.value
        for key in ("outcome", "final_state", "invoked_actions", "futile_occurrences"):
            assert row[key] == artifact[key]
        assert row["responses"] == [turn.response.text for turn in context.turns]
        assert row["observations"] == [
            turn.request.messages[-1].content for turn in context.turns[1:]
        ]
    before = {p: p.read_bytes() for p in result.path.rglob("*") if p.is_file()}
    comparison = compare_runs(result.path, result.path)
    assert all(
        r.status == "available" and r.evaluator_version == "1.2.0"
        for r in comparison.evaluator_resolution
    )
    assert {p: p.read_bytes() for p in before} == before
