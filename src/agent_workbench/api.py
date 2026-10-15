"""Loopback-only debugger; each execution owns a separate worker process."""

from __future__ import annotations

import contextlib
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import Field

from .engine import SCENARIOS, Engine
from .models import RunConfig, StrictModel
from .store import BoundaryError, BusyRun, Store
from .util import ROOT

ASSETS = Path(__file__).parent


class StartRequest(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    scenario: str = Field(default="repair", pattern=r"^[a-z-]{1,40}$")
    pause: bool = True


class ResumeRequest(StrictModel):
    pass


def create_app(directory: Path, port: int = 8765) -> FastAPI:
    store = Store(directory)
    engine = Engine(store)
    sessions: dict[str, str] = {}
    workers: dict[str, subprocess.Popen[bytes]] = {}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    templates = Environment(
        loader=FileSystemLoader(ASSETS / "templates"), autoescape=select_autoescape(["html"])
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        for run_id, proc in workers.items():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            with contextlib.suppress(BusyRun, BoundaryError):
                with store.ownership(run_id):
                    engine.sandbox.cleanup(run_id)
        engine.tracer_provider.shutdown()

    app = FastAPI(
        title="Durable Agent Workbench",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.workers = workers

    @app.middleware("http")
    async def boundary(request: Request, call_next: Any) -> Response:
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "unexpected loopback Host"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != f"http://{host}":
                return JSONResponse({"detail": "exact same-origin required"}, status_code=403)
            session = request.cookies.get("workbench_session", "")
            expected = sessions.get(session)
            supplied = request.headers.get("x-csrf-token", "")
            if expected is None or not secrets.compare_digest(expected, supplied):
                return JSONResponse(
                    {"detail": "valid session CSRF token required"}, status_code=403
                )
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_session(request: Request, response: Response) -> str:
        session = request.cookies.get("workbench_session", "")
        if session not in sessions:
            if len(sessions) > 1000:
                raise HTTPException(503, "local session capacity exceeded; restart server")
            session = secrets.token_urlsafe(32)
            sessions[session] = secrets.token_urlsafe(32)
            response.set_cookie("workbench_session", session, httponly=True, samesite="strict")
        return sessions[session]

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        response = HTMLResponse("")
        token = get_session(request, response)
        response.body = templates.get_template("index.html").render(csrf_token=token).encode()
        response.headers["content-length"] = str(len(response.body))
        return response

    @app.get("/api/csrf")
    def csrf(request: Request, response: Response) -> dict[str, str]:
        return {"token": get_session(request, response)}

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return store.runs()

    @app.get("/api/runs/{run_id}")
    def replay(run_id: str) -> dict[str, Any]:
        try:
            snapshot = engine.snapshot(run_id)
        except BoundaryError as exc:
            raise HTTPException(404, "unknown run") from exc
        if snapshot["run"]["parent_id"]:
            raise HTTPException(404, "open reviewer through parent timeline")
        proc = workers.get(run_id)
        alive = proc is not None and proc.poll() is None
        snapshot["worker_alive"] = alive
        snapshot["recoverable"] = not alive and snapshot["run"]["status"] in {
            "running",
            "pending",
            "interrupted",
            "failed",
        }
        return snapshot

    def dispatch(run_id: str, pause: bool = False) -> None:
        previous = workers.get(run_id)
        if previous is not None and previous.poll() is None:
            raise HTTPException(409, "worker already running")
        command = [
            sys.executable,
            "-m",
            "agent_workbench.cli",
            "--data",
            str(store.directory),
            "resume",
            run_id,
        ]
        if pause:
            command.extend(["--pause-after", "3"])
        workers[run_id] = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

    @app.post("/api/runs", status_code=201)
    def start(body: StartRequest) -> dict[str, str]:
        if body.scenario not in SCENARIOS:
            raise HTTPException(422, "unknown scenario")
        try:
            run_id = engine.create(body.question, RunConfig(scenario=body.scenario))
            dispatch(run_id, body.pause)
        except BoundaryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/resume")
    def resume(run_id: str, body: ResumeRequest) -> dict[str, str]:
        del body
        try:
            run = store.run(run_id)
            if run["parent_id"]:
                raise HTTPException(403, "child cannot dispatch independently")
            if run["config"]["mode"] != "scripted":
                raise HTTPException(403, "web cannot execute live providers")
            dispatch(run_id)
        except BoundaryError as exc:
            raise HTTPException(404, "unknown run") from exc
        return {"run_id": run_id}

    @app.get("/api/benchmark")
    def benchmark() -> dict[str, Any]:
        path = ROOT / "evals" / "benchmark.json"
        if not path.exists():
            return {"measured": False, "detail": "Run make eval; no benchmark is fabricated."}
        import json

        result: dict[str, Any] = json.loads(path.read_text())
        return result

    app.mount("/static", StaticFiles(directory=ASSETS / "static"), name="static")
    return app
