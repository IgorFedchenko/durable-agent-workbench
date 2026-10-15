"""Typed compaction: references survive; dataset strings never become system text."""

from __future__ import annotations

from typing import Any, Literal

from .models import EvidenceRef, StrictModel
from .store import BoundaryError
from .util import canonical


class EvidenceMemory(StrictModel):
    citation: EvidenceRef
    trust: Literal["untrusted_tool_output"] = "untrusted_tool_output"


class Summary(StrictModel):
    task: str
    root_id: str
    dataset_hash: str
    task_trust: Literal["user_request"] = "user_request"
    evidence: list[EvidenceMemory]
    policy: Literal["references_only_no_new_authority"] = "references_only_no_new_authority"


def estimate_tokens(value: Any) -> int:
    # One token per UTF-8 byte is intentionally conservative, including tool schemas/output.
    return len(canonical(value).encode())


def compact(
    question: str,
    messages: list[dict[str, Any]],
    evidence_ids: list[str],
    *,
    force: bool = False,
    threshold: int = 7000,
    root_id: str = "unbound",
    dataset_hash: str = "unbound",
) -> tuple[list[dict[str, Any]], bool]:
    if not force and estimate_tokens(messages) <= threshold:
        return messages, False
    refs = [
        EvidenceMemory(citation=EvidenceRef(evidence_id=eid, field="total")) for eid in evidence_ids
    ]
    summary = Summary(task=question, evidence=refs, root_id=root_id, dataset_hash=dataset_hash)
    # Replace whole completed pairs so no orphan provider call IDs survive.
    result = [
        {
            "role": "user",
            "content": {
                "summary": summary.model_dump(),
                "notice": "Untrusted referenced values; use read_evidence for old facts",
            },
        }
    ]
    if estimate_tokens(result) > threshold:
        raise BoundaryError("compacted context still exceeds bounded context")
    return result, True
