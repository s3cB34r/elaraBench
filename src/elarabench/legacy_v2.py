"""Read-only compatibility for canonical result-schema-v2 run evidence.

The models in this module intentionally preserve the exact pre-Thinking serialization
shape. They must not acquire v3 defaults: doing so would change historical hashes.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field

from elarabench.hashing import (
    hash_canonical,
    hash_evaluation_specification,
    hash_run_fingerprint,
    sha256_bytes,
)
from elarabench.models import (
    AggregationConfiguration,
    BenchmarkCase,
    ChatMessage,
    DomainModel,
    EndpointMetadata,
    EnvironmentMetadata,
    FrameworkMetadata,
    GenerationParameters,
    Identifier,
    RequestPlanEntry,
    ResponseFormatConstraint,
    RetryPolicy,
    RunLifecycle,
    Score,
    SeedControlMetadata,
    SemanticVersion,
    Sha256Digest,
    SnapshotFixture,
)


class LegacyV2GenerationRequest(DomainModel):
    """Exact M2 request shape, before Thinking became canonical."""

    messages: Annotated[tuple[ChatMessage, ...], Field(min_length=1)]
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    seed: int | None = None
    timeout_seconds: Annotated[float, Field(gt=0.0)] = 120.0
    response_format: ResponseFormatConstraint | None = None


class LegacyV2SuiteDefaults(DomainModel):
    """Exact M2 suite defaults."""

    repeats: Annotated[int, Field(gt=0)] = 1
    timeout_seconds: Annotated[float, Field(gt=0.0)] = 120.0


class LegacyV2BenchmarkSuite(DomainModel):
    """Exact validated M2 benchmark suite shape."""

    schema_version: Literal[1]
    id: Identifier
    version: SemanticVersion
    title: str
    description: str | None = None
    license: str | None = None
    provenance: str | None = None
    tags: tuple[Identifier, ...] = ()
    cases: tuple[BenchmarkCase, ...]
    defaults: LegacyV2SuiteDefaults = Field(default_factory=LegacyV2SuiteDefaults)
    aggregation: AggregationConfiguration = Field(default_factory=AggregationConfiguration)


class LegacyV2RunConfiguration(DomainModel):
    """Exact M2 run configuration, with no implicit Thinking field."""

    suite_path: str
    provider: Identifier = "fake"
    model: str = "elarabench-fake-v1"
    endpoint: str | None = None
    repeats: Annotated[int, Field(gt=0)] = 1
    generation_parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    seed: int | None = None
    timeout_seconds: Annotated[float, Field(gt=0.0)] = 120.0
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    concurrency: Literal[1] = 1
    minimum_scored_coverage: Score = 0.95


class LegacyV2ProviderCapabilities(DomainModel):
    """Exact M2 provider capability shape."""

    seed: bool = False
    structured_output: bool = False
    tools: bool = False
    usage_metrics: bool = False


class LegacyV2ProviderMetadata(DomainModel):
    """Exact M2 provider identity shape."""

    type: Identifier
    adapter_version: SemanticVersion
    endpoint: EndpointMetadata
    capabilities: LegacyV2ProviderCapabilities


class LegacyV2ModelIdentity(DomainModel):
    """Exact M2 model identity shape."""

    provider: Identifier
    backend: Identifier
    model: str
    model_digest: str | None = None
    quantization: str | None = None
    backend_version: str | None = None
    tokenizer: str | None = None
    chat_template: str | None = None
    format: str | None = None
    family: str | None = None
    parameter_size: str | None = None
    capabilities: tuple[str, ...] = ()
    parameters_hash: Sha256Digest | None = None
    template_hash: Sha256Digest | None = None


class LegacyV2RunManifest(DomainModel):
    """Exact immutable identity/configuration shape written by M2."""

    schema_version: Literal[2] = 2
    run_id: Identifier
    run_fingerprint: Sha256Digest
    framework: FrameworkMetadata
    suite_id: Identifier
    suite_version: SemanticVersion
    suite_hash: Sha256Digest
    benchmark_snapshot_hash: Sha256Digest
    configuration: LegacyV2RunConfiguration
    provider: LegacyV2ProviderMetadata
    model: LegacyV2ModelIdentity
    seed_control: SeedControlMetadata
    environment: EnvironmentMetadata
    request_plan: tuple[RequestPlanEntry, ...]
    lifecycle: RunLifecycle


class LegacyV2BenchmarkSnapshot(DomainModel):
    """Exact self-contained benchmark snapshot written by M2."""

    schema_version: Literal[1] = 1
    suite: LegacyV2BenchmarkSuite
    fixtures: tuple[SnapshotFixture, ...] = ()
    minimum_scored_coverage: Score = 0.95
    benchmark_content_hash: Sha256Digest
    snapshot_hash: Sha256Digest


def hash_legacy_v2_request(request: LegacyV2GenerationRequest) -> str:
    """Hash a historical request without injecting v3 Thinking defaults."""
    return hash_canonical(request)


def validate_legacy_v2_snapshot(snapshot: LegacyV2BenchmarkSnapshot) -> None:
    """Verify snapshot and fixture hashes with exact schema-v2 semantics."""
    snapshot_payload = snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
    if hash_canonical(snapshot_payload) != snapshot.snapshot_hash:
        raise ValueError("benchmark snapshot hash mismatch")
    fixture_hashes: dict[str, str] = {}
    for fixture in snapshot.fixtures:
        relative = PurePosixPath(fixture.path)
        if (
            "\\" in fixture.path
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0] != "fixtures"
        ):
            raise ValueError(f"unsafe snapshot fixture path {fixture.path!r}")
        if fixture.path in fixture_hashes:
            raise ValueError(f"duplicate snapshot fixture {fixture.path!r}")
        try:
            content = base64.b64decode(fixture.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError(f"invalid base64 fixture {fixture.path!r}") from error
        if sha256_bytes(content) != fixture.content_hash:
            raise ValueError(f"fixture content hash mismatch for {fixture.path!r}")
        fixture_hashes[fixture.path] = fixture.content_hash
    referenced = {reference for case in snapshot.suite.cases for reference in case.fixtures}
    if set(fixture_hashes) != referenced:
        raise ValueError("snapshot fixtures do not match benchmark references")
    fixtures = [
        {"path": path, "sha256": fixture_hashes[path]}
        for path in sorted(fixture_hashes)
    ]
    content_hash = hash_canonical(
        {"suite": snapshot.suite.model_dump(mode="json"), "fixtures": fixtures}
    )
    if content_hash != snapshot.benchmark_content_hash:
        raise ValueError("benchmark snapshot content hash mismatch")


def legacy_v2_fingerprint_payload(
    manifest: LegacyV2RunManifest,
    snapshot: LegacyV2BenchmarkSnapshot,
) -> dict[str, object]:
    """Recreate the exact fingerprint payload used by M2."""
    configuration = manifest.configuration
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
        "request_plan": [
            entry.model_dump(mode="json") for entry in manifest.request_plan
        ],
        "provider": manifest.provider.model_dump(mode="json"),
        "model": manifest.model.model_dump(mode="json"),
        "execution": {
            "generation_parameters": configuration.generation_parameters.model_dump(
                mode="json"
            ),
            "seed": configuration.seed,
            "seed_control": manifest.seed_control.model_dump(mode="json"),
            "repeats": configuration.repeats,
            "timeout_seconds": configuration.timeout_seconds,
            "retry_policy": configuration.retry_policy.model_dump(mode="json"),
            "concurrency": configuration.concurrency,
            "minimum_scored_coverage": configuration.minimum_scored_coverage,
        },
        "framework": manifest.framework.model_dump(mode="json"),
    }


def compute_legacy_v2_fingerprint(
    manifest: LegacyV2RunManifest,
    snapshot: LegacyV2BenchmarkSnapshot,
) -> str:
    """Verify historical run identity without converting it to schema v3."""
    return hash_run_fingerprint(legacy_v2_fingerprint_payload(manifest, snapshot))

