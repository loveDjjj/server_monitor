const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../static/console.js"),
  "utf8",
);
const common = fs.readFileSync(
  path.join(__dirname, "../static/common.js"),
  "utf8",
);
const context = {
  document: { querySelector: () => ({ addEventListener() {} }) },
};
vm.createContext(context);
vm.runInContext(
  common +
    "\n" +
    source.slice(0, source.indexOf("function editor(")) +
    "\nglobalThis.renderCard = card;",
  context,
);

const item = {
  key: "h100",
  config: {
    display_name: "H100",
    image: "image",
    resources: { gpu_count: 2, spec: "gpu", cpu: "8", memory: "128GiB" },
    schedule: {
      start: { enabled: true, time: "08:00" },
      stop: { enabled: true, time: "04:00" },
    },
    rollover: { enabled: true, period_minutes: 230 },
  },
  observed: { state: "SUSPENDED", sampled_at: new Date().toISOString() },
  operation: {
    status: "failed",
    action: "restart",
    source: "rollover",
    message: "describe timed out",
  },
  reason: "上次操作失败，自动重试已暂停",
};

const legacy = context.renderCard(item);
assert.match(legacy, /当前运行时间[\s\S]*已停止/);
assert.match(legacy, /最近操作（历史）/);
assert.match(legacy, /data-action="resume"/);

const current = context.renderCard({
  ...item,
  auto_retry_paused: false,
  reason: "运行中",
  observed: { state: "RUNNING", sampled_at: new Date().toISOString() },
  runtime_fresh: true,
  run_started_at: Date.now() / 1000 - 5,
});
assert.doesNotMatch(current, /data-action="resume"/);
assert.match(current, /00:00:0[5-6]/);
console.log("console render OK");
const autoEnabled = context.renderCard({
  ...item,
  config: { ...item.config, auto_start_on_stop: true },
});
assert.match(autoEnabled, /data-auto-start="h100" checked/);
assert.match(autoEnabled, /停止后自动启动/);
const autoBusy = context.renderCard({
  ...item,
  config: { ...item.config, auto_start_on_stop: true },
  operation: { status: "running" },
});
assert.match(autoBusy, /data-auto-start="h100" checked disabled/);
assert.doesNotMatch(legacy, /data-auto-start="h100" checked/);
assert.doesNotMatch(current, /data-action="start"/);
assert.match(legacy, /data-action="start"/);
assert.match(current, /class="action-menu"/);
assert.match(current, /class="technical-details"/);
assert.match(current, /aria-label="复制镜像地址"/);
