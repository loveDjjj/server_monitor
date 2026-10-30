let data,
  tab = "instances",
  logKey = null,
  points = [],
  gpuEditing = false,
  pending = new Set();
let adding = false,
  refreshRunning = false;
let selectedInstance = null;
$("#instance-count").onclick = () => {
  if (!data) return;
  $("#sampling-form [name=interval]").value = data.status_interval_seconds;
  $("#sampling-error").hidden = true;
  $("#sampling-dialog").showModal();
};
$("#sampling-cancel").onclick = () => $("#sampling-dialog").close();
$("#sampling-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("[type=submit]");
  button.disabled = true;
  try {
    await api("/api/instances/sampling", {
      status_interval_seconds: Number(new FormData(e.target).get("interval")),
    });
    $("#sampling-dialog").close();
    toast("实例采样间隔已更新");
    await refresh();
  } catch (err) {
    $("#sampling-error").textContent = err.message;
    $("#sampling-error").hidden = false;
  } finally {
    button.disabled = false;
  }
};
$("#add-instance").onclick = () => {
  if (adding) return;
  $("#add-form").reset();
  $("#add-form [name=workspace]").value = data?.default_workspace || "";
  $("#add-error").hidden = true;
  $("#add-dialog").showModal();
};
$("#add-cancel").onclick = () => $("#add-dialog").close();
$("#add-dialog").addEventListener("cancel", (e) => {
  if (adding) e.preventDefault();
});
$("#add-form").onsubmit = async (e) => {
  e.preventDefault();
  if (adding) return;
  const f = new FormData(e.target);
  adding = true;
  $("#add-error").hidden = true;
  $("#add-submit").textContent = "正在查询平台…";
  $("#add-form")
    .querySelectorAll("input,button")
    .forEach((x) => (x.disabled = true));
  try {
    await api("/api/instances", {
      workspace: f.get("workspace").trim(),
      name: f.get("name").trim(),
    });
    $("#add-dialog").close();
    toast("实例已添加，开始只读监控");
    await refresh();
  } catch (err) {
    $("#add-error").textContent = err.message;
    $("#add-error").hidden = false;
  } finally {
    adding = false;
    $("#add-submit").textContent = "查询并添加";
    $("#add-form")
      .querySelectorAll("input,button")
      .forEach((x) => (x.disabled = false));
  }
};
function card(item) {
  const c = item.config,
    o = item.observed,
    r = c.resources,
    key = esc(item.key);
  const busy =
    pending.has(item.key) ||
    ["queued", "running"].includes(item.operation?.status);
  const paused =
    item.auto_retry_paused ??
    ["failed", "interrupted"].includes(item.operation?.status);
  const stopped = ["SUSPENDED", "STOPPED"].includes(o.state),
    stable = ["RUNNING", "SUSPENDED", "STOPPED"].includes(o.state);
  return `<div class="detail-heading"><div class="detail-title"><span class="resource-icon">${icon(r.gpu_count ? "server" : "cpu")}</span><div><h2>${esc(c.display_name)}</h2><p class="detail-subtitle">${esc(c.workspace)} <span> / </span> ${esc(c.cluster)}</p></div></div><div class="detail-actions">
 ${stopped ? `<button class="primary" data-key="${key}" data-action="start" ${busy ? "disabled" : ""}>${icon("play")}启动实例</button>` : ""}
 <button data-edit="${key}" ${busy ? "disabled" : ""}>${icon("settings")}设置</button>
 <button class="icon-button" data-log="${key}" title="查看日志" aria-label="查看日志">${icon("history")}</button>
 <details class="action-menu" id="more-${key}"><summary title="更多操作" aria-label="更多操作">${icon("more")}</summary><div class="menu-items"><button data-key="${key}" data-action="restart" ${busy || !stable ? "disabled" : ""}>重新启动</button><button class="danger-text" data-key="${key}" data-action="stop" ${busy || o.state !== "RUNNING" ? "disabled" : ""}>停止实例</button></div></details>
 </div></div>
 <div class="detail-layout"><div class="detail-main"><div class="runtime-summary"><div><small>当前运行时间</small><strong data-runtime="${key}">${o.state === "RUNNING" ? runtimeText(item) : stopped ? "已停止" : "等待就绪"}</strong></div><div><small>平台状态</small>${stateBadge(o.state)}</div></div>
 <label class="auto-start-switch"><span>停止后自动启动</span><input type="checkbox" data-auto-start="${key}" ${c.auto_start_on_stop ? "checked" : ""} ${busy || c.auto_start_on_stop === undefined ? "disabled" : ""}></label>
 <div class="resource-facts"><div><small>GPU</small><b>${r.gpu_count} <span>卡</span></b></div><div><small>vCPU</small><b>${esc(r.cpu)} <span>核</span></b></div><div><small>内存</small><b>${esc(r.memory)}</b></div></div>
 <div class="info-row"><span>资源规格</span><code>${esc(r.spec)}</code></div>
 <details class="technical-details" id="technical-${key}"><summary>镜像与启动配置 ${icon("chevron")}</summary><div class="technical-body"><small>容器镜像</small><div class="image"><code>${esc(c.image)}</code><button class="icon-button" data-copy="${key}" title="复制镜像地址" aria-label="复制镜像地址">${icon("copy")}</button></div>${c.command ? `<small>启动命令</small><pre>${esc(c.command.join(" "))}</pre>` : ""}${item.pending_image ? `<p class="pending">待应用镜像：${esc(item.pending_image)}</p>` : ""}</div></details>
 </div><div class="detail-schedule"><div class="section-title"><h3>${icon("clock")}运行计划</h3><button class="text-button" data-edit="${key}">编辑</button></div>
 ${["start", "stop"].map((a) => `<div class="setting"><span>定时${actions[a]}</span><time class="${c.schedule[a].enabled ? "" : "muted"}">${esc(c.schedule[a].time)}</time><span class="setting-status ${c.schedule[a].enabled ? "enabled" : "disabled"}">${c.schedule[a].enabled ? "开启" : "关闭"}</span></div>`).join("")}
 <div class="setting"><span>周期续杯</span><time class="${c.rollover.enabled ? "" : "muted"}">${c.rollover.period_minutes} 分钟</time><span class="setting-status ${c.rollover.enabled ? "enabled" : "disabled"}">${c.rollover.enabled ? "开启" : "关闭"}</span></div>
 <div class="info-row"><span>启动就绪超时</span><span>${c.start_timeout_minutes ?? 60} 分钟</span></div>
 <p class="next-plan">${icon("chevron")}${esc(nextPlan(c))}</p></div></div>
 ${item.error ? `<p class="error">${esc(item.error)}</p>` : ""}
 ${item.manual_paused ? '<p class="hint">人工暂停 · 等待恢复或下一次定时启动</p>' : ""}
 ${paused && item.reason?.includes("自动重试已暂停") ? `<p class="error">${esc(item.reason)}</p>` : ""}
 ${item.manual_paused || paused ? `<button data-key="${key}" data-action="resume" ${busy ? "disabled" : ""}>恢复自动运行</button>` : ""}
 ${item.operation ? `<details class="operation-history" id="operation-${key}" ${busy ? "open" : ""}><summary>${busy ? "操作执行中" : "最近操作（历史）"} ${icon("chevron")}</summary>${operation(item.operation)}</details>` : ""}
 <div class="detail-footer"><span>状态采样 ${age(o.sampled_at)}</span><span>${o.platform_updated_at ? "平台更新 " + fmt(o.platform_updated_at) : ""}</span></div>`;
}
function renderInstances() {
  if (!data.instances.some((i) => i.key === selectedInstance))
    selectedInstance =
      data.instances.find((i) => i.observed.state === "RUNNING")?.key ||
      data.instances[0]?.key;
  const el = $("#instances"),
    opened = [...el.querySelectorAll("details[open]")].map((d) => d.id);
  const focused = el.contains(document.activeElement)
    ? Object.entries(document.activeElement.dataset)
    : null;
  el.innerHTML = `<div class="instance-list"><div class="instance-list-heading"><span>实例名称</span><span>状态</span><span>资源</span><span>运行时间</span><span>下一次计划</span><span></span></div>${data.instances.map((i) => `<button class="instance-row ${i.key === selectedInstance ? "is-selected" : ""}" data-select-instance="${esc(i.key)}" aria-pressed="${i.key === selectedInstance}"><span class="instance-name"><span class="list-icon">${icon(i.config.resources.gpu_count ? "server" : "cpu")}</span><span><strong>${esc(i.config.display_name)}</strong><small>${esc(i.config.name)}</small></span></span>${stateBadge(i.observed.state)}<span class="list-resources">${i.config.resources.gpu_count} GPU <span>·</span> ${esc(i.config.resources.cpu)} 核 <span>·</span> ${esc(i.config.resources.memory)}</span><span class="list-runtime" data-runtime="${esc(i.key)}">${i.observed.state === "RUNNING" ? runtimeText(i) : "—"}</span><span class="list-plan">${esc(nextPlan(i.config))}</span>${icon("chevron")}</button>`).join("")}</div>`;
  const item = data.instances.find((i) => i.key === selectedInstance);
  if (item)
    el.insertAdjacentHTML(
      "beforeend",
      `<article class="instance-detail" id="card-${esc(item.key)}">${card(item)}</article>`,
    );
  else el.insertAdjacentHTML("beforeend", '<p class="empty">尚未添加实例</p>');
  for (const id of opened) {
    const detail = document.getElementById(id);
    if (detail) detail.open = true;
  }
  if (focused?.length) {
    const target = [...el.querySelectorAll("button,input,summary")].find((b) =>
      focused.every(([k, v]) => b.dataset[k] === v),
    );
    target?.focus({ preventScroll: true });
  }
}
function operation(op) {
  const busy = ["queued", "running"].includes(op.status);
  const message = op.message?.includes("appMemberQuotaExceeded")
    ? "平台个人GPU配额不足，请查看日志"
    : op.message?.length > 160
      ? op.message.slice(0, 160) + "…（详情见日志）"
      : op.message;
  return `<div class="operation ${esc(op.status)}"><strong class="${busy ? "busy" : ""}">${busy ? "正在执行" : "最近操作（历史）"} · ${esc(sources[op.source] || op.source)} · ${actions[op.action]} · ${statuses[op.status]}</strong><div>${esc(message)}</div>${busy && op.resume_at ? `<small>剩余 ${Math.max(0, Math.ceil(op.resume_at - Date.now() / 1000))} 秒</small>` : ""}</div>`;
}
function editor(item) {
  const c = item.config;
  return `<h2>编辑 ${esc(c.display_name)}</h2><form class="edit-form" data-form="${esc(item.key)}"><label>显示名称<input name="display_name" value="${esc(c.display_name)}" required></label><label>镜像（下次启动应用）<input name="image" value="${esc(item.pending_image || c.image)}" required></label><div class="row">${["start", "stop"].map((a) => `<div><label class="switch"><input type="checkbox" name="${a}_enabled" ${c.schedule[a].enabled ? "checked" : ""}>启用定时${actions[a]}</label><label>每天时间<input name="${a}_time" type="time" value="${c.schedule[a].time}" required></label></div>`).join("")}</div><label class="switch"><input type="checkbox" name="rollover_enabled" ${c.rollover.enabled ? "checked" : ""}>启用周期续杯</label><label>周期（1–230分钟）<input name="period" type="number" min="1" max="230" value="${c.rollover.period_minutes}" required></label><label>启动就绪超时（分钟）<input name="start_timeout_minutes" type="number" min="1" max="180" step="1" value="${c.start_timeout_minutes ?? 60}" required></label><p class="hint">调度设置保存后立即生效；启动就绪超时从下一次启动或重启生效。续杯确认停止后等待5秒直接启动。镜像变化可能替换实例并短暂中断连接。</p><div class="form-actions"><button type="button" data-cancel="${esc(item.key)}">放弃</button><button class="primary" type="submit">保存</button></div></form>`;
}
function render() {
  if (!data) return;
  $("#connection").textContent = data.execute ? "本地服务在线" : "演练模式";
  $("#instance-count").textContent =
    `${data.instances.length} 个实例 · ${data.status_interval_seconds}秒采样`;
  renderInstances();
  if (!gpuEditing) {
    const g = data.gpu.config;
    $("#gpu-settings").textContent =
      `${g.enabled ? "监控已启用" : "监控已关闭"} · 每${g.interval_seconds}秒采样 · 保留${g.history_hours}小时`;
  }
  const select = $("#cluster-select"),
    previous = select.value;
  select.innerHTML = data.gpu.config.clusters
    .map(
      (c) =>
        `<option value="${esc(c.key)}">${esc(c.display_name || c.name)}</option>`,
    )
    .join("");
  if (previous) select.value = previous;
  const sample = data.gpu.latest[select.value];
  $("#gpu-latest").textContent = sample
    ? sample.error
      ? "采样失败"
      : `${sample.free_gpus} 卡空闲 · ${age(sample.timestamp)}`
    : "等待采样";
}
async function refresh() {
  if (refreshRunning) return;
  refreshRunning = true;
  try {
    data = await api("/api/status");
    render();
    window.renderWorkspace?.();
    if (tab === "gpu") await chartRefresh();
    if (logKey) await refreshLogs();
  } catch (e) {
    $("#connection").textContent = "连接异常";
    toast(e.message);
  } finally {
    refreshRunning = false;
  }
}
document.addEventListener("change", async (e) => {
  const key = e.target.dataset.autoStart;
  if (!key) return;
  const enabled = e.target.checked;
  pending.add(key);
  render();
  try {
    await api(`/api/instances/${key}/config`, { auto_start_on_stop: enabled });
    toast("停止后自动启动已" + (enabled ? "开启" : "关闭"));
    await refresh();
  } catch (err) {
    toast(err.message);
  } finally {
    pending.delete(key);
    render();
  }
});
async function chartRefresh() {
  const key = $("#cluster-select").value;
  if (!key) return;
  points = await api(`/api/gpu/${key}/metrics?hours=${$("#hours").value}`);
  draw();
}
function draw() {
  const canvas = $("#chart"),
    rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  const dpr = devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const w = rect.width,
    h = rect.height,
    left = 35,
    right = w - 12,
    top = 12,
    bottom = h - 30,
    end = Date.now() / 1000,
    start = end - Number($("#hours").value) * 3600,
    max = Math.max(4, ...points.map((p) => p.free_gpus ?? 0)),
    ceiling = Math.ceil((max + 1) / 4) * 4;
  ctx.font = "11px system-ui";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    let y = bottom - ((bottom - top) * i) / 4;
    ctx.strokeStyle = "#e5ebf2";
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.fillStyle = "#7b8999";
    ctx.fillText(String((ceiling * i) / 4), 4, y + 4);
    let t = start + ((end - start) * i) / 4;
    ctx.fillText(
      new Date(t * 1000).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      }),
      left + ((right - left) * i) / 4 - 14,
      h - 7,
    );
  }
  ctx.strokeStyle = "#147d64";
  ctx.lineWidth = 2;
  ctx.beginPath();
  let connected = false,
    prev = 0;
  for (const p of points) {
    if (p.free_gpus === null) {
      connected = false;
      continue;
    }
    const x = left + ((p.epoch - start) / (end - start)) * (right - left),
      y = bottom - (p.free_gpus / ceiling) * (bottom - top);
    if (!connected || p.epoch - prev > data.gpu.config.interval_seconds * 2.5)
      ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
    connected = true;
    prev = p.epoch;
  }
  ctx.stroke();
  if (points.length === 1 && points[0].free_gpus !== null) {
    ctx.beginPath();
    ctx.arc(
      left + ((points[0].epoch - start) / (end - start)) * (right - left),
      bottom - (points[0].free_gpus / ceiling) * (bottom - top),
      3,
      0,
      7,
    );
    ctx.fillStyle = "#147d64";
    ctx.fill();
  }
}
$("#chart").onmousemove = (e) => {
  if (!points.length) return;
  const box = e.target.getBoundingClientRect(),
    target =
      Date.now() / 1000 -
      (1 - (e.clientX - box.left) / box.width) *
        Number($("#hours").value) *
        3600,
    p = points.reduce((a, b) =>
      Math.abs(a.epoch - target) < Math.abs(b.epoch - target) ? a : b,
    ),
    tip = $("#tooltip");
  tip.textContent = `${fmt(p.timestamp)} · ${p.free_gpus === null ? "采样失败" : p.free_gpus + "卡"}`;
  tip.hidden = false;
  tip.style.left = Math.min(e.clientX - box.left, box.width - 220) + "px";
  tip.style.top = "12px";
};
$("#chart").onmouseleave = () => ($("#tooltip").hidden = true);
async function refreshLogs() {
  const ops = await api(`/api/instances/${logKey}/operations`),
    events = await api(`/api/instances/${logKey}/events`),
    filter = $("#log-filter").value;
  const rows = [
    ...ops.map((o) => ({ ...o, time: o.created_at })),
    ...events
      .filter((e) => !e.operation_id)
      .map((e) => ({ ...e, time: e.timestamp })),
  ].sort((a, b) => new Date(b.time) - new Date(a.time));
  $("#log-items").innerHTML =
    rows
      .filter(
        (x) => filter === "all" || x.source === filter || x.status === filter,
      )
      .map(
        (x) =>
          `<div class="log-item"><time>${fmt(x.time)}</time><p>${esc(sources[x.source] || x.source || "记录")} · ${esc(x.action ? actions[x.action] : "")} ${esc(statuses[x.status] || "")}</p>${x.message?.length > 200 ? `<details><summary>查看完整错误信息</summary><pre>${esc(x.message)}</pre></details>` : `<p>${esc(x.message)}</p>`}${x.steps ? `<details><summary>查看执行阶段</summary><pre>${esc(x.steps.map((s) => `${fmt(s.time)}  ${s.message}`).join("\n"))}</pre></details>` : ""}</div>`,
      )
      .join("") || '<p class="hint">暂无记录</p>';
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try {
    if (b.dataset.selectInstance) {
      selectedInstance = b.dataset.selectInstance;
      render();
    }
    if (b.dataset.tab) {
      tab = b.dataset.tab;
      for (const section of document.querySelectorAll("main > section"))
        section.hidden = section.id !== tab + "-page";
      document
        .querySelectorAll("[data-tab]")
        .forEach((x) => x.classList.toggle("selected", x.dataset.tab === tab));
      if (tab === "gpu") await chartRefresh();
    }
    if (b.dataset.edit) {
      const key = b.dataset.edit;
      $("#edit-dialog").innerHTML = editor(
        data.instances.find((i) => i.key === key),
      );
      $("#edit-dialog").showModal();
    }
    if (b.dataset.cancel) {
      $("#edit-dialog").close();
      render();
    }
    if (b.dataset.copy)
      await navigator.clipboard.writeText(
        data.instances.find((i) => i.key === b.dataset.copy).config.image,
      );
    if (b.dataset.log) {
      logKey = b.dataset.log;
      $("#log-title").textContent =
        data.instances.find((i) => i.key === logKey).config.display_name +
        " · 日志";
      $("#logs").showModal();
      await refreshLogs();
    }
    if (b.dataset.action) {
      const key = b.dataset.key;
      pending.add(key);
      render();
      try {
        await api(`/api/instances/${key}/actions/${b.dataset.action}`, {});
        await refresh();
      } finally {
        pending.delete(key);
        render();
      }
    }
  } catch (err) {
    toast(err.message);
  }
});
document.addEventListener("submit", async (e) => {
  if (!e.target.dataset.form) return;
  e.preventDefault();
  const form = e.target,
    key = form.dataset.form,
    f = new FormData(form),
    body = {
      display_name: f.get("display_name"),
      image: f.get("image"),
      schedule: {
        start: { enabled: f.has("start_enabled"), time: f.get("start_time") },
        stop: { enabled: f.has("stop_enabled"), time: f.get("stop_time") },
      },
      rollover: {
        enabled: f.has("rollover_enabled"),
        period_minutes: Number(f.get("period")),
      },
      start_timeout_minutes: Number(f.get("start_timeout_minutes")),
    };
  try {
    await api(`/api/instances/${key}/config`, body);
    $("#edit-dialog").close();
    toast("设置已保存");
    await refresh();
  } catch (err) {
    toast(err.message);
  }
});
$("#gpu-edit").onclick = () => {
  gpuEditing = true;
  const g = data.gpu.config;
  $("#gpu-settings").innerHTML =
    `<form id="gpu-form" class="edit-form"><label class="switch"><input name="enabled" type="checkbox" ${g.enabled ? "checked" : ""}>启用GPU监控</label><div class="row"><label>采样步长（秒）<input name="interval" type="number" min="10" max="3600" value="${g.interval_seconds}" required></label><label>保留时长（小时）<input name="history" type="number" min="1" max="168" value="${g.history_hours}" required></label></div><div class="form-actions"><button type="button" id="gpu-cancel">放弃</button><button class="primary">保存</button></div></form>`;
  $("#gpu-cancel").onclick = () => {
    gpuEditing = false;
    render();
  };
  $("#gpu-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api("/api/gpu/config", {
        enabled: f.has("enabled"),
        interval_seconds: Number(f.get("interval")),
        history_hours: Number(f.get("history")),
      });
      gpuEditing = false;
      await refresh();
    } catch (err) {
      toast(err.message);
    }
  };
};
$("#close-logs").onclick = () => {
  $("#logs").close();
  logKey = null;
};
$("#logs").onclose = () => (logKey = null);
$("#log-filter").onchange = refreshLogs;
$("#cluster-select").onchange = chartRefresh;
$("#hours").onchange = chartRefresh;
addEventListener("resize", draw);
setInterval(() => {
  for (const i of data?.instances || []) {
    if (i.observed.state === "RUNNING")
      for (const el of document.querySelectorAll(`[data-runtime="${i.key}"]`))
        el.textContent = runtimeText(i);
  }
}, 1000);
setInterval(refresh, 3000);
refresh();
