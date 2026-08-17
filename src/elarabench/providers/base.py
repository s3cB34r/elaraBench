"""Narrow provider contract shared by fake and future real adapters."""

from __future__ import annotations

from typing import Protocol

from elarabench.models import (
    EndpointMetadata,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
)


class ProviderConfigurationError(RuntimeError):
    """Provider preflight or configuration cannot support a trustworthy run."""


class ModelProvider(Protocol):
    """Translate normalized requests into normalized model responses."""

    adapter_version: str

    def describe(self) -> ModelIdentity:
        """Return stable model and backend identity."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """Return explicitly supported provider features."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate one response without evaluating it."""
        ...

    def endpoint_metadata(self) -> EndpointMetadata:
        """Return credential-free connection identity."""
        ...

    def close(self) -> None:
        """Release provider-owned resources."""
        ...
