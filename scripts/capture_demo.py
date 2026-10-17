"""Capture actual local UI frames after the real browser/server/Docker journey.

Use your optional local command prefix. Playwright Chromium must already be installed.
This script never fabricates UI or changes engine responses.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def save_snapshots(url: str, evidence: Path) -> None:
    """Read-only API evidence; diagnostic errors never replace a journey failure."""
    snapshots: dict[str, object] = {}
    try:
        with urlopen(url + "/api/runs", timeout=5) as response:
            runs = json.load(response)
        snapshots["runs"] = runs
        for run in runs:
            if run.get("parent_id"):
                continue
            with urlopen(url + "/api/runs/" + run["id"], timeout=5) as response:
                snapshots[run["id"]] = json.load(response)
    except Exception as exc:
        snapshots["diagnostic_error"] = f"{type(exc).__name__}: {exc}"
    (evidence / "run-snapshots.json").write_text(json.dumps(snapshots, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="fresh retained local state directory")
    parser.add_argument("--evidence-dir", type=Path, help="fresh retained diagnostic directory")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    workspace = ROOT / ".workbench" / ("capture-" + uuid.uuid4().hex)
    directory = (args.data_dir or workspace / "data").resolve()
    evidence = (args.evidence_dir or workspace / "evidence").resolve()
    directory.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    # Fail before launching if another process owns this loopback endpoint.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    url = f"http://127.0.0.1:{args.port}"
    frames: list[Image.Image] = []
    print(f"Retained capture state: {directory}; evidence: {evidence}", flush=True)
    with (evidence / "server.log").open("wb") as server_log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_workbench.cli",
                "--data",
                str(directory),
                "serve",
                "--port",
                str(args.port),
            ],
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        identity = {
            "server_pid": server.pid,
            "data_dir": str(directory),
            "evidence_dir": str(evidence),
            "url": url,
        }
        (evidence / "server-identity.json").write_text(json.dumps(identity, indent=2))
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("owned capture server exited; inspect server.log")
                try:
                    with urlopen(url, timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("owned capture server was not ready")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(
                    viewport={"width": 1420, "height": 1000}, device_scale_factor=1
                )
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                page = context.new_page()
                try:
                    page.goto(url)

                    def capture() -> None:
                        frames.append(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))

                    capture()
                    # Match the explicit journey used by the real browser suite.
                    page.wait_for_function("document.getElementById('start').onclick !== null")
                    page.locator("#scenario").select_option("repair")
                    page.locator("#pause").set_checked(True)
                    page.locator("#start").click()
                    expect(page.locator("#run-id")).to_contain_text("ROOT ", timeout=15000)
                    expect(page.locator("#run-status")).to_have_text("INTERRUPTED", timeout=60000)
                    failed_attempt = page.locator("#timeline details.failure").first
                    failed_attempt.locator("summary").click()
                    expect(failed_attempt).to_have_attribute("open", "")
                    # Timeline polling replaces detail nodes. Resolve the current
                    # locator and scroll synchronously, without an element-handle
                    # stability wait that can outlive the rendered node.
                    failed_attempt.evaluate(
                        "node => node.scrollIntoView({block: 'center', behavior: 'instant'})"
                    )
                    capture()
                    page.locator("#resume").click()
                    expect(page.locator("#run-status")).to_have_text("COMPLETED", timeout=60000)
                    reviewer = (
                        page.locator("#timeline details")
                        .filter(has_text="Reviewer child linked")
                        .first
                    )
                    reviewer.locator("summary").click()
                    expect(reviewer).to_have_attribute("open", "")
                    reviewer.evaluate(
                        "node => node.scrollIntoView({block: 'center', behavior: 'instant'})"
                    )
                    capture()
                    page.locator("#workspace").scroll_into_view_if_needed()
                    capture()
                    page.locator("#new-run").click()
                    page.locator("#scenario").select_option("regions")
                    page.locator("#pause").uncheck()
                    page.locator("#start").click()
                    expect(page.locator("#run-status")).to_have_text("COMPLETED", timeout=60000)
                    page.locator("#workspace").scroll_into_view_if_needed()
                    capture()
                    # Cover framing is selected only after the real report/chart exists.
                    page.locator("#report .chart-row").first.wait_for()
                    run_id = page.locator("#report").get_attribute("data-run-id")
                    if not run_id:
                        raise RuntimeError("completed report has no run identity")
                    # A 4:3 desktop viewport keeps both columns; Chromium renders the
                    # final pixels directly at 1000x750. No image resizing is used.
                    cover_context = browser.new_context(
                        viewport={"width": 1420, "height": 1065},
                        device_scale_factor=1000 / 1420,
                    )
                    cover_page = cover_context.new_page()
                    cover_page.goto(url)
                    cover_page.locator("#run-list button").filter(has_text=run_id[:8]).click()
                    cover_page.locator("#report .chart-row").first.wait_for()
                    expect(cover_page.locator("#run-status")).to_have_text("COMPLETED")
                    # Expand the persisted reviewer linkage through its actual UI
                    # summary; the adjacent verified report/chart stays unchanged.
                    cover_reviewer = (
                        cover_page.locator("#timeline details")
                        .filter(has_text="Reviewer child linked")
                        .first
                    )
                    cover_reviewer.locator("summary").click()
                    cover_page.evaluate("document.fonts.ready")
                    cover_page.locator("#workspace").evaluate(
                        "node => node.scrollIntoView({block: 'start'})"
                    )
                    cover_output = evidence / "portfolio-cover.png"
                    cover_page.screenshot(path=str(cover_output))
                    with Image.open(cover_output) as cover_image:
                        print(f"Actual browser cover: {cover_output} {cover_image.size}")
                    cover_context.close()
                except Exception as failure:
                    try:
                        page.screenshot(path=str(evidence / "failure-browser.png"), full_page=True)
                    except Exception as diagnostic_failure:
                        failure.add_note(f"failure screenshot unavailable: {diagnostic_failure}")
                    raise
                finally:
                    primary_failure = sys.exception()
                    try:
                        save_snapshots(url, evidence)
                        context.tracing.stop(path=str(evidence / "browser-trace.zip"))
                    except Exception as diagnostic_failure:
                        if primary_failure is None:
                            raise
                        primary_failure.add_note(
                            f"capture diagnostics incomplete: {diagnostic_failure}"
                        )
                    finally:
                        browser.close()
            output = evidence / "demo.gif"
            frames[0].save(
                output,
                save_all=True,
                append_images=frames[1:],
                duration=[2200, 2500, 3000, 3000, 3500],
                loop=0,
                optimize=True,
            )
            # Publish only after the complete real journey and diagnostics succeed.
            for filename in ("demo.gif", "portfolio-cover.png"):
                (ROOT / "docs" / filename).write_bytes((evidence / filename).read_bytes())
            print(f"Actual browser capture: {ROOT / 'docs/demo.gif'}")
        finally:
            identity["status_before_shutdown"] = server.poll()
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            identity["status_after_shutdown"] = server.returncode
            (evidence / "server-identity.json").write_text(json.dumps(identity, indent=2))


if __name__ == "__main__":
    main()
