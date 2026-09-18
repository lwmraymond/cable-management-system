
import { Infrastructure3DViewer } from "./webgl-viewer.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const API = "/api/v1";

const state = {
  context: null,
  tenant: null,
  dashboard: null,
  rack: null,
  trace: null,
  compliance: null,
  audits: [],
  workOrders: [],
  cables: [],
  tests: [],
  rackViewer: null,
  routeViewer: null,
};

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[character]));
}

function prettify(value) {
  return String(value ?? "—").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function headersFor(actor = "owner", scoped = false) {
  const context = state.context;
  if (!context) return {};
  const actorId = {
    owner: context.owner_id,
    supervisor: context.supervisor_id,
    contractor: context.contractor_id,
  }[actor];
  const headers = {
    "X-Tenant-ID": context.tenant_id,
    "X-Actor-ID": actorId,
    "Content-Type": "application/json",
  };
  if (scoped) {
    headers["X-Project-ID"] = context.project_id;
    headers["X-Location-ID"] = context.location_id;
  }
  return headers;
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  let payload = null;
  const text = await response.text();
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }
  if (!response.ok) {
    const detail = payload?.detail || payload?.message || text || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

function announce(message, type = "info") {
  const notice = $("#notice");
  notice.hidden = false;
  notice.className = `notice ${type}`;
  notice.textContent = message;
  clearTimeout(announce.timer);
  announce.timer = setTimeout(() => { notice.hidden = true; }, 5200);
}

function setBusy(button, busy, label = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.original = button.textContent;
    button.disabled = true;
    button.textContent = label;
  } else {
    button.disabled = false;
    button.textContent = button.dataset.original || button.textContent;
  }
}

function navigate(view) {
  $$(".nav").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  const titles = {
    dashboard: "运营总览",
    rack: "3D 机架",
    trace: "线缆追踪",
    field: "现场工作流",
    compliance: "TIA-606 合规",
    audit: "审计记录",
  };
  $("#page-title").textContent = titles[view] || "Structured Infrastructure Manager";
  history.replaceState(null, "", `#${view}`);
  requestAnimationFrame(() => {
    if (view === "rack") state.rackViewer?.render();
    if (view === "trace") state.routeViewer?.render();
  });
}

function metricCard(label, value, hint, tone = "blue") {
  return `<article class="metric ${tone}">
    <span>${escapeHTML(label)}</span>
    <strong>${escapeHTML(value)}</strong>
    <small>${escapeHTML(hint)}</small>
  </article>`;
}

function renderDashboard() {
  const counts = state.dashboard.counts;
  $("#metrics").innerHTML = [
    metricCard("Buildings", counts.buildings, "已建模建筑", "blue"),
    metricCard("Telecom Rooms", counts.telecom_rooms, "TR / MDF / MMR / ER", "cyan"),
    metricCard("Racks", counts.racks, "持久化机架", "violet"),
    metricCard("Active Cables", counts.active_cables, "非 Removed 状态", "green"),
    metricCard("Open Work Orders", counts.open_work_orders, "待执行或待审批", "amber"),
    metricCard("Failed Tests", counts.failed_tests, "需整改", counts.failed_tests ? "red" : "green"),
  ].join("");

  const locations = [
    ["Administration Building", "MC-ADM", "BUILDING"],
    ["Engineering Building", "MC-ENG", "ACTIVE PROJECT"],
    ["Data Center 1", "MC-DC1", "DATA CENTER"],
  ];
  $("#campus").innerHTML = locations.map(([name, id, type], index) => `
    <button class="campus-node ${index === 1 ? "selected" : ""}" data-go="${index === 1 ? "rack" : "dashboard"}">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <div><strong>${escapeHTML(name)}</strong><small>${escapeHTML(id)} · ${escapeHTML(type)}</small></div>
      <b>→</b>
    </button>`).join("");

  $("#orders").innerHTML = state.dashboard.recent_work_orders.length
    ? state.dashboard.recent_work_orders.map((order) => `
      <button class="list-row" data-go="field">
        <span class="status-dot ${escapeHTML(order.status)}"></span>
        <div><strong>${escapeHTML(order.number)}</strong><small>${escapeHTML(order.title)}</small></div>
        <em>${escapeHTML(prettify(order.status))}</em>
      </button>`).join("")
    : `<div class="empty">没有待处理工单。</div>`;

  const selected = state.trace.items.filter((item) => ["port", "cable"].includes(item.kind));
  $("#mini-trace").innerHTML = selected.slice(0, 7).map((item, index) => `
    <div class="mini-hop ${escapeHTML(item.kind)}">
      <span>${item.kind === "port" ? "P" : "C"}</span>
      <div><strong>${escapeHTML(item.identifier)}</strong><small>${escapeHTML(item.kind === "port" ? item.device?.name : item.media_type)}</small></div>
      ${index < selected.slice(0, 7).length - 1 ? "<b>→</b>" : ""}
    </div>`).join("");

  const capacity = state.rack.capacity;
  $("#health").innerHTML = `
    <div class="gauge-row"><div><strong>${escapeHTML(capacity.utilization_percent)}%</strong><small>Rack U utilization</small></div>
      <div class="bar"><i style="width:${Math.min(100, Number(capacity.utilization_percent))}%"></i></div></div>
    <div class="health-grid">
      <div><strong>${escapeHTML(capacity.used_u)}U</strong><small>Used</small></div>
      <div><strong>${escapeHTML(capacity.free_u)}U</strong><small>Free</small></div>
      <div><strong>${escapeHTML(state.compliance.summary.score_percent)}%</strong><small>Compliance</small></div>
      <div><strong>${escapeHTML(state.compliance.summary.finding_count)}</strong><small>Findings</small></div>
    </div>`;
}

function renderRack() {
  $("#rack-title").textContent = `${state.rack.rack.identifier} · ${state.rack.rack.name}`;
  $("#device-table").innerHTML = state.rack.devices.map((device) => `
    <tr data-device="${escapeHTML(device.id)}">
      <td><strong>${escapeHTML(device.identifier)}</strong></td>
      <td>${escapeHTML(device.name)}</td>
      <td><span class="chip">${escapeHTML(prettify(device.device_type))}</span></td>
      <td>U${escapeHTML(device.start_u)}–U${escapeHTML(device.start_u + device.rack_units - 1)}</td>
      <td>${escapeHTML(device.ports)}</td>
      <td>${escapeHTML(prettify(device.face))}</td>
    </tr>`).join("");
  renderRackCapacity();
  state.rackViewer.setRack(state.rack);
}

function renderRackCapacity() {
  const { capacity, rack } = state.rack;
  $("#rack-capacity").innerHTML = `
    <div class="capacity-card">
      <div><small>RACK CAPACITY</small><strong>${escapeHTML(capacity.utilization_percent)}%</strong></div>
      <div class="bar"><i style="width:${Math.min(100, Number(capacity.utilization_percent))}%"></i></div>
      <dl>
        <div><dt>Height</dt><dd>${escapeHTML(rack.height_u)}U</dd></div>
        <div><dt>Used</dt><dd>${escapeHTML(capacity.used_u)}U</dd></div>
        <div><dt>Free</dt><dd>${escapeHTML(capacity.free_u)}U</dd></div>
        <div><dt>Dimensions</dt><dd>${escapeHTML(rack.width_mm)} × ${escapeHTML(rack.depth_mm)} mm</dd></div>
      </dl>
    </div>`;
}

function inspectDevice(device) {
  if (!device) return;
  $("#rack-inspector").className = "";
  $("#rack-inspector").innerHTML = `
    <div class="object-icon">${escapeHTML(device.device_type.includes("patch") ? "PP" : device.device_type.includes("fiber") ? "FP" : "SW")}</div>
    <h3>${escapeHTML(device.identifier)}</h3>
    <p>${escapeHTML(device.name)}</p>
    <dl class="inspector-dl">
      <div><dt>Type</dt><dd>${escapeHTML(prettify(device.device_type))}</dd></div>
      <div><dt>Rack position</dt><dd>U${escapeHTML(device.start_u)}–U${escapeHTML(device.start_u + device.rack_units - 1)}</dd></div>
      <div><dt>Face</dt><dd>${escapeHTML(prettify(device.face))}</dd></div>
      <div><dt>Ports</dt><dd>${escapeHTML(device.ports)}</dd></div>
    </dl>`;
}

function traceLabel(item) {
  if (item.kind === "port") {
    return {
      code: "PORT",
      title: `${item.device?.identifier || "DEVICE"}:${item.identifier}`,
      subtitle: `${item.device?.name || "Unknown device"} · ${item.connector_type}`,
    };
  }
  if (item.kind === "cable") {
    return {
      code: item.selected ? "SELECTED CABLE" : "CABLE",
      title: item.identifier,
      subtitle: `${item.media_type} · ${prettify(item.status)}`,
    };
  }
  return {
    code: "INTERNAL MAP",
    title: prettify(item.mapping_type),
    subtitle: item.lane ? `Lane ${item.lane}` : "Front ↔ rear physical mapping",
  };
}

function renderTrace() {
  $("#trace-title").textContent = state.trace.selected_identifier;
  $("#trace-state").textContent = state.trace.complete ? `COMPLETE · ${state.trace.hop_count} HOPS` : "INCOMPLETE";
  $("#trace-state").className = `badge ${state.trace.complete ? "good" : "warn"}`;
  $("#trace-flow").innerHTML = state.trace.items.map((item, index) => {
    const label = traceLabel(item);
    return `<div class="trace-item ${escapeHTML(item.kind)} ${item.selected ? "selected" : ""}">
      <div class="trace-symbol">${item.kind === "port" ? "P" : item.kind === "cable" ? "C" : "↕"}</div>
      <div><small>${escapeHTML(label.code)}</small><strong>${escapeHTML(label.title)}</strong><span>${escapeHTML(label.subtitle)}</span></div>
      ${item.kind === "cable" && item.route?.length ? `<em>${item.route.length} pathway segments</em>` : ""}
      ${index < state.trace.items.length - 1 ? '<i class="trace-arrow">↓</i>' : ""}
    </div>`;
  }).join("");
  state.routeViewer.setTrace(state.trace);
}

function renderField() {
  const cable = state.cables.find((item) => item.id === state.context.cable_id);
  const order = state.workOrders.find((item) => item.id === state.context.work_order_id);
  const testRecord = state.tests.find((item) => item.cable_id === state.context.cable_id);
  $("#field-id").textContent = cable ? `${cable.identifier} · ${cable.media_type}` : "线缆未找到";
  $("#field-state").textContent = cable ? prettify(cable.installation_status).toUpperCase() : "UNKNOWN";
  $("#field-state").className = `state ${cable?.installation_status || ""}`;

  $("#install").disabled = !cable || !["planned", "approved", "ordered", "staged", "terminated"].includes(cable.installation_status);
  $("#test-pass").disabled = !cable || !["installed", "terminated", "tested"].includes(cable.installation_status);
  $("#approve").disabled = !testRecord || testRecord.status === "approved" || testRecord.result !== "PASS";

  const events = [
    ["Work order", order ? `${order.work_order_number} · ${prettify(order.status)}` : "Not found"],
    ["Cable state", cable ? prettify(cable.installation_status) : "Unknown"],
    ["Installer", cable?.installer_id ? "Metro technician recorded" : "Not recorded"],
    ["Test", testRecord ? `${testRecord.result} · ${prettify(testRecord.status)}` : "Not submitted"],
    ["Approval", testRecord?.approved_at ? new Date(testRecord.approved_at).toLocaleString("zh-Hans") : "Pending"],
  ];
  $("#field-log").innerHTML = events.map(([label, value]) => `
    <div><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`).join("");
}

function renderCompliance() {
  $("#profile").textContent = `${state.compliance.profile.family}-${state.compliance.profile.edition}`;
  $("#score").textContent = `${state.compliance.summary.score_percent}%`;
  $("#finding-count").textContent = String(state.compliance.summary.finding_count);
  $("#legal").textContent = state.compliance.legal_notice;
  $("#findings").innerHTML = state.compliance.findings.length
    ? state.compliance.findings.slice(0, 30).map((finding) => `
      <div class="finding ${escapeHTML(finding.severity)}">
        <span>${finding.severity === "error" ? "!" : "△"}</span>
        <div><strong>${escapeHTML(prettify(finding.type))}</strong><small>${escapeHTML(finding.object_type)} · ${escapeHTML(finding.identifier || finding.object_id)}</small><p>${escapeHTML(finding.recommendation)}</p></div>
      </div>`).join("")
    : `<div class="empty">当前配置规则未发现例外。</div>`;
}

function renderAudit() {
  $("#audits").innerHTML = state.audits.length
    ? state.audits.map((event) => `
      <div class="audit-row">
        <span class="audit-mark"></span>
        <div class="audit-main"><small>${escapeHTML(new Date(event.timestamp).toLocaleString("zh-Hans"))}</small>
          <strong>${escapeHTML(event.action)}</strong>
          <p>${escapeHTML(event.object_type)} · ${escapeHTML(event.object_id)}</p></div>
        <code>${escapeHTML(event.request_id || "seed")}</code>
      </div>`).join("")
    : `<div class="empty">暂无审计事件。</div>`;
}

async function refreshData({ preserveNotice = false } = {}) {
  if (!preserveNotice) $("#notice").hidden = true;
  const ownerHeaders = headersFor("owner");
  const cableId = state.context.cable_id;
  const rackId = state.context.rack_id;
  const [
    tenant,
    dashboard,
    rack,
    trace,
    compliance,
    audits,
    workOrders,
    cables,
    tests,
  ] = await Promise.all([
    request("/tenants/current", { headers: ownerHeaders }),
    request("/dashboard", { headers: ownerHeaders }),
    request(`/racks/${rackId}/elevation`, { headers: ownerHeaders }),
    request(`/cables/${cableId}/trace`, { headers: ownerHeaders }),
    request("/compliance/report", { headers: ownerHeaders }),
    request("/audit-events?limit=60", { headers: ownerHeaders }),
    request("/work-orders", { headers: ownerHeaders }),
    request("/cables?limit=200", { headers: ownerHeaders }),
    request(`/test-results?cable_id=${encodeURIComponent(cableId)}`, { headers: ownerHeaders }),
  ]);
  Object.assign(state, {
    tenant,
    dashboard,
    rack,
    trace,
    compliance,
    audits: audits.items,
    workOrders,
    cables,
    tests,
  });
  $("#tenant-name").textContent = tenant.name;
  $("#breadcrumb").textContent = `${tenant.name} / Main Campus / Engineering Building`;
  renderDashboard();
  renderRack();
  renderTrace();
  renderField();
  renderCompliance();
  renderAudit();
}

let searchTimer;
async function searchResources(value) {
  const panel = $("#search-results");
  const query = value.trim();
  clearTimeout(searchTimer);
  if (!query) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const result = await request(`/search?q=${encodeURIComponent(query)}&limit=12`, { headers: headersFor("owner") });
      panel.innerHTML = result.items.length
        ? result.items.map((item) => `
          <button class="search-hit" data-type="${escapeHTML(item.type)}">
            <span>${escapeHTML(item.type.slice(0, 2).toUpperCase())}</span>
            <div><strong>${escapeHTML(item.identifier)}</strong><small>${escapeHTML(item.name)}</small></div>
          </button>`).join("")
        : `<div class="empty">没有匹配的租户内资源。</div>`;
      panel.hidden = false;
    } catch (error) {
      announce(`搜索失败：${error.message}`, "error");
    }
  }, 250);
}

async function runAction(button, action, success) {
  setBusy(button, true);
  try {
    await action();
    await refreshData({ preserveNotice: true });
    announce(success, "success");
  } catch (error) {
    announce(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function generateLabel() {
  const result = await request(`/labels/cables/${state.context.cable_id}`, {
    method: "POST",
    headers: headersFor("owner"),
  });
  $("#qr").innerHTML = `<div class="qr-svg">${result.qr_svg}</div>
    <p><strong>${escapeHTML(result.identifier)}</strong><small>${escapeHTML(result.qr_payload)}</small></p>`;
}

function installCable() {
  return request(`/cables/${state.context.cable_id}/install?work_order_id=${encodeURIComponent(state.context.work_order_id)}`, {
    method: "POST",
    headers: headersFor("contractor", true),
  });
}

function submitTest() {
  return request(`/cables/${state.context.cable_id}/tests`, {
    method: "POST",
    headers: headersFor("contractor", true),
    body: JSON.stringify({
      result: "PASS",
      work_order_id: state.context.work_order_id,
      measurements: {
        profile: "configured-copper-certification",
        wiremap: "PASS",
        length_m: 45.7,
        insertion_loss: "PASS",
        next: "PASS",
      },
      attachment_name: "HC-00001-certification.pdf",
    }),
  });
}

async function approveTest() {
  const tests = await request(`/test-results?cable_id=${encodeURIComponent(state.context.cable_id)}`, {
    headers: headersFor("supervisor", true),
  });
  const pending = tests.find((record) => record.result === "PASS" && record.status !== "approved");
  if (!pending) throw new Error("没有可审批的 PASS 测试记录。");
  return request(`/tests/${pending.id}/approve`, {
    method: "POST",
    headers: headersFor("supervisor", true),
  });
}

function wireUI() {
  $$(".nav").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-go]");
    if (target) navigate(target.dataset.go);
  });
  $("#search").addEventListener("input", (event) => searchResources(event.target.value));
  $("#search").addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.target.value = "";
      $("#search-results").hidden = true;
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".search") && !event.target.closest("#search-results")) {
      $("#search-results").hidden = true;
    }
  });
  $("#refresh").addEventListener("click", (event) => runAction(event.currentTarget, () => refreshData(), "数据已重新核对。"));
  $("#front").addEventListener("click", () => {
    state.rackViewer.setFront(true);
    $("#front").classList.add("on");
    $("#rear").classList.remove("on");
  });
  $("#rear").addEventListener("click", () => {
    state.rackViewer.setFront(false);
    $("#rear").classList.add("on");
    $("#front").classList.remove("on");
  });
  $("#projection").addEventListener("click", (event) => {
    const orthographic = state.rackViewer.toggleProjection();
    event.currentTarget.textContent = orthographic ? "正交" : "透视";
  });
  $("#label").addEventListener("click", (event) => runAction(event.currentTarget, generateLabel, "已生成权限保护的线缆 QR 标签。"));
  $("#install").addEventListener("click", (event) => runAction(event.currentTarget, installCable, "安装动作已写入线缆、工单和审计记录。"));
  $("#test-pass").addEventListener("click", (event) => runAction(event.currentTarget, submitTest, "PASS 测试结果已提交，等待独立审批。"));
  $("#approve").addEventListener("click", (event) => runAction(event.currentTarget, approveTest, "主管审批完成，线缆已进入 In Service。"));
}

async function boot() {
  try {
    state.context = await request("/demo/context");
    state.rackViewer = new Infrastructure3DViewer($("#rack-canvas"), { onSelect: inspectDevice });
    state.routeViewer = new Infrastructure3DViewer($("#route-canvas"));
    wireUI();
    await refreshData();
    $("#boot").hidden = true;
    $("#shell").hidden = false;
    const requested = location.hash.slice(1);
    navigate(["dashboard", "rack", "trace", "field", "compliance", "audit"].includes(requested) ? requested : "dashboard");
    const fieldCable = new URLSearchParams(location.search).get("fieldCable");
    if (fieldCable === state.context.cable_id) navigate("field");
  } catch (error) {
    $("#boot").innerHTML = `<div class="logo">!</div><div><strong>应用启动失败</strong><span>${escapeHTML(error.message)}</span></div>`;
  }
}

boot();

async function downloadCableSchedule() {
  const button = $("#export-cables");
  setBusy(button, true);
  try {
    const response = await fetch(`${API}/reports/cable-schedule.csv`, { headers: headersFor() });
    if (!response.ok) throw new Error(`导出失败：HTTP ${response.status}`);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "cable-schedule.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    announce(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}
$("#export-cables").addEventListener("click", downloadCableSchedule);
