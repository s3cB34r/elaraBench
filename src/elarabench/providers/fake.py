"""A deterministic, offline provider for tests and core development."""

from __future__ import annotations

from collections.abc import Mapping

from elarabench.hashing import hash_generation_request
from elarabench.models import (
    GenerationError,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
)


class FakeProvider:
    """Return configured responses or stable request-hash-derived output."""

    def __init__(
        self,
        *,
        responses: Mapping[str, str | GenerationResponse] | None = None,
        errors: Mapping[str, GenerationError] | None = None,
        identity: ModelIdentity | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._errors = dict(errors or {})
        self._identity = identity or ModelIdentity(
            provider="fake",
            backend="deterministic",
            model="elarabench-fake-v1",
            model_digest="sha256:elarabench-fake-v1",
            backend_version="1.0.0",
        )

    def describe(self) -> ModelIdentity:
        """Return the configured stable fake identity."""
        return self._identity

    def capabilities(self) -> ProviderCapabilities:
        """Advertise only behavior implemented by the fake provider."""
        return ProviderCapabilities(seed=True, structured_output=True)

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Return a deterministic response selected by canonical request hash."""
        request_hash = hash_generation_request(request)
        if request_hash in self._errors:
            return GenerationResponse(error=self._errors[request_hash])
        configured = self._responses.get(request_hash)
        if isinstance(configured, GenerationResponse):
            return configured
        if isinstance(configured, str):
            return GenerationResponse(text=configured, finish_reason="stop")
        return GenerationResponse(
            text=f"fake:{request_hash}",
            finish_reason="stop",
            raw_payload={"request_hash": request_hash},
        )
