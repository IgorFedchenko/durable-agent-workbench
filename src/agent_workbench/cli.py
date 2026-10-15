"""Local CLI. Live providers require explicit flags, frozen prices and environment keys."""

from __future__ import annotations

import argparse
import json
import logging
import signal
from pathlib import Path
from typing import Any

from .engine import SCENARIOS, Engine
from .models import Limits, RunConfig
from .store import BoundaryError, Store


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="workbench")
    result.add_argument("--data", type=Path, default=Path(".workbench"))
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="create and execute an offline authored scenario")
    run.add_argument("--scenario", choices=SCENARIOS, default="repair")
    run.add_argument("--question", default="What is our total revenue? Cite the evidence.")
    run.add_argument("--pause-after", type=int)
    resume = sub.add_parser("resume", help="resume under the original immutable manifest")
    resume.add_argument("run_id")
    resume.add_argument("--pause-after", type=int)
    replay = sub.add_parser("replay", help="read-only persisted timeline; never dispatches")
    replay.add_argument("run_id")
    serve = sub.add_parser("serve", help="loopback UI; execution runs in separate workers")
    serve.add_argument("--port", type=int, default=8765)
    evaluate = sub.add_parser("eval", help="execute all ten real Docker golden cases")
    evaluate.add_argument("--output", type=Path, default=Path("evals/benchmark.json"))
    judge = sub.add_parser("judge", help="strict rubric pipeline, offline by default")
    judge.add_argument("run_id")
    _live_options(judge, optional=True)
    live = sub.add_parser("live-smoke", help="manual paid opt-in; contract-tested/live-unverified")
    _live_options(live)
    return result


def _live_options(command: argparse.ArgumentParser, optional: bool = False) -> None:
    command.add_argument("--allow-live", action="store_true")
    command.add_argument("--provider", choices=["openai", "anthropic"], required=not optional)
    command.add_argument("--model")
    command.add_argument("--input-price", type=float)
    command.add_argument("--output-price", type=float)
    command.add_argument("--max-cost", type=float, default=0.25)
    command.add_argument("--max-output", type=int, default=1500)


def live_config(args: Any) -> RunConfig:
    import os

    if not args.allow_live or not args.provider or not args.model:
        raise BoundaryError("live mode requires --allow-live, --provider and --model")
    if args.input_price is None or args.output_price is None:
        raise BoundaryError("live mode requires explicit configured input/output prices")
    key = "OPENAI_API_KEY" if args.provider == "openai" else "ANTHROPIC_API_KEY"
    if not os.environ.get(key):
        raise BoundaryError(
            f"live mode requires environment variable {key}; keys never go in files"
        )
    return RunConfig(
        mode=args.provider,
        model=args.model,
        allow_live=True,
        scenario="total",
        limits=Limits(
            input_price_per_million=args.input_price,
            output_price_per_million=args.output_price,
            max_cost_usd=args.max_cost,
            output_token_cap=args.max_output,
        ),
    )


def main() -> None:
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(
                create_app(args.data, args.port),
                host="127.0.0.1",
                port=args.port,
                log_level="warning",
                access_log=False,
            )
            return
        store = Store(args.data)
        engine = Engine(store)
        if args.command == "run":
            run_id = engine.create(args.question, RunConfig(scenario=args.scenario))
            print(json.dumps({"run_id": run_id}), flush=True)
            print(json.dumps(engine.resume(run_id, pause_after=args.pause_after)["metrics"]))
        elif args.command == "resume":
            # SIGTERM unwinds sandbox supervision; SIGKILL is recovered by the next owner.
            def terminate(signum: int, frame: Any) -> None:
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, terminate)
            print(json.dumps(engine.resume(args.run_id, pause_after=args.pause_after)["metrics"]))
        elif args.command == "replay":
            print(json.dumps(engine.snapshot(args.run_id), indent=2))
        elif args.command == "eval":
            from .evals import run_benchmark

            result = run_benchmark(args.data, args.output)
            print(json.dumps(result["aggregate"], indent=2))
            if not result["aggregate"]["passed"]:
                raise SystemExit(1)
        elif args.command == "judge":
            from .judge import judge_report, live_judge

            result = (
                live_judge(engine, args.run_id, live_config(args))
                if args.allow_live or args.provider
                else judge_report(engine, args.run_id)
            )
            print(json.dumps(result, indent=2))
        elif args.command == "live-smoke":
            config = live_config(args)
            run_id = engine.create(
                "Calculate total revenue; review and cite immutable evidence.", config
            )
            print(json.dumps({"run_id": run_id}), flush=True)
            print(json.dumps(engine.resume(run_id)["metrics"]))
    except BoundaryError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
