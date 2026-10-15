"""One-call provider contract. Automatic SDK retries are deliberately disabled."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from ..models import Decision, Final, JudgeResult, Report, ToolCall, Turn, Usage, Verdict
from ..store import BoundaryError


class ProviderError(BoundaryError):
    pass


class TransientProviderError(ProviderError):
    """Retryable transport failure; each retry needs a fresh durable reservation."""


class Provider(Protocol):
    def turn(
        self, system: str, messages: list[dict[str, Any]], *, role: str, turn: int, output_cap: int
    ) -> Turn: ...


def parse_decision(value: Any) -> Decision:
    try:
        return Turn.model_validate({"decision": value}).decision
    except ValidationError as exc:
        raise ProviderError("malformed normalized response; no automatic schema repair") from exc


def normalize_call(name: str, call_id: str, arguments: Any) -> ToolCall | Final:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError as exc:
            raise ProviderError("malformed tool arguments JSON") from exc
    if not isinstance(arguments, dict):
        raise ProviderError("tool arguments must be an object")
    if name == "final_answer":
        try:
            answer = (
                JudgeResult.model_validate(arguments)
                if "correctness" in arguments
                else Verdict.model_validate(arguments)
                if "accepted" in arguments
                else Report.model_validate(arguments)
            )
            return Final(answer=answer)
        except ValidationError as exc:
            raise ProviderError("invalid structured final answer") from exc
    return parse_decision({"kind": "tool", "id": call_id, "name": name, "arguments": arguments})


def usage_or_none(input_tokens: Any, output_tokens: Any) -> Usage | None:
    if input_tokens is None or output_tokens is None:
        return None
    try:
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    except ValidationError as exc:
        raise ProviderError("invalid provider usage") from exc
