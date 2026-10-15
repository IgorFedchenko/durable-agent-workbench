"""Official Anthropic SDK adapter; contract-tested/live-unverified."""

from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic, APIConnectionError, APITimeoutError, RateLimitError

from ..models import Turn
from .base import (
    ProviderError,
    TransientProviderError,
    normalize_call,
    parse_decision,
    usage_or_none,
)
from .schema import request_payload


class AnthropicProvider:
    def __init__(self, model: str, client: Any = None):
        self.model = model
        self.client: Any = client or Anthropic(max_retries=0, timeout=30)
        if getattr(self.client, "max_retries", 0) != 0:
            raise ProviderError("SDK automatic retries must be disabled")

    def turn(
        self, system: str, messages: list[dict[str, Any]], *, role: str, turn: int, output_cap: int
    ) -> Turn:
        payload = request_payload("anthropic", self.model, system, messages, role, output_cap)
        try:
            response = self.client.messages.create(**payload)
        except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
            raise TransientProviderError(
                "Anthropic transient transport failure; outcome uncertain"
            ) from exc
        if response.stop_reason not in ("end_turn", "tool_use"):
            raise ProviderError("refused, truncated or unsupported Anthropic response")
        if any(b.type not in {"text", "tool_use"} for b in response.content):
            raise ProviderError("unsupported Anthropic content block")
        calls = [b for b in response.content if b.type == "tool_use"]
        if len(calls) > 1:
            raise ProviderError("parallel/multiple tool calls are forbidden")
        if calls:
            if any(b.type == "text" and b.text.strip() for b in response.content):
                raise ProviderError("conflicting tool and text response")
            call = calls[0]
            decision = normalize_call(call.name, call.id, call.input)
        else:
            blocks = [b.text for b in response.content if b.type == "text"]
            try:
                decision = parse_decision(json.loads("".join(blocks)))
            except ValueError as exc:
                raise ProviderError(
                    "expected structured final answer, refusal is not a report"
                ) from exc
            if decision.kind != "final":
                raise ProviderError("text response must be a structured final answer")
        return Turn(
            decision=decision,
            usage=usage_or_none(
                getattr(response.usage, "input_tokens", None),
                getattr(response.usage, "output_tokens", None),
            ),
        )
