"""Frozen original M5.4a identity, no-write rejection, upgrade and availability."""

import json
from pathlib import Path

import pytest

from elarabench.comparison import compare_runs
from elarabench.evidence import RunIntegrityError, validate_stored_run
from elarabench.models import SampleIdentity
from elarabench.reactive_execution import ReactiveEvaluator, replay_reactive
from elarabench.scoring import _reactive_context, score_run, summarize_run
from elarabench.storage import RunArtifactStore


def contents(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def historical(tmp_path):
    fixture = json.loads(
        Path("tests/fixtures/reactive_execution/historical-v1-run.json").read_text()
    )
    path = tmp_path / json.loads(fixture["files"]["manifest.json"])["run_id"]
    for name, content in fixture["files"].items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return path


def test_historical_read_replay_score_transactionality_and_summarize(historical):
    store = RunArtifactStore(historical.parent, historical.name)
    manifest, snapshot = validate_stored_run(store)
    case = next(c for c in snapshot.suite.cases if c.evaluation.type == "reactive_execution")
    assert "capability" not in case.evaluation.config
    assert "objective" not in case.evaluation.config
    context = _reactive_context(
        store, SampleIdentity(case_id=case.id, repeat_index=0), case.evaluation
    )
    runtime, _ = replay_reactive(context)
    assert runtime.outcome.value == "completed_without_execution_failure"
    before = contents(historical)
    with pytest.raises(RunIntegrityError, match="metadata incomplete"):
        score_run(historical)
    assert (
        contents(historical) == before
    )  # Includes ordinary evaluation, summary and no score events.
    summary = summarize_run(historical)
    assert summary.reactive_execution is None
    assert summary.sample_status_counts.pending_review == 1
    assert summary.schema_version == 4
    assert "reactive_execution" not in summary.model_dump()
    assert manifest.schema_version == 4


def test_historical_comparison_unavailable_without_mutation(historical):
    before = contents(historical)
    result = compare_runs(historical, historical)
    resolution = [
        r for r in result.evaluator_resolution if r.evaluator_name == "reactive_execution"
    ]
    assert resolution and all(r.status == "unavailable" for r in resolution)
    assert contents(historical) == before


def test_historical_resume_rejects_before_preflight(historical, reactive):
    provider = reactive.provider()
    provider.describe = lambda: pytest.fail("provider contacted")
    provider.capabilities = lambda: pytest.fail("provider contacted")
    before = contents(historical)
    with pytest.raises(RuntimeError, match=r"predates M5\.4b"):
        reactive.runner(provider).resume(historical)
    assert provider.calls == []
    assert contents(historical) == before


def test_narrow_upgrade_and_stale_resume(reactive):
    provider = reactive.provider()
    result = reactive.run(provider)
    store = RunArtifactStore(result.path.parent, result.path.name)
    _, snapshot = validate_stored_run(store)
    case = snapshot.suite.cases[0]
    identity = SampleIdentity(case_id=case.id, repeat_index=0)
    context = _reactive_context(store, identity, case.evaluation)
    store.write_evaluation(
        identity, ReactiveEvaluator().derive(context, version="1.0.0"), replace=True
    )
    before = contents(result.path)
    provider.describe = lambda: pytest.fail("provider contacted")
    with pytest.raises(RuntimeError, match="explicit score upgrade"):
        reactive.runner(provider).resume(result.path)
    assert contents(result.path) == before
    summary = score_run(result.path)
    assert summary.schema_version == 7
    current = store.read_evaluation(identity, source_result_schema_version=4)
    assert current.evaluator_version == "1.3.0" and current.score == 1
    after = contents(result.path)
    for name, content in before.items():
        if name not in {"summary.json", "events.jsonl"} and not name.endswith("evaluation.json"):
            assert after[name] == content


def test_new_run_ineligible_before_provider(reactive):
    from dataclasses import replace

    from elarabench.models import EvaluationSpecification, RunConfiguration

    loaded = reactive.suite()
    case = loaded.suite.cases[0]
    case = case.model_copy(
        update={
            "evaluation": EvaluationSpecification(
                type="reactive_execution", config=reactive.fixture["config"]
            )
        }
    )
    from elarabench.hashing import hash_suite

    suite = loaded.suite.model_copy(update={"cases": (case,)})
    loaded = replace(loaded, suite=suite, content_hash=hash_suite(suite, {}))
    provider = reactive.provider()
    provider.describe = lambda: pytest.fail("provider contacted")
    with pytest.raises(ValueError, match="metadata incomplete"):
        reactive.runner(provider).run(
            loaded,
            RunConfiguration(
                suite_path=str(loaded.suite_dir), provider="fake", model="elarabench-fake-v1"
            ),
        )
    assert provider.calls == []


def test_frozen_m54b_upgrade_preserves_original_evidence(tmp_path, reactive):
    fixture = json.loads(
        Path("tests/fixtures/reactive_execution/historical-v1.1-run.json").read_text()
    )
    path = tmp_path / json.loads(fixture["files"]["manifest.json"])["run_id"]
    for name, content in fixture["files"].items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    before = contents(path)
    store = RunArtifactStore(path.parent, path.name)
    manifest, snapshot = validate_stored_run(store)
    case = snapshot.suite.cases[0]
    assert "failure_schedule" not in case.evaluation.config
    assert "failure_catalog" not in case.evaluation.config
    identity = SampleIdentity(case_id=case.id, repeat_index=0)
    context = _reactive_context(store, identity, case.evaluation)
    assert store.read_evaluation(identity, source_result_schema_version=4) == (
        ReactiveEvaluator().derive(context, version="1.1.0"))
    assert replay_reactive(context)[0].failure_state == ()
    provider = reactive.provider()
    provider.describe = lambda: pytest.fail("provider contacted")
    provider.capabilities = lambda: pytest.fail("provider contacted")
    with pytest.raises(RuntimeError, match="explicit score upgrade"):
        reactive.runner(provider).resume(path)
    comparison = compare_runs(path, path)
    assert all(r.status != "unavailable" for r in comparison.evaluator_resolution)
    assert contents(path) == before
    upgraded = score_run(path)
    assert upgraded.schema_version == 7
    assert upgraded.reactive_execution.evaluator_version == "1.3.0"
    assert summarize_run(path) == upgraded
    assert manifest.schema_version == 4
    after = contents(path)
    for name, content in before.items():
        if name not in {"summary.json", "events.jsonl"} and not name.endswith("evaluation.json"):
            assert after[name] == content


def test_frozen_m55_upgrade_and_canonical_preservation(tmp_path, reactive):
    fixture = json.loads(
        Path('tests/fixtures/reactive_execution/historical-v1.2-run.json').read_text())
    path = tmp_path / json.loads(fixture['files']['manifest.json'])['run_id']
    for name, content in fixture['files'].items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    before = contents(path)
    store = RunArtifactStore(path.parent, path.name)
    _, snapshot = validate_stored_run(store)
    for case in snapshot.suite.cases:
        assert 'observability' not in case.evaluation.config
        identity = SampleIdentity(case_id=case.id, repeat_index=0)
        context = _reactive_context(store, identity, case.evaluation)
        assert store.read_evaluation(identity, source_result_schema_version=4) == (
            ReactiveEvaluator().derive(context, version='1.2.0'))
        assert replay_reactive(context)[0].revealed_keys == frozenset()
    provider = reactive.provider()
    provider.describe = lambda: pytest.fail('provider contacted')
    provider.capabilities = lambda: pytest.fail('provider contacted')
    with pytest.raises(RuntimeError, match='explicit score upgrade'):
        reactive.runner(provider).resume(path)
    comparison = compare_runs(path, path)
    assert all(r.status != 'unavailable' for r in comparison.evaluator_resolution)
    assert contents(path) == before
    upgraded = score_run(path)
    assert upgraded.schema_version == 8
    assert upgraded.reactive_failure.evaluator_version == '1.3.0'
    assert summarize_run(path) == upgraded
    for name, content in before.items():
        if name not in {'summary.json', 'events.jsonl'} and not name.endswith('evaluation.json'):
            assert contents(path)[name] == content
