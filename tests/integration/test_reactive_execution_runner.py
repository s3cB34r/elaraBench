"""Live causal execution, own-Turn retries, and physical layout compatibility."""

import pytest

from elarabench.models import EvaluationStatus, SampleIdentity
from elarabench.storage import RunArtifactStore


@pytest.mark.parametrize("retry_turn", [None, 0, 1])
def test_live_multiturn_and_retries(reactive, retry_turn):
    provider = reactive.provider(retry_turn=retry_turn)
    result = reactive.run(provider)
    assert provider.calls == ([0, 0, 1] if retry_turn == 0
                              else [0, 1, 1] if retry_turn == 1 else [0, 1])
    store = RunArtifactStore(reactive.root, "reactive-test")
    identity = SampleIdentity(case_id="reactive", repeat_index=0)
    assert result.manifest.schema_version == 4
    assert store.turn_indices(identity, reactive=True) == (0, 1)
    for index in (0, 1):
        view = store.for_turn(index)
        attempts = view.read_attempts(identity)
        assert [a.attempt_index for a in attempts] == list(range(len(attempts)))
        assert len(attempts) == (2 if index == retry_turn else 1)
    request = store.for_turn(1).read_request(identity)
    assert [m.role.value for m in request.messages] == ["system", "user", "assistant", "user"]
    assert request.messages[2].content == store.read_response(identity).text
    evaluation = store.read_evaluation(identity, source_result_schema_version=4)
    assert evaluation.status is EvaluationStatus.SCORED
    assert evaluation.score == 1 and evaluation.passed is True
    assert not list(result.path.glob("samples/*/*/request.json"))


@pytest.mark.parametrize("mixed", [False, True])
def test_mixed_v4_and_ordinary_v3(reactive, mixed):
    provider = reactive.provider()
    result = reactive.run(provider, mixed=mixed, ordinary=not mixed)
    assert result.manifest.schema_version == (4 if mixed else 3)
    store = RunArtifactStore(reactive.root, "reactive-test")
    identity = SampleIdentity(case_id="exact-001", repeat_index=0)
    evaluation = store.read_evaluation(
        identity, source_result_schema_version=4 if mixed else 3)
    assert evaluation.source_result_schema_version == (4 if mixed else 3)
    assert evaluation.score == 1
    sample = result.path / "samples" / "exact-001" / identity.repeat_id
    assert (sample / "turns/000/request.json").exists() is mixed
    assert (sample / "request.json").exists() is (not mixed)
