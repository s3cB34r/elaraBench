"""Production M5.6 probes through durable execution, replay, scoring and comparison."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import available_builtin_suites, get_builtin_suite_path
from elarabench.comparison import compare_runs
from elarabench.hashing import hash_generation_request
from elarabench.models import GenerationParameters, RunConfiguration, SampleIdentity
from elarabench.providers import FakeProvider
from elarabench.reactive_execution import ReactiveExecutionConfig, replay_reactive
from elarabench.reactive_execution_corpus import analyze_blind_policy_group, strategy_context
from elarabench.reactive_observability_corpus import (
    STRATEGIES,
    validate_reactive_observability_corpus,
)
from elarabench.scoring import _reactive_context, score_run, summarize_run
from elarabench.storage import RunArtifactStore


@pytest.mark.parametrize("name", STRATEGIES)
def test_live_offline_mandatory_strategy(reactive, name):
    loaded = load_benchmark_suite(get_builtin_suite_path("reactive_observability.core"))
    responses = {}
    contexts = {}
    for case in loaded.suite.cases:
        context = strategy_context(
            case,
            lambda _c, runtime, request: STRATEGIES[name](request, runtime.durable_model_responses),
        )
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
        run_id=f"observability-{name}",
    )
    summary = result.summary
    obs = summary.reactive_observability
    assert result.manifest.schema_version == 4
    assert summary.schema_version == 9
    assert summary.reactive_execution is summary.reactive_failure is None
    assert summary.coverage.ratio == obs.coverage == 1
    assert summary.sample_status_counts.scored == 24
    axes = (
        obs.information_acquisition_rate.headline_value,
        obs.information_restraint_rate.headline_value,
        obs.observability_discrimination_rate.headline_value,
    )
    if name == "perfect":
        assert axes == (1, 1, 1) and summary.score == summary.partial_score == 1
    else:
        assert summary.score <= 1 / 3 and axes[2] == 0
    if name in {"always_refuse", "always_request_approval", "malformed"}:
        assert axes == (0, 0, 0) and summary.score == 0
    if name in {"left_then_right", "right_then_left"}:
        assert axes == (0.5, 0, 0)
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
        identity = SampleIdentity(case_id=case.id, repeat_index=0)
        evaluation = store.read_evaluation(identity, source_result_schema_version=4)
        artifact = evaluation.artifacts["reactive_execution"]
        assert evaluation.evaluator_version == "1.3.0" and evaluation.score == 1
        assert artifact["outcome"] == "completed_without_execution_failure"
        assert artifact["precondition_failures"] == 0
        assert artifact["final_state"] == case.evaluation.config["expected_state"]
        # Independent chain-length oracle, including one inspection only for required cases.
        required = case.evaluation.config["capability"] == "information_required"
        distance = ("easy", "medium", "hard").index(case.difficulty) + 2 + int(required)
        assert artifact["invoked_actions"] == artifact["durable_model_responses"] == distance
        assert "revealed_keys" not in artifact
        runtime, _ = replay_reactive(_reactive_context(store, identity, case.evaluation))
        assert runtime.revealed_keys == (frozenset({"route"}) if required else frozenset())
        assert all(
            ("route" in step.observation.resulting_state) == required for step in runtime.steps
        )
    before = {p: p.read_bytes() for p in result.path.rglob("*") if p.is_file()}
    comparison = compare_runs(result.path, result.path)
    assert all(
        r.status == "available" and r.evaluator_version == "1.3.0"
        for r in comparison.evaluator_resolution
    )
    assert all(p.read_bytes() == data for p, data in before.items())


def test_production_gates_and_exhaustive_binary_proofs():
    loaded = load_benchmark_suite(get_builtin_suite_path("reactive_observability.core"))
    findings = validate_reactive_observability_corpus(loaded)
    assert findings.valid, findings.errors
    groups = defaultdict(list)
    for case in loaded.suite.cases:
        for tag in case.tags:
            if tag.startswith("contrastive-group-"):
                groups[tag].append(case)
    assert len(groups) == 6 and {len(pair) for pair in groups.values()} == {2}
    proofs = [
        analyze_blind_policy_group(
            *(ReactiveExecutionConfig.model_validate(c.evaluation.config) for c in pair)
        )
        for pair in groups.values()
    ]
    assert all(proof.error is None and proof.expanded_nodes < 250000 for proof in proofs)
    assert Counter(c.difficulty for c in loaded.suite.cases) == {"easy": 8, "medium": 8, "hard": 8}


def test_tenth_hash_independently_and_catalog_arithmetic():
    pins = json.loads(Path("tests/fixtures/builtin_suite_hashes.json").read_text())
    assert len(pins) == len(available_builtin_suites()) == 10
    total = 0
    for name in available_builtin_suites():
        loaded = load_benchmark_suite(get_builtin_suite_path(name))
        assert loaded.content_hash == pins[name]["content_hash"]
        assert len(loaded.suite.cases) == pins[name]["case_count"]
        total += len(loaded.suite.cases)
        if name == "reactive_observability.core":
            assert not loaded.fixture_files
            payload = {"suite": loaded.suite.model_dump(mode="json"), "fixtures": []}
            encoded = json.dumps(
                payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            assert hashlib.sha256(encoded).hexdigest() == pins[name]["content_hash"]
            assert Counter(c.evaluation.config["capability"] for c in loaded.suite.cases) == {
                "information_required": 12,
                "information_sufficient": 12,
            }
    assert total == 282
