// Offline browser regression suite: all API responses are in-memory fixtures.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright-core");

const schedule = () => ({
  start: { enabled: false, time: "08:00" },
  stop: { enabled: false, time: "23:00" },
});
function instance(key, state) {
  return {
    key,
    config: {
      name: key,
      display_name: "OneDay " + key,
      workspace: "space",
      cluster: "cluster",
      image: "registry/image:tag",
      command: ["bash", "start.sh"],
      resources: {
        gpu_count: key === "cpu" ? 0 : 2,
        cpu: "2",
        memory: "4GiB",
        spec: "test-spec",
      },
      schedule: schedule(),
      rollover: { enabled: false, period_minutes: 230 },
      auto_start_on_stop: true,
      start_timeout_minutes: 120,
    },
    observed: { state, sampled_at: new Date().toISOString() },
    operation: null,
    runtime_fresh: true,
    run_started_at: Date.now() / 1000 - 60,
  };
}
const fixture = {
  execute: false,
  timezone: "Asia/Shanghai",
  status_interval_seconds: 30,
  instances: [instance("h100", "STOPPED"), instance("cpu", "RUNNING")],
  gpu: {
    config: {
      enabled: false,
      interval_seconds: 30,
      history_hours: 24,
      clusters: [{ key: "test", name: "test" }],
    },
    latest: {},
  },
  acp: {
    config: {
      hours: 30,
      cluster: "cluster",
      image: "image",
      worker_spec: "spec",
      mount: "volume:/data",
      ready_timeout_minutes: 120,
      auto_restart: false,
      schedule: schedule(),
    },
    state: { current: "pt-current" },
    current_job: {
      name: "pt-current",
      uid: "current",
      display_name: "Current",
      state: "STARTING",
      start_time: null,
    },
    operation: { action: "start", status: "running" },
    jobs: Array.from({ length: 10 }, (_, n) => ({
      name: "pt-" + n,
      uid: "uid-" + n,
      display_name: "History " + n,
      state: "SUCCEEDED",
      create_time: "2026-10-30T00:00:00Z",
      start_time: "2026-10-30T00:00:00Z",
      complete_time: "2026-10-30T01:00:00Z",
    })),
    workers: {},
    events: [],
    history_page: 1,
    history_more: false,
    sampled_at: new Date().toISOString(),
    error: "",
  },
};
const requests = [];
const root = path.join(__dirname, "..");
const files = {
  "/": "templates/console.html",
  "/static/common.js": "static/common.js",
  "/static/console.js": "static/console.js",
  "/static/acp.js": "static/acp.js",
  "/static/console.css": "static/console.css",
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  let payload;
  if (files[url.pathname]) {
    res.setHeader(
      "Content-Type",
      url.pathname.endsWith(".css")
        ? "text/css"
        : url.pathname.endsWith(".js")
          ? "text/javascript"
          : "text/html",
    );
    res.end(fs.readFileSync(path.join(root, files[url.pathname])));
    return;
  }
  let body = "";
  for await (const chunk of req) body += chunk;
  requests.push({
    url: url.pathname,
    method: req.method,
    body: body ? JSON.parse(body) : {},
  });
  if (url.pathname === "/api/status") payload = fixture;
  else if (url.pathname === "/api/acp/config") {
    Object.assign(fixture.acp.config, JSON.parse(body));
    payload = fixture.acp;
  } else if (url.pathname === "/api/acp/actions/stop") {
    fixture.acp.state.stop_requested = true;
    payload = { status: "running", stop_requested: true };
  } else if (url.pathname === "/api/acp/workers")
    payload = {
      sampled_at: new Date().toISOString(),
      items: [
        {
          name: "worker-0",
          pod_ip: "10.1.2.3",
          host_ip: "10.2.3.4",
          state: "Running",
        },
      ],
    };
  else if (url.pathname === "/api/acp/logs")
    payload = { text: "GPU ready", partial: true };
  else payload = [];
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(payload));
});
(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = "http://127.0.0.1:" + server.address().port;
  const browser = await chromium.launch({
    headless: true,
    ...(process.platform === "win32" ? { channel: "msedge" } : {}),
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(base);
    await page.locator("#card-cpu").waitFor();
    assert.equal(
      await page.locator("#card-cpu [data-action=start]").count(),
      0,
    );
    await page.click("[data-select-instance=h100]");
    await page.locator("#card-h100 [data-action=start]").waitFor();
    await page.click("#technical-h100>summary");
    await page.click("#more-h100>summary");
    await page.waitForTimeout(3200);
    assert.equal(
      await page.locator("#technical-h100").getAttribute("open"),
      "",
    );
    assert.equal(await page.locator("#more-h100").getAttribute("open"), "");
    await page.click("#card-h100 [data-edit]");
    await page
      .locator("#edit-dialog [name=display_name]")
      .fill("Unsaved draft");
    await page.waitForTimeout(3200);
    assert.equal(
      await page.locator("#edit-dialog [name=display_name]").inputValue(),
      "Unsaved draft",
    );
    await page.click("#edit-dialog [data-cancel]");
    await page.click("[data-tab=acp]");
    await page.locator("[data-acp-action=stop]").waitFor();
    assert.equal(
      await page.locator("[data-acp-action=stop]").isEnabled(),
      true,
    );
    await page.click("[data-acp-action=stop]");
    await page.waitForTimeout(250);
    assert.ok(
      requests.some(
        (r) => r.url === "/api/acp/actions/stop" && r.method === "POST",
      ),
    );
    assert.equal(
      await page.locator("[data-acp-action=stop]").isDisabled(),
      true,
    );
    fixture.acp.operation = null;
    fixture.acp.state.stop_requested = false;
    await page.click("#acp-edit");
    await page.locator("#acp-form [name=hours]").fill("29");
    await page.click("#acp-form [type=submit]");
    await page.waitForTimeout(250);
    assert.equal(fixture.acp.config.hours, 29);
    await page.fill("#acp-search", "History 1");
    assert.equal(await page.locator("tbody tr").count(), 1);
    await page.fill("#acp-search", "");
    await page.click("[data-history-more]");
    assert.equal(await page.locator("tbody tr").count(), 10);
    await page.click('[data-job="pt-0"]');
    await page.click("#acp-detail [data-workers]");
    await page.waitForFunction(() =>
      document
        .querySelector("#detail-workers")
        .textContent.includes("10.1.2.3"),
    );
    await page.click("[data-job-logs]");
    await page.waitForFunction(() =>
      document.querySelector("#detail-logs").textContent.includes("GPU ready"),
    );
    assert.match(await page.locator("#detail-logs").textContent(), /快照/);
    await page.locator("#acp-detail").evaluate((d) => d.close());
    const screenshotDir = fs.mkdtempSync(path.join(os.tmpdir(), "oneday-ui-"));
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 950 });
      for (const tab of ["instances", "overview", "acp", "gpu", "activity"]) {
        await page.click(`[data-tab=${tab}]`);
        await page.waitForTimeout(100);
        assert.equal(
          await page.evaluate(
            () => document.documentElement.scrollWidth > innerWidth,
          ),
          false,
          `${tab} overflow ${width}`,
        );
      }
      await page.screenshot({
        path: path.join(screenshotDir, `ui-${width}.png`),
        fullPage: true,
      });
    }
    assert.deepEqual(errors, []);
    console.log(
      "Offline browser workflows passed; screenshots: " + screenshotDir,
    );
  } finally {
    await browser.close();
    server.close();
  }
})().catch((e) => {
  console.error(e);
  server.close();
  process.exitCode = 1;
});
