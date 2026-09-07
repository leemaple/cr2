"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  user: null,
  route: "dashboard",
  projects: [],
  datasets: [],
  profiles: [],
  runs: [],
  selectedRun: null,
  resultTab: "ranking",
  busy: false,
};
const routeNames = {
  dashboard: "工作台",
  projects: "项目空间",
  datasets: "向量数据",
  profiles: "参数配置",
  runs: "检索任务",
  compare: "对比评测",
  audit: "审计记录",
  system: "系统维护",
  result: "任务结果",
};
const statuses = {
  pending: ["待执行", "neutral"],
  running: ["运行中", "warning"],
  completed: ["已完成", "success"],
  failed: ["失败", "error"],
  cancelled: ["已取消", "neutral"],
  active: ["进行中", "success"],
  archived: ["已归档", "neutral"],
};
const actionNames = {
  "auth.setup": "初始化管理员",
  "auth.login": "登录系统",
  "auth.failed": "登录失败",
  "auth.logout": "退出系统",
  "auth.password": "修改口令",
  "project.create": "创建项目",
  "project.update": "更新项目",
  "project.delete": "删除空项目",
  "dataset.create": "创建数据集",
  "dataset.delete": "删除数据集",
  "profile.create": "创建参数配置",
  "profile.delete": "删除参数配置",
  "run.create": "创建任务",
  "run.start": "开始执行",
  "run.complete": "任务完成",
  "run.fail": "任务失败",
  "run.cancel": "取消任务",
  "run.recover": "恢复中断任务",
  "report.export": "导出或预览报告",
  "system.backup": "数据库备份",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}
const e = escapeHtml;

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined) return "不适用";
  if (!Number.isFinite(Number(value))) return "无效值";
  const number = Number(value);
  if (number !== 0 && Math.abs(number) < 0.001) return number.toExponential(3);
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "无效日期";
  return date.toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatBytes(value) {
  const size = Number(value);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}

function badge(status) {
  const [label, kind] = statuses[status] || [status, "neutral"];
  return `<span class="badge ${e(kind)}">${e(label)}</span>`;
}

function engineBadge(engine) {
  return engine === "ckks"
    ? '<span class="badge success">真实 CKKS</span>'
    : '<span class="badge neutral">明文基线</span>';
}

function toast(message, error = false) {
  const node = document.createElement("div");
  node.className = "toast" + (error ? " error" : "");
  node.textContent = message;
  $("#toast-region").append(node);
  setTimeout(() => node.remove(), 6500);
}

function clearToasts() {
  $("#toast-region").replaceChildren();
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (state.user) headers["X-CSRF-Token"] = state.user.csrf;
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch (_) {
      // An interrupted or non-JSON response must not hide its HTTP status.
    }
    if (response.status === 401 && path !== "/api/login") showLogin();
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function send(path, body = {}, method = "POST") {
  return api(path, { method, body: JSON.stringify(body) });
}

async function download(path, filename) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "导出失败");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  toast(`已导出 ${filename}`);
}

function showLogin() {
  state.user = null;
  state.selectedRun = null;
  $("#workspace").hidden = true;
  $("#login-screen").hidden = false;
  $("#busy-overlay").hidden = true;
  if ($("#editor-dialog").open) $("#editor-dialog").close();
  $("#login-password").value = "";
}

async function showWorkspace() {
  $("#login-screen").hidden = true;
  $("#workspace").hidden = false;
  $("#session-user").textContent = state.user.username;
  const engine = await api("/api/engine");
  const indicator = $("#engine-badge");
  indicator.textContent = engine.ckks_available ? "CKKS 引擎就绪" : "CKKS 未安装";
  indicator.className = "badge " + (engine.ckks_available ? "success" : "warning");
  await navigate("dashboard");
}

function pageHeading(title, description, actions = "") {
  return `<div class="page-heading">
    <div><p class="eyebrow">VECTOR RETRIEVAL WORKBENCH</p>
    <h1>${e(title)}</h1><p class="muted">${e(description)}</p></div>
    <div class="actions">${actions}</div>
  </div>`;
}

function button(action, text, identifier = "", className = "") {
  return `<button type="button" class="${e(className)}"
    data-action="${e(action)}" data-id="${e(identifier)}">${e(text)}</button>`;
}

function emptyState(title, description) {
  return `<div class="empty-state"><span class="empty-icon">▦</span>
    <h3>${e(title)}</h3><p>${e(description)}</p></div>`;
}

function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>
    ${headers.map((text) => `<th>${e(text)}</th>`).join("")}
    </tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function details(items, wide = false) {
  return `<dl class="detail-grid ${wide ? "wide-label" : ""}">
    ${items.map(([key, value]) => `<dt>${e(key)}</dt><dd>${e(value)}</dd>`).join("")}
    </dl>`;
}

function options(items, selected = "", placeholder = "请选择") {
  return `<option value="">${e(placeholder)}</option>` + items.map((item) =>
    `<option value="${e(item.id)}" ${item.id === selected ? "selected" : ""}>
    ${e(item.name)}</option>`
  ).join("");
}

function field(name, label, type = "text", value = "", attributes = "") {
  return `<div><label for="field-${e(name)}">${e(label)}</label>
    <input id="field-${e(name)}" name="${e(name)}" type="${e(type)}"
    value="${e(value)}" ${attributes}></div>`;
}

function formFooter(text = "保存") {
  return `<p id="form-error" class="error-text" role="alert"></p>
    <div class="form-actions">
    ${button("close-dialog", "取消")}
    <button type="submit" class="primary" id="form-save">${e(text)}</button></div>`;
}

function dialog(title, content, onSubmit = null, wide = false) {
  const element = $("#editor-dialog");
  element.classList.toggle("wide", wide);
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = content;
  if (!element.open) element.showModal();
  const form = $("form", element);
  if (form && onSubmit) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = $("[type=submit]", form);
      const error = $("#form-error", form);
      error.textContent = "";
      submit.disabled = true;
      try {
        await onSubmit(new FormData(form));
      } catch (exception) {
        error.textContent = exception.message;
      } finally {
        submit.disabled = false;
      }
    });
  }
}

function closeDialog() {
  $("#editor-dialog").close();
}

async function loadLookups() {
  [state.projects, state.datasets, state.profiles] = await Promise.all([
    api("/api/projects"),
    api("/api/datasets"),
    api("/api/profiles"),
  ]);
}

async function navigate(route) {
  if (state.busy) return;
  state.route = route;
  $("#breadcrumb-current").textContent = routeNames[route] || "工作台";
  $$("#navigation button").forEach((item) => {
    const active = item.dataset.route === (route === "result" ? "runs" : route);
    item.classList.toggle("active", active);
    item.setAttribute("aria-current", active ? "page" : "false");
  });
  const views = {
    dashboard: renderDashboard,
    projects: renderProjects,
    datasets: renderDatasets,
    profiles: renderProfiles,
    runs: renderRuns,
    compare: renderCompare,
    audit: renderAudit,
    system: renderSystem,
    result: renderResult,
  };
  try {
    await (views[route] || renderDashboard)();
  } catch (exception) {
    toast(exception.message, true);
  }
}

function runRows(runs, brief = false) {
  return runs.map((run) => `<tr>
    <td><strong>${e(run.name)}</strong><small>${e(run.dataset_name)}</small></td>
    <td>${engineBadge(run.engine)}</td><td>${badge(run.status)}</td>
    <td>${e(formatDate(run.created_at))}</td>
    <td><div class="actions">
    ${run.status === "pending" ? button("execute-run", "运行", run.id, "small-button primary") : ""}
    ${button("view-run", "详情", run.id, "small-button")}
    ${!brief ? button("clone-run", "复制", run.id, "small-button") : ""}
    ${!brief && run.status === "pending" ? button("cancel-run", "取消", run.id, "small-button") : ""}
    </div></td></tr>`);
}

async function renderDashboard() {
  const data = await api("/api/dashboard");
  const count = data.counts;
  const cards = [
    ["项目空间", count.projects, "PROJECTS", "按课题组织检索实验"],
    ["向量数据", count.vectors, "VECTORS", `${count.datasets} 个不可变数据集`],
    ["检索任务", count.runs, "RETRIEVAL RUNS", `${count.completed} 个已完成任务`],
    ["参数配置", count.profiles, "PROFILES", "CKKS / 明文参考基线"],
  ];
  const flow = [
    ["01", "导入向量", "格式与维度校验"],
    ["02", "选择配置", "度量与精度预设"],
    ["03", "同态评分", "真实密文点积"],
    ["04", "核对结果", "误差与Recall@K"],
    ["05", "归档报告", "结果与输入摘要"],
  ];
  $("#main-content").innerHTML = pageHeading(
    "检索评测工作台", "从向量数据到可核验结果，集中管理每一次检索实验。",
    button("new-project", "+ 新建项目", "", "primary")
  ) + `<div class="metrics-grid">${cards.map(([title, value, label, note]) =>
    `<div class="metric-card"><small>${e(title)}</small><strong>${e(value)}</strong>
    <span class="metric-label">${e(label)}</span><p>${e(note)}</p></div>`
  ).join("")}</div>
  <section class="card"><div class="card-heading"><h2>实验工作流</h2>
  <span class="badge ${data.engine.ckks_available ? "success" : "warning"}">
  ${data.engine.ckks_available ? "TenSEAL " + e(data.engine.tenseal_version) : "请安装加密引擎"}
  </span></div><div class="workflow-track">${flow.map(([index, title, note]) =>
    `<div class="workflow-step"><b>${index}</b><strong>${title}</strong><small>${note}</small></div>`
  ).join("")}</div></section>
  <div class="columns"><section class="card"><div class="card-heading">
  <h2>最近检索任务</h2>${button("go-runs", "查看全部", "", "quiet")}</div>
  ${data.recent_runs.length ? table(["任务", "引擎", "状态", "创建时间", "操作"],
    runRows(data.recent_runs, true)) : emptyState("尚无检索任务", "创建项目和数据集后，即可开始首次检索。")}
  </section><section class="card"><h2>最近操作</h2><ul class="event-list">
  ${data.recent_audit.map((item) => `<li><span>${e(actionNames[item.action] || item.action)}</span>
    <small>${e(formatDate(item.timestamp))}</small></li>`).join("")}</ul>
  <div class="notice compact"><strong>本地可信边界</strong><br>
  原始向量在本机保存，CKKS得分解密后排序。不宣称远程数据隔离或全密态Top-K。</div>
  </section></div>`;
}

async function renderProjects() {
  state.projects = await api("/api/projects");
  $("#main-content").innerHTML = pageHeading(
    "项目空间", "组织独立课题。归档保留数据与结果，空项目可以删除。",
    button("new-project", "+ 新建项目", "", "primary")
  ) + `<section class="card"><div class="filter-row">
  <input id="project-search" placeholder="搜索项目名称或说明" aria-label="搜索项目">
  <span class="muted">共 ${state.projects.length} 个项目</span></div>
  <div id="project-table"></div></section>`;
  drawProjectTable("");
  $("#project-search").addEventListener("input", (event) => drawProjectTable(event.target.value));
}

function drawProjectTable(keyword) {
  const projects = state.projects.filter((item) =>
    (item.name + " " + item.description).toLowerCase().includes(keyword.toLowerCase())
  );
  $("#project-table").innerHTML = projects.length ? table(
    ["项目名称", "状态", "数据集", "任务", "更新时间", "操作"],
    projects.map((item) => `<tr><td><strong>${e(item.name)}</strong>
    <small>${e(item.description || "暂无说明")}</small></td><td>${badge(item.status)}</td>
    <td>${item.dataset_count}</td><td>${item.run_count}</td><td>${e(formatDate(item.updated_at))}</td>
    <td><div class="actions">${button("edit-project", "编辑", item.id, "small-button")}
    ${button("delete-project", "删除", item.id, "small-button danger")}</div></td></tr>`)
  ) : emptyState("没有匹配的项目", "新建一个项目，开始组织向量检索实验。");
}

async function editProject(identifier = "") {
  if (!state.projects.length) state.projects = await api("/api/projects");
  const item = state.projects.find((project) => project.id === identifier);
  const status = item ? `<label for="field-status">项目状态</label>
    <select name="status" id="field-status">
    <option value="active" ${item.status === "active" ? "selected" : ""}>进行中</option>
    <option value="archived" ${item.status === "archived" ? "selected" : ""}>已归档</option>
    </select><p class="field-help">归档后禁止新增数据集、任务和执行任务，已有结果可查看。</p>` : "";
  dialog(item ? "编辑项目" : "新建项目", `<form id="project-form">
    ${field("name", "项目名称", "text", item?.name || "", 'required minlength="2" maxlength="60"')}
    <label for="field-description">项目说明</label>
    <textarea id="field-description" name="description" maxlength="500"
    placeholder="描述本次实验用途，勿录入敏感信息">${e(item?.description || "")}</textarea>
    ${status}${formFooter("保存项目")}</form>`, async (form) => {
      const body = { name: form.get("name"), description: form.get("description") };
      if (item) body.status = form.get("status");
      await send(item ? `/api/projects/${item.id}` : "/api/projects", body, item ? "PUT" : "POST");
      closeDialog();
      toast(item ? "项目已更新" : "项目已创建");
      await navigate("projects");
    });
}

async function renderDatasets() {
  await loadLookups();
  $("#main-content").innerHTML = pageHeading(
    "向量数据", "支持JSON / CSV导入与可复现合成数据；每个数据集保留SHA-256摘要。",
    button("import-dataset", "导入数据") + button("new-dataset", "+ 合成数据集", "", "primary")
  ) + `<section class="card"><div class="filter-row">
  <select id="dataset-project-filter" aria-label="筛选所属项目">
  ${options(state.projects, "", "全部项目")}</select>
  <input id="dataset-search" placeholder="搜索数据集名称" aria-label="搜索数据集">
  </div><div id="dataset-table"></div></section>
  <div class="notice"><strong>数据范围：</strong>每个数据集1至64条记录，每条2至128维；
  元素必须为[-1,1]内的有限实数。数据集创建后不可改写，变更请以新名称重新导入。</div>`;
  const redraw = () => drawDatasetTable(
    $("#dataset-project-filter").value, $("#dataset-search").value
  );
  $("#dataset-project-filter").addEventListener("change", redraw);
  $("#dataset-search").addEventListener("input", redraw);
  redraw();
}

function drawDatasetTable(project, keyword) {
  const datasets = state.datasets.filter((item) =>
    (!project || item.project_id === project) && item.name.includes(keyword)
  );
  $("#dataset-table").innerHTML = datasets.length ? table(
    ["数据集", "所属项目", "规模", "内容摘要", "创建时间", "操作"],
    datasets.map((item) => `<tr><td><strong>${e(item.name)}</strong>
    <small>${e(item.id)}</small></td><td>${e(item.project_name)}</td>
    <td>${item.row_count} 条 × ${item.dimension} 维</td>
    <td><code title="${e(item.content_hash)}">${e(item.content_hash.slice(0, 12))}…</code></td>
    <td>${e(formatDate(item.created_at))}</td><td><div class="actions">
    ${button("view-dataset", "查看", item.id, "small-button")}
    ${button("export-dataset", "导出", item.id, "small-button")}
    ${button("delete-dataset", "删除", item.id, "small-button danger")}
    </div></td></tr>`)
  ) : emptyState("暂无数据集", "选择合成数据或导入符合规范的向量文件。");
}

async function newDataset() {
  await loadLookups();
  const active = state.projects.filter((item) => item.status === "active");
  if (!active.length) throw new Error("请先创建一个进行中的项目");
  dialog("生成合成向量数据集", `<form id="dataset-form">
    <div class="form-grid"><div class="span-all"><label for="field-project">所属项目</label>
    <select id="field-project" name="project_id" required>${options(active)}</select></div>
    <div class="span-all">${field("name", "数据集名称", "text", "",
      'required minlength="2" maxlength="60"')}</div>
    ${field("dimension", "向量维度", "number", 8, 'required min="2" max="128" step="1"')}
    ${field("count", "记录数量", "number", 12, 'required min="2" max="64" step="1"')}
    <div class="span-all">${field("seed", "随机种子", "number", 2026,
      'required min="0" max="2147483647" step="1"')}</div></div>
    <div class="notice compact">合成数据不代表真实文本嵌入。相同种子、规模与维度可复现相同向量。</div>
    ${formFooter("生成并保存")}</form>`, async (form) => {
      await send("/api/datasets/generate", {
        project_id: form.get("project_id"),
        name: form.get("name"),
        dimension: Number(form.get("dimension")),
        count: Number(form.get("count")),
        seed: Number(form.get("seed")),
      });
      closeDialog();
      toast("合成数据集已保存");
      await navigate("datasets");
    });
}

async function importDataset() {
  await loadLookups();
  const active = state.projects.filter((item) => item.status === "active");
  if (!active.length) throw new Error("请先创建一个进行中的项目");
  const example = JSON.stringify([
    { id: "a", label: "样本A", vector: [1, 0, 0, 0], tags: ["示例"] },
    { id: "b", label: "样本B", vector: [0.8, 0.6, 0, 0], tags: ["示例"] },
    { id: "c", label: "样本C", vector: [0, 0, 1, 0], tags: ["对照"] },
  ], null, 2);
  dialog("导入向量数据", `<form id="import-form">
    <div class="form-grid"><div><label for="field-project">所属项目</label>
    <select id="field-project" name="project_id" required>${options(active)}</select></div>
    ${field("name", "数据集名称", "text", "", 'required minlength="2" maxlength="60"')}
    </div><label for="field-format">文件格式</label>
    <select id="field-format" name="format"><option value="json">JSON</option>
    <option value="csv">CSV</option></select>
    <label for="import-file">选择文件（也可直接编辑下方内容）</label>
    <input id="import-file" type="file" accept=".json,.csv">
    <label for="field-content">导入内容</label>
    <textarea id="field-content" name="content" class="mono" rows="9" required>${e(example)}</textarea>
    <p class="field-help">JSON为记录数组；CSV表头固定为id,label,vector,tags，
    向量列为JSON数组，标签用分号分隔。</p>
    ${formFooter("校验并导入")}</form>`, async (form) => {
      await send("/api/datasets/import", {
        project_id: form.get("project_id"),
        name: form.get("name"),
        format: form.get("format"),
        content: form.get("content"),
      });
      closeDialog();
      toast("数据导入成功，格式与维度校验通过");
      await navigate("datasets");
    }, true);
  $("#import-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 1000000) {
      $("#form-error").textContent = "文件不能超过1MB";
      return;
    }
    $("#field-content").value = await file.text();
    $("#field-format").value = file.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
  });
}

async function viewDataset(identifier) {
  const data = await api(`/api/datasets/${identifier}`);
  const stats = data.statistics;
  dialog("数据集详情 · " + data.name, `<div class="score-grid">
    <div class="score-item"><small>记录数量</small><strong>${stats.rows}</strong></div>
    <div class="score-item"><small>向量维度</small><strong>${stats.dimension}</strong></div>
    <div class="score-item"><small>平均范数</small><strong>${formatNumber(stats.mean_norm)}</strong></div>
    </div>${details([
      ["数据范围", `${formatNumber(stats.minimum)} 至 ${formatNumber(stats.maximum)}`],
      ["零 / 近零向量", stats.zero_vectors],
      ["完整SHA-256", data.content_hash],
    ])}<div class="pill-list">${Object.entries(stats.tags).map(([tag, count]) =>
      `<span class="pill">${e(tag)} · ${count}</span>`).join("")}</div>
    ${table(["ID", "标签", "向量", "分类"], data.records.map((item) =>
      `<tr><td>${e(item.id)}</td><td>${e(item.label)}</td>
      <td class="mono">${e(JSON.stringify(item.vector))}</td>
      <td>${e(item.tags.join(" / "))}</td></tr>`))}
    <div class="form-actions">${button("export-dataset", "导出CSV", identifier, "primary")}
    ${button("close-dialog", "关闭")}</div>`, null, true);
}

async function renderProfiles() {
  [state.profiles, state.engine] = await Promise.all([
    api("/api/profiles"), api("/api/engine"),
  ]);
  $("#main-content").innerHTML = pageHeading(
    "参数配置", "固定预设限制误配；配置被任务引用后不可删除。",
    button("new-profile", "+ 新建配置", "", "primary")
  ) + `<div class="columns equal"><div class="card flat"><h2>CKKS 标准精度</h2>
    <p class="muted">环维度8192 · 模数链60/40/40/60 · 缩放2^40</p>
    <span class="badge success">balanced · 误差阈值1e-4</span></div>
    <div class="card flat"><h2>CKKS 紧凑精度</h2>
    <p class="muted">环维度8192 · 模数链50/30/30/50 · 缩放2^30</p>
    <span class="badge neutral">compact · 误差阈值1e-2</span></div></div>
    <section class="card"><div class="card-heading"><h2>已保存配置</h2>
    <span class="muted">${state.profiles.length} 项</span></div>
    ${state.profiles.length ? table(["配置名称", "引擎", "预设", "度量", "创建时间", "操作"],
      state.profiles.map((item) => `<tr><td><strong>${e(item.name)}</strong></td>
      <td>${engineBadge(item.engine)}</td><td>${e(item.preset)}</td>
      <td>${item.metric === "cosine" ? "余弦相似度" : "内积"}</td>
      <td>${e(formatDate(item.created_at))}</td>
      <td>${button("delete-profile", "删除", item.id, "small-button danger")}</td></tr>`))
      : emptyState("尚无参数配置", "创建CKKS配置进行真实同态计算，或创建明文基线配置。")}
    </section><div class="notice">参数预设适用于本软件限定的小规模向量评测。
    精度通过仅表示本次输出误差不超过阈值，不代表密码安全等级认证。</div>`;
}

async function newProfile() {
  dialog("新建检索配置", `<form id="profile-form">
    ${field("name", "配置名称", "text", "", 'required minlength="2" maxlength="60"')}
    <div class="form-grid"><div><label for="field-engine">计算引擎</label>
    <select id="field-engine" name="engine"><option value="ckks">真实CKKS同态计算</option>
    <option value="plain">明文参考基线</option></select></div>
    <div><label for="field-metric">相似度度量</label>
    <select id="field-metric" name="metric"><option value="cosine">余弦相似度</option>
    <option value="dot">内积</option></select></div></div>
    <label for="field-preset">参数预设</label><select id="field-preset" name="preset"></select>
    <div id="profile-description" class="notice compact"></div>
    ${formFooter("保存配置")}</form>`, async (form) => {
      await send("/api/profiles", Object.fromEntries(form.entries()));
      closeDialog();
      toast("检索配置已保存");
      await navigate("profiles");
    });
  const refreshPresets = () => {
    const engine = $("#field-engine").value;
    $("#field-preset").innerHTML = engine === "ckks"
      ? '<option value="balanced">balanced / 标准精度</option>' +
        '<option value="compact">compact / 紧凑精度</option>'
      : '<option value="baseline">baseline / 明文参考</option>';
    refreshDescription();
  };
  const refreshDescription = () => {
    const preset = $("#field-preset").value;
    const descriptions = {
      balanced: "CKKS：8192 / [60,40,40,60] / 2^40。密态点积后解密排序；不回退为模拟结果。",
      compact: "CKKS：8192 / [50,30,30,50] / 2^30。较低缩放位数用于精度对比，误差阈值1e-2。",
      baseline: "明文基线：使用math.fsum计算参考分数，不生成密文；与CKKS结果明确区分。",
    };
    $("#profile-description").textContent = descriptions[preset];
  };
  $("#field-engine").addEventListener("change", refreshPresets);
  $("#field-preset").addEventListener("change", refreshDescription);
  refreshPresets();
}

async function renderRuns() {
  await loadLookups();
  state.runs = await api("/api/runs");
  $("#main-content").innerHTML = pageHeading(
    "检索任务", "输入快照固定保存；已完成任务不可重跑，重复实验请复制。",
    button("new-run", "+ 新建任务", "", "primary")
  ) + `<section class="card"><div class="filter-row">
    <select id="run-project-filter" aria-label="任务项目筛选">
    ${options(state.projects, "", "全部项目")}</select>
    <select id="run-status-filter" aria-label="任务状态筛选"><option value="">全部状态</option>
    ${["pending", "completed", "failed", "cancelled"].map((key) =>
      `<option value="${key}">${statuses[key][0]}</option>`).join("")}</select>
    <input id="run-search" aria-label="搜索任务" placeholder="搜索任务名称"></div>
    <div id="run-table"></div></section>`;
  const redraw = () => {
    const project = $("#run-project-filter").value;
    const status = $("#run-status-filter").value;
    const name = $("#run-search").value;
    const rows = state.runs.filter((item) =>
      (!project || item.project_id === project) &&
      (!status || item.status === status) && item.name.includes(name)
    );
    $("#run-table").innerHTML = rows.length
      ? table(["任务名称", "引擎", "状态", "创建时间", "操作"], runRows(rows))
      : emptyState("暂无符合条件的任务", "选择数据集与配置，新建一次可追溯的检索任务。");
  };
  $("#run-project-filter").addEventListener("change", redraw);
  $("#run-status-filter").addEventListener("change", redraw);
  $("#run-search").addEventListener("input", redraw);
  redraw();
}

async function newRun() {
  await loadLookups();
  if (!state.datasets.length || !state.profiles.length) {
    throw new Error("请先创建数据集和参数配置");
  }
  const activeIds = new Set(
    state.projects.filter((item) => item.status === "active").map((item) => item.id)
  );
  const datasets = state.datasets.filter((item) => activeIds.has(item.project_id));
  if (!datasets.length) throw new Error("没有进行中项目的数据集");
  dialog("新建检索任务", `<form id="run-form">
    ${field("name", "任务名称", "text", "", 'required minlength="2" maxlength="80"')}
    <div class="form-grid"><div><label for="field-dataset">向量数据集</label>
    <select id="field-dataset" name="dataset_id" required>${options(datasets)}</select></div>
    <div><label for="field-profile">参数配置</label>
    <select id="field-profile" name="profile_id" required>${options(state.profiles)}</select></div></div>
    <label for="field-query">查询向量（JSON数组）</label>
    <textarea id="field-query" name="query" rows="3" class="mono" required
    placeholder="先选择数据集，再输入同维度查询向量"></textarea>
    <div class="actions">${button("sample-query", "使用数据集首条向量")}</div>
    <div class="form-grid">${field("top_k", "返回数量 Top-K", "number", 5,
      'required min="1" max="64" step="1"')}
    <div><label for="field-tag">标签过滤</label>
    <select id="field-tag" name="tag"><option value="">不筛选</option></select></div></div>
    <p id="query-help" class="field-help">查询元素范围[-1,1]；余弦模式会在本地归一化。</p>
    ${formFooter("创建任务")}</form>`, async (form) => {
      let query;
      try { query = JSON.parse(form.get("query")); }
      catch (_) { throw new Error("查询向量必须是有效JSON数组"); }
      await send("/api/runs", {
        name: form.get("name"),
        dataset_id: form.get("dataset_id"),
        profile_id: form.get("profile_id"),
        query,
        top_k: Number(form.get("top_k")),
        tag: form.get("tag"),
      });
      closeDialog();
      toast("检索任务已创建，输入快照已固定");
      await navigate("runs");
    });
  $("#field-dataset").addEventListener("change", async () => {
    const id = $("#field-dataset").value;
    if (!id) return;
    try {
      const data = await api(`/api/datasets/${id}`);
      $("#field-tag").innerHTML = '<option value="">不筛选</option>' +
        Object.keys(data.statistics.tags).map((tag) =>
          `<option value="${e(tag)}">${e(tag)}</option>`).join("");
      $("#field-top_k").max = data.row_count;
      $("#field-top_k").value = Math.min(Number($("#field-top_k").value), data.row_count);
      $("#query-help").textContent =
        `需要${data.dimension}维查询；共${data.row_count}条候选，` +
        "标签过滤后K仍须不大于候选数。";
    } catch (exception) { $("#form-error").textContent = exception.message; }
  });
}

async function sampleQuery() {
  const id = $("#field-dataset")?.value;
  if (!id) throw new Error("请先选择数据集");
  const data = await api(`/api/datasets/${id}`);
  $("#field-query").value = JSON.stringify(data.records[0].vector);
  $("#query-help").textContent = "已填入首条向量。它通常与自身最相似，适合初次流程校验。";
}

async function executeRun(identifier) {
  if (state.busy) return;
  state.busy = true;
  $("#busy-overlay").hidden = false;
  clearToasts();
  try {
    const run = await send(`/api/runs/${identifier}/execute`);
    state.selectedRun = run;
    state.resultTab = "ranking";
    toast(run.status === "completed" ? "检索完成，实测结果已保存" : "任务失败：" + run.error,
      run.status !== "completed");
  } finally {
    state.busy = false;
    $("#busy-overlay").hidden = true;
  }
  await navigate("runs");
}

async function viewRun(identifier) {
  state.selectedRun = await api(`/api/runs/${identifier}`);
  state.resultTab = "ranking";
  await navigate("result");
}

async function cloneRun(identifier) {
  const run = await api(`/api/runs/${identifier}`);
  state.profiles = await api("/api/profiles");
  dialog("复制检索任务", `<form id="clone-form">
    ${field("name", "新任务名称", "text", run.name + "-复测", 'required minlength="2" maxlength="80"')}
    <label for="field-profile">新任务配置</label>
    <select id="field-profile" name="profile_id" required>
    ${options(state.profiles, run.profile_id)}</select>
    <div class="notice compact">保留相同数据集、查询、Top-K与标签。
    可切换至明文基线或另一CKKS预设进行对比。</div>
    ${formFooter("保存副本")}</form>`, async (form) => {
      await send(`/api/runs/${identifier}/clone`, Object.fromEntries(form.entries()));
      closeDialog();
      toast("任务副本已创建");
      await navigate("runs");
    });
}

async function renderResult() {
  const run = state.selectedRun;
  if (!run) return navigate("runs");
  const result = run.result;
  const snapshot = run.snapshot;
  let actions = button("go-runs", "返回任务") + button("clone-run", "复制任务", run.id);
  if (run.status === "pending") actions += button("execute-run", "运行任务", run.id, "primary");
  $("#main-content").innerHTML = pageHeading(run.name, "任务编号：" + run.id, actions) +
    `<section class="card"><div class="card-heading"><h2>任务概况</h2>${badge(run.status)}</div>
    ${details([
      ["数据集", snapshot.dataset_name],
      ["检索配置", snapshot.profile.name],
      ["计算方式", snapshot.profile.engine === "ckks" ? "真实CKKS同态点积" : "明文参考基线"],
      ["候选规模", `${snapshot.records.length} 条 × ${snapshot.query.length} 维`],
      ["Top-K / 标签", `${snapshot.top_k} / ${snapshot.tag || "不筛选"}`],
      ["输入快照SHA-256", run.snapshot_hash],
    ])}</section>`;
  if (!result) {
    $("#main-content").insertAdjacentHTML("beforeend", `<section class="card">
      ${emptyState(statuses[run.status][0], run.error || "任务尚未产生结果；仅待执行状态允许运行。")}
      <h3>已保存查询向量</h3><pre class="code-box">${e(JSON.stringify(snapshot.query))}</pre></section>`);
    return;
  }
  $("#main-content").insertAdjacentHTML("beforeend", `<section class="card">
    <div class="card-heading"><h2>检索与评测结果</h2><div class="actions">
    ${button("preview-report", "预览报告", run.id)}
    ${button("export-json", "导出JSON", run.id)}
    ${button("export-csv", "导出CSV", run.id, "primary")}</div></div>
    <div class="score-grid"><div class="score-item"><small>Recall@${snapshot.top_k}</small>
    <strong>${formatNumber(result.metrics.recall_at_k * 100)}%</strong></div>
    <div class="score-item"><small>最大绝对误差</small>
    <strong>${formatNumber(result.metrics.max_abs_error, 8)}</strong></div>
    <div class="score-item"><small>本次总耗时</small>
    <strong>${formatNumber(result.total_ms, 2)} <small>ms</small></strong></div></div>
    <div class="tabs" role="tablist">
    ${[
      ['ranking', 'Top-K排名'], ['metrics', '精度与耗时'], ['snapshot', '输入与可追溯性']
    ].map(([key, name]) =>
      `<button role="tab" data-action="result-tab" data-id="${key}"
      class="${state.resultTab === key ? "active" : ""}">${name}</button>`).join("")}</div>
    <div id="result-tab-content"></div></section>`);
  drawResultTab();
}

function drawResultTab() {
  const run = state.selectedRun;
  const result = run.result;
  const metrics = result.metrics;
  $$(".tabs button").forEach((node) => {
    const active = node.dataset.id === state.resultTab;
    node.classList.toggle("active", active);
    node.setAttribute("aria-selected", active ? "true" : "false");
  });
  const target = $("#result-tab-content");
  if (state.resultTab === "ranking") {
    target.innerHTML = table(["排名", "记录", "实测得分", "明文参考", "绝对误差", "分类"],
      metrics.top_k.map((row, index) => `<tr><td>${index + 1}</td>
      <td><strong>${e(row.label)}</strong><small>${e(row.id)}</small></td>
      <td class="mono">${formatNumber(row.score, 8)}</td>
      <td class="mono">${formatNumber(row.reference, 8)}</td>
      <td class="mono">${formatNumber(row.abs_error, 10)}</td>
      <td>${e(row.tags.join(" / "))}</td></tr>`)) +
      `<div class="notice compact">${metrics.near_tie ?
        "注意：Top-K边界存在近似并列，微小误差可能改变名次。" :
        "结果根据本次实测得分降序排列；完全相同的得分按记录ID排序。"}</div>`;
    return;
  }
  if (state.resultTab === "metrics") {
    const timing = result.timing;
    const phases = [
      ["密钥准备", timing.keygen_ms],
      ["向量加密", timing.encrypt_ms],
      ["评分计算", timing.evaluate_ms],
      ["结果解密", timing.decrypt_ms],
      ["明文参考", timing.reference_ms],
    ];
    const maximum = Math.max(...phases.map((item) => item[1]), 0.001);
    target.innerHTML = `<div class="columns equal"><div><h3>分阶段实测耗时</h3>
      ${phases.map(([name, value]) => `<div class="bar-row"><span>${name}</span>
      <div class="bar-track"><div class="bar-fill"
      style="width:${Math.max(1, value / maximum * 100)}%"></div></div>
      <span class="bar-value">${formatNumber(value, 3)} ms</span></div>`).join("")}
      </div><div><h3>精度与上下文检查</h3>${details([
        ["均方根误差", formatNumber(metrics.rmse, 10)],
        ["预设误差阈值", formatNumber(metrics.tolerance, 8)],
        ["精度检查", metrics.precision_pass ? "通过" : "未通过"],
        ["Top-1一致", metrics.top1_match ? "是" : "否"],
        ["计算上下文含私钥", result.engine === "ckks" ?
          (result.evaluator_has_secret_key ? "是（异常）" : "否") : "不适用"],
        ["输入密文大小", formatBytes(timing.ciphertext_bytes)],
        ["输出密文大小", formatBytes(timing.result_bytes)],
      ])}</div></div><div class="notice compact">${e(result.measurement_note)}</div>`;
    return;
  }
  target.innerHTML = details([
    ["数据集SHA-256", result.dataset_hash],
    ["候选集合SHA-256", result.candidate_hash],
    ["任务快照SHA-256", result.snapshot_hash],
    ["后端版本", result.backend_version],
    ["完成时间", formatDate(run.finished_at)],
  ]) + `<h3 style="margin-top:24px">查询向量</h3>
    <pre class="code-box">${e(JSON.stringify(result.query))}</pre><h3>实际参数</h3>
    <pre class="code-box">${e(JSON.stringify(result.parameters, null, 2))}</pre>
    <div class="notice warning">${e(result.security_boundary)}</div>`;
}

async function renderCompare() {
  const runs = (await api("/api/runs")).filter((run) => run.status === "completed");
  $("#main-content").innerHTML = pageHeading(
    "对比评测", "仅比较数据、候选集、查询、度量与Top-K一致的已完成任务。"
  ) + `<section class="card"><form id="compare-form"><div class="form-grid">
    <div><label for="compare-left">任务 A</label><select id="compare-left" name="left" required>
    ${options(runs)}</select></div><div><label for="compare-right">任务 B</label>
    <select id="compare-right" name="right" required>${options(runs)}</select></div></div>
    <p id="compare-error" class="error-text" role="alert"></p>
    <div class="form-actions"><button class="primary" type="submit">开始对比</button></div>
    </form></section><section class="card" id="compare-result">
    ${emptyState("选择两个任务进行对比", "建议复制CKKS任务并切换为明文基线，执行后再比较。")}</section>`;
  $("#compare-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#compare-error").textContent = "";
    const submit = $("[type=submit]", event.target);
    submit.disabled = true;
    try {
      const data = await send("/api/compare", Object.fromEntries(new FormData(event.target)));
      $("#compare-result").innerHTML = `<div class="card-heading"><h2>对比结果</h2>
      <span class="badge success">输入一致性校验通过</span></div>
      <p class="muted">Top-K集合重合率：${formatNumber(data.overlap_at_k * 100)}%</p>
      ${table(["指标", data.left_name, data.right_name, "B - A"], data.metrics.map((item) =>
        `<tr><td>${e(item.name)}</td><td class="mono">${formatNumber(item.left, 8)}</td>
        <td class="mono">${formatNumber(item.right, 8)}</td>
        <td class="mono">${formatNumber(item.delta, 8)}</td></tr>`))}
      <div class="notice compact">${e(data.note)}</div>`;
    } catch (exception) {
      $("#compare-error").textContent = exception.message;
    } finally {
      submit.disabled = false;
    }
  });
}

async function renderAudit() {
  $("#main-content").innerHTML = pageHeading(
    "审计记录", "关键业务变更保留操作人、时间、实体和哈希链接。",
    button("verify-audit", "校验哈希链") + button("export-audit", "导出最近1000条", "", "primary")
  ) + `<section class="card"><div class="filter-row"><select id="audit-filter" aria-label="审计类型筛选">
    <option value="">全部操作</option><option value="auth.">账号与会话</option>
    <option value="project.">项目管理</option><option value="dataset.">向量数据</option>
    <option value="profile.">参数配置</option><option value="run.">检索任务</option>
    <option value="report.">报告导出</option><option value="system.">系统维护</option>
    </select><span class="muted">当前列表最多显示最近200条</span></div>
    <div id="audit-verification"></div><div id="audit-table"></div></section>
    <div class="notice warning">哈希链只能检查现存本地记录的一致性；
    没有外部锚定，不能证明管理员未重写整条链或截断末尾。</div>`;
  $("#audit-filter").addEventListener("change", () =>
    drawAudit().catch((error) => toast(error.message, true))
  );
  await drawAudit();
}

async function drawAudit() {
  const filter = $("#audit-filter").value;
  const rows = await api("/api/audit?action=" + encodeURIComponent(filter));
  $("#audit-table").innerHTML = rows.length ? table(
    ["序号", "时间", "操作人", "操作", "关联记录", "条目摘要"],
    rows.map((item) => `<tr><td>${item.seq}</td><td>${e(formatDate(item.timestamp))}</td>
    <td>${e(item.actor)}</td><td><strong>${e(actionNames[item.action] || item.action)}</strong>
    <small>${e(item.action)}</small></td><td class="mono">${e(item.entity)}</td>
    <td><code title="${e(item.entry_hash)}">${e(item.entry_hash.slice(0, 14))}…</code></td></tr>`)
  ) : emptyState("没有匹配的审计记录", "选择其他操作类型或完成一次业务操作。");
}

async function verifyAudit() {
  const result = await api("/api/audit/verify");
  $("#audit-verification").innerHTML = `<div class="notice ${result.ok ? "" : "warning"}">
    <strong>${result.ok ? "哈希链校验通过" : "校验失败"}</strong> · 已检查 ${result.count} 条记录<br>
    ${result.ok ? "当前链头：<code>" + e(result.head) + "</code>" :
      "异常序号：" + e(result.bad_seq)}</div>`;
}

async function renderSystem() {
  const [engine, backups] = await Promise.all([api("/api/engine"), api("/api/backups")]);
  $("#main-content").innerHTML = pageHeading(
    "系统维护", "检查运行引擎、创建一致性备份与管理本机口令。"
  ) + `<div class="columns equal"><section class="card"><h2>运行环境</h2>
    ${details([
      ["软件版本", "V1.0"],
      ["加密引擎", engine.ckks_available ? "TenSEAL " + engine.tenseal_version : "未安装"],
      ["明文基线", "可用"],
      ["监听范围", "127.0.0.1（本机）"],
      ["数据库", "SQLite / WAL"],
      ["数据持久化", "本机数据目录"],
      ["CKKS私钥", "每任务临时生成，不写入数据库"],
    ])}<div class="notice compact">第三方库作为运行依赖，未计入本项目原创源程序量。</div>
    </section><section class="card"><h2>账号与使用边界</h2>
    <p class="muted">当前管理员：${e(state.user.username)}</p>
    <p>修改口令会撤销该账号的全部会话，并返回登录页面。</p>
    ${button("change-password", "修改管理员口令")}
    <div class="notice warning">${e(engine.boundary)}<br>备份含原始数据和账号哈希，应存放在受控位置。</div>
    </section></div><section class="card"><div class="card-heading"><h2>本机数据库备份</h2>
    ${button("create-backup", "+ 创建备份", "", "primary")}</div><div id="backup-notice"></div>
    ${backups.length ? table(["文件名称", "大小", "SHA-256"], backups.map((item) =>
      `<tr><td class="mono">${e(item.filename)}</td><td>${formatBytes(item.bytes)}</td>
      <td><code>${e(item.sha256)}</code></td></tr>`)) :
      emptyState("尚无备份", "点击创建备份，系统使用SQLite备份接口并运行完整性检查。")}
    <p class="field-help">备份位置为数据目录下的backups子目录。
    V1.0不提供在线恢复，避免覆盖正在使用的数据。</p>
    </section>`;
}

async function changePassword() {
  dialog("修改管理员口令", `<form id="password-form">
    ${field("old_password", "当前口令", "password", "", 
      'required autocomplete="current-password" maxlength="128"')}
    ${field("new_password", "新口令", "password", "", 
      'required autocomplete="new-password" minlength="12" maxlength="128"')}
    ${field("confirm", "确认新口令", "password", "", 
      'required autocomplete="new-password" minlength="12" maxlength="128"')}
    <div class="notice warning">新口令至少12个字符。保存后所有会话失效，需要重新登录。</div>
    ${formFooter("修改并退出")}</form>`, async (form) => {
      if (form.get("new_password") !== form.get("confirm")) throw new Error("两次新口令不一致");
      await send("/api/password", {
        old_password: form.get("old_password"),
        new_password: form.get("new_password"),
      });
      closeDialog();
      showLogin();
      toast("口令已更新，请使用新口令登录");
    });
}

async function confirmDelete(kind, identifier) {
  const labels = {
    projects: "空项目", datasets: "未被任务引用的数据集", profiles: "未被任务引用的参数配置",
  };
  dialog("确认删除", `<form id="delete-form"><div class="notice warning">
    即将删除${e(labels[kind])}。已产生关联记录的数据不能删除；项目建议归档保留。
    </div><p class="mono">${e(identifier)}</p>${formFooter("确认删除")}</form>`, async () => {
      await api(`/api/${kind}/${identifier}`, { method: "DELETE" });
      closeDialog();
      toast("记录已删除");
      await navigate(kind);
    });
}

async function cancelRun(identifier) {
  dialog("取消待执行任务", `<form id="cancel-form"><p>取消后保留任务快照，任务不可再执行。</p>
    <p class="mono">${e(identifier)}</p>${formFooter("确认取消")}</form>`, async () => {
      await send(`/api/runs/${identifier}/cancel`);
      closeDialog();
      toast("待执行任务已取消");
      await navigate("runs");
    });
}

async function handleAction(action, identifier) {
  const actions = {
    "new-project": () => editProject(),
    "edit-project": () => editProject(identifier),
    "delete-project": () => confirmDelete("projects", identifier),
    "new-dataset": newDataset,
    "import-dataset": importDataset,
    "view-dataset": () => viewDataset(identifier),
    "export-dataset": () => download(`/api/datasets/${identifier}/export`, `${identifier}.csv`),
    "delete-dataset": () => confirmDelete("datasets", identifier),
    "new-profile": newProfile,
    "delete-profile": () => confirmDelete("profiles", identifier),
    "new-run": newRun,
    "sample-query": sampleQuery,
    "execute-run": () => executeRun(identifier),
    "view-run": () => viewRun(identifier),
    "clone-run": () => cloneRun(identifier),
    "cancel-run": () => cancelRun(identifier),
    "go-runs": () => navigate("runs"),
    "close-dialog": closeDialog,
    "preview-report": () => window.open(`/api/runs/${identifier}/report`, "_blank", "noopener"),
    "export-json": () => download(`/api/runs/${identifier}/export/json`, `${identifier}.json`),
    "export-csv": () => download(`/api/runs/${identifier}/export/csv`, `${identifier}.csv`),
    "result-tab": () => { state.resultTab = identifier; drawResultTab(); },
    "verify-audit": verifyAudit,
    "export-audit": () => download("/api/audit/export", "audit-latest-1000.csv"),
    "change-password": changePassword,
    "create-backup": async () => {
      const result = await send("/api/backups");
      await renderSystem();
      $("#backup-notice").innerHTML = `<div class="notice">
      备份完成 · integrity_check=${e(result.integrity)}<br>
      <code>${e(result.filename)}</code> · ${formatBytes(result.bytes)}</div>`;
      toast("数据库备份与完整性检查已完成");
    },
  };
  if (actions[action]) await actions[action]();
}

function bindEvents() {
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#login-error").textContent = "";
    $("#login-submit").disabled = true;
    try {
      state.user = await send("/api/login", Object.fromEntries(new FormData(event.target)));
      $("#login-password").value = "";
      await showWorkspace();
    } catch (exception) {
      $("#login-error").textContent = exception.message;
    } finally {
      $("#login-submit").disabled = false;
    }
  });
  $("#logout").addEventListener("click", async () => {
    try {
      await send("/api/logout");
      showLogin();
      toast("已安全退出");
    } catch (exception) { toast(exception.message, true); }
  });
  $("#dialog-close").addEventListener("click", closeDialog);
  $("#navigation").addEventListener("click", async (event) => {
    const item = event.target.closest("[data-route]");
    if (item) await navigate(item.dataset.route);
  });
  document.addEventListener("click", async (event) => {
    const item = event.target.closest("[data-action]");
    if (!item || item.disabled || state.busy) return;
    item.disabled = true;
    try {
      await handleAction(item.dataset.action, item.dataset.id || "");
    } catch (exception) {
      toast(exception.message, true);
    } finally {
      item.disabled = false;
    }
  });
}

async function start() {
  bindEvents();
  try {
    const setup = await api("/api/setup-status");
    $("#setup-notice").hidden = setup.initialized;
    if (!setup.initialized) return;
    const response = await fetch("/api/session", { credentials: "same-origin" });
    if (response.ok) {
      state.user = await response.json();
      await showWorkspace();
    }
  } catch (exception) {
    $("#login-error").textContent = "服务不可用：" + exception.message;
  }
}

start();
