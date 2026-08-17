"""Deterministic fake provider contract tests."""

from __future__ import annotations

from elarabench.hashing import hash_generation_request
from elarabench.models import GenerationError, GenerationRequest, GenerationResponse
from elarabench.providers import FakeProvider, ModelProvider


def request() -> GenerationRequest:
    return GenerationRequest.model_validate(
        {"messages": [{"role": "user", "content": "synthetic request"}], "seed": 42}
    )


def test_fake_provider_satisfies_contract_and_is_deterministic() -> None:
    provider: ModelProvider = FakeProvider()

    assert provider.generate(request()) == provider.generate(request())
    assert provider.describe() == provider.describe()
    assert provider.describe().provider == "fake"
    assert provider.capabilities().seed is True


def test_fake_provider_uses_configured_response_by_request_hash() -> None:
    generation_request = request()
    request_hash = hash_generation_request(generation_request)
    provider = FakeProvider(responses={request_hash: "configured"})

    assert provider.generate(generation_request) == GenerationResponse(
        text="configured",
        finish_reason="stop",
    )


def test_fake_provider_uses_configured_structured_response() -> None:
    generation_request = request()
    configured = GenerationResponse(text="structured", raw_payload={"test": True})
    provider = FakeProvider(responses={hash_generation_request(generation_request): configured})

    assert provider.generate(generation_request) is configured


def test_fake_provider_returns_configured_error_deterministically() -> None:
    generation_request = request()
    error = GenerationError(code="synthetic_failure", message="configured failure")
    provider = FakeProvider(errors={hash_generation_request(generation_request): error})

    first = provider.generate(generation_request)
    assert first == provider.generate(generation_request)
    assert first.error == error
    assert first.text == ""
