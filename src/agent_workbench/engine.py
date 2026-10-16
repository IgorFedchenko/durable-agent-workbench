"""Small durable state machine. Root flock covers recovery, parent and reviewer."""

from __future__ import annotations

import csv
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import __version__
from .context import compact, estimate_tokens
from .models import (
    AnalyzeArgs,
    EmptyArgs,
    ReadArgs,
    Report,
    ReviewArgs,
    RunConfig,
    ToolCall,
    Turn,
    Verdict,
)
from .providers.base import Provider, TransientProviderError
from .providers.schema import request_payload, tools
from .providers.scripted import ScriptedProvider
from .sandbox import DockerSandbox
from .store import BoundaryError, Store
from .telemetry import log_event, safe_span, setup, trace_ids
from .util import ROOT, file_hash, object_hash, source_fingerprint

SCENARIOS = (
    "total",
    "regions",
    "months",
    "filter",
    "empty-filter",
    "repair",
    "injection",
    "compaction",
    "interruption",
    "reviewer-rejection",
)
STATE_SCHEMA = 1
ENGINE_VERSION = "1"


class IncompatibleResume(BoundaryError):
    pass


class Engine:
    def __init__(
        self, store: Store, sandbox: DockerSandbox | None = None, provider: Provider | None = None
    ):
        self.store = store
        self.sandbox = sandbox or DockerSandbox(store)
        self.provider_override = provider
        self.tracer_provider = setup(store.directory / "traces.jsonl")
        self.tracer = self.tracer_provider.get_tracer("agent_workbench", __version__)

    def dataset(self, config: RunConfig) -> Path:
        return (
            ROOT
            / "datasets"
            / ("injection.csv" if config.scenario == "injection" else "orders.csv")
        )

    def manifest(self, config: RunConfig) -> dict[str, Any]:
        return {
            "state_schema": STATE_SCHEMA,
            "engine_behavior": ENGINE_VERSION,
            "package_version": __version__,
            "source_fingerprint": source_fingerprint(),
            "sandbox_image_id": self.sandbox.image_identity(),
            "prompts": {
                name: file_hash(ROOT / "prompts" / name)
                for name in ("parent-v1.txt", "reviewer-v1.txt", "judge-v1.txt")
            },
            "dataset_hash": file_hash(self.dataset(config)),
            "recording_hash": file_hash(ROOT / "recordings" / f"{config.scenario}.json"),
            "config_hash": object_hash(config.model_dump()),
            "provider": config.mode,
            "model": config.model,
            "pricing": {
                "input": config.limits.input_price_per_million,
                "output": config.limits.output_price_per_million,
            },
        }

    def create(self, question: str, config: RunConfig | None = None) -> str:
        config = config or RunConfig()
        if not 1 <= len(question) <= 1000:
            raise BoundaryError("question must contain 1..1000 characters")
        if config.scenario not in SCENARIOS:
            raise BoundaryError("unknown scripted scenario")
        if config.mode != "scripted":
            if not config.allow_live or config.model == "author-written-v1":
                raise BoundaryError("live use requires explicit opt-in and exact model")
            if (
                config.limits.input_price_per_million is None
                or config.limits.output_price_per_million is None
            ):
                raise BoundaryError("live use requires configured frozen pricing")
        manifest = self.manifest(config)
        run_id = self.store.create_run(question, manifest, config.model_dump())
        self.store.event(
            run_id,
            "created",
            {"provenance": self.provenance(config), "manifest_hash": object_hash(manifest)},
        )
        return run_id

    @staticmethod
    def provenance(config: RunConfig) -> str:
        return (
            "AUTHOR-WRITTEN SCRIPTED RECORDINGS; synthetic tokens; $0 actual API spend"
            if config.mode == "scripted"
            else "LIVE provider; contract-tested/live-unverified adapter"
        )

    def provider(self, config: RunConfig) -> Provider:
        if self.provider_override is not None:
            return self.provider_override
        if config.mode == "scripted":
            return ScriptedProvider(ROOT / "recordings" / f"{config.scenario}.json")
        if not config.allow_live:
            raise BoundaryError("live provider not explicitly enabled")
        if config.mode == "openai":
            from .providers.openai import OpenAIProvider

            return OpenAIProvider(config.model)
        from .providers.anthropic import AnthropicProvider

        return AnthropicProvider(config.model)

    def resume(self, run_id: str, *, pause_after: int | None = None) -> dict[str, Any]:
        run = self.store.run(run_id)
        if run["parent_id"]:
            raise BoundaryError("reviewer children execute only under root owner")
        # Root ownership precedes manifest inspection, cleanup and all dispatch.
        with self.store.ownership(run_id):
            self.store.checkpoint("root_owned", {"root_id": run_id})
            failure_ids: dict[str, str] = {}
            try:
                config = RunConfig.model_validate(run["config"])
                current = self.manifest(config)
                if current != run["manifest"]:
                    changed = sorted(k for k in current if current[k] != run["manifest"].get(k))
                    raise IncompatibleResume(
                        "incompatible immutable manifest: "
                        + ", ".join(changed)
                        + "; start a new run; no migration override"
                    )
                self.sandbox.cleanup(run_id, remove_namespace=False)
                self.store.mark_ambiguous(run_id)
                if self.store.report(run_id):
                    self.sandbox.cleanup(run_id)
                    return self.snapshot(run_id)
                provider = self.provider(config)
                self.store.status(run_id, "running")
                with safe_span(
                    self.tracer,
                    "run",
                    attributes={
                        "run.id": run_id,
                        "provider.mode": config.mode,
                        "workflow.provenance": self.provenance(config),
                    },
                ) as span:
                    failure_ids = trace_ids(span)
                    self.store.event(run_id, "execution_started", failure_ids)
                    self._execute(run_id, config, provider, role="parent", pause_after=pause_after)
                if self.store.run(run_id)["status"] == "completed":
                    self.sandbox.cleanup(run_id)
            except Exception as exc:
                self._persist_failure(run_id, exc, failure_ids)
                raise
        return self.snapshot(run_id)

    def _persist_failure(self, run_id: str, exc: Exception, ids: dict[str, str]) -> None:
        category = type(exc).__name__
        message = str(exc)[:1000] if isinstance(exc, BoundaryError) else category
        self.store.status(run_id, "failed", message)
        self.store.event(
            run_id,
            "failed",
            {
                "category": category,
                "message": message,
                **ids,
                "action": "start a new run"
                if isinstance(exc, IncompatibleResume)
                else "inspect timeline",
            },
        )
        log_event("failed", run_id=run_id, category=category, **ids)

    @staticmethod
    def provider_input_estimate(
        config: RunConfig, system: str, messages: list[dict[str, Any]], role: str
    ) -> int:
        return estimate_tokens(
            request_payload(
                config.mode, config.model, system, messages, role, config.limits.output_token_cap
            )
        )

    @classmethod
    def admit_provider(
        cls, config: RunConfig, system: str, messages: list[dict[str, Any]], role: str
    ) -> int:
        estimate = cls.provider_input_estimate(config, system, messages, role)
        if estimate > config.limits.input_token_cap:
            raise BoundaryError("conservative input estimate exceeds configured input cap")
        if estimate + config.limits.output_token_cap > config.limits.context_tokens:
            raise BoundaryError("input estimate plus output allowance exceeds context_tokens")
        return estimate

    def _execute(
        self,
        run_id: str,
        config: RunConfig,
        provider: Provider,
        *,
        role: str,
        pause_after: int | None = None,
        evidence_id: str | None = None,
    ) -> None:
        run = self.store.run(run_id)
        root_id = run["root_id"]
        system = (
            ROOT / "prompts" / ("reviewer-v1.txt" if role == "reviewer" else "parent-v1.txt")
        ).read_text()
        initial: dict[str, Any] = {"question": run["question"], "trust": "user_request"}
        if evidence_id:
            initial["evidence_id"] = evidence_id
        messages: list[dict[str, Any]] = [{"role": "user", "content": initial}]
        compacted = False
        for turn_index in range(3 if role == "reviewer" else config.limits.max_steps):
            persisted = self.store.decision(run_id, turn_index)
            if persisted:
                turn = Turn.model_validate(persisted)
            else:
                ids = [e["id"] for e in self.store.all_evidence(root_id)]
                messages, did_compact = compact(
                    run["question"],
                    messages,
                    ids,
                    force=(config.scenario == "compaction" and bool(ids) and not compacted),
                    threshold=max(
                        100,
                        min(
                            config.limits.input_token_cap,
                            config.limits.context_tokens - config.limits.output_token_cap,
                        )
                        - estimate_tokens({"system": system, "tools": tools(role)}),
                    ),
                    root_id=root_id,
                    dataset_hash=run["manifest"]["dataset_hash"],
                )
                if did_compact:
                    compacted = True
                    self.store.event(
                        run_id,
                        "compaction",
                        {
                            "evidence_refs": ids,
                            "trust": "untrusted_tool_output",
                            "context": messages,
                        },
                    )
                self.admit_provider(config, system, messages, role)
                turn = self._provider_turn(
                    run_id, root_id, config, provider, system, messages, role, turn_index
                )
                self.store.save_decision(run_id, turn_index, turn.model_dump())
                self.store.checkpoint("decision_committed", {"run_id": run_id, "turn": turn_index})
                self.store.event(
                    run_id,
                    "decision",
                    {
                        "step": turn_index,
                        "decision": turn.decision.model_dump(),
                        "usage": turn.usage.model_dump() if turn.usage else None,
                    },
                )
            decision = turn.decision
            if decision.kind == "final":
                if role == "reviewer":
                    if not isinstance(decision.answer, Verdict):
                        raise BoundaryError("reviewer must return Verdict")
                    if evidence_id not in decision.answer.evidence_ids:
                        raise BoundaryError("reviewer omitted requested evidence")
                    self.store.save_verdict(root_id, run_id, decision.answer)
                    self.store.status(run_id, "completed")
                else:
                    if not isinstance(decision.answer, Report):
                        raise BoundaryError("parent must return Report")
                    self.store.publish(root_id, "final-answer", decision.answer)
                return
            result = self.store.begin_call(run_id, decision.id, decision.name, decision.arguments)
            if result is None:
                attempt = self.store.tool_step(root_id, run_id, decision.id)
                self.store.event(
                    run_id, "tool_dispatched", {"call_id": decision.id, "attempt": attempt}
                )
                with safe_span(
                    self.tracer,
                    "tool",
                    attributes={"run.id": run_id, "tool.name": decision.name, "step": turn_index},
                ) as span:
                    start = time.monotonic()
                    result = self._tool(run_id, config, provider, decision, role)
                    if decision.name != "publish":
                        self.store.finish_call(run_id, decision.id, result)
                    self.store.checkpoint(
                        "call_completed", {"run_id": run_id, "call_id": decision.id}
                    )
                    log_event(
                        "tool_result",
                        run_id=run_id,
                        tool=decision.name,
                        step=turn_index,
                        attempt=attempt,
                        duration_seconds=time.monotonic() - start,
                        **trace_ids(span),
                    )
                    self.store.event(
                        run_id,
                        "tool_result",
                        {
                            "call_id": decision.id,
                            "tool": decision.name,
                            "result": result,
                            "duration_seconds": time.monotonic() - start,
                            **trace_ids(span),
                        },
                    )
            messages.extend(
                [
                    {"role": "assistant", "call": decision.model_dump(), "content": ""},
                    {"role": "tool", "call_id": decision.id, "content": result},
                ]
            )
            if decision.name == "publish":
                return
            if pause_after is not None and turn_index + 1 >= pause_after:
                self.store.status(run_id, "interrupted")
                self.store.event(run_id, "interrupted", {"after_turn": turn_index + 1})
                return
        raise BoundaryError("bounded state machine exhausted max turns")

    def _provider_turn(
        self,
        run_id: str,
        root_id: str,
        config: RunConfig,
        provider: Provider,
        system: str,
        messages: list[dict[str, Any]],
        role: str,
        index: int,
    ) -> Turn:
        limits = config.limits
        for attempt in range(limits.provider_attempts):
            self.admit_provider(config, system, messages, role)
            reservation = self.store.reserve(root_id, run_id, limits, config.mode != "scripted")
            self.store.checkpoint(
                "provider_reserved", {"run_id": run_id, "reservation": reservation}
            )
            with safe_span(
                self.tracer,
                "provider",
                attributes={
                    "run.id": run_id,
                    "step": index,
                    "attempt": attempt + 1,
                    "provider.mode": config.mode,
                },
            ) as span:
                # Persist before external I/O; any unknown outcome keeps full reservation.
                self.store.dispatched(reservation)
                log_event(
                    "provider_dispatched",
                    run_id=run_id,
                    step=index,
                    attempt=attempt + 1,
                    reservation=reservation,
                    **trace_ids(span),
                )
                self.store.event(
                    run_id,
                    "provider_dispatched",
                    {
                        "step": index,
                        "attempt": attempt + 1,
                        "reservation": reservation,
                        **trace_ids(span),
                    },
                )
                self.store.checkpoint(
                    "provider_dispatched", {"run_id": run_id, "reservation": reservation}
                )
                started = time.monotonic()
                try:
                    turn = provider.turn(
                        system, messages, role=role, turn=index, output_cap=limits.output_token_cap
                    )
                except TransientProviderError:
                    self.store.settle(reservation, None, limits)
                    self.store.event(
                        run_id,
                        "provider_uncertain",
                        {
                            "step": index,
                            "attempt": attempt + 1,
                            "reservation": reservation,
                            "billing": "unknown outcome; conservative charge retained",
                        },
                    )
                    if attempt + 1 >= limits.provider_attempts:
                        raise
                    time.sleep(0.1 * 2**attempt)
                    continue
                except Exception:
                    self.store.settle(reservation, None, limits)
                    raise
                self.store.settle(reservation, turn.usage, limits)
                span.set_attribute("latency.seconds", time.monotonic() - started)
                span.set_attribute("usage.known", turn.usage is not None)
                if turn.usage:
                    span.set_attribute("usage.input_tokens", turn.usage.input_tokens)
                    span.set_attribute("usage.output_tokens", turn.usage.output_tokens)
                    span.set_attribute("usage.synthetic", turn.usage.synthetic)
                return turn
        raise AssertionError("unreachable")

    def _tool(
        self, run_id: str, config: RunConfig, provider: Provider, call: ToolCall, role: str
    ) -> dict[str, Any]:
        run = self.store.run(run_id)
        root_id = run["root_id"]
        if role == "reviewer" and call.name != "read_evidence":
            raise BoundaryError("reviewer capability violation: read_evidence only")
        try:
            if call.name == "inspect_dataset":
                EmptyArgs.model_validate(call.arguments)
                with self.dataset(config).open(newline="") as file:
                    reader = csv.DictReader(file)
                    rows = list(reader)
                return {
                    "columns": reader.fieldnames,
                    "row_count": len(rows),
                    "dataset_hash": run["manifest"]["dataset_hash"],
                    "trust": "untrusted_participant_content",
                    "sample": rows[:2],
                }
            if call.name == "analyze":
                args = AnalyzeArgs.model_validate(call.arguments)
                result = self.sandbox.execute(
                    root_id, run_id, call.id, args.code, self.dataset(config)
                )
                if result["ok"]:
                    eid = self.store.add_evidence(run_id, call.id, result["data"])
                    return {
                        "ok": True,
                        "evidence_id": eid,
                        "data": result["data"],
                        "dataset_hash": run["manifest"]["dataset_hash"],
                        "trust": "untrusted_tool_output",
                        "duration_seconds": result["duration_seconds"],
                    }
                return result
            if call.name == "read_evidence":
                args_read = ReadArgs.model_validate(call.arguments)
                ev = self.store.evidence(root_id, args_read.evidence_id)
                return {
                    "evidence_id": ev["id"],
                    "data": ev["body"],
                    "dataset_hash": ev["dataset_hash"],
                    "trust": "untrusted_tool_output",
                }
            if call.name == "review":
                args_review = ReviewArgs.model_validate(call.arguments)
                self.store.evidence(root_id, args_review.evidence_id)
                child_id = str(uuid.uuid5(uuid.UUID(root_id), call.id))
                if not any(
                    e["kind"] == "child_created" and e["payload"].get("child_id") == child_id
                    for e in self.store.events(root_id)
                ):
                    try:
                        self.store.run(child_id)
                    except BoundaryError:
                        self.store.create_run(
                            "Review requested immutable analysis evidence",
                            run["manifest"],
                            run["config"],
                            parent_id=root_id,
                            run_id=child_id,
                        )
                    self.store.event(
                        run_id,
                        "child_created",
                        {
                            "child_id": child_id,
                            "evidence_id": args_review.evidence_id,
                            "capabilities": ["read_evidence"],
                            "max_turns": 3,
                        },
                    )
                if self.store.verdict(root_id) is None:
                    with safe_span(
                        self.tracer,
                        "reviewer_child",
                        attributes={"run.id": child_id, "parent.id": root_id},
                    ):
                        # Child context is bounded separately; all reservations share root budget.
                        self._execute(
                            child_id,
                            config,
                            provider,
                            role="reviewer",
                            evidence_id=args_review.evidence_id,
                        )
                return {"child_id": child_id, "verdict": self.store.verdict(root_id)}
            if call.name == "publish":
                return self.store.publish(
                    root_id,
                    call.id,
                    Report.model_validate(call.arguments),
                    original_arguments=call.arguments,
                )
        except ValidationError as exc:
            raise BoundaryError("tool argument schema validation failed") from exc
        raise BoundaryError("unknown tool capability")

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.store.run(run_id)
        ledger = self.store.ledger(run_id)
        return {
            "run": run,
            "events": self.store.events(run_id),
            "evidence": self.store.all_evidence(run_id),
            "verdict": self.store.verdict(run_id),
            "report": self.store.report(run_id),
            "ledger": ledger,
            "metrics": {
                "steps": self.store.step_count(run_id),
                "charged_tokens": sum(r["charged_tokens"] for r in ledger),
                "charged_cost_usd": sum(r["charged_cost"] for r in ledger),
                "usage_unknown": any(r["actual_usage"] is None for r in ledger),
                "actual_api_spend_usd": 0 if run["config"]["mode"] == "scripted" else None,
            },
            "provenance": self.provenance(RunConfig.model_validate(run["config"])),
        }
