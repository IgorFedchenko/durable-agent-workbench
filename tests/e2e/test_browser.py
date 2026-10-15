"""Actual Playwright → HTTP → worker → Docker → SQLite → UI journeys."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright

from agent_workbench.engine import Engine
from agent_workbench.models import Limits, RunConfig
from agent_workbench.store import Store
from tests.helpers import Worker, wait_for

pytestmark = [pytest.mark.e2e, pytest.mark.docker]


@pytest.fixture
def server(tmp_path):
    directory = tmp_path / "browser-state"
    directory.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "TEST_WORKBENCH_DATA": str(directory), "TEST_WORKBENCH_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.browser_server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"

    def alive():
        if proc.poll() is not None:
            raise AssertionError(proc.communicate()[1].decode())
        try:
            with urlopen(url, timeout=0.5):
                return True
        except OSError:
            return False

    wait_for(alive, 15)
    yield url, Store(directory), proc
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)
    proc.stdout.close()
    proc.stderr.close()


@pytest.fixture
def page(server, tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1420, "height": 1000})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.goto(server[0])
        yield page
        page.screenshot(path=str(tmp_path / "actual-browser.png"), full_page=True)
        context.tracing.stop(path=str(tmp_path / "browser-trace.zip"))
        browser.close()


def start(page, scenario="total", pause=False):
    page.locator("#scenario").select_option(scenario)
    page.locator("#pause").set_checked(pause)
    page.locator("#start").click()
    expect(page.locator("#run-id")).to_contain_text("ROOT ", timeout=15000)
    return page.locator("#run-id").inner_text().removeprefix("ROOT ")


def complete(page):
    expect(page.locator("#run-status")).to_have_text("COMPLETED", timeout=60000)


def test_browser_real_example_report_chart(server, page):
    root = start(page, "regions")
    complete(page)
    store = server[1]
    snap = Engine(store).snapshot(root)
    expect(page.locator("#report .fact strong").first).to_have_text("175")
    assert (
        "75" in page.locator("#report").inner_text()
        and "100" in page.locator("#report").inner_text()
    )
    assert page.locator("#report").get_attribute("data-run-id") == root
    assert (
        "AUTHOR-WRITTEN SCRIPTED RECORDINGS" in page.content()
        and "$0 actual API spend" in page.content()
    )
    assert (
        snap["report"]
        and snap["verdict"]["accepted"]
        and any(e["kind"] == "child_created" for e in snap["events"])
    )
    launches = [e for e in snap["events"] if e["kind"] == "sandbox_created"]
    assert len(launches) == 1
    assert launches[0]["payload"]["host_config"]["NetworkMode"] == "none"
    for citation in page.locator("#report .citation").all_inner_texts():
        assert any(ev["id"] in citation for ev in snap["evidence"])


def test_browser_repair_child_compaction_timeline(server, page):
    root = start(page, "repair", True)
    expect(page.locator("#run-status")).to_have_text("INTERRUPTED", timeout=60000)
    assert "failed / retained" in page.locator("#timeline").inner_text()
    page.locator("#resume").click()
    complete(page)
    assert "Reviewer child linked" in page.locator("#timeline").inner_text()
    page.locator("#timeline details").first.click()
    assert page.locator("#timeline details[open] pre").inner_text()
    page.reload()
    page.locator("#run-list button").filter(has_text=root[:8]).click()
    complete(page)
    store = server[1]
    before = (store.step_count(root), len(store.events(root)))
    page.reload()
    page.locator("#run-list button").filter(has_text=root[:8]).click()
    complete(page)
    assert (store.step_count(root), len(store.events(root))) == before
    page.locator("#new-run").click()
    compacted = start(page, "compaction")
    complete(page)
    assert "Context compacted" in page.locator("#timeline").inner_text()
    assert any(
        e["kind"] == "tool_result" and e["payload"].get("call_id") == "retrieve-early"
        for e in store.events(compacted)
    )


def test_browser_worker_kill_restart_resume(server, page):
    # A separate real worker is killed at the committed analysis barrier; HTTP then resumes it.
    url, store, _ = server
    engine = Engine(store)
    root = engine.create("SIGKILL browser recovery", RunConfig(scenario="total"))
    worker = Worker(store.directory, root, "call_completed", call_id="analysis-1")
    try:
        message = worker.reached()
        assert message["pid"] != os.getpid()
        worker.kill()
        page.reload()
        page.locator("#run-list button").filter(has_text=root[:8]).click()
        expect(page.locator("#resume")).to_be_visible()
        page.locator("#resume").click()
        complete(page)
        assert store.report(root)["facts"][0]["value"] == 175
        assert sum(e["kind"] == "sandbox_created" for e in store.events(root)) == 1
        with store.connect() as db:
            assert (
                db.execute("SELECT count(*) FROM reports WHERE root_id=?", (root,)).fetchone()[0]
                == 1
            )
    finally:
        worker.close()


def test_browser_rejection_and_budget_failure(server, page):
    root = start(page, "reviewer-rejection")
    expect(page.locator("#run-status")).to_have_text("FAILED", timeout=60000)
    expect(page.locator("#report")).to_contain_text("Publication refused.")
    assert server[1].report(root) is None and page.locator("#timeline details").count() > 0
    store = server[1]
    engine = Engine(store)
    budget = engine.create(
        "Exhausted shared root budget", RunConfig(scenario="total", limits=Limits(max_steps=1))
    )
    page.reload()
    page.locator("#run-list button").filter(has_text=budget[:8]).click()
    page.locator("#resume").click()
    expect(page.locator("#run-status")).to_have_text("FAILED", timeout=30000)
    assert store.report(budget) is None and "ceiling" in page.locator("#notice").inner_text()


def test_browser_run_selection_attribution(server, page):
    root = start(page, "total")
    complete(page)
    store = server[1]
    page.locator("#new-run").click()
    rejected = start(page, "reviewer-rejection")
    expect(page.locator("#run-status")).to_have_text("FAILED", timeout=60000)
    assert (
        page.locator("#report .fact").count() == 0
        and page.locator("#report").get_attribute("data-run-id") == rejected
    )
    page.locator("#run-list button").filter(has_text=root[:8]).click()
    complete(page)
    # Delay the actual HTTP read on the actual server, with no intercepted response.
    control = store.directory / "delay-read"
    control.write_text("/api/runs/" + root)
    wait_for(lambda: (store.directory / "read-blocked").exists(), 10)
    page.locator("#run-list button").filter(has_text=rejected[:8]).click()
    expect(page.locator("#run-id")).to_have_text("ROOT " + rejected)
    assert page.locator("#report .fact").count() == 0
    control.unlink()
    expect(page.locator("#run-status")).to_have_text("FAILED")
    page.wait_for_timeout(1100)
    assert (
        page.locator("#report").get_attribute("data-run-id") == rejected
        and page.locator("#report .fact").count() == 0
    )
    pending = Engine(store).create("Pending selected root", RunConfig(scenario="total"))
    page.reload()
    page.locator("#run-list button").filter(has_text=root[:8]).click()
    complete(page)
    page.locator("#run-list button").filter(has_text=pending[:8]).click()
    expect(page.locator("#run-status")).to_have_text("PENDING")
    assert page.locator("#report .fact").count() == 0 and page.locator("#notice").is_hidden()


def test_web_loopback_host_origin_csrf(server, page):
    url, store, _ = server
    host = url.removeprefix("http://")
    token = page.locator("meta[name=csrf-token]").get_attribute("content")
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in page.context.cookies())
    # urllib is used here so the paid HTTP transport ban remains enforced for SDKs.
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    def post(path, headers, body):
        request = Request(
            url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie, **headers},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as reply:
                return reply.status, json.load(reply)
        except HTTPError as exc:
            return exc.code, None

    valid = {"Host": host, "Origin": url, "X-CSRF-Token": token}
    with urlopen(url + "/api/csrf", timeout=10) as fresh_session:
        other_session_token = json.load(fresh_session)["token"]
    assert other_session_token != token
    with socket.socket() as wrong_interface:
        wrong_interface.settimeout(1)
        assert wrong_interface.connect_ex(("127.0.0.2", int(host.rsplit(":", 1)[1]))) != 0
    root = Engine(store).create("CSRF resume target", RunConfig(scenario="total"))
    before = (len(store.runs()), store.step_count(root))
    headers = [
        {**valid, "Host": "attacker.invalid"},
        {**valid, "Host": host + ".attacker.invalid"},
        {**valid, "Origin": "null"},
        {**valid, "Origin": "https://" + host},
        {**valid, "Origin": "http://127.0.0.1:1"},
        {**valid, "Origin": "http://evil.invalid"},
        {**valid, "Origin": ""},
        {**valid, "X-CSRF-Token": ""},
        {**valid, "X-CSRF-Token": "wrong"},
        {**valid, "X-CSRF-Token": other_session_token},
    ]
    for path, body in [
        ("/api/runs", {"question": "q", "scenario": "total", "pause": False}),
        ("/api/runs/" + root + "/resume", {}),
    ]:
        for header in headers:
            assert 400 <= post(path, header, body)[0] < 500
    assert (len(store.runs()), store.step_count(root)) == before
    assert post("/api/runs/" + root + "/resume", valid, {})[0] == 200
    wait_for(lambda: store.report(root), 40)


def test_web_read_only_and_method_boundaries(server, page):
    root = start(page, "total")
    complete(page)
    store = server[1]
    before = (store.events(root), store.ledger(root))
    for _ in range(2):
        assert page.request.get(server[0] + "/api/runs/" + root).ok
        assert page.request.get(server[0] + "/api/runs").ok
    assert (store.events(root), store.ledger(root)) == before
    assert page.request.get(server[0] + "/api/runs/" + root + "/resume").status == 405
    token = page.locator("meta[name=csrf-token]").get_attribute("content")
    for extra in [
        {"provider": "openai"},
        {"image": "evil"},
        {"path": "/etc/passwd"},
        {"shell": "touch exploit"},
        {"question": "x" * 1001},
    ]:
        reply = page.request.post(
            server[0] + "/api/runs",
            data={"question": "q", **extra},
            headers={"Origin": server[0], "X-CSRF-Token": token},
        )
        assert reply.status == 422


def test_browser_xss_csp_and_secret_absence(server, page):
    payload = (
        '<script>window.__probe=1</script><svg onload="window.__probe=2"></svg>'
        '<img src=x onerror="window.__probe=3">javascript:alert(1)'
    )
    page.locator("#question").fill(payload)
    root = start(page, "total")
    complete(page)
    assert page.evaluate("window.__probe") is None

    assert (
        page.locator("#run-title").inner_text() == payload
        and page.locator("#run-title script,#run-title svg,#run-title img").count() == 0
    )
    reply = page.request.get(server[0])
    assert (
        "script-src 'self'" in reply.headers["content-security-policy"]
        and "object-src 'none'" in reply.headers["content-security-policy"]
    )
    assert "synthetic-not-a-real-key" not in page.content()
    assert server[1].report(root)
    # Authored hostile tool/report strings traverse real Docker, SQLite and HTTP rendering.
    from agent_workbench.providers.scripted import ScriptedProvider

    base = ScriptedProvider(Path(__file__).resolve().parents[2] / "recordings/total.json")

    class HostileStrings:
        def turn(self, system, messages, **kwargs):
            turn = base.turn(system, messages, **kwargs)
            decision = turn.decision
            if decision.kind == "tool" and decision.name == "analyze":
                code = (
                    "import json;print(json.dumps("
                    + repr(
                        {
                            "total": 175,
                            "orders": 6,
                            "note": payload,
                        }
                    )
                    + "))"
                )
                decision = decision.model_copy(update={"arguments": {"code": code}})
            if decision.kind == "tool" and decision.name == "publish":
                decision = decision.model_copy(
                    update={
                        "arguments": {
                            **decision.arguments,
                            "title": payload,
                            "explanation": payload,
                        }
                    }
                )
            return turn.model_copy(update={"decision": decision})

    engine = Engine(server[1], provider=HostileStrings())
    hostile = engine.create("Hostile authored output", RunConfig(scenario="total"))
    engine.resume(hostile)
    page.reload()
    page.locator("#run-list button").filter(has_text=hostile[:8]).click()
    complete(page)
    assert payload in page.locator("#report").inner_text()
    assert page.locator("#report script,#report svg,#report img").count() == 0
    assert page.evaluate("window.__probe") is None


def test_browser_preflight_resume_diagnostic(server, page):
    store = server[1]
    engine = Engine(store)
    config = RunConfig(scenario="total")
    manifest = {**engine.manifest(config), "sandbox_image_id": "sha256:" + "0" * 64}
    root = store.create_run("Incompatible image resume", manifest, config.model_dump())
    page.reload()
    page.locator("#run-list button").filter(has_text=root[:8]).click()
    page.locator("#resume").click()
    expect(page.locator("#run-status")).to_have_text("FAILED", timeout=30000)
    expect(page.locator("#notice")).to_contain_text("sandbox_image_id")
    expect(page.locator("#notice")).to_contain_text("start a new run")
    assert not store.ledger(root) and not store.containers(root) and store.report(root) is None
