"""Dedicated Reactive Turn Engine seam beneath Runner.

Provider retry and filesystem lifecycle remain Runner responsibilities; this narrow engine owns
the deterministic continuation state used by both live turns and replay.
"""

from __future__ import annotations

from dataclasses import dataclass

from elarabench.models import GenerationRequest, GenerationResponse
from elarabench.reactive_execution import (
    ReactiveEvaluationContext,
    ReactiveExecutionConfig,
    ReactiveRuntime,
    next_reactive_request,
    replay_reactive,
    step_reactive,
)


@dataclass
class ReactiveTurnEngine:
    """Stateful per-sample deterministic turn continuation."""

    config: ReactiveExecutionConfig
    runtime: ReactiveRuntime

    def consume(self, response: GenerationResponse) -> None:
        self.runtime = step_reactive(self.config, self.runtime, response)

    def next_request(
        self, request: GenerationRequest, response: GenerationResponse,
    ) -> GenerationRequest:
        return next_reactive_request(request, response, self.runtime)

    @classmethod
    def replay(
        cls, context: ReactiveEvaluationContext,
    ) -> tuple[ReactiveRuntime, GenerationRequest]:
        return replay_reactive(context)
