"""Best-effort environment discovery must never require NVIDIA tooling."""

from __future__ import annotations

import subprocess

import pytest

from elarabench import environment


def test_no_nvidia_tool_is_a_normal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment, "_which", lambda _name: None)

    metadata = environment.discover_environment()

    assert metadata.gpus == ()
    assert metadata.gpu_driver is None
    assert not any("nvidia-smi" in message for message in metadata.diagnostics)


def test_failed_gpu_probe_is_diagnostic_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment, "_which", lambda _name: "/usr/bin/nvidia-smi")

    def timeout(*_args: object, **_kwargs: object) -> str:
        raise subprocess.TimeoutExpired("nvidia-smi", 2)

    monkeypatch.setattr(environment, "_probe", timeout)
    metadata = environment.discover_environment()

    assert metadata.gpus == ()
    assert metadata.diagnostics[-1] == "nvidia-smi discovery failed: TimeoutExpired"


def test_gpu_probe_parses_multiple_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(environment, "_which", lambda _name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        environment,
        "_probe",
        lambda *_args, **_kwargs: "RTX 4090, 580.10\nRTX 3090, 580.10",
    )

    metadata = environment.discover_environment()

    assert [gpu.name for gpu in metadata.gpus] == ["RTX 4090", "RTX 3090"]
    assert metadata.gpu_driver == "580.10"


def test_framework_metadata_uses_product_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from elarabench import __version__

    monkeypatch.setattr(environment, "_probe", lambda *_a, **_kw: "")
    assert environment.discover_framework_metadata().version == __version__ == "0.4.0"
