"""Narrow provider contract shared by fake and future real adapters."""

from __future__ import annotations

from typing import Protocol

from elarabench.models import (
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
)


class ModelProvider(Protocol):
    """Translate normalized requests into normalized model responses."""

    def describe(self) -> ModelIdentity:
        """Return stable model and backend identity."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """Return explicitly supported provider features."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate one response without evaluating it."""
        ...
