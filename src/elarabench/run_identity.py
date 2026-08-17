"""Deterministic run fingerprint and physical run identifier helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from elarabench.hashing import hash_evaluation_specification, hash_run_fingerprint
from elarabench.models import (
    BenchmarkSnapshot,
    FrameworkMetadata,
    ModelIdentity,
    ProviderMetadata,
    RequestPlanEntry,
    RunConfiguration,
    SeedControlMetadata,
)


def run_fingerprint_payload(
    *,
    snapshot: BenchmarkSnapshot,
    configuration: RunConfiguration,
    provider: ProviderMetadata,
    model: ModelIdentity,
    seed_control: SeedControlMetadata,
    framework: FrameworkMetadata,
    request_plan: tuple[RequestPlanEntry, ...],
) -> dict[str, Any]:
    """Return logical execution identity without timestamps, paths, or hardware."""
    return {
        "fingerprint_schema_version": 1,
        "benchmark": {
            "snapshot_hash": snapshot.snapshot_hash,
            "content_hash": snapshot.benchmark_content_hash,
            "ordered_case_ids": [case.id for case in snapshot.suite.cases],
            "evaluator_configuration_hashes": [
                hash_evaluation_specification(case.evaluation)
                for case in snapshot.suite.cases
            ],
        },
        "request_plan": [entry.model_dump(mode="json") for entry in request_plan],
        "provider": provider.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
        "execution": {
            "generation_parameters": configuration.generation_parameters.model_dump(
                mode="json"
            ),
            "seed": configuration.seed,
            "seed_control": seed_control.model_dump(mode="json"),
            "repeats": configuration.repeats,
            "timeout_seconds": configuration.timeout_seconds,
            "retry_policy": configuration.retry_policy.model_dump(mode="json"),
            "concurrency": configuration.concurrency,
            "minimum_scored_coverage": configuration.minimum_scored_coverage,
        },
        "framework": framework.model_dump(mode="json"),
    }


def compute_run_fingerprint(
    *,
    snapshot: BenchmarkSnapshot,
    configuration: RunConfiguration,
    provider: ProviderMetadata,
    model: ModelIdentity,
    seed_control: SeedControlMetadata,
    framework: FrameworkMetadata,
    request_plan: tuple[RequestPlanEntry, ...],
) -> str:
    """Hash the versioned logical reproducibility identity."""
    return hash_run_fingerprint(
        run_fingerprint_payload(
            snapshot=snapshot,
            configuration=configuration,
            provider=provider,
            model=model,
            seed_control=seed_control,
            framework=framework,
            request_plan=request_plan,
        )
    )


def generate_run_id(fingerprint: str, timestamp: datetime) -> str:
    """Create a human-sortable physical execution ID; time is not fingerprint input."""
    normalized = timestamp.astimezone(UTC)
    return f"{normalized.strftime('%Y%m%dT%H%M%S%fZ')}-{fingerprint[:12]}"
