"""Best-effort, non-identifying execution environment discovery."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
from pathlib import Path

from elarabench import __version__
from elarabench.hashing import sha256_bytes
from elarabench.models import (
    EnvironmentMetadata,
    FrameworkMetadata,
    GPUInfo,
    SourceIdentity,
)

_which = shutil.which


def _probe(args: list[str], *, timeout: float = 2.0, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def _cpu_name() -> str | None:
    name = platform.processor().strip()
    if name:
        return name
    cpuinfo = Path("/proc/cpuinfo")
    try:
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.partition(":")[2].strip() or None
    except OSError:
        pass
    return None


def _discover_gpus() -> tuple[tuple[GPUInfo, ...], str | None, tuple[str, ...]]:
    executable = _which("nvidia-smi")
    if executable is None:
        return (), None, ()
    try:
        output = _probe(
            [
                executable,
                "--query-gpu=name,driver_version",
                "--format=csv,noheader,nounits",
            ]
        )
        gpus: list[GPUInfo] = []
        drivers: set[str] = set()
        for line in output.splitlines():
            name, separator, driver = line.partition(",")
            if not separator:
                continue
            driver = driver.strip()
            drivers.add(driver)
            gpus.append(GPUInfo(name=name.strip(), driver_version=driver))
        driver_value = ",".join(sorted(drivers)) or None
        return tuple(gpus), driver_value, ()
    except (OSError, subprocess.SubprocessError) as error:
        return (), None, (f"nvidia-smi discovery failed: {type(error).__name__}",)


def discover_environment() -> EnvironmentMetadata:
    """Collect useful metadata without ever requiring optional system tooling."""
    diagnostics: list[str] = []
    gpus, gpu_driver, gpu_diagnostics = _discover_gpus()
    diagnostics.extend(gpu_diagnostics)
    runtime_versions: dict[str, str] = {}
    for distribution in ("elarabench", "httpx", "pydantic"):
        try:
            runtime_versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            diagnostics.append(f"runtime version unavailable: {distribution}")
    return EnvironmentMetadata(
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        operating_system=platform.system(),
        os_release=platform.release(),
        architecture=platform.machine(),
        cpu=_cpu_name(),
        gpus=gpus,
        gpu_driver=gpu_driver,
        runtime_versions=runtime_versions,
        diagnostics=tuple(diagnostics),
    )


def discover_framework_metadata(repository: Path | None = None) -> FrameworkMetadata:
    """Collect version and Git source identity, degrading to unknown outside a checkout."""
    root = repository or Path.cwd()
    try:
        commit = _probe(["git", "rev-parse", "HEAD"], cwd=root)
        status = _probe(["git", "status", "--porcelain"], cwd=root)
        dirty = bool(status)
        state_hash: str | None = None
        if dirty:
            diff = _probe(["git", "diff", "--binary", "HEAD"], cwd=root)
            untracked = _probe(
                ["git", "ls-files", "--others", "--exclude-standard"], cwd=root
            )
            state = diff.encode("utf-8")
            for relative in sorted(filter(None, untracked.splitlines())):
                path = root / relative
                if path.is_file():
                    state += relative.encode("utf-8") + b"\0" + path.read_bytes()
            state_hash = sha256_bytes(state)
        source = SourceIdentity(
            git_commit=commit or None,
            git_dirty=dirty,
            source_state_hash=state_hash,
        )
    except (OSError, subprocess.SubprocessError):
        source = SourceIdentity()
    return FrameworkMetadata(version=__version__, source=source)
