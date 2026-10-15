"""Official OpenAI SDK adapter; contract-tested/live-unverified."""

from __future__ import annotations

import json
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from ..models import Turn
from .base import (
    ProviderError,
    TransientProviderError,
    normalize_call,
    parse_decision,
    usage_or_none,
)
from .schema import request_payload


class OpenAIProvider:
    def __init__(self, model: str, client: Any = None):
        self.model = model
        self.client: Any = client or OpenAI(max_retries=0, timeout=30)
        if getattr(self.client, "max_retries", 0) != 0:
            raise ProviderError("SDK automatic retries must be disabled")

    def turn(
        self, system: str, messages: list[dict[str, Any]], *, role: str, turn: int, output_cap: int
    ) -> Turn:
        payload = request_payload("openai", self.model, system, messages, role, output_cap)
        try:
            response = self.client.chat.completions.create(**payload)
        except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
            raise TransientProviderError(
                "OpenAI transient transport failure; outcome uncertain"
            ) from exc
        if len(response.choices) != 1:
            raise ProviderError("expected exactly one response choice")
        choice = response.choices[0]
        if choice.finish_reason not in ("stop", "tool_calls"):
            raise ProviderError("truncated or unsupported OpenAI response")
        message = choice.message
        if getattr(message, "refusal", None):
            raise ProviderError("provider refused request")
        calls = message.tool_calls or []
        if len(calls) > 1:
            raise ProviderError("parallel/multiple tool calls are forbidden")
        if calls:
            if message.content and message.content.strip():
                raise ProviderError("conflicting tool and text response")
            call = calls[0]
            if getattr(call, "type", "function") != "function":
                raise ProviderError("unsupported OpenAI content item")
            decision = normalize_call(call.function.name, call.id, call.function.arguments)
        else:
            try:
                decision = parse_decision(json.loads(message.content or ""))
            except ValueError as exc:
                raise ProviderError("expected structured final answer") from exc
            if decision.kind != "final":
                raise ProviderError("text response must be a structured final answer")
        usage = response.usage
        return Turn(
            decision=decision,
            usage=usage_or_none(
                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
            ),
        )
