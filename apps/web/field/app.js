import { OfflineMutationQueue } from "./offline-queue.js";

const $ = (id) => document.getElementById(id);
const allowedTypes = new Set(["cable", "work_order", "rack", "device", "port", "location", "test_record"]);
let stream = null;
let scanToken = 0;
let validated = null;
let activeConflict = null;

function readContext() {
  return {
    tenantId: $("tenant").value.trim(),
    actorId: $("actor").value.trim(),
    projectId: $("project").value.trim() || null,
    locationId: $("location").value.trim() || null,
  };
}

function isUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value || ""));
}

function validateContext() {
  const context = readContext();
  if (!isUuid(context.tenantId) || !isUuid(context.actorId)) throw new Error("Tenant and actor must be UUIDs");
  if (context.projectId && !isUuid(context.projectId)) throw new Error("Project must be a UUID");
  if (context.locationId && !isUuid(context.locationId)) throw new Error("Location must be a UUID");
  return context;
}

function saveContext() {
  const context = validateContext();
  localStorage.setItem("sim.field.context", JSON.stringify(context));
  status("Field context saved. Queues remain isolated by tenant and actor.");
  renderQueue();
}

function loadContext() {
  try {
    const context = JSON.parse(localStorage.getItem("sim.field.context") || "{}");
    $("tenant").value = context.tenantId || "";
    $("actor").value = context.actorId || "";
    $("project").value = context.projectId || "";
    $("location").value = context.locationId || "";
  } catch { /* do not trust corrupt local settings */ }
}

const queue = new OfflineMutationQueue({ context: readContext });
queue.addEventListener("change", () => renderQueue());

function status(message, error = false) {
  $("scanResult").textContent = message;
  $("scanResult").classList.toggle("error", error);
}

function normalizePayload(raw) {
  const value = String(raw || "").trim();
  if (!value || value.length > 2_000) throw new Error("QR payload is empty or too large");
  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    let url;
    try { url = new URL(value, location.origin); } catch { throw new Error("QR must contain approved JSON or a same-origin record URL"); }
    if (url.origin !== location.origin) throw new Error("External QR URLs are not accepted");
    const match = url.pathname.match(/^\/app\/(?:record\/)?(cable|work_order|rack|device|port|location|test_record)\/([0-9a-f-]{36})\/?$/i);
    if (!match) throw new Error("QR URL does not match an approved record route");
    parsed = { type: match[1], id: match[2] };
    const qrTenant = url.searchParams.get("tenant_id");
    if (qrTenant) parsed.tenant_id = qrTenant;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("QR JSON must be an object");
  if (!allowedTypes.has(parsed.type)) throw new Error("QR resource type is not allowed");
  if (!isUuid(parsed.id)) throw new Error("QR resource id is invalid");
  const context = validateContext();
  if (parsed.tenant_id && parsed.tenant_id !== context.tenantId) {
    throw new Error("QR tenant does not match the active field context");
  }
  return { type: parsed.type, id: parsed.id, tenantId: context.tenantId };
}

function acceptPayload(raw) {
  validated = normalizePayload(raw);
  $("resourceId").value = validated.id;
  status(`Validated ${validated.type} ${validated.id}. Tenant authority comes from the active session, not the QR.`);
  stopCamera();
}

async function startCamera() {
  stopCamera();
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera API is unavailable; use manual input");
  stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
  $("camera").srcObject = stream;
  await $("camera").play();
  const token = ++scanToken;
  if (!("BarcodeDetector" in window)) {
    status("Camera started, but BarcodeDetector is unavailable. Use the manual payload fallback.");
    return;
  }
  const detector = new BarcodeDetector({ formats: ["qr_code"] });
  async function frame() {
    if (!stream || token !== scanToken) return;
    try {
      const results = await detector.detect($("camera"));
      if (results.length) {
        acceptPayload(results[0].rawValue);
        return;
      }
    } catch (error) {
      status(`Camera scan paused: ${error.message}. Manual input remains available.`, true);
      return;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function stopCamera() {
  scanToken += 1;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  $("camera").srcObject = null;
}

async function enqueue() {
  const context = validateContext();
  let path;
  let body = null;
  let label;
  const action = $("action").value;
  const resourceId = $("resourceId").value.trim();
  if (action === "install") {
    if (!isUuid(resourceId)) throw new Error("Cable resource UUID is required");
    if (validated && (validated.type !== "cable" || validated.id !== resourceId)) {
      throw new Error("Validated QR and selected cable do not match");
    }
    path = `/api/v1/cables/${resourceId}/install`;
    label = `Install cable ${resourceId.slice(0, 8)}`;
  } else {
    path = $("customPath").value.trim();
    if (!path.startsWith("/api/v1/")) throw new Error("Custom path must remain under /api/v1/");
    try { body = JSON.parse($("body").value || "null"); }
    catch { throw new Error("Mutation body is not valid JSON"); }
    label = `Custom ${path}`;
  }
  await queue.enqueue({ method: "POST", path, body, label });
  status(`Mutation queued for tenant ${context.tenantId.slice(0, 8)}. It will keep one idempotency key across retries.`);
  if (navigator.onLine) await queue.flush();
}

function itemActions(item, article) {
  const controls = document.createElement("div");
  controls.className = "row3";
  if (item.status === "conflict") {
    const resolve = document.createElement("button");
    resolve.textContent = "Resolve";
    resolve.addEventListener("click", () => showConflict(item));
    controls.append(resolve);
  }
  const retry = document.createElement("button");
  retry.textContent = "Retry as new";
  retry.className = "secondary";
  retry.addEventListener("click", async () => {
    await queue.retryAsNew(item.id);
    await queue.flush();
  });
  const remove = document.createElement("button");
  remove.textContent = "Discard";
  remove.className = "danger";
  remove.addEventListener("click", () => queue.remove(item.id));
  if (item.status !== "done") controls.append(retry);
  controls.append(remove);
  article.append(controls);
}

async function renderQueue() {
  const container = $("queue");
  let items = [];
  try { items = await queue.list(readContext().tenantId); }
  catch (error) { container.textContent = error.message; return; }
  container.replaceChildren();
  if (!items.length) { container.textContent = "Queue is empty."; return; }
  for (const item of items) {
    const article = document.createElement("article");
    const title = document.createElement("strong"); title.textContent = item.label;
    const badge = document.createElement("span"); badge.className = `badge ${item.status}`; badge.textContent = item.status;
    const meta = document.createElement("div"); meta.className = "muted";
    meta.textContent = `${item.method} ${item.path} · attempts ${item.attempts} · key ${item.idempotencyKey.slice(0, 8)}`;
    article.append(title, document.createTextNode(" "), badge, meta);
    if (item.lastError) {
      const error = document.createElement("div"); error.className = "muted"; error.textContent = item.lastError; article.append(error);
    }
    itemActions(item, article);
    container.append(article);
  }
}

function showConflict(item) {
  activeConflict = item;
  $("conflictBody").textContent = JSON.stringify({ local: item.body, server: item.serverResponse }, null, 2);
  $("conflictDialog").showModal();
}

async function clearTenantQueue() {
  const context = validateContext();
  if (!confirm(`Delete every offline mutation stored for tenant ${context.tenantId}?`)) return;
  await queue.clearTenant(context.tenantId);
  status("Tenant queue cleared from this device.");
}

function updateNetwork() {
  $("network").textContent = navigator.onLine ? "online" : "offline";
  if (navigator.onLine) queue.flush();
}

$("saveContext").addEventListener("click", () => run(saveContext));
$("clearTenant").addEventListener("click", () => run(clearTenantQueue));
$("startCamera").addEventListener("click", () => run(startCamera));
$("stopCamera").addEventListener("click", stopCamera);
$("parseQr").addEventListener("click", () => run(() => acceptPayload($("qrText").value)));
$("enqueue").addEventListener("click", () => run(enqueue));
$("sync").addEventListener("click", () => run(() => queue.flush()));
$("refresh").addEventListener("click", renderQueue);
$("closeConflict").addEventListener("click", () => $("conflictDialog").close());
$("discardConflict").addEventListener("click", () => run(async () => {
  if (activeConflict) await queue.remove(activeConflict.id);
  activeConflict = null; $("conflictDialog").close();
}));
$("retryConflict").addEventListener("click", () => run(async () => {
  if (activeConflict) await queue.retryAsNew(activeConflict.id);
  activeConflict = null; $("conflictDialog").close(); await queue.flush();
}));
window.addEventListener("online", updateNetwork);
window.addEventListener("offline", updateNetwork);
window.addEventListener("beforeunload", stopCamera);

async function run(action) {
  try { await action(); }
  catch (error) { status(error instanceof Error ? error.message : String(error), true); }
}

loadContext();
updateNetwork();
renderQueue();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("./sw.js", { scope: "./" });
