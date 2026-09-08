"""Small test-only Reactive fixture harness; no production suite registration."""

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.hashing import hash_suite
from elarabench.models import (
    BenchmarkCase,
    EnvironmentMetadata,
    FrameworkMetadata,
    GenerationResponse,
    RunConfiguration,
    SourceIdentity,
)
from elarabench.providers import FakeProvider
from elarabench.reactive_execution import ReactiveExecutionConfig, render_reactive_task
from elarabench.runner import Runner


class ReactiveHarness:
    def __init__(self, root):
        self.root = root
        self.fixture = json.loads(
            Path("tests/fixtures/reactive_execution/fixture.json").read_text())

    def config(self, **updates):
        return ReactiveExecutionConfig.model_validate(self.fixture["config"] | updates)

    def suite(self, *, mixed=False, ordinary=False):
        base = load_benchmark_suite("tests/fixtures/tiny_suite")
        config = self.config()
        case = BenchmarkCase.model_validate({
            "id": "reactive", "category": "synthetic",
            "messages": [
                {"role": "system", "content": "Follow the task using strict Action/Control JSON."},
                {"role": "user",
                 "content": render_reactive_task(config, self.fixture["objective"])},
            ],
            "evaluation": {"type": "reactive_execution", "config": config.model_dump(mode="json")},
        })
        ordinary_case = base.suite.cases[0].model_copy(update={"fixtures": ()})
        cases = (ordinary_case,) if ordinary else (case,)
        if mixed:
            cases += (ordinary_case,)
        suite = base.suite.model_copy(update={"cases": cases})
        return LoadedBenchmarkSuite(base.suite_dir, suite, hash_suite(suite, {}),
                                    MappingProxyType({}))

    def runner(self, provider):
        return Runner(
            provider, runs_dir=self.root,
            framework=FrameworkMetadata(
                version="0.2.1", source=SourceIdentity(git_commit="a" * 40, git_dirty=False)),
            environment=EnvironmentMetadata(
                python_version="3.12", python_implementation="CPython",
                operating_system="Linux", os_release="test", architecture="test", cpu="test"),
            sleeper=lambda _: None,
        )

    def run(self, provider, **options):
        suite = self.suite(**options)
        configuration = RunConfiguration(
            suite_path=str(suite.suite_dir), provider="fake", model="elarabench-fake-v1")
        return self.runner(provider).run(suite, configuration, run_id="reactive-test")

    def provider(self, *, retry_turn=None):
        goldens = json.loads(Path("tests/fixtures/reactive_execution/golden.json").read_text())
        responses = next(row["responses"] for row in goldens if row["id"] == "e5_multi")

        class ScriptedProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.retried = False

            def generate(self, request):
                from elarabench.models import GenerationError, GenerationErrorKind

                turn = (len(request.messages) - 2) // 2
                self.calls.append(turn)
                if turn == retry_turn and not self.retried:
                    self.retried = True
                    return GenerationResponse(error=GenerationError(
                        code="test_timeout", kind=GenerationErrorKind.TIMEOUT,
                        message="test retry", retryable=True))
                if "Behavioral objective:" not in request.messages[-1].content and turn == 0:
                    return GenerationResponse(text="ELARA")
                return GenerationResponse(text=responses[turn])
        return ScriptedProvider()


@pytest.fixture
def reactive(tmp_path):
    return ReactiveHarness(tmp_path / "runs")
