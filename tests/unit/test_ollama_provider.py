"""Offline native Ollama adapter tests using HTTPX MockTransport."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pydantic import JsonValue

from elarabench.models import (
    GenerationErrorKind,
    GenerationRequest,
    ResponseFormatConstraint,
    ResponseFormatType,
)
from elarabench.providers import OllamaProvider, ProviderConfigurationError


def request(**updates: object) -> GenerationRequest:
    base: dict[str, object] = {
        "messages": [{"role": "user", "content": "Say hi"}],
        "parameters": {
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 20,
            "max_tokens": 32,
            "stop": ["END"],
        },
        "seed": 7,
        "timeout_seconds": 3,
    }
    base.update(updates)
    return GenerationRequest.model_validate(base)


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def preflight_response(http_request: httpx.Request) -> httpx.Response:
    if http_request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.11.4"})
    if http_request.url.path == "/api/show":
        return httpx.Response(
            200,
            json={
                "details": {
                    "format": "gguf",
                    "family": "gemma3",
                    "parameter_size": "4.3B",
                    "quantization_level": "Q4_K_M",
                },
                "model_info": {"tokenizer.ggml.model": "gemma"},
                "template": "{{ .Prompt }}",
                "parameters": "temperature 0.7",
                "capabilities": ["completion", "vision"],
            },
        )
    if http_request.url.path == "/api/tags":
        return httpx.Response(
            200,
            json={"models": [{"name": "gemma3:latest", "digest": "sha256:abc"}]},
        )
    raise AssertionError(f"unexpected request: {http_request.method} {http_request.url}")


def test_metadata_discovery_is_complete_and_cached() -> None:
    calls: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request.url.path)
        return preflight_response(http_request)

    provider = OllamaProvider(model="gemma3", client=client(handler))
    first = provider.describe()
    second = provider.describe()

    assert first == second
    assert calls == ["/api/version", "/api/show", "/api/tags"]
    assert first.backend_version == "0.11.4"
    assert first.model_digest == "sha256:abc"
    assert first.quantization == "Q4_K_M"
    assert first.capabilities == ("completion", "vision")
    assert first.template_hash is not None
    assert first.parameters_hash is not None


def test_generation_translates_all_parameters_and_preserves_payloads() -> None:
    payloads: list[dict[str, object]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url.path == "/api/chat"
        payload = http_request.read()
        parsed = __import__("json").loads(payload)
        payloads.append(parsed)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "hi"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 4,
                "eval_count": 2,
                "total_duration": 2_000_000_000,
                "load_duration": 500_000_000,
                "prompt_eval_duration": 300_000_000,
                "eval_duration": 1_000_000_000,
            },
        )

    provider = OllamaProvider(model="gemma3", client=client(handler))
    response = provider.generate(request())

    assert response.error is None
    assert response.text == "hi"
    assert response.finish_reason == "stop"
    assert response.usage is not None and response.usage.total_tokens == 6
    assert response.timing is not None and response.timing.provider_total_seconds == 2.0
    assert response.raw_payload is not None
    assert response.raw_request_payload == payloads[0]
    assert payloads[0]["stream"] is False
    assert payloads[0]["options"] == {
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 20,
        "num_predict": 32,
        "stop": ["END"],
        "seed": 7,
    }


def test_json_schema_is_sent_as_native_format() -> None:
    captured: list[dict[str, object]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured.append(__import__("json").loads(http_request.read()))
        return httpx.Response(
            200,
            json={"message": {"content": "{\"answer\":7}"}, "done": True},
        )

    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
    }
    provider = OllamaProvider(model="gemma3", client=client(handler))
    response = provider.generate(
        request(
            response_format=ResponseFormatConstraint(
                type=ResponseFormatType.JSON_SCHEMA,
                json_schema=schema,
            )
        )
    )

    assert response.error is None
    assert captured[0]["format"] == schema


def test_tool_messages_fail_without_network() -> None:
    def unexpected(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be used")

    provider = OllamaProvider(model="gemma3", client=client(unexpected))
    response = provider.generate(
        GenerationRequest.model_validate(
            {"messages": [{"role": "tool", "content": "result"}]}
        )
    )

    assert response.error is not None
    assert response.error.kind is GenerationErrorKind.CONFIGURATION
    assert response.error.retryable is False


def test_timeout_is_normalized_as_retryable() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=http_request)

    provider = OllamaProvider(model="gemma3", client=client(handler))
    response = provider.generate(request())

    assert response.error is not None
    assert response.error.kind is GenerationErrorKind.TIMEOUT
    assert response.error.retryable is True


def test_transient_http_error_with_non_json_body_remains_retryable() -> None:
    provider = OllamaProvider(
        model="gemma3",
        client=client(lambda _request: httpx.Response(503, text="temporarily unavailable")),
    )
    response = provider.generate(request())

    assert response.error is not None
    assert response.error.kind is GenerationErrorKind.HTTP
    assert response.error.http_status == 503
    assert response.error.retryable is True
    assert response.raw_payload == "temporarily unavailable"


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (404, False), (422, False), (429, True), (500, True), (503, True)],
)
def test_http_status_retry_policy(status: int, retryable: bool) -> None:
    provider = OllamaProvider(
        model="gemma3",
        client=client(lambda _request: httpx.Response(status, json={"error": "failed"})),
    )
    response = provider.generate(request())

    assert response.error is not None
    assert response.error.kind is GenerationErrorKind.HTTP
    assert response.error.http_status == status
    assert response.error.retryable is retryable


@pytest.mark.parametrize(
    "payload",
    [None, [], {"done": True}, {"message": {"content": "ok"}}, {"error": "backend failed"}],
)
def test_malformed_and_application_errors_are_durable(payload: object) -> None:
    provider = OllamaProvider(
        model="gemma3",
        client=client(lambda _request: httpx.Response(200, json=payload)),
    )
    response = provider.generate(request())

    assert response.error is not None
    assert response.error.retryable is False
    assert response.raw_payload == ("" if payload is None else payload)


def test_model_not_found_during_preflight_is_configuration_failure() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.11.4"})
        return httpx.Response(404, json={"error": "model not found"})

    provider = OllamaProvider(model="missing", client=client(handler))
    with pytest.raises(ProviderConfigurationError, match="was not found"):
        provider.describe()


@pytest.mark.parametrize(
    "endpoint",
    ["ftp://localhost:11434", "http://user:secret@localhost", "http://localhost?token=x"],
)
def test_endpoint_rejects_unsafe_or_credential_bearing_values(endpoint: str) -> None:
    with pytest.raises(ProviderConfigurationError):
        OllamaProvider(model="gemma3", endpoint=endpoint)
