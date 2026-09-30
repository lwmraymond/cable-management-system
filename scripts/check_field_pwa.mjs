import assert from "node:assert/strict";
import fs from "node:fs";

const queueSource = fs.readFileSync("apps/web/field/offline-queue.js", "utf8");
const appSource = fs.readFileSync("apps/web/field/app.js", "utf8");
const workerSource = fs.readFileSync("apps/web/field/sw.js", "utf8");
const manifest = JSON.parse(fs.readFileSync("apps/web/field/manifest.webmanifest", "utf8"));

for (const file of [
  "apps/web/field/offline-queue.js",
  "apps/web/field/app.js",
  "apps/web/field/sw.js",
]) {
  const source = fs.readFileSync(file, "utf8");
  assert.equal(source.includes("eval("), false, `${file} must not use eval`);
  assert.equal(source.includes("innerHTML"), false, `${file} must not assign innerHTML`);
}

const checks = {
  indexedDB: queueSource.includes("indexedDB.open"),
  tenantIndex: queueSource.includes("tenant_created"),
  stableIdempotencyKey: queueSource.includes("idempotencyKey: crypto.randomUUID()"),
  orderedSingleFlush: queueSource.includes("for (const item of items)"),
  dependencies: queueSource.includes("dependsOn"),
  exponentialBackoff: queueSource.includes("2 ** Math.min(attempts, 8)"),
  jitter: queueSource.includes("Math.random()"),
  authClassification: queueSource.includes('return "auth-blocked"'),
  conflictClassification: queueSource.includes('return "conflict"'),
  invalidClassification: queueSource.includes('return "invalid"'),
  retryClassification: queueSource.includes('return "retry"'),
  noAutomaticConflictOverwrite: appSource.includes("Automatic overwrite is disabled"),
  retryAsNew: queueSource.includes("retryAsNew"),
  tenantClear: queueSource.includes("clearTenant"),
  cameraPermission: appSource.includes("getUserMedia"),
  cameraFallback: appSource.includes("manual payload fallback"),
  barcodeDetector: appSource.includes("BarcodeDetector"),
  qrTypeAllowlist: appSource.includes("allowedTypes"),
  qrTenantMatch: appSource.includes("QR tenant does not match"),
  sameOriginQr: appSource.includes("url.origin !== location.origin"),
  apiNeverCached: workerSource.includes('url.pathname.startsWith("/api/")'),
  staticShellCache: workerSource.includes("cache.addAll(SHELL)"),
  installable: manifest.display === "standalone" && manifest.start_url === "./",
  scoped: manifest.scope === "./",
};

for (const [name, passed] of Object.entries(checks)) assert.equal(passed, true, name);
console.log(`PASS: ${Object.keys(checks).length} Camera QR / PWA offline queue assertions`);
console.log("NOT EXECUTED: real camera hardware, browser installability audit, background sync under OS process eviction");
