const acpLabels = {
  ...labels,
  SUCCEEDED: "已完成",
  QUEUEING: "排队中",
  WAITING: "等待资源",
  STARTING: "启动中",
  SUSPENDING: "停止中",
  DELETED: "已删除",
};
for (const [tabName, iconName] of Object.entries({
  overview: "overview",
  instances: "server",
  acp: "cpu",
  gpu: "chart",
  activity: "history",
})) {
  const button = document.querySelector(`[data-tab="${tabName}"]`);
  button.insertAdjacentHTML("afterbegin", icon(iconName));
}
$("#add-instance").insertAdjacentHTML("afterbegin", icon("plus"));
$("#acp-edit").insertAdjacentHTML("afterbegin", icon("settings"));
$("#gpu-edit").insertAdjacentHTML("afterbegin", icon("settings"));
const badge = (s) => stateBadge(s, acpLabels);
const jobDuration = (j) =>
  !j?.start_time
    ? "尚未开始"
    : ["SUSPENDED", "STOPPED", "FAILED", "DELETED", "SUCCEEDED"].includes(
          j.state,
        ) && !j.complete_time
      ? "结束时间未提供"
      : duration(
          new Date(j.start_time) / 1000,
          j.complete_time
            ? new Date(j.complete_time) / 1000
            : Date.now() / 1000,
        );
const scheduleLine = (c, a) =>
  `<div class="setting"><span>定时${a === "start" ? "启动" : "停止"}</span><time>${esc(c.schedule[a].time)}</time><span class="${c.schedule[a].enabled ? "enabled" : "disabled"}">${c.schedule[a].enabled ? "已开启" : "已关闭"}</span></div>`;
let acpBusy = false,
  detailName = null,
  historyLimit = 8,
  historyQuery = "";
$("#acp-search").oninput = (e) => {
  historyQuery = e.target.value.trim().toLowerCase();
  historyLimit = 8;
  renderWorkspace();
};
window.renderWorkspace = () => {
  if (!data) return;
  const a = data.acp;
  $("#overview-content").innerHTML =
    `<div class="summary-grid"><div><small>CCI 运行中</small><strong>${data.instances.filter((i) => i.observed.state === "RUNNING").length} / ${data.instances.length}</strong></div><div><small>ACP 运行中</small><strong>${a?.jobs.filter((j) => j.state === "RUNNING").length ?? "—"}</strong></div><div><small>实例采样</small><strong>${data.status_interval_seconds}<small>秒</small></strong></div></div>` +
    data.instances
      .map(
        (i) =>
          `<div class="resource-row"><div><strong>${esc(i.config.display_name)}</strong><small>${esc(i.config.resources.spec)}</small></div>${badge(i.observed.state)}<button data-edit="${esc(i.key)}">设置</button></div>`,
      )
      .join("");
  if (!a) {
    $("#acp-content").innerHTML = '<p class="empty">ACP 服务尚未加载</p>';
    return;
  }
  const c = a.config,
    j = a.current_job || a.jobs.find((x) => x.name === a.state.current),
    busy = acpBusy || !!a.operation;
  const filteredJobs = a.jobs.filter((x) =>
    `${x.name} ${x.display_name}`.toLowerCase().includes(historyQuery),
  );
  const canStop =
    !acpBusy &&
    (!a.operation || a.operation.action === "start") &&
    !a.state.stop_requested;
  const active =
    j &&
    !["SUCCEEDED", "SUSPENDED", "STOPPED", "FAILED", "DELETED"].includes(
      j.state,
    );
  $("#acp-content").innerHTML =
    `<div class="acp-current"><div class="card-head"><div><h2>${esc(j?.display_name || "4 GPU 工作环境")}</h2><p class="resources">4 GPU · 56 vCPU · 792 GiB · ${esc(c.cluster)}</p></div>${badge(j?.state || "尚未创建")}</div><div class="facts"><div><small>当前运行时间</small><strong>${jobDuration(j)}</strong></div><div><small>单次时长</small><strong>${c.hours} 小时</strong></div><div><small>计划用量（估算）</small><strong>${c.hours * 4} / 128 卡时</strong></div></div>${a.error ? `<p class="alert">采样失败：${esc(a.error)}</p>` : ""}${a.state.failure ? `<p class="alert">自动操作已暂停：${esc(a.state.failure)}</p>` : ""}${a.state.paused ? '<p class="hint">人工暂停</p>' : ""}${a.state.pending ? `<p class="alert">提交结果待确认：${esc(a.state.pending.name)}</p>` : ""}<div class="acp-actions"><button class="primary" data-acp-action="start" ${busy || active || a.state.pending ? "disabled" : ""}>${icon("play")}启动新任务</button>${active || a.operation?.action === "start" ? `<button data-acp-action="stop" ${canStop ? "" : "disabled"}>${a.state.stop_requested ? "正在取消…" : a.operation?.action === "start" ? "取消启动并停止" : "停止并暂停"}</button>` : ""}${a.state.failure || a.state.paused ? `<button data-acp-action="resume" ${busy ? "disabled" : ""}>${j?.state === "FAILED" ? "确认失败并恢复调度" : "恢复自动运行"}</button>` : ""}</div>${a.operation ? `<p class="hint">执行中 · ${esc(actions[a.operation.action] || a.operation.action)}</p>` : ""}<p class="hint">最近采样 ${age(a.sampled_at)}</p></div><div class="acp-columns"><div><div class="section-title"><h3>调度</h3></div>${scheduleLine(c, "start")}${scheduleLine(c, "stop")}<div class="setting"><span>结束后自动启动</span><span class="${c.auto_restart ? "enabled" : "disabled"}">${c.auto_restart ? "已开启" : "已关闭"}</span></div></div><div><div class="section-title"><h3>当前 Worker</h3>${j ? `<button data-workers="${esc(j.name)}">刷新地址</button>` : ""}</div><div>${j ? workersHTML(a.workers[j.name], !active) : '<p class="empty">尚无当前任务</p>'}</div></div></div><div class="section-title"><h3>任务历史</h3><small>${filteredJobs.length} 个任务</small></div><div class="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>运行时长</th><th>创建时间</th></tr></thead><tbody>${filteredJobs
      .slice(0, historyLimit)
      .map(
        (x) =>
          `<tr><td><button data-job="${esc(x.name)}">${esc(x.display_name || x.name)}</button><small>${esc(x.name)}</small></td><td>${badge(x.state)}</td><td>${jobDuration(x)}</td><td>${fmt(x.create_time)}</td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div>${filteredJobs.length === 0 ? '<p class="empty">没有匹配的任务</p>' : ""}<div class="history-footer"><small>第 ${a.history_page || 1} 页 · 显示 ${Math.min(historyLimit, filteredJobs.length)} / ${filteredJobs.length} 个任务</small><div class="heading-actions">${a.history_page > 1 ? `<button data-history-page="${a.history_page - 1}">上一页</button>` : ""}${a.history_more ? `<button data-history-page="${(a.history_page || 1) + 1}">下一页</button>` : ""}</div>${filteredJobs.length > 8 ? `<button class="text-button" data-history-more>${historyLimit === 8 ? "查看全部任务" : "收起历史"}</button>` : ""}</div>`;
  const events = [
    ...data.instances
      .filter((i) => i.operation)
      .map((i) => ({
        timestamp: i.operation.created_at,
        message: i.operation.message,
        name: i.config.display_name,
      })),
    ...a.events.map((e) => ({ ...e, name: "ACP" })),
  ].sort((x, y) => new Date(y.timestamp) - new Date(x.timestamp));
  $("#activity-content").innerHTML =
    events
      .map(
        (e) =>
          `<div class="activity-row"><small>${fmt(e.timestamp)} · ${esc(e.name)}</small>${esc(e.message)}</div>`,
      )
      .join("") || '<p class="empty">暂无操作记录</p>';
};
function workersHTML(value, historical) {
  return value
    ? `${value.error ? `<p class="error">地址采样失败：${esc(value.error)}（以下为旧数据）</p>` : ""}<p class="hint">${historical ? "历史任务地址 · " : ""}采样 ${fmt(value.sampled_at)} · Pod IP 为集群内网地址</p>` +
        value.items
          .map(
            (w) =>
              `<div class="worker"><strong>${esc(w.name)}</strong><small>${esc(w.state)}</small><p>Pod IP <span class="ip">${esc(w.pod_ip || "待分配")}</span> ${w.pod_ip ? `<button class="icon-button" data-ip="${esc(w.pod_ip)}" title="复制 Pod IP" aria-label="复制 Pod IP">${icon("copy")}</button>` : ""}</p><p>Host IP <span class="ip">${esc(w.host_ip || "待分配")}</span></p></div>`,
          )
          .join("") +
        (value.items.length
          ? ""
          : '<p class="empty">平台尚未返回 Worker 地址</p>')
    : '<p class="empty">尚未采样 Worker</p>';
}
$("#edit-dialog").addEventListener("close", () => {
  render();
});
$("#acp-edit").onclick = () => {
  const c = data?.acp?.config;
  if (!c) return;
  $("#acp-dialog").innerHTML =
    `<h2>ACP 环境设置</h2><form id="acp-form" class="edit-form"><h3>调度</h3>${["start", "stop"].map((a) => `<div class="row"><label class="switch"><input name="${a}_enabled" type="checkbox" ${c.schedule[a].enabled ? "checked" : ""}>定时${a === "start" ? "启动" : "停止"}</label><label>每天时间<input name="${a}_time" type="time" value="${c.schedule[a].time}" required></label></div>`).join("")}<label class="switch"><input name="auto_restart" type="checkbox" ${c.auto_restart ? "checked" : ""}>结束后自动启动</label><label>单次运行时长（小时）<input name="hours" type="number" min="1" max="30" value="${c.hours}" required></label><label>启动就绪超时（分钟）<input name="ready_timeout_minutes" type="number" min="1" max="180" value="${c.ready_timeout_minutes}" required></label><h3>资源</h3><p class="hint">1 Worker · 4 GPU · 56 vCPU · 792 GiB</p><label>集群<input name="cluster" value="${esc(c.cluster)}" required></label><label>镜像<input name="image" value="${esc(c.image)}" required></label><details><summary>启动命令与挂载</summary><pre>bash /data/260010081/ops/acp-start.sh --hours ${c.hours}\n${esc(c.mount)}\n${esc(c.worker_spec)}</pre></details><p class="hint">定时停止、人工暂停和失败暂停优先。新任务会重新排队，训练进程不会自动续接。</p><p id="acp-form-error" class="error" hidden></p><div class="form-actions"><button type="button" id="acp-cancel">取消</button><button type="submit" class="primary">保存</button></div></form>`;
  $("#acp-dialog").showModal();
  $("#acp-cancel").onclick = () => $("#acp-dialog").close();
  $("#acp-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target),
      b = e.target.querySelector("[type=submit]");
    b.disabled = true;
    try {
      await api("/api/acp/config", {
        hours: Number(f.get("hours")),
        ready_timeout_minutes: Number(f.get("ready_timeout_minutes")),
        image: f.get("image"),
        cluster: f.get("cluster"),
        auto_restart: f.has("auto_restart"),
        schedule: {
          start: { enabled: f.has("start_enabled"), time: f.get("start_time") },
          stop: { enabled: f.has("stop_enabled"), time: f.get("stop_time") },
        },
      });
      $("#acp-dialog").close();
      toast("ACP 设置已保存");
      await refresh();
    } catch (err) {
      $("#acp-form-error").textContent = err.message;
      $("#acp-form-error").hidden = false;
    } finally {
      b.disabled = false;
    }
  };
};
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try {
    if (b.dataset.historyPage) {
      b.disabled = true;
      try {
        await api("/api/acp/history?page=" + b.dataset.historyPage);
        historyLimit = 8;
        await refresh();
      } finally {
        b.disabled = false;
      }
    }
    if (b.hasAttribute("data-history-more")) {
      historyLimit = historyLimit === 8 ? 100 : 8;
      renderWorkspace();
    }
    if (b.dataset.acpAction) {
      acpBusy = true;
      renderWorkspace();
      try {
        await api("/api/acp/actions/" + b.dataset.acpAction, {});
        await refresh();
      } finally {
        acpBusy = false;
        renderWorkspace();
      }
    }
    if (b.dataset.ip) {
      await navigator.clipboard.writeText(b.dataset.ip);
      toast("Pod IP 已复制");
    }
    if (b.dataset.workers) {
      b.disabled = true;
      const name = b.dataset.workers;
      try {
        const value = await api(
          "/api/acp/workers?name=" + encodeURIComponent(name),
        );
        data.acp.workers[name] = value;
        renderWorkspace();
        if (detailName === name)
          $("#detail-workers").innerHTML = workersHTML(value, true);
      } finally {
        b.disabled = false;
      }
    }
    if (b.dataset.job) {
      detailName = b.dataset.job;
      const j = data.acp.jobs.find((x) => x.name === detailName);
      $("#acp-detail-content").innerHTML =
        `<h3>${esc(j.display_name)}</h3><p>${badge(j.state)}</p><p class="hint">${esc(j.name)} · ${fmt(j.start_time)}</p><div id="detail-workers">${workersHTML(data.acp.workers[j.name], ["SUCCEEDED", "SUSPENDED", "FAILED"].includes(j.state))}</div><button data-workers="${esc(j.name)}">查询 Worker / Pod IP</button><details><summary>启动命令</summary><pre>${esc(j.command)}</pre></details><button data-job-logs="${esc(j.name)}">读取任务日志</button><pre id="detail-logs"></pre>`;
      $("#acp-detail").showModal();
    }
    if (b.dataset.jobLogs) {
      b.disabled = true;
      try {
        const result = await api(
          "/api/acp/logs?name=" + encodeURIComponent(b.dataset.jobLogs),
        );
        $("#detail-logs").textContent =
          (result.partial ? "日志快照（已限时截取）\n" : "") +
          (result.text || "暂无日志");
      } finally {
        b.disabled = false;
      }
    }
  } catch (err) {
    toast(err.message);
  }
});
