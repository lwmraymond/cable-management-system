/* Local syntax/helper gate only. This is not a dependency build or browser E2E. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");

const root = path.resolve(__dirname, "..");
let ts;
try {
  ts = createRequire(path.join(root, "apps/web-react/package.json"))("typescript");
} catch {
  ts = require("typescript");
}
const files = [
  "apps/web-react/src/App.tsx",
  "apps/web-react/src/components/AppShell.tsx",
  "apps/web-react/src/pages/FloorPlanPage.tsx",
  "apps/web-react/src/pages/floorPlanUi.ts",
];
for (const file of files) {
  const result = ts.transpileModule(
    fs.readFileSync(path.join(root, file), "utf8"),
    {
      fileName: file,
      reportDiagnostics: true,
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        isolatedModules: true,
      },
    },
  );
  const errors = (result.diagnostics ?? []).filter(
    diagnostic => diagnostic.category === ts.DiagnosticCategory.Error,
  );
  assert.equal(
    errors.length,
    0,
    errors.map(diagnostic => ts.flattenDiagnosticMessageText(
      diagnostic.messageText,
      "\n",
    )).join("\n"),
  );
}

const helperFile = path.join(
  root,
  "apps/web-react/src/pages/floorPlanUi.ts",
);
const program = ts.createProgram([helperFile], {
  strict: true,
  noEmit: true,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.CommonJS,
  lib: ["lib.es2022.d.ts", "lib.dom.d.ts"],
});
const diagnostics = ts.getPreEmitDiagnostics(program);
assert.equal(
  diagnostics.length,
  0,
  diagnostics.map(diagnostic => ts.flattenDiagnosticMessageText(
    diagnostic.messageText,
    "\n",
  )).join("\n"),
);
const compiled = ts.transpileModule(
  fs.readFileSync(helperFile, "utf8"),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
    },
  },
).outputText;
const sandbox = { exports: {}, Error, Number, Math, JSON };
vm.runInNewContext(compiled, sandbox);
const api = sandbox.exports;
const resourceId = "11111111-2222-4333-8444-555555555555";
assert.equal(api.uuid(` ${resourceId} `), resourceId);
assert.throws(() => api.uuid("../other-tenant"));
assert.equal(api.snap(24, 10), 20);
assert.equal(api.snap(26, 10), 30);
assert.equal(api.zoomLevel(0), 0.25);
assert.equal(api.zoomLevel(8), 4);
assert.equal(api.zoomLevel(Number.NaN), 1);
const document = api.newDocument(25);
const object = {
  id: "rack-1",
  object_type: "rack",
  object_id: resourceId,
  x: 990,
  y: -20,
  width: 100,
  height: 100,
  rotation: 0,
  z_index: 1,
  locked: false,
  label: "Rack",
};
const inserted = api.upsertObject(document, object, 1000, 800);
assert.equal(inserted.objects[0].x, 900);
assert.equal(inserted.objects[0].y, 0);
const moved = api.moveObject(inserted, "rack-1", 126, 149, 1000, 800);
assert.equal(moved.objects[0].x, 125);
assert.equal(moved.objects[0].y, 150);
const locked = {
  ...moved,
  objects: [{ ...moved.objects[0], locked: true }],
};
assert.equal(
  api.moveObject(locked, "rack-1", 300, 300, 1000, 800).objects[0].x,
  125,
);
assert.equal(api.removeObject(locked, "rack-1").objects.length, 1);
assert.equal(api.removeObject(moved, "rack-1").objects.length, 0);
assert.throws(() => api.upsertObject(inserted, {
  ...object,
  id: "rack-2",
}, 1000, 800));
assert.equal(api.floorPlanError({ status: 409 }).conflict, true);
for (const status of [401, 403, 404, 503]) {
  assert.equal(api.floorPlanError({ status }).conflict, false);
}
assert.match(
  fs.readFileSync(path.join(root, files[0]), "utf8"),
  /path="floor-plans"/,
);
assert.match(
  fs.readFileSync(path.join(root, files[1]), "utf8"),
  /2D Floor Plan/,
);
assert.match(
  fs.readFileSync(path.join(root, files[2]), "utf8"),
  /\/floor-plans\/\$\{uuid\(plan\.id\)\}\/draft/,
);
assert.match(
  fs.readFileSync(path.join(root, files[2]), "utf8"),
  /onPointerMove=\{continueDrag\}/,
);
console.log(
  "PASS: 4 TypeScript/TSX syntax checks; strict Floor Plan helper typecheck; 24 helper/route assertions.",
);
console.log(
  "NOT EXECUTED: full React/Ant Design dependency typecheck, Vite build or browser E2E.",
);
