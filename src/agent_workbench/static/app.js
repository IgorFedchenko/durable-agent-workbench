"use strict";

const byId = (id) => document.getElementById(id);
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
let activeRun = null;
let selectionVersion = 0;
let pendingRead = null;
let lastSnapshot = "";

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showError(error) {
  byId("notice").textContent = error.message || String(error);
  byId("notice").hidden = false;
}

async function request(path, body, signal) {
  const options = { signal };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    throw new Error(detail);
  }
  return data;
}

async function listRuns() {
  const runs = await request("/api/runs");
  byId("run-list").replaceChildren();
  for (const run of runs) {
    const button = element("button", run.question);
    button.append(element("small", `${run.status} · ${run.id.slice(0, 8)}`));
    if (run.id === activeRun) button.classList.add("active");
    button.onclick = () => selectRun(run.id).catch(showError);
    byId("run-list").append(button);
  }
}

function cancelPendingRead() {
  if (pendingRead) pendingRead.controller.abort();
  pendingRead = null;
}

function clearRunView() {
  byId("notice").hidden = true;
  byId("notice").textContent = "";
  byId("run-title").textContent = "Loading selected run…";
  byId("run-id").textContent = activeRun ? `ROOT ${activeRun}` : "";
  byId("run-status").textContent = "LOADING";
  byId("resume").hidden = true;
  byId("timeline").replaceChildren();
  byId("manifest").textContent = "";
  for (const id of ["metric-steps", "metric-tokens", "metric-cost", "metric-latency"]) {
    byId(id).textContent = "—";
  }
  renderReport(null);
}

async function selectRun(id) {
  cancelPendingRead();
  activeRun = id;
  selectionVersion += 1;
  lastSnapshot = "";
  clearRunView();
  byId("workspace").hidden = false;
  byId("composer").hidden = true;
  byId("how").hidden = true;
  await refresh();
  await listRuns();
}

function renderReport(report, run = null, verdict = null) {
  const target = byId("report");
  target.replaceChildren();
  target.dataset.runId = run ? run.id : activeRun || "";
  if (!report) {
    const empty = element("div", undefined, "empty-state");
    const rejected = verdict && !verdict.accepted;
    empty.append(
      element("span", "⌁"),
      element("h3", rejected ? "Publication refused." : "Evidence comes first."),
      element("p", rejected
        ? "The reviewer rejected this run. No report was published."
        : "This selected run has no published report. Analysis, reviewer acceptance and citation validation must finish first."),
    );
    target.append(empty);
    return;
  }
  const body = element("div", undefined, "report-body");
  body.append(element("h3", report.title), element("p", report.explanation));
  if (report.no_data) body.append(element("p", "No data · no matching orders", "pill"));
  const facts = element("div", undefined, "facts");
  for (const fact of report.facts) {
    const block = element("div", undefined, "fact");
    block.append(
      element("label", fact.label),
      element("strong", fact.value.toLocaleString()),
      element("span", `${fact.citation.evidence_id} · ${fact.citation.field}`, "citation"),
    );
    facts.append(block);
  }
  body.append(facts);
  const maximum = Math.max(...report.chart.map((point) => Math.abs(point.value)), 1);
  for (const point of report.chart) {
    const row = element("div", undefined, "chart-row");
    const label = element("div", undefined, "chart-label");
    label.append(element("span", point.label), element("strong", String(point.value)));
    const bar = element("div", undefined, "chart-bar");
    const fill = element("div");
    fill.style.width = `${Math.max(0, Math.min(100, Math.abs(point.value) / maximum * 100))}%`;
    bar.append(fill);
    row.append(label, bar,
      element("span", `${point.citation.evidence_id} · ${point.citation.field}`, "citation"));
    body.append(row);
  }
  target.append(body);
}

function renderTimeline(events) {
  const labels = {
    created: "Run created",
    execution_started: "Worker acquired execution",
    decision: "Provider decision",
    tool_result: "Tool completed",
    sandbox_created: "Docker sandbox created",
    child_created: "Reviewer child linked",
    compaction: "Context compacted · evidence retained",
    interrupted: "Paused · ready to resume",
    failed: "Execution failed",
    provider_uncertain: "Provider outcome unknown",
  };
  const target = byId("timeline");
  const opened = new Set([...target.querySelectorAll("details[open]")].map((node) => node.dataset.seq));
  target.replaceChildren();
  for (const event of events) {
    if (event.kind === "provider_dispatched") continue;
    const payload = event.payload;
    const failure = event.kind === "failed" || payload.result?.ok === false;
    const entry = element("details", undefined, `event${failure ? " failure" : ""}`);
    entry.dataset.seq = String(event.seq);
    entry.open = opened.has(String(event.seq));
    const summary = element("summary");
    let title = labels[event.kind] || event.kind;
    if (event.kind === "decision") {
      const name = payload.decision.kind === "tool" ? payload.decision.name : "Structured verdict";
      title = `${name} · turn ${payload.step + 1}`;
    } else if (event.kind === "tool_result") {
      title = `${payload.tool} · ${payload.result?.ok === false ? "failed / retained" : "completed"}`;
    }
    const timing = payload.duration_seconds === undefined
      ? new Date(event.created * 1000).toLocaleTimeString()
      : `${payload.duration_seconds.toFixed(2)}s`;
    summary.append(element("span", title), element("small", timing));
    entry.append(
      summary,
      element("p", `${event.run_id === activeRun ? "parent" : "reviewer child"} · event ${event.seq}`),
      element("pre", JSON.stringify(payload, null, 2)),
    );
    target.append(entry);
  }
}

function renderSnapshot(data) {
  byId("run-title").textContent = data.run.question;
  byId("run-id").textContent = `ROOT ${data.run.id}`;
  byId("run-status").textContent = data.run.status.toUpperCase();
  byId("resume").hidden = !data.recoverable;
  byId("metric-steps").textContent = data.metrics.steps;
  byId("metric-tokens").textContent = data.metrics.charged_tokens.toLocaleString();
  byId("usage-note").textContent = data.metrics.usage_unknown
    ? "unknown usage · full reservation retained"
    : "authored usage, shared ceiling";
  byId("metric-cost").textContent = data.metrics.actual_api_spend_usd === 0 ? "$0" : "Unknown";
  const latency = data.events
    .filter((event) => event.kind === "tool_result" && event.payload.tool === "analyze")
    .reduce((total, event) => total + event.payload.duration_seconds, 0);
  byId("metric-latency").textContent = `${latency.toFixed(2)}s`;
  byId("manifest").textContent = JSON.stringify(data.run.manifest, null, 2);
  byId("notice").textContent = data.run.error || "";
  byId("notice").hidden = !data.run.error;
  renderTimeline(data.events);
  renderReport(data.report, data.run, data.verdict);
}

async function refresh() {
  if (!activeRun) return;
  const requestedId = activeRun;
  const requestedVersion = selectionVersion;
  if (pendingRead && pendingRead.version === requestedVersion) return;
  cancelPendingRead();
  const controller = new AbortController();
  pendingRead = { controller, version: requestedVersion };
  try {
    const data = await request(`/api/runs/${requestedId}`, undefined, controller.signal);
    // A previous root's response must never be attributed to the new selection.
    if (activeRun !== requestedId || selectionVersion !== requestedVersion) return;
    if (data.run.id !== requestedId) throw new Error("Run response identity mismatch");
    const encoded = JSON.stringify(data);
    if (encoded === lastSnapshot) return;
    lastSnapshot = encoded;
    renderSnapshot(data);
  } catch (error) {
    if (error.name !== "AbortError" && activeRun === requestedId
        && selectionVersion === requestedVersion) showError(error);
  } finally {
    if (pendingRead?.controller === controller) pendingRead = null;
  }
}

byId("start").onclick = async () => {
  byId("start").disabled = true;
  byId("notice").hidden = true;
  try {
    const data = await request("/api/runs", {
      question: byId("question").value,
      scenario: byId("scenario").value,
      pause: byId("pause").checked,
    });
    await selectRun(data.run_id);
  } catch (error) {
    showError(error);
  } finally {
    byId("start").disabled = false;
  }
};

byId("resume").onclick = async () => {
  const requestedId = activeRun;
  byId("resume").disabled = true;
  byId("notice").hidden = true;
  try {
    await request(`/api/runs/${requestedId}/resume`, {});
    if (activeRun === requestedId) {
      lastSnapshot = "";
      await refresh();
    }
  } catch (error) {
    if (activeRun === requestedId) showError(error);
  } finally {
    byId("resume").disabled = false;
  }
};

byId("new-run").onclick = () => {
  cancelPendingRead();
  activeRun = null;
  selectionVersion += 1;
  lastSnapshot = "";
  clearRunView();
  byId("workspace").hidden = true;
  byId("composer").hidden = false;
  byId("how").hidden = false;
  listRuns().catch(showError);
};

byId("benchmark-link").onclick = async () => {
  try {
    byId("benchmark-content").textContent = JSON.stringify(await request("/api/benchmark"), null, 2);
    byId("benchmark-dialog").showModal();
  } catch (error) {
    showError(error);
  }
};
byId("close-dialog").onclick = () => byId("benchmark-dialog").close();
listRuns().catch(showError);
setInterval(refresh, 800);
