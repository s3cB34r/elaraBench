"""A deterministic, offline provider for tests and core development."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from elarabench.hashing import hash_generation_request
from elarabench.models import (
    EndpointMetadata,
    GenerationError,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
    ThinkingControlKind,
)


class FakeProvider:
    """Return configured responses or stable request-hash-derived output."""

    adapter_version = "1.1.0"

    def __init__(
        self,
        *,
        responses: Mapping[str, str | GenerationResponse] | None = None,
        errors: Mapping[str, GenerationError] | None = None,
        scripts: Mapping[str, Sequence[GenerationResponse | BaseException]] | None = None,
        identity: ModelIdentity | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._errors = dict(errors or {})
        self._scripts = {key: list(value) for key, value in (scripts or {}).items()}
        self._calls: dict[str, int] = {}
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
        return ProviderCapabilities(
            seed=True,
            thinking_control=ThinkingControlKind.BOOLEAN,
            structured_output=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Return a deterministic response selected by canonical request hash."""
        request_hash = hash_generation_request(request)
        call_index = self._calls.get(request_hash, 0)
        self._calls[request_hash] = call_index + 1
        script = self._scripts.get(request_hash)
        if script:
            item = script[min(call_index, len(script) - 1)]
            if isinstance(item, BaseException):
                raise item
            return item
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

    def endpoint_metadata(self) -> EndpointMetadata:
        """Return a stable non-network endpoint identity."""
        return EndpointMetadata(
            scheme="fake",
            host="local",
            path="/",
            is_local=True,
        )

    def close(self) -> None:
        """The fake provider owns no external resources."""
