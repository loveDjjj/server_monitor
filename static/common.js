const $ = (s) => document.querySelector(s),
  esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
// Lucide 0.468.0 icons, ISC license; paths are vendored for offline use.
const iconPaths = {
  server:
    '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
  overview:
    '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  chart:
    '<path d="M12 16v5M16 14v7M20 10v11m2-18-8.646 8.646a.5.5 0 0 1-.708 0L9.354 8.354a.5.5 0 0 0-.707 0L2 15M4 18v3M8 14v7"/>',
  history:
    '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8M3 3v5h5M12 7v5l4 2"/>',
  settings:
    '<path d="M20 7h-9M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  plus: '<path d="M5 12h14M12 5v14"/>',
  chevron: '<path d="m9 18 6-6-6-6"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6h4.5"/>',
};
const icon = (name) =>
  `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${iconPaths[name] || iconPaths.server}</svg>`;
const stateBadge = (s, names = labels) =>
  `<span class="state ${esc(s)}">${esc(names[s] || s || "待查询")}</span>`;
function nextPlan(c) {
  const next = [];
  for (const action of ["start", "stop"]) {
    if (!c.schedule[action].enabled) continue;
    const now = new Date(),
      at = new Date(now),
      [h, m] = c.schedule[action].time.split(":").map(Number);
    at.setHours(h, m, 0, 0);
    if (at <= now) at.setDate(at.getDate() + 1);
    next.push({
      at,
      label: `${at.getDate() === now.getDate() ? "今天" : "明天"} ${c.schedule[action].time} ${actions[action]}`,
    });
  }
  next.sort((a, b) => a.at - b.at);
  return (
    next[0]?.label ||
    (c.auto_start_on_stop
      ? "停止后自动启动"
      : c.rollover.enabled
        ? `每 ${c.rollover.period_minutes} 分钟续杯`
        : "未设置")
  );
}
const labels = {
  RUNNING: "运行中",
  SUSPENDED: "已停止",
  STOPPED: "已停止",
  PROGRESSING: "启动中",
  CREATING: "创建中",
  UNKNOWN: "待查询",
  FAILED: "失败",
  MISSING: "不存在",
};
const actions = {
    start: "启动",
    stop: "停止",
    restart: "重启",
    resume: "恢复自动运行",
  },
  sources = {
    auto_start: "停止后自动启动",
    manual: "人工",
    schedule: "定时",
    rollover: "周期续杯",
    config: "配置修改",
    monitor: "状态监控",
    platform: "平台状态",
  };
const statuses = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  interrupted: "进程中断",
  warning: "查询异常",
  observed: "已观测",
};
const fmt = (t) =>
  t ? new Date(t).toLocaleString("zh-CN", { hour12: false }) : "—";
const age = (t) =>
  t
    ? `${Math.max(0, Math.floor((Date.now() - new Date(t)) / 1000))} 秒前`
    : "尚未采样";
const duration = (t, at = Date.now() / 1000) => {
  if (!t) return "起点待确认";
  let n = Math.max(0, Math.floor(at - t));
  return [Math.floor(n / 3600), Math.floor((n % 3600) / 60), n % 60]
    .map((v) => String(v).padStart(2, "0"))
    .join(":");
};
const runtimeText = (item) =>
  item.runtime_fresh
    ? duration(item.run_started_at)
    : item.run_started_at
      ? duration(item.run_started_at, item.runtime_sampled_at) + "（已过期）"
      : "等待平台时间";
async function api(url, body) {
  const r = await fetch(
    url,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const v = await r.json();
  if (!r.ok) throw Error(v.error || r.status);
  return v;
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  setTimeout(() => ($("#toast").hidden = true), 6000);
}
