"""SQLite journal, root ownership, immutable evidence and conservative budget ledger."""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from .models import Limits, Report, Usage, Verdict
from .util import canonical, object_hash


class BoundaryError(RuntimeError):
    """An application invariant rejected an operation."""


class BusyRun(BoundaryError):
    pass


class Store:
    def __init__(
        self, directory: Path, checkpoint: Callable[[str, dict[str, Any]], None] | None = None
    ):
        self.checkpoint = checkpoint or (lambda name, payload: None)
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "journal.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY, root_id TEXT NOT NULL, parent_id TEXT, question TEXT NOT NULL,
              manifest TEXT NOT NULL, config TEXT NOT NULL, status TEXT NOT NULL,
              created REAL NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(
              run_id TEXT NOT NULL, turn INTEGER NOT NULL, body TEXT NOT NULL,
              PRIMARY KEY(run_id,turn));
            CREATE TABLE IF NOT EXISTS calls(
              run_id TEXT NOT NULL, call_id TEXT NOT NULL, args_hash TEXT NOT NULL,
              name TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
              PRIMARY KEY(run_id,call_id));
            CREATE TABLE IF NOT EXISTS evidence(
              id TEXT PRIMARY KEY, root_id TEXT NOT NULL, run_id TEXT NOT NULL,
              dataset_hash TEXT NOT NULL, call_id TEXT NOT NULL, body TEXT NOT NULL,
              body_hash TEXT NOT NULL, UNIQUE(run_id,call_id));
            CREATE TABLE IF NOT EXISTS verdicts(
              root_id TEXT PRIMARY KEY, child_id TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reports(
              root_id TEXT PRIMARY KEY, body TEXT NOT NULL, body_hash TEXT NOT NULL,
              call_id TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger(
              id TEXT PRIMARY KEY, root_id TEXT NOT NULL, run_id TEXT NOT NULL,
              state TEXT NOT NULL, reserved_tokens INTEGER NOT NULL, reserved_cost REAL NOT NULL,
              charged_tokens INTEGER NOT NULL, charged_cost REAL NOT NULL,
              actual_usage TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS activities(
              id TEXT PRIMARY KEY, root_id TEXT NOT NULL, run_id TEXT NOT NULL,
              kind TEXT NOT NULL, call_id TEXT);
            CREATE TABLE IF NOT EXISTS containers(
              name TEXT PRIMARY KEY, root_id TEXT NOT NULL, run_id TEXT NOT NULL,
              call_id TEXT NOT NULL, state TEXT NOT NULL, staging TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS evidence_immutable_update BEFORE UPDATE ON evidence
              BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
            CREATE TRIGGER IF NOT EXISTS evidence_immutable_delete BEFORE DELETE ON evidence
              BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
            CREATE TRIGGER IF NOT EXISTS manifest_immutable BEFORE UPDATE OF manifest,config ON runs
              BEGIN SELECT RAISE(ABORT,'immutable manifest'); END;
            CREATE TRIGGER IF NOT EXISTS decisions_immutable BEFORE UPDATE ON decisions
              BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
            CREATE TRIGGER IF NOT EXISTS events_immutable BEFORE UPDATE ON events
              BEGIN SELECT RAISE(ABORT,'immutable event'); END;
            CREATE TRIGGER IF NOT EXISTS reports_immutable BEFORE UPDATE ON reports
              BEGIN SELECT RAISE(ABORT,'immutable report'); END;
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @contextlib.contextmanager
    def ownership(self, root_id: str) -> Iterator[None]:
        # Never unlink this file: every contender must lock the same inode.
        uuid.UUID(root_id)
        path = self.directory / f"{root_id}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BusyRun("run has a live owner; stop that worker before resuming") from exc
            yield
        finally:
            os.close(fd)

    def create_run(
        self,
        question: str,
        manifest: dict[str, Any],
        config: dict[str, Any],
        parent_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        run_id = run_id or str(uuid.uuid4())
        root_id = self.run(parent_id)["root_id"] if parent_id else run_id
        with self.transaction() as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    root_id,
                    parent_id,
                    question,
                    canonical(manifest),
                    canonical(config),
                    "pending",
                    time.time(),
                    None,
                ),
            )
        return run_id

    def run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise BoundaryError("unknown run")
        result = dict(row)
        for key in ("manifest", "config"):
            result[key] = json.loads(result[key])
        return result

    def runs(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,question,status,created,error FROM runs WHERE parent_id IS NULL "
                    "ORDER BY created DESC LIMIT 100"
                )
            ]

    def status(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?,error=? WHERE id=?", (status, error, run_id))

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                (run_id, kind, canonical(payload), time.time()),
            )

    def events(self, root_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT e.* FROM events e JOIN runs r ON r.id=e.run_id "
                "WHERE r.root_id=? ORDER BY seq",
                (root_id,),
            )
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def decision(self, run_id: str, turn: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT body FROM decisions WHERE run_id=? AND turn=?", (run_id, turn)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_decision(self, run_id: str, turn: int, body: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO decisions VALUES(?,?,?)", (run_id, turn, canonical(body)))

    def begin_call(
        self, run_id: str, call_id: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        fingerprint = object_hash(arguments)
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM calls WHERE run_id=? AND call_id=?", (run_id, call_id)
            ).fetchone()
            if old:
                if old["args_hash"] != fingerprint or old["name"] != name:
                    raise BoundaryError("call ID reused with changed arguments or tool")
                return json.loads(old["result"]) if old["result"] else None
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?)",
                (run_id, call_id, fingerprint, name, "pending", None),
            )
        return None

    def finish_call(self, run_id: str, call_id: str, result: dict[str, Any]) -> None:
        encoded = canonical(result)
        with self.transaction() as db:
            call = db.execute(
                "SELECT state,result FROM calls WHERE run_id=? AND call_id=?", (run_id, call_id)
            ).fetchone()
            if call is None:
                raise BoundaryError("cannot complete an unknown call")
            if call["state"] == "completed" and call["result"] != encoded:
                raise BoundaryError("completed tool result is immutable")
            db.execute(
                "UPDATE calls SET state='completed',result=? WHERE run_id=? AND call_id=?",
                (encoded, run_id, call_id),
            )

    def add_evidence(self, run_id: str, call_id: str, body: dict[str, Any]) -> str:
        run = self.run(run_id)
        evidence_id = f"ev-{object_hash([run_id, call_id])[:24]}"
        # Ensure bounded structured JSON, reject NaN even in nested outputs.
        encoded = canonical(body)
        if len(encoded.encode()) > 64000:
            raise BoundaryError("evidence exceeds size bound")
        with self.transaction() as db:
            existing = db.execute(
                "SELECT body_hash FROM evidence WHERE id=?", (evidence_id,)
            ).fetchone()
            if existing and existing[0] != object_hash(body):
                raise BoundaryError("replayed analysis changed immutable evidence")
            db.execute(
                "INSERT OR IGNORE INTO evidence(id,root_id,run_id,dataset_hash,"
                "call_id,body,body_hash) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    run["root_id"],
                    run_id,
                    run["manifest"]["dataset_hash"],
                    call_id,
                    encoded,
                    object_hash(body),
                ),
            )
        return evidence_id

    def evidence(self, root_id: str, evidence_id: str) -> dict[str, Any]:
        root = self.run(root_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM evidence WHERE id=? AND root_id=?", (evidence_id, root_id)
            ).fetchone()
        if row is None or row["dataset_hash"] != root["manifest"]["dataset_hash"]:
            raise BoundaryError("foreign, nonexistent or mismatched dataset evidence")
        body = json.loads(row["body"])
        if object_hash(body) != row["body_hash"]:
            raise BoundaryError("evidence integrity failure")
        return {**dict(row), "body": body}

    def all_evidence(self, root_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM evidence WHERE root_id=?", (root_id,))]
        return [self.evidence(root_id, eid) for eid in ids]

    def save_verdict(self, root_id: str, child_id: str, verdict: Verdict) -> None:
        child = self.run(child_id)
        if child["root_id"] != root_id or child["parent_id"] != root_id:
            raise BoundaryError("verdict must come from the root's reviewer child")
        for eid in verdict.evidence_ids:
            self.evidence(root_id, eid)
        with self.transaction() as db:
            existing = db.execute(
                "SELECT body FROM verdicts WHERE root_id=?", (root_id,)
            ).fetchone()
            if existing and existing[0] != canonical(verdict.model_dump()):
                raise BoundaryError("immutable reviewer verdict already persisted")
            db.execute(
                "INSERT OR IGNORE INTO verdicts VALUES(?,?,?)",
                (root_id, child_id, canonical(verdict.model_dump())),
            )

    def verdict(self, root_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM verdicts WHERE root_id=?", (root_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def publish(
        self,
        root_id: str,
        call_id: str,
        report: Report,
        *,
        original_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.run(root_id)["parent_id"] is not None:
            raise BoundaryError("child cannot publish")
        verdict = self.verdict(root_id)
        if not verdict or not verdict["accepted"]:
            raise BoundaryError("publication requires a persisted accepting reviewer verdict")
        for point in report.chart:
            if point.label != point.citation.field.rsplit(".", 1)[-1]:
                raise BoundaryError("chart label differs from cited series")
        for fact in report.facts + report.chart:
            ref = fact.citation
            if ref.evidence_id not in verdict["evidence_ids"]:
                raise BoundaryError("citation not accepted by reviewer")
            value: Any = self.evidence(root_id, ref.evidence_id)["body"]
            for component in ref.field.split("."):
                if not isinstance(value, dict) or component not in value:
                    raise BoundaryError("numeric citation field missing")
                value = value[component]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or abs(value - fact.value) > 1e-6
            ):
                raise BoundaryError("report number differs from cited numeric evidence")
        body = report.model_dump()
        result = {"published": True, "report_key": root_id}
        args_hash = object_hash(original_arguments if original_arguments is not None else body)
        self.checkpoint("publication_before_transaction", {"root_id": root_id})
        with self.transaction() as db:
            call = db.execute(
                "SELECT * FROM calls WHERE run_id=? AND call_id=?", (root_id, call_id)
            ).fetchone()
            if call is not None:
                if call["name"] != "publish" or call["args_hash"] != args_hash:
                    raise BoundaryError(
                        "publication call ID collides with different tool or arguments"
                    )
                if call["state"] == "completed" and call["result"] != canonical(result):
                    raise BoundaryError("completed publication result is immutable")
            old = db.execute("SELECT body_hash FROM reports WHERE root_id=?", (root_id,)).fetchone()
            if old and old[0] != object_hash(body):
                raise BoundaryError("stable publication key already has a different report")
            db.execute(
                "INSERT OR IGNORE INTO reports VALUES(?,?,?,?,?)",
                (root_id, canonical(body), object_hash(body), call_id, time.time()),
            )
            self.checkpoint("publication_uncommitted", {"root_id": root_id})
            db.execute(
                "INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?)",
                (root_id, call_id, args_hash, "publish", "pending", None),
            )
            db.execute(
                "UPDATE calls SET state='completed',result=? WHERE run_id=? AND call_id=?",
                (canonical(result), root_id, call_id),
            )
            db.execute("UPDATE runs SET status='completed',error=NULL WHERE id=?", (root_id,))
        self.checkpoint("publication_committed", {"root_id": root_id})
        return result

    def report(self, root_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM reports WHERE root_id=?", (root_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def reserve(self, root_id: str, run_id: str, limits: Limits, live: bool) -> str:
        frozen = self.run(root_id)
        if self.run(run_id)["root_id"] != root_id:
            raise BoundaryError("budget reservation has foreign root")
        if Limits.model_validate(frozen["config"]["limits"]) != limits:
            raise BoundaryError("budget limits must equal immutable root configuration")
        if live != (frozen["config"]["mode"] != "scripted"):
            raise BoundaryError("budget provider mode differs from immutable root")
        if live and (
            limits.input_price_per_million is None or limits.output_price_per_million is None
        ):
            raise BoundaryError("live dispatch requires explicit frozen input/output prices")
        tokens = limits.input_token_cap + limits.output_token_cap
        cost_nano = (
            self.cost_nano(limits.input_token_cap, limits.output_token_cap, limits) if live else 0
        )
        cost = cost_nano / 1e9
        reservation = str(uuid.uuid4())
        with self.transaction() as db:
            if db.execute(
                "SELECT 1 FROM ledger WHERE root_id=? AND state='overrun'", (root_id,)
            ).fetchone():
                raise BoundaryError(
                    "prior provider usage exceeded reservation; further spend blocked"
                )
            total = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(charged_tokens),0),"
                "COALESCE(SUM(CAST(ROUND(charged_cost * 1000000000) AS INTEGER)),0) "
                "FROM ledger WHERE root_id=?",
                (root_id,),
            ).fetchone()
            assert total is not None
            steps = db.execute(
                "SELECT COUNT(*) FROM activities WHERE root_id=?", (root_id,)
            ).fetchone()[0]
            if (
                steps >= limits.max_steps
                or total[1] + tokens > limits.max_tokens
                or total[2] + cost_nano > int(Decimal(str(limits.max_cost_usd)) * 1000000000)
            ):
                raise BoundaryError("root step/token/cost reservation ceiling exceeded")
            db.execute(
                "INSERT INTO activities VALUES(?,?,?,?,?)",
                (reservation, root_id, run_id, "provider", None),
            )
            db.execute(
                "INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    reservation,
                    root_id,
                    run_id,
                    "reserved",
                    tokens,
                    cost,
                    tokens,
                    cost,
                    None,
                    time.time(),
                ),
            )
        return reservation

    def dispatched(self, reservation: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE ledger SET state='dispatched' WHERE id=? AND state='reserved'",
                (reservation,),
            )

    @staticmethod
    def cost_nano(input_tokens: int, output_tokens: int, limits: Limits) -> int:
        value = (
            Decimal(input_tokens) * Decimal(str(limits.input_price_per_million or 0))
            + Decimal(output_tokens) * Decimal(str(limits.output_price_per_million or 0))
        ) * 1000
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    def settle(self, reservation: str, usage: Usage | None, limits: Limits) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM ledger WHERE id=?", (reservation,)).fetchone()
            if row is None:
                raise BoundaryError("unknown budget reservation")
            if row["state"] in ("settled", "overrun"):
                if usage is None or row["actual_usage"] != canonical(usage.model_dump()):
                    raise BoundaryError("settled usage is immutable")
                return
            if usage is None:
                db.execute("UPDATE ledger SET state='ambiguous' WHERE id=?", (reservation,))
                return
            config = self.run(row["root_id"])["config"]
            if usage.synthetic != (config["mode"] == "scripted"):
                raise BoundaryError("usage provenance differs from configured provider mode")
            overrun = (
                usage.input_tokens > limits.input_token_cap
                or usage.output_tokens > limits.output_token_cap
            )
            cost = (
                0.0
                if usage.synthetic
                else self.cost_nano(usage.input_tokens, usage.output_tokens, limits) / 1e9
            )
            db.execute(
                "UPDATE ledger SET state=?,charged_tokens=?,charged_cost=?,actual_usage=? "
                "WHERE id=?",
                (
                    "overrun" if overrun else "settled",
                    usage.input_tokens + usage.output_tokens,
                    cost,
                    canonical(usage.model_dump()),
                    reservation,
                ),
            )
        if overrun:
            raise BoundaryError(
                "provider usage exceeded configured cap; actual usage retained; spend blocked"
            )

    def mark_ambiguous(self, root_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE ledger SET state='ambiguous' WHERE root_id=? "
                "AND state IN ('reserved','dispatched')",
                (root_id,),
            )

    def ledger(self, root_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM ledger WHERE root_id=? ORDER BY created", (root_id,)
                )
            ]

    def container_intent(
        self, root_id: str, run_id: str, call_id: str, name: str, staging: str
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO containers VALUES(?,?,?,?,?,?)",
                (name, root_id, run_id, call_id, "intent", staging),
            )

    def containers(self, root_id: str) -> list[str]:
        with self.connect() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT name FROM containers WHERE root_id=? AND state!='removed'", (root_id,)
                )
            ]

    def container_removed(self, name: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE containers SET state='removed' WHERE name=?", (name,))

    def staging(self, name: str) -> Path:
        with self.connect() as db:
            row = db.execute("SELECT staging FROM containers WHERE name=?", (name,)).fetchone()
        if row is None:
            raise BoundaryError("unknown container staging")
        return Path(row[0])

    def tool_step(self, root_id: str, run_id: str, call_id: str) -> int:
        limits = Limits.model_validate(self.run(root_id)["config"]["limits"])
        with self.transaction() as db:
            steps = db.execute(
                "SELECT COUNT(*) FROM activities WHERE root_id=?", (root_id,)
            ).fetchone()[0]
            attempts = db.execute(
                "SELECT COUNT(*) FROM activities WHERE run_id=? AND kind='tool' AND call_id=?",
                (run_id, call_id),
            ).fetchone()[0]
            if steps >= limits.max_steps or attempts >= 3:
                raise BoundaryError("root step or pure-tool retry ceiling exceeded")
            db.execute(
                "INSERT INTO activities VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), root_id, run_id, "tool", call_id),
            )
        return int(attempts + 1)

    def step_count(self, root_id: str) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM activities WHERE root_id=?", (root_id,)
                ).fetchone()[0]
            )
