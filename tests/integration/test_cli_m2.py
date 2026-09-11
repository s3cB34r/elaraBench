"""Complete M2 CLI flow through an offline fake provider."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.cli import build_parser, main
from elarabench.hashing import hash_generation_request
from elarabench.models import GenerationRequest, ThinkingPolicy
from elarabench.providers import FakeProvider
from elarabench.storage import ArtifactStore

OUTPUTS = {
    "exact-001": "ELARA",
    "numeric-001": "2.5",
    "choice-001": "B",
    "json-001": '{"answer": 7}',
    "content-001": "alpha and beta",
}


def response_map(
    *,
    thinking: ThinkingPolicy,
    timeout_seconds: float,
) -> dict[str, str]:
    loaded = load_benchmark_suite(Path("tests/fixtures/tiny_suite"))
    responses: dict[str, str] = {}
    for case in loaded.suite.cases:
        request = GenerationRequest(
            messages=case.messages,
            thinking=thinking,
            timeout_seconds=timeout_seconds,
            response_format=case.response_format,
        )
        responses[hash_generation_request(request)] = OUTPUTS[case.id]
    return responses


def test_run_score_and_summarize_cli_are_fully_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite_path = Path("tests/fixtures/tiny_suite")
    loaded = load_benchmark_suite(suite_path)
    responses = response_map(
        thinking=ThinkingPolicy.DISABLED,
        timeout_seconds=loaded.suite.defaults.timeout_seconds,
    )

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
    store = ArtifactStore(runs_dir).open_run("cli-run")
    manifest = store.read_manifest()
    stored_request = store.read_request(
        manifest.request_plan[0].identity
    )
    assert manifest.configuration.thinking is ThinkingPolicy.DISABLED
    assert manifest.configuration.timeout_seconds == 120.0
    assert stored_request.thinking is ThinkingPolicy.DISABLED
    assert stored_request.timeout_seconds == 120.0
    assert main(["score", str(run_path)]) == 0
    assert main(["summarize", str(run_path)]) == 0
    output = capsys.readouterr().out
    assert "Scored coverage: 100.00%" in output
    assert output.count("Score: 1.0") == 3


@pytest.mark.parametrize(
    ("policy_arguments", "expected_policy", "expected_timeout"),
    [
        (["--think"], ThinkingPolicy.ENABLED, 120.0),
        (["--no-think"], ThinkingPolicy.DISABLED, 120.0),
        (["--thinking", "provider-default"], ThinkingPolicy.PROVIDER_DEFAULT, 120.0),
        (["--timeout", "17"], ThinkingPolicy.DISABLED, 17.0),
    ],
)
def test_cli_resolves_explicit_runtime_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_arguments: list[str],
    expected_policy: ThinkingPolicy,
    expected_timeout: float,
) -> None:
    responses = response_map(
        thinking=expected_policy,
        timeout_seconds=expected_timeout,
    )

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
    arguments = [
        "run",
        "tests/fixtures/tiny_suite",
        "--provider",
        "fake",
        "--model",
        "elarabench-fake-v1",
        "--runs-dir",
        str(runs_dir),
        "--run-id",
        "policy-run",
        *policy_arguments,
    ]

    assert main(arguments) == 0
    manifest = ArtifactStore(runs_dir).open_run("policy-run").read_manifest()
    assert manifest.configuration.thinking is expected_policy
    assert manifest.configuration.timeout_seconds == expected_timeout


def test_cli_rejects_contradictory_thinking_flags() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(
            [
                "run",
                "tests/fixtures/tiny_suite",
                "--model",
                "model",
                "--think",
                "--no-think",
            ]
        )

    assert error.value.code == 2


@pytest.mark.parametrize(
    ("policy_arguments", "expected_policy"),
    [([], ThinkingPolicy.ENABLED), (["--no-think"], ThinkingPolicy.DISABLED)],
)
def test_cli_thinking_precedence_over_suite_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_arguments: list[str],
    expected_policy: ThinkingPolicy,
) -> None:
    suite_path = tmp_path / "suite"
    shutil.copytree("tests/fixtures/tiny_suite", suite_path)
    manifest_path = suite_path / "suite.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "  timeout_seconds: 120",
            "  timeout_seconds: 120\n  thinking: enabled",
        ),
        encoding="utf-8",
    )
    responses = response_map(thinking=expected_policy, timeout_seconds=120.0)

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
    arguments = [
        "run",
        str(suite_path),
        "--provider",
        "fake",
        "--model",
        "elarabench-fake-v1",
        "--runs-dir",
        str(runs_dir),
        "--run-id",
        "suite-thinking-run",
        *policy_arguments,
    ]

    assert main(arguments) == 0
    manifest = ArtifactStore(runs_dir).open_run("suite-thinking-run").read_manifest()
    assert manifest.configuration.thinking is expected_policy


def test_readme_reasoning_golden_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from elarabench import __version__
    from elarabench.builtin import get_builtin_suite_path
    from elarabench.models import GenerationParameters, ModelIdentity

    loaded = load_benchmark_suite(get_builtin_suite_path("reasoning.core"))
    golden_path = (
        Path(__file__).parents[1] / "fixtures/builtin_suite_goldens/reasoning-core-v1.jsonl"
    )
    goldens = {row["case_id"]: row["correct_response"]
               for row in map(json.loads, golden_path.read_text().splitlines())}
    responses = {
        hash_generation_request(GenerationRequest(
            messages=case.messages, parameters=GenerationParameters(temperature=0, max_tokens=64),
            seed=42, thinking=ThinkingPolicy.DISABLED, timeout_seconds=120,
            response_format=case.response_format,
        )): goldens[case.id] for case in loaded.suite.cases
    }

    def fake_factory(
        provider_type: str, *, model: str, endpoint: str | None = None,
    ) -> FakeProvider:
        assert provider_type == "ollama"
        assert model == "YOUR_INSTALLED_MODEL"
        assert endpoint == "http://127.0.0.1:11434"
        return FakeProvider(responses=responses, identity=ModelIdentity(
            provider=provider_type, model=model, model_digest="sha256:golden",
            backend="deterministic", backend_version="1.0.0",
        ))

    monkeypatch.setattr("elarabench.cli.create_provider", fake_factory)
    monkeypatch.chdir(tmp_path)
    assert main(["list"]) == 0
    assert main(["validate", "reasoning.core"]) == 0
    assert main([
        "run", "reasoning.core", "--provider", "ollama", "--model", "YOUR_INSTALLED_MODEL",
        "--endpoint", "http://127.0.0.1:11434", "--temperature", "0", "--seed", "42",
        "--repeats", "1", "--no-think", "--timeout", "120", "--max-retries", "0",
        "--max-tokens", "64", "--runs-dir", "runs",
    ]) == 0
    run_path = next((tmp_path / "runs").iterdir())
    output = capsys.readouterr().out
    assert "Score: 1.0" in output
    assert str(run_path) in output
    manifest = json.loads((run_path / "manifest.json").read_text())
    assert manifest["framework"]["version"] == __version__ == "0.4.0"
    for name in ("benchmark.json", "manifest.json", "samples", "summary.json", "events.jsonl"):
        assert (run_path / name).exists()
    before = {p: p.read_bytes() for p in run_path.rglob("*.json")
              if p.name in {"request.json", "response.json"}}
    assert before
    assert main(["summarize", str(run_path)]) == 0
    assert main(["score", str(run_path)]) == 0
    assert main(["compare", str(run_path), str(run_path), "--intent", "model"]) in (0, 1)
    assert all(p.read_bytes() == raw for p, raw in before.items())
