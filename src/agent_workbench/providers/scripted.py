"""Author-written responses with synthetic usage; real tools still execute."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Turn
from .base import ProviderError


class ScriptedProvider:
    def __init__(self, recording: Path):
        self.recording = json.loads(recording.read_text())
        if self.recording.get("schema_version") != 1:
            raise ProviderError("unsupported author-written recording version")

    def turn(
        self, system: str, messages: list[dict[str, Any]], *, role: str, turn: int, output_cap: int
    ) -> Turn:
        del system, output_cap
        turns = self.recording["reviewer" if role == "reviewer" else "parent"]
        if turn >= len(turns):
            raise ProviderError("authored recording exhausted before terminal result")
        evidence_ids: list[str] = []

        def discover(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "evidence_id" and isinstance(child, str) and child.startswith("ev-"):
                        evidence_ids.append(child)
                    discover(child)
            elif isinstance(value, list):
                for child in value:
                    discover(child)

        discover(messages)

        def bind(value: Any) -> Any:
            if value == "$analysis":
                if not evidence_ids:
                    raise ProviderError("recording requires successful analysis evidence")
                return evidence_ids[0]
            if isinstance(value, dict):
                return {k: bind(v) for k, v in value.items()}
            if isinstance(value, list):
                return [bind(v) for v in value]
            return value

        return Turn.model_validate(bind(turns[turn]))
