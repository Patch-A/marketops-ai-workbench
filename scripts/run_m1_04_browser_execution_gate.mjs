#!/usr/bin/env node

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, rm } from "node:fs/promises";
import { extname, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const TIMEOUT_MS = 20_000;
const IDS = Object.freeze({
  project: "10000000-0000-4000-8000-000000000001",
  run: "10000000-0000-4000-8000-000000000002",
  plan: "10000000-0000-4000-8000-000000000003",
  proposal: "10000000-0000-4000-8000-000000000004",
  candidate: "10000000-0000-4000-8000-000000000005",
  snapshot: "10000000-0000-4000-8000-000000000006",
  approval: "10000000-0000-4000-8000-000000000007",
  artifact: "10000000-0000-4000-8000-000000000008",
  source: "10000000-0000-4000-8000-000000000009",
});
const TASK_ID = `candidate:${IDS.candidate}`;
const SHA = "a".repeat(64);
const CREATED_AT = "2026-08-15T12:00:00Z";
const RESULTS = new Set([
  "executionRead",
  "executionConflictReconciled",
  "executionUpdate",
  "csvExport",
  "xlsxExport",
  "requestBoundary",
  "responsive",
  "themes",
  "noConsoleFailures",
  "noExternalRequests",
]);

function parseArgs(argv) {
  const values = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i]?.startsWith("--") || argv[i + 1] === undefined)
      throw new Error("invalid gate arguments");
    values[argv[i].slice(2)] = argv[i + 1];
  }
  if (!values.browser || !values.profile)
    throw new Error("missing --browser or --profile");
  return values;
}
function delay(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}
async function stopBrowser(browser, profile) {
  if (process.platform === "win32") {
    await new Promise((resolvePromise) => {
      const powershell = resolve(
        process.env.SystemRoot || "C:\\Windows",
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
      );
      const script =
        '$target = $env:MARKETOPS_GATE_PROFILE; Get-CimInstance Win32_Process -Filter "Name = \'chrome.exe\'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($target) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }';
      const killer = spawn(
        powershell,
        ["-NoProfile", "-NonInteractive", "-Command", script],
        {
          env: { ...process.env, MARKETOPS_GATE_PROFILE: profile },
          stdio: "ignore",
        },
      );
      killer.once("exit", (code) => resolvePromise(code === 0));
      killer.once("error", () => resolvePromise(false));
    });
  } else {
    if (browser.exitCode !== null) return;
    const exited = new Promise((resolvePromise) =>
      browser.once("exit", resolvePromise),
    );
    browser.kill();
    await Promise.race([exited, delay(5_000)]);
  }
}
async function waitUntil(check, label) {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await delay(50);
  }
  throw new Error(
    `${label} timed out${lastError ? `: ${lastError.message}` : ""}`,
  );
}
function json(response, status, value, headers = {}) {
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": "application/json",
    ...headers,
  });
  response.end(JSON.stringify(value));
}
function errorEnvelope(response, status, code) {
  json(response, status, {
    code,
    message: "Synthetic execution browser gate failure.",
    retryable: false,
    requestId: "execution-gate-request",
  });
}
async function readBody(request) {
  let raw = "";
  for await (const chunk of request) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}
function citation() {
  return {
    sourceVersionId: IDS.proposal,
    sourceSha256: SHA,
    location: { kind: "line_range", startLine: 1, endLine: 1 },
    sectionPath: ["Approved proposal"],
    quote: "Publish the launch brief.",
  };
}
function projectDetail() {
  return {
    projectId: IDS.project,
    name: "Synthetic Execution Browser Gate",
    status: "active",
    createdAt: CREATED_AT,
    sourceFile: {
      artifactId: IDS.artifact,
      versionId: IDS.source,
      filename: "source.md",
      mediaType: "text/markdown",
      sizeBytes: 64,
    },
    approvedProposal: {
      artifactId: IDS.artifact,
      versionId: IDS.proposal,
      filename: "approved.md",
      mediaType: "text/markdown",
      sizeBytes: 128,
      sha256: SHA,
      proposalVersion: 1,
      approvalStatus: "approved",
      approvedAt: CREATED_AT,
    },
  };
}
function reviewDetail() {
  return {
    run: {
      runId: IDS.run,
      proposalVersionId: IDS.proposal,
      proposalSha256: SHA,
      candidateCount: 1,
      latestReviewVersion: 2,
      createdAt: CREATED_AT,
    },
    selectedReviewVersion: 2,
    availableReviewVersions: [1, 2],
    selectedDecision: {
      decisionId: IDS.approval,
      reviewVersion: 2,
      candidateId: IDS.candidate,
      action: "approve",
      reason: "Approved",
      comment: null,
      replacementText: null,
      actorId: "40000000-0000-4000-8000-000000000001",
      createdAt: CREATED_AT,
    },
    candidates: [
      {
        ordinal: 1,
        candidateId: IDS.candidate,
        kind: "deliverable",
        text: "Publish the launch brief.",
        classification: "fact",
        confidence: 0.95,
        sourceCitation: citation(),
        review: {
          status: "approve",
          replacementText: null,
          lastDecision: {
            decisionId: IDS.approval,
            reviewVersion: 2,
            candidateId: IDS.candidate,
            action: "approve",
            reason: "Approved",
            comment: null,
            replacementText: null,
            actorId: "40000000-0000-4000-8000-000000000001",
            createdAt: CREATED_AT,
          },
        },
      },
    ],
  };
}
function plan() {
  return {
    planId: IDS.plan,
    projectId: IDS.project,
    proposalVersionId: IDS.proposal,
    proposalSha256: SHA,
    sourceReviewRunId: IDS.run,
    sourceReviewSnapshotId: IDS.snapshot,
    sourceReviewVersion: 2,
    selectedPlanVersion: 1,
    availablePlanVersions: [1],
    status: "draft",
    planDigest: SHA,
    createdAt: CREATED_AT,
    tasks: [
      {
        taskId: TASK_ID,
        candidateId: IDS.candidate,
        kind: "deliverable",
        classification: "fact",
        sourceText: "Publish the launch brief.",
        title: "Publish the launch brief.",
        sourceCitation: citation(),
        reviewStatus: "approve",
        durationWorkdays: 1,
        predecessors: [],
        ownerRole: "Campaign owner",
        plannedStart: null,
        plannedFinish: null,
        hardDeadline: null,
        approvedBufferWorkdays: 0,
        isLocked: false,
        status: "not_started",
      },
    ],
    controls: [],
  };
}
function statePayload(state) {
  return {
    projectId: IDS.project,
    planId: IDS.plan,
    planVersion: 1,
    editable: true,
    tasks: [
      {
        taskId: TASK_ID,
        title: "Publish the launch brief.",
        ownerRole: "Campaign owner",
        plannedStart: "2026-08-17",
        plannedFinish: "2026-08-17",
        status: state.status,
        blockerReason: state.blockerReason,
        actualStart: state.actualStart,
        actualFinish: state.actualFinish,
        note: state.note,
        sequenceNo: state.sequenceNo,
        updatedAt: CREATED_AT,
      },
    ],
  };
}
async function handleApi(request, response, state, url) {
  const method = request.method || "GET";
  state.requests.push({ method, path: url.pathname });
  if (method === "GET" && url.pathname === "/v1/projects")
    return json(response, 200, {
      projects: [
        {
          projectId: IDS.project,
          name: "Synthetic Execution Browser Gate",
          status: "active",
          approvedProposalVersion: 1,
          createdAt: CREATED_AT,
        },
      ],
    });
  if (method === "GET" && url.pathname === `/v1/projects/${IDS.project}`)
    return json(response, 200, projectDetail());
  if (
    method === "GET" &&
    url.pathname === `/v1/projects/${IDS.project}/extraction-runs`
  )
    return json(response, 200, {
      runs: [
        {
          runId: IDS.run,
          proposalVersionId: IDS.proposal,
          proposalSha256: SHA,
          candidateCount: 1,
          latestReviewVersion: 2,
          createdAt: CREATED_AT,
        },
      ],
    });
  if (
    method === "GET" &&
    url.pathname === `/v1/projects/${IDS.project}/extraction-runs/${IDS.run}`
  )
    return json(response, 200, reviewDetail());
  if (
    method === "POST" &&
    url.pathname === `/v1/projects/${IDS.project}/wbs-plans`
  )
    return json(response, 201, { plan: plan(), replayed: false });
  if (
    method === "GET" &&
    url.pathname === `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}`
  )
    return json(response, 200, plan());
  if (
    method === "POST" &&
    url.pathname ===
      `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/schedule-snapshots`
  )
    return json(response, 201, {
      snapshot: {
        snapshotId: IDS.snapshot,
        planId: IDS.plan,
        planVersion: 1,
        status: "ready",
        projectStart: "2026-08-17",
        holidays: [],
        planDigest: SHA,
        scheduleDigest: SHA,
        createdAt: CREATED_AT,
        topologicalOrder: [TASK_ID],
        tasks: [
          {
            taskId: TASK_ID,
            plannedStart: "2026-08-17",
            plannedFinish: "2026-08-17",
          },
        ],
        conflicts: [],
        deadlineMisses: [],
        sourceDateDrift: [],
      },
      replayed: false,
    });
  if (
    method === "GET" &&
    url.pathname ===
      `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/approvals`
  )
    return json(response, 200, {
      approval: state.approved
        ? {
            approvalId: IDS.approval,
            planId: IDS.plan,
            planVersion: 1,
            scheduleSnapshotId: IDS.snapshot,
            planDigest: SHA,
            scheduleDigest: SHA,
            reason: "Ready for execution",
            approvedAt: CREATED_AT,
          }
        : null,
    });
  if (
    method === "POST" &&
    url.pathname ===
      `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/approvals`
  ) {
    state.approved = true;
    return json(response, 201, {
      approval: {
        approvalId: IDS.approval,
        planId: IDS.plan,
        planVersion: 1,
        scheduleSnapshotId: IDS.snapshot,
        planDigest: SHA,
        scheduleDigest: SHA,
        reason: "Ready for execution",
        approvedAt: CREATED_AT,
      },
      replayed: false,
    });
  }
  if (
    method === "GET" &&
    url.pathname ===
      `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/execution`
  )
    return json(response, 200, statePayload(state));
  if (
    method === "POST" &&
    url.pathname ===
      `/v1/projects/${IDS.project}/wbs-plans/${IDS.plan}/execution-updates`
  ) {
    const body = await readBody(request);
    state.bodies.push(body);
    if (state.conflictOnce) {
      state.conflictOnce = false;
      return errorEnvelope(response, 409, "EXECUTION_CONFLICT");
    }
    state.sequenceNo += 1;
    state.status = body.status;
    state.blockerReason = body.blockerReason || null;
    state.actualStart = body.actualStart || null;
    state.actualFinish = body.actualFinish || null;
    state.note = body.note || null;
    return json(response, 201, {
      update: {
        taskId: TASK_ID,
        sequenceNo: state.sequenceNo,
        status: state.status,
        blockerReason: state.blockerReason,
        actualStart: state.actualStart,
        actualFinish: state.actualFinish,
        note: state.note,
        updatedAt: CREATED_AT,
      },
      replayed: false,
    });
  }
  if (method === "GET" && url.pathname.endsWith("/exports/execution.csv")) {
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition":
        'attachment; filename="marketops-execution-v1.csv"',
    });
    return response.end("taskId,title\n");
  }
  if (method === "GET" && url.pathname.endsWith("/exports/execution.xlsx")) {
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "Content-Disposition":
        'attachment; filename="marketops-execution-v1.xlsx"',
    });
    return response.end(Buffer.from("PK\x03\x04"));
  }
  return errorEnvelope(response, 404, "NOT_FOUND");
}
function contentType(pathname) {
  return (
    {
      ".html": "text/html; charset=utf-8",
      ".js": "text/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8",
    }[extname(pathname)] || "application/octet-stream"
  );
}
async function startServer(state) {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", "http://127.0.0.1");
      if (url.pathname.startsWith("/v1/"))
        return await handleApi(request, response, state, url);
      const relative =
        url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      if (
        ![
          "index.html",
          "app.js",
          "project-import.js",
          "review-workbench.js",
          "schedule-workbench.js",
          "execution-workbench.js",
          "styles.css",
        ].includes(relative)
      ) {
        response.writeHead(404);
        return response.end();
      }
      response.writeHead(200, {
        "Content-Type": contentType(relative),
        "Cache-Control": "no-store",
      });
      response.end(await readFile(resolve(ROOT, relative)));
    } catch {
      if (!response.headersSent)
        errorEnvelope(response, 500, "GATE_SERVER_ERROR");
      else response.destroy();
    }
  });
  await new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolvePromise);
  });
  return { server, baseUrl: `http://127.0.0.1:${server.address().port}` };
}
class CdpClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.waiters = [];
    this.listeners = [];
  }
  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolvePromise, reject) => {
      const timeout = setTimeout(
        () => reject(new Error("CDP connection timed out")),
        TIMEOUT_MS,
      );
      this.socket.addEventListener(
        "open",
        () => {
          clearTimeout(timeout);
          resolvePromise();
        },
        { once: true },
      );
      this.socket.addEventListener(
        "error",
        () => {
          clearTimeout(timeout);
          reject(new Error("CDP connection failed"));
        },
        { once: true },
      );
    });
    this.socket.addEventListener("message", (event) =>
      this.receive(event.data),
    );
  }
  receive(raw) {
    const message = JSON.parse(raw);
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error)
        pending.reject(
          new Error(message.error.message || "CDP command failed"),
        );
      else pending.resolve(message.result || {});
      return;
    }
    for (const listener of this.listeners)
      listener(message.method, message.params || {});
    for (const waiter of [...this.waiters])
      if (
        waiter.method === message.method &&
        waiter.predicate(message.params || {})
      ) {
        clearTimeout(waiter.timeout);
        this.waiters = this.waiters.filter((item) => item !== waiter);
        waiter.resolve(message.params || {});
      }
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolvePromise, reject) => {
      this.pending.set(id, { resolve: resolvePromise, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  on(listener) {
    this.listeners.push(listener);
  }
  waitFor(method, predicate = () => true) {
    return new Promise((resolvePromise, reject) => {
      const waiter = { method, predicate, resolve: resolvePromise, reject };
      waiter.timeout = setTimeout(() => {
        this.waiters = this.waiters.filter((item) => item !== waiter);
        reject(new Error(`${method} timed out`));
      }, TIMEOUT_MS);
      this.waiters.push(waiter);
    });
  }
  close() {
    this.socket?.close();
  }
}
async function evaluate(client, expression, awaitPromise = false) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true,
    userGesture: true,
  });
  if (result.exceptionDetails)
    throw new Error(
      `browser evaluation failed: ${result.exceptionDetails.exception?.description || "exception"}`,
    );
  return result.result?.value;
}
async function navigate(client, url) {
  const loaded = client.waitFor("Page.loadEventFired");
  const result = await client.send("Page.navigate", { url });
  if (result.errorText)
    throw new Error(`navigation failed: ${result.errorText}`);
  await loaded;
}
async function run(args) {
  const state = {
    approved: false,
    status: "not_started",
    blockerReason: null,
    actualStart: null,
    actualFinish: null,
    note: null,
    sequenceNo: 0,
    conflictOnce: true,
    bodies: [],
    requests: [],
  };
  const profile = resolve(args.profile);
  await rm(profile, { recursive: true, force: true });
  await mkdir(profile, { recursive: true });
  const { server, baseUrl } = await startServer(state);
  const browser = spawn(
    resolve(args.browser),
    [
      "--headless=new",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-sync",
      "--disable-extensions",
      "--remote-debugging-port=0",
      `--user-data-dir=${profile}`,
      "about:blank",
    ],
    { stdio: ["ignore", "ignore", "ignore"] },
  );
  let client;
  try {
    const port = await waitUntil(
      async () =>
        (await readFile(resolve(profile, "DevToolsActivePort"), "utf8"))
          .trim()
          .split(/\r?\n/)[0],
      "Chrome DevTools port",
    );
    const target = await waitUntil(async () => {
      const response = await fetch(
        `http://127.0.0.1:${port}/json/new?about:blank`,
        { method: "PUT" },
      );
      return response.ok ? response.json() : null;
    }, "Chrome target");
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();
    const failures = [];
    const external = [];
    client.on((method, params) => {
      if (method === "Runtime.exceptionThrown") failures.push("runtime");
      if (
        method === "Runtime.consoleAPICalled" &&
        ["error", "warning"].includes(params.type)
      )
        failures.push(`console-${params.type}`);
      if (method === "Network.requestWillBeSent") {
        const requestUrl = new URL(params.request.url);
        if (
          /^https?:$/.test(requestUrl.protocol) &&
          requestUrl.origin !== baseUrl
        )
          external.push(requestUrl.origin);
      }
    });
    await Promise.all([
      client.send("Page.enable"),
      client.send("Runtime.enable"),
      client.send("Network.enable"),
    ]);
    await navigate(client, `${baseUrl}/?projectId=${IDS.project}`);
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#scheduleWorkbench')?.dataset.state === 'empty'",
        ),
      "project restored",
    );
    await evaluate(
      client,
      "document.querySelector('#scheduleNav').click(); true",
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#createWbsPlan')?.disabled === false",
        ),
      "WBS enabled",
    );
    await evaluate(
      client,
      "document.querySelector('#createWbsPlan').click(); true",
    );
    await waitUntil(
      () => evaluate(client, "document.querySelector('[data-task-id]')"),
      "WBS created",
    );
    await evaluate(
      client,
      "document.querySelector('#scheduleProjectStart').value='2026-08-17'; document.querySelector('#recalculateSchedule').click(); true",
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#planApprovalState')?.textContent === 'READY'",
        ),
      "schedule ready",
    );
    await evaluate(
      client,
      "document.querySelector('#planApprovalReason').value='Ready for execution'; document.querySelector('#planApprovalReason').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#approveWbsPlan').click(); true",
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#planApprovalState')?.textContent === 'APPROVED'",
        ),
      "plan approved",
    );
    await evaluate(
      client,
      "document.querySelector('#syncExecution').click(); true",
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#executionTaskList [data-execution-task]')",
        ),
      "execution state loaded",
    );
    const executionRead = await evaluate(
      client,
      "document.querySelector('#executionPlanMeta')?.textContent.includes('批准 WBS v1') && document.querySelector('#executionProgress')?.textContent === '0 / 1'",
    );
    const rowSelector = `document.querySelector('[data-execution-task=${JSON.stringify(TASK_ID)}]')`;
    await evaluate(
      client,
      `${rowSelector}.querySelector('[data-execution-field=\"status\"]').value='in_progress'; ${rowSelector}.querySelector('[data-execution-field=\"status\"]').dispatchEvent(new Event('change',{bubbles:true})); ${rowSelector}.querySelector('[data-execution-field=\"actualStart\"]').value='2026-08-17'; ${rowSelector}.querySelector('[data-execution-save]').click(); true`,
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#executionStatus')?.dataset.state === 'warning'",
        ),
      "execution conflict reconciliation",
    );
    const executionConflictReconciled =
      state.requests.filter((item) => item.path.endsWith("/execution"))
        .length >= 2;
    await evaluate(
      client,
      `${rowSelector}.querySelector('[data-execution-save]').click(); true`,
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#executionProgress')?.textContent === '0 / 1' && document.querySelector('[data-execution-task] select')?.value === 'in_progress'",
        ),
      "execution update",
    );
    const executionUpdate =
      state.sequenceNo === 1 &&
      state.bodies[1]?.expectedExecutionSequence === 0 &&
      state.bodies[1]?.status === "in_progress";
    await evaluate(
      client,
      "document.querySelector('#exportExecutionCsv').click(); true",
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#executionStatusText')?.textContent.includes('已下载')",
        ),
      "execution export",
    );
    await evaluate(
      client,
      "document.querySelector('#exportExecutionXlsx').click(); true",
    );
    await waitUntil(
      () =>
        evaluate(
          client,
          "document.querySelector('#executionStatusText')?.textContent.includes('XLSX')",
        ),
      "xlsx export",
    );
    const csvExport = state.requests.some((item) =>
      item.path.endsWith("/exports/execution.csv"),
    );
    const xlsxExport = state.requests.some((item) =>
      item.path.endsWith("/exports/execution.xlsx"),
    );
    const requestBoundary = state.bodies.every((body) =>
      Object.keys(body).every(
        (key) => !/scope|actor|digest|timestamp|versionId/i.test(key),
      ),
    );
    let responsive = true;
    for (const width of [375, 1440]) {
      await client.send("Emulation.setDeviceMetricsOverride", {
        width,
        height: 900,
        deviceScaleFactor: 1,
        mobile: width < 768,
      });
      const layout = await evaluate(
        client,
        "(() => { const section=document.querySelector('#executionWorkbench').getBoundingClientRect(); return section.left >= 0 && section.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth; })()",
      );
      responsive &&= layout;
    }
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 1440,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const themes = await evaluate(
      client,
      "(() => { const before=document.documentElement.dataset.theme || 'dark'; document.querySelector('#themeToggle').click(); const after=document.documentElement.dataset.theme || 'dark'; return before !== after && ['dark','light'].includes(after); })()",
    );
    const result = {
      executionRead,
      executionConflictReconciled,
      executionUpdate,
      csvExport,
      xlsxExport,
      requestBoundary,
      responsive,
      themes,
      noConsoleFailures: failures.length === 0,
      noExternalRequests: external.length === 0,
    };
    if (
      Object.keys(result).length !== RESULTS.size ||
      Object.values(result).some((value) => value !== true)
    )
      throw new Error(
        `execution browser result failed: ${JSON.stringify(result)}`,
      );
    return result;
  } finally {
    client?.close();
    await stopBrowser(browser, profile);
    await new Promise((resolvePromise) => server.close(resolvePromise));
    await rm(profile, {
      recursive: true,
      force: true,
      maxRetries: 50,
      retryDelay: 200,
    });
  }
}
async function serveOnly() {
  const state = {
    approved: false,
    status: "not_started",
    blockerReason: null,
    actualStart: null,
    actualFinish: null,
    note: null,
    sequenceNo: 0,
    conflictOnce: false,
    bodies: [],
    requests: [],
  };
  const { server, baseUrl } = await startServer(state);
  process.stdout.write(`${baseUrl}\n`);
  await new Promise((resolvePromise) => {
    const close = () => server.close(resolvePromise);
    process.once("SIGINT", close);
    process.once("SIGTERM", close);
  });
}

if (process.argv[2] === "--self-test") {
  process.stdout.write("M1-04 browser execution gate contract passed\n");
} else if (process.argv[2] === "--serve-only") {
  await serveOnly();
} else {
  try {
    process.stdout.write(
      `${JSON.stringify(await run(parseArgs(process.argv.slice(2))))}\n`,
    );
  } catch (error) {
    process.stderr.write(
      `M1-04 browser execution gate failed: ${error.message}\n`,
    );
    process.exitCode = 1;
  }
}
