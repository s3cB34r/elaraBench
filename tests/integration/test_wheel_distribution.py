"""Build and install the wheel to prove first-party corpus distribution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).parents[2]
EXPECTED_WHEEL_FILES = {
    "elarabench/builtin_benchmarks/LICENSE",
    "elarabench/builtin_benchmarks/reasoning/core-v1/suite.yaml",
    "elarabench/builtin_benchmarks/reasoning/core-v1/cases.jsonl",
    "elarabench/builtin_benchmarks/instruction_following/core-v1/suite.yaml",
    "elarabench/builtin_benchmarks/instruction_following/core-v1/cases.jsonl",
    "elarabench/builtin_benchmarks/coding/core-v1/suite.yaml",
    "elarabench/builtin_benchmarks/coding/core-v1/cases.jsonl",
    "elarabench/builtin_benchmarks/cybersecurity/core-v1/suite.yaml",
    "elarabench/builtin_benchmarks/cybersecurity/core-v1/cases.jsonl",
    "elarabench/builtin_benchmarks/refusal_compliance/core-v1/suite.yaml",
    "elarabench/builtin_benchmarks/refusal_compliance/core-v1/cases.jsonl",
}
EXPECTED_HASHES = {
    "reasoning.core": "76e8699add4c94921e40215b85b2b8870abf25023dff02bff92b0a51e6021b3c",
    "instruction_following.core": (
        "2dc75a0d7fa60c0503e1430cb797d35f7310cff1e325b8468b2d92bdaac10b39"
    ),
    "coding.core": "0f1c5d78434ed711d7759eff8c5e727553a65364d2006b788f5710f5a91d7d94",
    "cybersecurity.core": (
        "61f0ce35f487ed1ad9c7cf10f7feaa5bd233ad5ceb0885b2bd1f940eb46d1ba3"
    ),
    "refusal_compliance.core": (
        "efb6802abfb536629c82380568b8e7c5b74cac2d56314a7cdd0cec10760ee7a9"
    ),
}


def completed(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def build_interpreter() -> str:
    """Select an interpreter containing the declared offline wheel build backend."""
    base_executable = cast(str, getattr(sys, "_base_executable", sys.executable))
    candidates = dict.fromkeys((sys.executable, base_executable))
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        check = completed(
            [candidate, "-c", "import setuptools; import wheel"],
            cwd=PROJECT_ROOT,
        )
        if check.returncode == 0:
            return candidate
    raise AssertionError(
        "offline wheel test requires the declared development dependencies setuptools and wheel"
    )


def test_wheel_contains_and_runs_bundled_suites(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = completed(
        [
            build_interpreter(),
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=PROJECT_ROOT,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert names >= EXPECTED_WHEEL_FILES
    assert not any("builtin_suite_goldens" in name for name in names)
    assert not any(name.startswith("tests/") for name in names)

    installed = tmp_path / "installed"
    install = completed(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        cwd=tmp_path,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    isolated_cwd = tmp_path / "isolated-cwd"
    isolated_cwd.mkdir()
    probe = completed(
        [
            sys.executable,
            "-I",
            "-c",
            """
import json
import pathlib
import sys

installed = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(installed))
import elarabench
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path

assert pathlib.Path(elarabench.__file__).resolve().is_relative_to(installed)
result = {}
for suite_id in (
    "reasoning.core",
    "instruction_following.core",
    "coding.core",
    "cybersecurity.core",
    "refusal_compliance.core",
):
    path = get_builtin_suite_path(suite_id)
    assert path.is_relative_to(installed)
    loaded = load_benchmark_suite(path)
    result[suite_id] = {
        "path": str(path),
        "case_count": len(loaded.suite.cases),
        "hash": loaded.content_hash,
    }
print(json.dumps(result, sort_keys=True))
""",
            str(installed),
        ],
        cwd=isolated_cwd,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    result = cast(dict[str, dict[str, object]], json.loads(probe.stdout))
    assert result["reasoning.core"]["case_count"] == 18
    assert result["instruction_following.core"]["case_count"] == 18
    assert result["coding.core"]["case_count"] == 12
    assert result["cybersecurity.core"]["case_count"] == 12
    assert result["refusal_compliance.core"]["case_count"] == 54
    assert {
        suite_id: data["hash"] for suite_id, data in result.items()
    } == EXPECTED_HASHES
