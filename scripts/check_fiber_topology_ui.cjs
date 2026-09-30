/* Local syntax/helper gate only. This is NOT a dependency build or browser E2E. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const root = path.resolve(__dirname, "..");
let ts;
try { ts = createRequire(path.join(root, "apps/web-react/package.json"))("typescript"); }
catch { ts = require("typescript"); }
const files = [
  "apps/web-react/src/App.tsx",
  "apps/web-react/src/components/AppShell.tsx",
  "apps/web-react/src/pages/FiberPage.tsx",
  "apps/web-react/src/pages/FiberTopologyPage.tsx",
  "apps/web-react/src/pages/fiberUi.ts",
  "apps/web-react/src/pages/fiberTopologyUi.ts",
];
for (const file of files) {
  const result = ts.transpileModule(fs.readFileSync(path.join(root, file), "utf8"), {
    fileName: file,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      isolatedModules: true,
    },
  });
  const errors = (result.diagnostics ?? []).filter(d => d.category === ts.DiagnosticCategory.Error);
  assert.equal(errors.length, 0, errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, "\n")).join("\n"));
}
const helperFile = path.join(root, "apps/web-react/src/pages/fiberTopologyUi.ts");
const program = ts.createProgram([helperFile], {
  strict: true,
  noEmit: true,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.CommonJS,
  lib: ["lib.es2022.d.ts", "lib.dom.d.ts"],
});
const diagnostics = ts.getPreEmitDiagnostics(program);
assert.equal(diagnostics.length, 0, diagnostics.map(d => ts.flattenDiagnosticMessageText(d.messageText, "\n")).join("\n"));
const helper = ts.transpileModule(fs.readFileSync(helperFile, "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const sandbox = { exports: {}, Error, Number, Date, Set, JSON };
vm.runInNewContext(helper, sandbox);
const api = sandbox.exports;
const a = "11111111-2222-4333-8444-555555555555";
const b = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
assert.deepEqual(JSON.parse(JSON.stringify(api.parseChannelMembers(`fiber_strand,${a},tx\ncopper_pair,${b},pair-1`))), [
  { kind: "fiber_strand", resource_id: a, role: "tx" },
  { kind: "copper_pair", resource_id: b, role: "pair-1" },
]);
assert.throws(() => api.parseChannelMembers(`bad,${a},x`));
assert.throws(() => api.parseChannelMembers(""));
assert.deepEqual(JSON.parse(JSON.stringify(api.parseBreakoutLegs(`${a},B,${b},A,Leg 1,0.2`)))[0], {
  parent_strand_id: a, parent_side: "B", child_strand_id: b, child_side: "A", label: "Leg 1", loss_db: 0.2,
});
assert.throws(() => api.parseBreakoutLegs(`${a},C,${b},A,,0`));
const events = JSON.parse(JSON.stringify(api.parseOtdrEvents("0,launch,0,,1,start\n100,splice,0.1,-60,0.9,splice\n200,end,,,1,end")));
assert.equal(events.length, 3);
assert.equal(events[1].reflectance_db, -60);
assert.throws(() => api.parseOtdrEvents("100,splice\n50,end"));
assert.equal(api.isoTimestamp("2026-09-04T10:00:00+08:00"), "2026-09-04T10:00:00+08:00");
assert.throws(() => api.isoTimestamp("2026-09-04T10:00:00"));
assert.match(api.traceItemText({ kind: "splice", slot_number: 4, loss_db: 0.1 }), /Slot 4/);
assert.match(fs.readFileSync(path.join(root, files[0]), "utf8"), /path="fiber-topology"/);
assert.match(fs.readFileSync(path.join(root, files[1]), "utf8"), /Fiber Topology/);
assert.match(fs.readFileSync(path.join(root, files[3]), "utf8"), /key=\{contextKey\(getContext\(\)\)\}/);
assert.match(fs.readFileSync(path.join(root, files[3]), "utf8"), /\/fiber\/otdr-records/);
assert.match(fs.readFileSync(path.join(root, files[3]), "utf8"), /\/fiber\/breakouts/);
assert.match(fs.readFileSync(path.join(root, files[3]), "utf8"), /\/fiber\/channels/);
console.log("PASS: 6 TypeScript/TSX syntax checks; strict advanced helper typecheck; 18 parser/route assertions.");
console.log("NOT EXECUTED: full React/Ant Design dependency typecheck, Vite build, browser E2E.");
