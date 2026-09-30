/* Local syntax/helper gate only. This is NOT a React dependency build or browser test. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const root = path.resolve(__dirname, "..");
let ts;
try { ts = createRequire(path.join(root, "apps/web-react/package.json"))("typescript"); }
catch { ts = require("typescript"); }
const files = ["apps/web-react/src/App.tsx", "apps/web-react/src/components/AppShell.tsx",
  "apps/web-react/src/pages/FiberPage.tsx", "apps/web-react/src/pages/fiberUi.ts"];
for (const file of files) {
  const result = ts.transpileModule(fs.readFileSync(path.join(root, file), "utf8"), {
    fileName: file, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, isolatedModules: true },
  });
  const errors = (result.diagnostics ?? []).filter(d => d.category === ts.DiagnosticCategory.Error);
  assert.equal(errors.length, 0, errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, "\n")).join("\n"));
}
const helperFile = path.join(root, files[3]);
const program = ts.createProgram([helperFile], { strict: true, noEmit: true, target: ts.ScriptTarget.ES2022 });
const diagnostics = ts.getPreEmitDiagnostics(program);
assert.equal(diagnostics.length, 0, diagnostics.map(d => ts.flattenDiagnosticMessageText(d.messageText, "\n")).join("\n"));
const helper = ts.transpileModule(fs.readFileSync(helperFile, "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const sandbox = { exports: {}, Error };
vm.runInNewContext(helper, sandbox);
const api = sandbox.exports;
const id = "11111111-2222-4333-8444-555555555555";
assert.equal(api.resourceId(` ${id} `), id);
assert.throws(() => api.resourceId("../other-tenant"));
assert.throws(() => api.resourceId("https://evil.invalid"));
assert.equal(api.expectedVersion({ version: 3 }).expected_version, 3);
for (const version of [0, -1, true, 1.5, NaN, Infinity, "1"]) {
  assert.throws(() => api.expectedVersion({ version }));
}
assert.notEqual(api.contextKey({tenantId:"a",actorId:"b"}), api.contextKey({tenantId:"b",actorId:"a"}));
assert.notEqual(api.contextKey({tenantId:"a",projectId:"p1"}), api.contextKey({tenantId:"a",projectId:"p2"}));
assert.notEqual(api.contextKey({tenantId:"a",locationId:"l1"}), api.contextKey({tenantId:"a",locationId:"l2"}));
assert.equal(api.fiberError({ status: 409 }).conflict, true);
for (const status of [401, 403, 404, 503]) assert.equal(api.fiberError({ status }).conflict, false);
assert.equal(api.fiberError(new Error("offline")).message, "offline");
assert.match(fs.readFileSync(path.join(root, files[0]), "utf8"), /path="fiber"/);
assert.match(fs.readFileSync(path.join(root, files[2]), "utf8"), /key=\{contextKey\(getContext\(\)\)\}/);
console.log("PASS: 4 TypeScript/TSX syntax checks; strict helper typecheck; 24 helper/integration assertions.");
console.log("NOT EXECUTED: full React/Ant Design typecheck, Vite build, browser E2E.");
