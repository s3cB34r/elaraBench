"""Native synchronous Ollama provider adapter."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import JsonValue

from elarabench.hashing import sha256_bytes
from elarabench.models import (
    ChatRole,
    EndpointMetadata,
    GenerationError,
    GenerationErrorKind,
    GenerationRequest,
    GenerationResponse,
    ModelIdentity,
    ProviderCapabilities,
    ResponseFormatType,
    ThinkingControlKind,
    ThinkingPolicy,
    TimingMetadata,
    UsageInformation,
)
from elarabench.providers.base import ProviderConfigurationError


class OllamaProvider:
    """Map provider-neutral requests to Ollama's native `/api/chat` API."""

    adapter_version = "1.3.0"

    def __init__(
        self,
        model: str,
        *,
        endpoint: str = "http://127.0.0.1:11434",
        client: httpx.Client | None = None,
    ) -> None:
        if not model.strip():
            raise ProviderConfigurationError("Ollama model name must not be empty")
        self._model = model
        self._endpoint, self._endpoint_metadata = _normalize_endpoint(endpoint)
        self._client = client or httpx.Client(trust_env=False)
        self._owns_client = client is None
        self._identity: ModelIdentity | None = None
        self._thinking_control = ThinkingControlKind.UNKNOWN

    def endpoint_metadata(self) -> EndpointMetadata:
        return self._endpoint_metadata

    def capabilities(self) -> ProviderCapabilities:
        self.describe()
        return ProviderCapabilities(
            seed=True,
            thinking_control=self._thinking_control,
            structured_output=True,
            tools=False,
            usage_metrics=True,
        )

    def describe(self) -> ModelIdentity:
        """Discover and cache backend/model identity once per provider lifetime."""
        if self._identity is None:
            self._identity = self._discover_identity()
        return self._identity

    def _url(self, path: str) -> str:
        return f"{self._endpoint}{path}"

    def _preflight_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = self._client.request(
                method,
                self._url(path),
                json=payload,
                timeout=httpx.Timeout(10.0, connect=5.0, pool=5.0),
            )
        except httpx.HTTPError as error:
            raise ProviderConfigurationError(
                f"Ollama preflight failed for {path}: {type(error).__name__}: {error}"
            ) from error
        if response.status_code >= 400:
            message = _response_error_message(response)
            if response.status_code == 404 and path == "/api/show":
                raise ProviderConfigurationError(
                    f"Ollama model {self._model!r} was not found: {message}"
                )
            raise ProviderConfigurationError(
                f"Ollama preflight HTTP {response.status_code} for {path}: {message}"
            )
        try:
            value = response.json()
        except ValueError as error:
            raise ProviderConfigurationError(
                f"Ollama preflight returned malformed JSON for {path}"
            ) from error
        if not isinstance(value, dict):
            raise ProviderConfigurationError(
                f"Ollama preflight response for {path} is not an object"
            )
        return cast(dict[str, object], value)

    def _discover_identity(self) -> ModelIdentity:
        version_payload = self._preflight_json("GET", "/api/version")
        show_payload = self._preflight_json(
            "POST",
            "/api/show",
            payload={"model": self._model, "verbose": False},
        )
        digest: str | None = None
        try:
            tags_payload = self._preflight_json("GET", "/api/tags")
            models = tags_payload.get("models")
            if isinstance(models, list):
                candidates = {self._model, f"{self._model}:latest"}
                for item in models:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("model") or item.get("name")
                    if name in candidates and isinstance(item.get("digest"), str):
                        digest = cast(str, item["digest"])
                        break
        except ProviderConfigurationError:
            pass

        details = show_payload.get("details")
        details = details if isinstance(details, dict) else {}
        model_info = show_payload.get("model_info")
        model_info = model_info if isinstance(model_info, dict) else {}
        template = show_payload.get("template")
        parameters = show_payload.get("parameters")
        capabilities = show_payload.get("capabilities")
        architecture = _optional_string(model_info.get("general.architecture"))
        self._thinking_control = _discover_thinking_control(
            capabilities,
            architecture=architecture,
        )
        tokenizer = model_info.get("tokenizer.ggml.model")
        return ModelIdentity(
            provider="ollama",
            backend="ollama",
            model=self._model,
            model_digest=digest,
            quantization=_optional_string(details.get("quantization_level")),
            backend_version=_optional_string(version_payload.get("version")),
            tokenizer=_optional_string(tokenizer),
            architecture=architecture,
            format=_optional_string(details.get("format")),
            family=_optional_string(details.get("family")),
            parameter_size=_optional_string(details.get("parameter_size")),
            capabilities=(
                tuple(value for value in capabilities if isinstance(value, str))
                if isinstance(capabilities, list)
                else ()
            ),
            parameters_hash=(
                sha256_bytes(parameters.encode("utf-8")) if isinstance(parameters, str) else None
            ),
            template_hash=(
                sha256_bytes(template.encode("utf-8")) if isinstance(template, str) else None
            ),
        )

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate one non-streaming response and normalize every expected failure."""
        payload_or_error = self._translate_request(request)
        if isinstance(payload_or_error, GenerationError):
            return GenerationResponse(error=payload_or_error)
        payload = payload_or_error
        timeout = httpx.Timeout(
            request.timeout_seconds,
            connect=min(5.0, request.timeout_seconds),
            write=min(10.0, request.timeout_seconds),
            pool=min(5.0, request.timeout_seconds),
        )
        started = time.monotonic()
        try:
            response = self._client.post(self._url("/api/chat"), json=payload, timeout=timeout)
        except httpx.TimeoutException as error:
            generation_read_timeout = isinstance(error, httpx.ReadTimeout)
            return _error_response(
                payload,
                GenerationErrorKind.TIMEOUT,
                "http_read_timeout" if generation_read_timeout else "http_timeout",
                str(error),
                retryable=not generation_read_timeout,
                latency=time.monotonic() - started,
            )
        except (httpx.NetworkError, httpx.ProtocolError) as error:
            return _error_response(
                payload,
                GenerationErrorKind.CONNECTION,
                "http_transport",
                str(error),
                retryable=True,
                latency=time.monotonic() - started,
            )
        except httpx.HTTPError as error:
            return _error_response(
                payload,
                GenerationErrorKind.CONNECTION,
                "http_client",
                str(error),
                retryable=False,
                latency=time.monotonic() - started,
            )
        latency = time.monotonic() - started
        raw: JsonValue
        try:
            raw_value = response.json()
            raw = cast(JsonValue, raw_value)
        except ValueError:
            if response.status_code >= 400:
                return _error_response(
                    payload,
                    GenerationErrorKind.HTTP,
                    "http_status",
                    response.text or f"HTTP {response.status_code}",
                    retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                    http_status=response.status_code,
                    raw_payload=response.text,
                    latency=latency,
                )
            return _error_response(
                payload,
                GenerationErrorKind.MALFORMED_RESPONSE,
                "malformed_json",
                "Ollama returned malformed JSON",
                retryable=False,
                http_status=response.status_code,
                raw_payload=response.text,
                latency=latency,
            )
        if response.status_code >= 400:
            retryable = response.status_code in {408, 429, 500, 502, 503, 504}
            return _error_response(
                payload,
                GenerationErrorKind.HTTP,
                "http_status",
                _payload_error_message(raw_value) or f"HTTP {response.status_code}",
                retryable=retryable,
                http_status=response.status_code,
                raw_payload=raw,
                latency=latency,
            )
        provider_error = _payload_error_message(raw_value)
        if provider_error is not None:
            return _error_response(
                payload,
                GenerationErrorKind.PROVIDER,
                "ollama_error",
                provider_error,
                retryable=False,
                raw_payload=raw,
                latency=latency,
            )
        if not isinstance(raw_value, dict):
            return _malformed_success(payload, raw, latency, "response is not an object")
        message = raw_value.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return _malformed_success(payload, raw, latency, "missing message.content")
        if raw_value.get("done") is not True:
            return _malformed_success(payload, raw, latency, "missing completed done=true state")

        input_tokens = _optional_nonnegative_int(raw_value.get("prompt_eval_count"))
        output_tokens = _optional_nonnegative_int(raw_value.get("eval_count"))
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return GenerationResponse(
            text=cast(str, message["content"]),
            finish_reason=_optional_string(raw_value.get("done_reason")),
            usage=UsageInformation(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            timing=TimingMetadata(
                latency_seconds=latency,
                provider_total_seconds=_nanoseconds(raw_value.get("total_duration")),
                provider_load_seconds=_nanoseconds(raw_value.get("load_duration")),
                provider_prompt_eval_seconds=_nanoseconds(raw_value.get("prompt_eval_duration")),
                provider_eval_seconds=_nanoseconds(raw_value.get("eval_duration")),
            ),
            raw_request_payload=cast(JsonValue, payload),
            raw_payload=raw,
        )

    def _translate_request(self, request: GenerationRequest) -> dict[str, object] | GenerationError:
        if any(message.role is ChatRole.TOOL for message in request.messages):
            return GenerationError(
                kind=GenerationErrorKind.CONFIGURATION,
                code="unsupported_tool_message",
                message="Ollama M2 does not support tool-role messages",
            )
        thinking_value = _thinking_value(
            request.thinking,
            self.capabilities().thinking_control,
        )
        if isinstance(thinking_value, GenerationError):
            return thinking_value
        options: dict[str, object] = {}
        parameters = request.parameters
        mappings = {
            "temperature": parameters.temperature,
            "top_p": parameters.top_p,
            "top_k": parameters.top_k,
            "num_predict": parameters.max_tokens,
        }
        options.update({key: value for key, value in mappings.items() if value is not None})
        if parameters.stop:
            options["stop"] = list(parameters.stop)
        if request.seed is not None:
            options["seed"] = request.seed
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "stream": False,
        }
        if thinking_value is not None:
            payload["think"] = thinking_value
        if options:
            payload["options"] = options
        if request.response_format is not None:
            if not self.capabilities().structured_output:
                return GenerationError(
                    kind=GenerationErrorKind.CONFIGURATION,
                    code="unsupported_structured_output",
                    message="configured Ollama endpoint does not support structured output",
                )
            if request.response_format.type is ResponseFormatType.JSON:
                payload["format"] = "json"
            elif request.response_format.type is ResponseFormatType.JSON_SCHEMA:
                payload["format"] = request.response_format.json_schema or {}
        return payload

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OllamaProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


_BOOLEAN_THINKING_ARCHITECTURES = frozenset({"qwen3", "qwen35"})
_LEVEL_THINKING_ARCHITECTURES = frozenset({"gptoss"})


def _discover_thinking_control(
    capabilities: object,
    *,
    architecture: str | None,
) -> ThinkingControlKind:
    """Classify only explicit capability plus documented architecture evidence.

    Ollama's broad ``thinking`` capability does not identify whether ``think`` accepts
    booleans or levels. Exact ``general.architecture`` values in these small compatibility
    sets are the stronger evidence; every unrecognized thinking architecture stays unknown.
    """
    if not isinstance(capabilities, list) or any(
        not isinstance(value, str) for value in capabilities
    ):
        return ThinkingControlKind.UNKNOWN
    normalized = {value.strip().lower().replace("-", "_") for value in capabilities}
    if "thinking" not in normalized:
        return ThinkingControlKind.NONE
    normalized_architecture = architecture.strip().lower() if architecture else None
    if normalized_architecture in _BOOLEAN_THINKING_ARCHITECTURES:
        return ThinkingControlKind.BOOLEAN
    if normalized_architecture in _LEVEL_THINKING_ARCHITECTURES:
        return ThinkingControlKind.LEVELS
    return ThinkingControlKind.UNKNOWN


def _thinking_value(
    policy: ThinkingPolicy,
    support: ThinkingControlKind,
) -> bool | GenerationError | None:
    if policy is ThinkingPolicy.PROVIDER_DEFAULT:
        return None
    if support is ThinkingControlKind.BOOLEAN:
        return policy is ThinkingPolicy.ENABLED
    if (
        support is ThinkingControlKind.NONE
        and policy is ThinkingPolicy.DISABLED
    ):
        return None
    if support is ThinkingControlKind.NONE:
        message = (
            "selected Ollama model does not advertise thinking capability; "
            "thinking cannot be enabled"
        )
        code = "thinking_not_supported"
    elif support is ThinkingControlKind.UNKNOWN:
        message = (
            f"Ollama cannot verify explicit thinking={policy.value!r} control; "
            "use provider_default only if provider/model defaults are intentional"
        )
        code = "thinking_control_unknown"
    else:
        message = (
            f"Ollama model exposes level-valued thinking control, which cannot safely "
            f"represent thinking={policy.value!r}; use provider_default or choose a "
            "boolean-controllable model"
        )
        code = "thinking_control_levels_unsupported"
    return GenerationError(
        kind=GenerationErrorKind.CONFIGURATION,
        code=code,
        message=message,
    )


def _normalize_endpoint(endpoint: str) -> tuple[str, EndpointMetadata]:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise ProviderConfigurationError(f"invalid Ollama endpoint: {error}") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderConfigurationError("Ollama endpoint must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderConfigurationError(
            "Ollama endpoint must not contain credentials or query data"
        )
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    netloc = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    root = urlunsplit((parsed.scheme, netloc, path, "", "")).rstrip("/")
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    return root, EndpointMetadata(
        scheme=parsed.scheme,
        host=parsed.hostname,
        port=port,
        path=f"{path}/api" if path else "/api",
        is_local=is_local,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _nanoseconds(value: object) -> float | None:
    number = _optional_nonnegative_int(value)
    return number / 1_000_000_000 if number is not None else None


def _payload_error_message(value: object) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("error"), str):
        return cast(str, value["error"])
    return None


def _response_error_message(response: httpx.Response) -> str:
    try:
        message = _payload_error_message(response.json())
    except ValueError:
        message = None
    return message or response.text or response.reason_phrase


def _error_response(
    request_payload: dict[str, object],
    kind: GenerationErrorKind,
    code: str,
    message: str,
    *,
    retryable: bool,
    latency: float,
    http_status: int | None = None,
    raw_payload: JsonValue | None = None,
) -> GenerationResponse:
    return GenerationResponse(
        error=GenerationError(
            kind=kind,
            code=code,
            message=message,
            retryable=retryable,
            http_status=http_status,
            provider_message=message,
        ),
        timing=TimingMetadata(latency_seconds=latency),
        raw_request_payload=cast(JsonValue, request_payload),
        raw_payload=raw_payload,
    )


def _malformed_success(
    request_payload: dict[str, object],
    raw_payload: JsonValue,
    latency: float,
    detail: str,
) -> GenerationResponse:
    return _error_response(
        request_payload,
        GenerationErrorKind.MALFORMED_RESPONSE,
        "malformed_success_response",
        f"Ollama success response is invalid: {detail}",
        retryable=False,
        latency=latency,
        raw_payload=raw_payload,
    )
