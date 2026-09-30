const DB_NAME = "sim-field-queue";
const DB_VERSION = 1;
const STORE = "mutations";

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id" });
        store.createIndex("tenant_created", ["tenantId", "createdAt"], { unique: false });
        store.createIndex("status", "status", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transaction(db, mode, work) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);
    let value;
    try { value = work(store); } catch (error) { reject(error); return; }
    tx.oncomplete = () => resolve(value);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
  });
}

function requestValue(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function backoff(attempts) {
  const base = Math.min(300_000, 1000 * (2 ** Math.min(attempts, 8)));
  const jitter = Math.floor(Math.random() * Math.max(1, base * 0.25));
  return base + jitter;
}

function classify(status) {
  if (status >= 200 && status < 300) return "done";
  if (status === 401 || status === 403) return "auth-blocked";
  if (status === 409) return "conflict";
  if (status === 422) return "invalid";
  if (status >= 500 || status === 429) return "retry";
  return "failed";
}

export class OfflineMutationQueue extends EventTarget {
  constructor({ context, fetchImpl = fetch } = {}) {
    super();
    this.context = context || (() => ({}));
    this.fetchImpl = fetchImpl;
    this.flushing = false;
  }

  async database() { return openDatabase(); }

  async enqueue({ method = "POST", path, body = null, dependsOn = null, label = "Field mutation" }) {
    const context = this.context();
    if (!context.tenantId || !context.actorId) throw new Error("Tenant and actor are required");
    if (!/^\/api\/v1\/[A-Za-z0-9_./?=&-]+$/.test(path)) throw new Error("Mutation path is invalid");
    const normalizedMethod = method.toUpperCase();
    if (!["POST", "PUT", "PATCH", "DELETE"].includes(normalizedMethod)) {
      throw new Error("Only mutating HTTP methods can be queued");
    }
    const item = {
      id: crypto.randomUUID(),
      idempotencyKey: crypto.randomUUID(),
      tenantId: context.tenantId,
      actorId: context.actorId,
      projectId: context.projectId || null,
      locationId: context.locationId || null,
      method: normalizedMethod,
      path,
      body,
      dependsOn,
      label,
      status: "queued",
      attempts: 0,
      nextAttemptAt: Date.now(),
      createdAt: Date.now(),
      updatedAt: Date.now(),
      lastError: null,
      serverResponse: null,
    };
    const db = await this.database();
    await transaction(db, "readwrite", (store) => store.add(item));
    db.close();
    this.dispatchEvent(new CustomEvent("change", { detail: item }));
    return item;
  }

  async list(tenantId = this.context().tenantId) {
    const db = await this.database();
    const rows = await requestValue(db.transaction(STORE).objectStore(STORE).getAll());
    db.close();
    return rows
      .filter((item) => item.tenantId === tenantId)
      .sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id));
  }

  async put(item) {
    item.updatedAt = Date.now();
    const db = await this.database();
    await transaction(db, "readwrite", (store) => store.put(item));
    db.close();
    this.dispatchEvent(new CustomEvent("change", { detail: item }));
  }

  async remove(id) {
    const db = await this.database();
    await transaction(db, "readwrite", (store) => store.delete(id));
    db.close();
    this.dispatchEvent(new CustomEvent("change", { detail: { id, removed: true } }));
  }

  async clearTenant(tenantId = this.context().tenantId) {
    for (const item of await this.list(tenantId)) await this.remove(item.id);
  }

  async retryAsNew(id, bodyOverride) {
    const item = (await this.list()).find((candidate) => candidate.id === id);
    if (!item) throw new Error("Queued mutation not found");
    await this.remove(item.id);
    return this.enqueue({
      method: item.method,
      path: item.path,
      body: bodyOverride === undefined ? item.body : bodyOverride,
      dependsOn: item.dependsOn,
      label: `${item.label} (resolved)`,
    });
  }

  async flush() {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const context = this.context();
      const items = await this.list(context.tenantId);
      const completed = new Set(items.filter((item) => item.status === "done").map((item) => item.id));
      for (const item of items) {
        if (!["queued", "retry"].includes(item.status)) continue;
        if (item.nextAttemptAt > Date.now()) continue;
        if (item.dependsOn && !completed.has(item.dependsOn)) continue;
        if (item.actorId !== context.actorId) {
          item.status = "auth-blocked";
          item.lastError = "Actor context changed";
          await this.put(item);
          continue;
        }
        item.status = "sending";
        item.attempts += 1;
        await this.put(item);
        try {
          const headers = {
            "Accept": "application/json",
            "Idempotency-Key": item.idempotencyKey,
            "X-Tenant-ID": item.tenantId,
            "X-Actor-ID": item.actorId,
          };
          if (item.projectId) headers["X-Project-ID"] = item.projectId;
          if (item.locationId) headers["X-Location-ID"] = item.locationId;
          if (item.body !== null) headers["Content-Type"] = "application/json";
          const response = await this.fetchImpl(item.path, {
            method: item.method,
            headers,
            body: item.body === null ? undefined : JSON.stringify(item.body),
            cache: "no-store",
          });
          const contentType = response.headers.get("content-type") || "";
          const payload = contentType.includes("json") ? await response.json() : await response.text();
          const outcome = classify(response.status);
          item.serverResponse = { status: response.status, body: payload };
          if (outcome === "done") {
            item.status = "done";
            item.lastError = null;
            completed.add(item.id);
          } else if (outcome === "retry") {
            item.status = "retry";
            item.nextAttemptAt = Date.now() + backoff(item.attempts);
            item.lastError = `HTTP ${response.status}`;
          } else {
            item.status = outcome;
            item.lastError = typeof payload === "object" ? JSON.stringify(payload) : String(payload);
          }
          await this.put(item);
          if (["auth-blocked", "conflict"].includes(item.status)) break;
        } catch (error) {
          item.status = "retry";
          item.nextAttemptAt = Date.now() + backoff(item.attempts);
          item.lastError = error instanceof Error ? error.message : String(error);
          await this.put(item);
          break;
        }
      }
    } finally {
      this.flushing = false;
    }
  }
}

export const queueInternals = { backoff, classify };
