"""Complete M2 CLI flow through an offline fake provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.cli import main
from elarabench.hashing import hash_generation_request
from elarabench.models import GenerationRequest
from elarabench.providers import FakeProvider


def test_run_score_and_summarize_cli_are_fully_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite_path = Path("tests/fixtures/tiny_suite")
    loaded = load_benchmark_suite(suite_path)
    outputs = {
        "exact-001": "ELARA",
        "numeric-001": "2.5",
        "choice-001": "B",
        "json-001": '{"answer": 7}',
        "content-001": "alpha and beta",
    }
    responses: dict[str, str] = {}
    for case in loaded.suite.cases:
        request = GenerationRequest(
            messages=case.messages,
            timeout_seconds=loaded.suite.defaults.timeout_seconds,
            response_format=case.response_format,
        )
        responses[hash_generation_request(request)] = outputs[case.id]

    def fake_factory(
        _provider_type: str,
        *,
        model: str,
        endpoint: str | None = None,
    ) -> FakeProvider:
        del model, endpoint
        return FakeProvider(responses=responses)

    monkeypatch.setattr("elarabench.cli.create_provider", fake_factory)
    runs_dir = tmp_path / "runs"
    run_code = main(
        [
            "run",
            str(suite_path),
            "--provider",
            "fake",
            "--model",
            "elarabench-fake-v1",
            "--runs-dir",
            str(runs_dir),
            "--run-id",
            "cli-run",
        ]
    )
    run_path = runs_dir / "cli-run"
    assert run_code == 0
    assert main(["score", str(run_path)]) == 0
    assert main(["summarize", str(run_path)]) == 0
    output = capsys.readouterr().out
    assert "Scored coverage: 100.00%" in output
    assert output.count("Score: 1.0") == 3
