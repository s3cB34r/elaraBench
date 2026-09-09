"""Offline derivation and rejection of transplanted or causally corrupt evidence."""

import json

import pytest

from elarabench.hashing import hash_generation_request
from elarabench.models import EvaluationStatus, GenerationRequest, SampleIdentity
from elarabench.scoring import score_run, summarize_run
from elarabench.storage import RunArtifactStore


def test_offline_rederivation_and_scored_summary(reactive):
    provider = reactive.provider()
    result = reactive.run(provider, mixed=True)
    store = RunArtifactStore(reactive.root, "reactive-test")
    identity = SampleIdentity(case_id="reactive", repeat_index=0)
    original = store.read_evaluation(identity, source_result_schema_version=4)
    canonical = {p: p.read_bytes() for p in result.path.glob("samples/**/turns/**/*")
                 if p.is_file()}
    calls = list(provider.calls)
    summary = score_run(result.path)
    assert store.read_evaluation(identity, source_result_schema_version=4) == original
    assert original.status is EvaluationStatus.SCORED
    assert original.score == 1 and original.passed is True
    assert summarize_run(result.path) == summary
    assert provider.calls == calls
    assert summary.sample_status_counts.scored == 2
    assert summary.coverage.ratio == 1
    assert summary.score is None and summary.partial_score is None
    assert summary.schema_version == 7
    for file, content in canonical.items():
        assert file.read_bytes() == content


@pytest.mark.parametrize("corruption", ["attempt", "request"])
def test_corrupt_turn_rejected(reactive, corruption):
    provider = reactive.provider()
    result = reactive.run(provider)
    sample = next((result.path / "samples/reactive").iterdir())
    if corruption == "attempt":
        source = next((sample / "turns/000/attempts").glob("*.json"))
        target = next((sample / "turns/001/attempts").glob("*.json"))
        target.write_bytes(source.read_bytes())
    else:
        target = sample / "turns/001/request.json"
        data = json.loads(target.read_text())
        data["messages"][-1]["content"] = "tampered observation"
        target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        # Repair own-Turn hash binding so only causal transcript replay detects the edit.
        request_hash = hash_generation_request(GenerationRequest.model_validate(data))
        for attempt in (sample / "turns/001/attempts").glob("*.json"):
            record = json.loads(attempt.read_text())
            record["request_hash"] = request_hash
            attempt.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    before = list(provider.calls)
    with pytest.raises(ValueError):
        summarize_run(result.path)
    with pytest.raises((ValueError, RuntimeError)):
        reactive.runner(provider).resume(result.path)
    assert provider.calls == before
