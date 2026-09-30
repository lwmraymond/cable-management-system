const $ = (id) => document.getElementById(id);
const state = {
  plan: null,
  document: null,
  selected: null,
  scale: 1,
  panX: 0,
  panY: 0,
  dragging: null,
};

function uuid(value, label) {
  const clean = String(value || "").trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(clean)) {
    throw new Error(`${label} must be a UUID`);
  }
  return clean;
}

function number(value, label, min = 0) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min) throw new Error(`${label} is invalid`);
  return parsed;
}

function headers(json = false) {
  const result = {
    "X-Tenant-ID": uuid($("tenant").value, "Tenant"),
    "X-Actor-ID": uuid($("actor").value, "Actor"),
  };
  if ($("project").value.trim()) result["X-Project-ID"] = uuid($("project").value, "Project");
  if ($("location").value.trim()) result["X-Location-ID"] = uuid($("location").value, "Location");
  if (json) result["Content-Type"] = "application/json";
  return result;
}

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) },
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(body?.detail || body || response.statusText);
    error.status = response.status;
    throw error;
  }
  return body;
}

function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}

function saveContext() {
  localStorage.setItem("sim.floorplan.context", JSON.stringify({
    tenant: $("tenant").value,
    actor: $("actor").value,
    project: $("project").value,
    location: $("location").value,
    planId: $("planId").value,
  }));
}

function loadContext() {
  try {
    const value = JSON.parse(localStorage.getItem("sim.floorplan.context") || "{}");
    for (const key of ["tenant", "actor", "project", "location", "planId"]) {
      if (value[key]) $(key).value = value[key];
    }
  } catch { /* ignore corrupt local settings */ }
}

function grid() {
  return Math.max(1, Number(state.document?.canvas?.grid_mm || 100));
}
function snap(value) {
  return state.document?.canvas?.snap ? Math.round(value / grid()) * grid() : value;
}

function canvasTransform() {
  const viewport = $("viewport").getBoundingClientRect();
  const width = state.document?.canvas?.width_mm || 1;
  const height = state.document?.canvas?.height_mm || 1;
  const pxPerMm = Math.min(viewport.width / width, viewport.height / height) * 0.9;
  return pxPerMm * state.scale;
}

function render() {
  const canvas = $("canvas");
  canvas.replaceChildren();
  if (!state.document) return;
  const ratio = canvasTransform();
  const { width_mm: width, height_mm: height, grid_mm: gridMm } = state.document.canvas;
  canvas.style.width = `${width * ratio}px`;
  canvas.style.height = `${height * ratio}px`;
  canvas.style.left = `${state.panX + ($("viewport").clientWidth - width * ratio) / 2}px`;
  canvas.style.top = `${state.panY + ($("viewport").clientHeight - height * ratio) / 2}px`;
  canvas.style.transform = "none";
  canvas.style.backgroundSize = `${gridMm * ratio}px ${gridMm * ratio}px`;
  for (const item of state.document.objects) {
    const node = document.createElement("div");
    node.className = `object ${item.kind}${item.client_id === state.selected ? " selected" : ""}`;
    node.dataset.id = item.client_id;
    node.textContent = item.label || item.kind;
    node.style.left = `${item.x_mm * ratio}px`;
    node.style.top = `${item.y_mm * ratio}px`;
    node.style.width = `${item.width_mm * ratio}px`;
    node.style.height = `${item.height_mm * ratio}px`;
    node.style.transform = `rotate(${item.rotation_deg || 0}deg)`;
    node.style.zIndex = String(item.z_index || 0);
    node.addEventListener("pointerdown", objectPointerDown);
    node.addEventListener("click", (event) => { event.stopPropagation(); select(item.client_id); });
    canvas.append(node);
  }
  $("zoomLabel").textContent = `${Math.round(state.scale * 100)}%`;
}

function select(clientId) {
  state.selected = clientId;
  const item = state.document?.objects.find((row) => row.client_id === clientId);
  if (item) {
    $("selectedLabel").value = item.label || "";
    $("selectedX").value = item.x_mm;
    $("selectedY").value = item.y_mm;
    $("selectedWidth").value = item.width_mm;
    $("selectedHeight").value = item.height_mm;
    $("selectedRotation").value = item.rotation_deg || 0;
  }
  render();
}

function objectPointerDown(event) {
  event.preventDefault();
  const id = event.currentTarget.dataset.id;
  select(id);
  const item = state.document.objects.find((row) => row.client_id === id);
  state.dragging = { id, startX: event.clientX, startY: event.clientY, x: item.x_mm, y: item.y_mm };
  event.currentTarget.setPointerCapture(event.pointerId);
}

window.addEventListener("pointermove", (event) => {
  if (!state.dragging || !state.document) return;
  const item = state.document.objects.find((row) => row.client_id === state.dragging.id);
  const ratio = canvasTransform();
  item.x_mm = Math.max(0, Math.min(state.document.canvas.width_mm, snap(state.dragging.x + (event.clientX - state.dragging.startX) / ratio)));
  item.y_mm = Math.max(0, Math.min(state.document.canvas.height_mm, snap(state.dragging.y + (event.clientY - state.dragging.startY) / ratio)));
  render();
});
window.addEventListener("pointerup", () => { state.dragging = null; });

async function loadPlan(view = "working") {
  saveContext();
  const planId = uuid($("planId").value, "Plan");
  const plan = await api(`/floor-plans/${planId}?view=${view}`);
  state.plan = plan;
  state.document = structuredClone(plan.document);
  $("planName").value = plan.name;
  $("planWidth").value = plan.width_mm;
  $("planHeight").value = plan.height_mm;
  $("gridSize").value = plan.document.canvas.grid_mm;
  state.selected = null;
  render();
  await refreshHistory();
  status(`Loaded ${view} revision ${plan.revision}; version ${plan.version}.`);
}

async function createPlan() {
  saveContext();
  const created = await api("/floor-plans", {
    method: "POST",
    body: JSON.stringify({
      project_id: uuid($("project").value, "Project"),
      location_id: uuid($("location").value, "Location"),
      name: $("planName").value.trim(),
      width_mm: number($("planWidth").value, "Width", 1),
      height_mm: number($("planHeight").value, "Height", 1),
      grid_mm: number($("gridSize").value, "Grid", 1),
    }),
  });
  $("planId").value = created.id;
  await loadPlan();
}

function normalizeBeforeSave() {
  state.document.canvas.grid_mm = number($("gridSize").value, "Grid", 1);
  state.document.objects.forEach((item, index) => { item.z_index = index; });
}

async function saveRevision() {
  if (!state.plan) throw new Error("Load a plan first");
  normalizeBeforeSave();
  await api(`/floor-plans/${state.plan.id}/revisions`, {
    method: "POST",
    body: JSON.stringify({
      expected_version: state.plan.version,
      document: state.document,
      note: `Editor save ${new Date().toISOString()}`,
    }),
  });
  await loadPlan();
}

async function publish() {
  if (!state.plan) throw new Error("Load a plan first");
  await api(`/floor-plans/${state.plan.id}/publish`, {
    method: "POST",
    body: JSON.stringify({ expected_version: state.plan.version, revision: state.plan.head_revision }),
  });
  await loadPlan();
  status(`Published revision ${state.plan.published_revision}.`);
}

async function restore(revision) {
  if (!state.plan) throw new Error("Load a plan first");
  await api(`/floor-plans/${state.plan.id}/restore`, {
    method: "POST",
    body: JSON.stringify({
      expected_version: state.plan.version,
      source_revision: revision,
      note: `Restored revision ${revision}`,
    }),
  });
  await loadPlan();
}

async function refreshHistory() {
  if (!state.plan) return;
  const history = await api(`/floor-plans/${state.plan.id}/revisions`);
  const tbody = $("history");
  tbody.replaceChildren();
  for (const row of history.items) {
    const tr = document.createElement("tr");
    const rev = document.createElement("td"); rev.textContent = `${row.revision}${row.published ? " ★" : ""}`;
    const note = document.createElement("td"); note.textContent = row.note;
    const action = document.createElement("td");
    const button = document.createElement("button"); button.textContent = "Restore";
    button.addEventListener("click", () => run(() => restore(row.revision)));
    action.append(button); tr.append(rev, note, action); tbody.append(tr);
  }
}

function addObject() {
  if (!state.document) throw new Error("Load a plan first");
  const kind = $("kind").value;
  const size = grid();
  const item = {
    client_id: crypto.randomUUID(),
    kind,
    resource_id: kind === "annotation" ? null : uuid($("resourceId").value, "Resource"),
    label: $("objectLabel").value.trim(),
    x_mm: snap(state.document.canvas.width_mm / 2 - size),
    y_mm: snap(state.document.canvas.height_mm / 2 - size / 2),
    width_mm: size * 2,
    height_mm: size,
    rotation_deg: 0,
    z_index: state.document.objects.length,
    geometry: {},
  };
  state.document.objects.push(item);
  select(item.client_id);
}

function applySelected() {
  const item = state.document?.objects.find((row) => row.client_id === state.selected);
  if (!item) throw new Error("Select an object first");
  item.label = $("selectedLabel").value.trim();
  item.x_mm = snap(number($("selectedX").value, "X"));
  item.y_mm = snap(number($("selectedY").value, "Y"));
  item.width_mm = number($("selectedWidth").value, "Width", 1);
  item.height_mm = number($("selectedHeight").value, "Height", 1);
  item.rotation_deg = number($("selectedRotation").value, "Rotation", -3600);
  render();
}
function removeSelected() {
  if (!state.document || !state.selected) return;
  state.document.objects = state.document.objects.filter((row) => row.client_id !== state.selected);
  state.selected = null;
  render();
}

async function run(action) {
  try { await action(); }
  catch (error) {
    if (error.status === 409) status("Conflict: the server plan changed. Reload before retrying; no write was replayed.", true);
    else if (error.status === 401) status("Authentication expired. Sign in again; no write was replayed.", true);
    else if (error.status === 403) status("Permission denied for the actual project/location scope.", true);
    else status(error.message || String(error), true);
  }
}

$("load").addEventListener("click", () => run(loadPlan));
$("create").addEventListener("click", () => run(createPlan));
$("save").addEventListener("click", () => run(saveRevision));
$("publish").addEventListener("click", () => run(publish));
$("refreshHistory").addEventListener("click", () => run(refreshHistory));
$("addObject").addEventListener("click", () => run(async () => addObject()));
$("applySelected").addEventListener("click", () => run(async () => applySelected()));
$("removeSelected").addEventListener("click", removeSelected);
$("zoomIn").addEventListener("click", () => { state.scale = Math.min(8, state.scale * 1.25); render(); });
$("zoomOut").addEventListener("click", () => { state.scale = Math.max(0.1, state.scale / 1.25); render(); });
$("fit").addEventListener("click", () => { state.scale = 1; state.panX = 0; state.panY = 0; render(); });
$("toggleGrid").addEventListener("click", () => $("canvas").classList.toggle("grid"));
$("canvas").addEventListener("click", () => select(null));
window.addEventListener("resize", render);
loadContext();
