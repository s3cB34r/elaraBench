"""Small explicit provider construction mapping for implemented adapters."""

from __future__ import annotations

from elarabench.providers.base import ModelProvider, ProviderConfigurationError
from elarabench.providers.ollama import OllamaProvider


def create_provider(
    provider_type: str,
    *,
    model: str,
    endpoint: str | None = None,
) -> ModelProvider:
    """Construct one implemented provider without dynamic discovery or plugins."""
    if provider_type == "ollama":
        if endpoint is None:
            return OllamaProvider(model=model)
        return OllamaProvider(model=model, endpoint=endpoint)
    raise ProviderConfigurationError(
        f"unknown provider {provider_type!r}; supported providers: ollama"
    )
