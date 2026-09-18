#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$ROOT/apps/web-react"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python - "$WEB/package.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
payload = json.loads(path.read_text())
required = {
    "@refinedev/core", "@refinedev/antd", "antd", "react", "react-dom",
    "react-router-dom", "@tanstack/react-query",
}
deps = set(payload.get("dependencies", {}))
missing = sorted(required - deps)
if missing:
    raise SystemExit(f"missing required dependencies: {missing}")
print(json.dumps({"package_json": "PASS", "declared_dependencies": len(required)}))
PY

cat > "$TMP/modules.d.ts" <<'TS'
declare module "*" {
  const value: any;
  export default value;
  export const Refine: any;
  export const ErrorComponent: any;
  export const notificationProvider: any;
  export const defineConfig: any;
}
declare namespace JSX { interface IntrinsicElements { [name: string]: any } }
TS

SOURCE_FILES=()
while IFS= read -r file; do SOURCE_FILES+=("$file"); done < <(find "$WEB/src" -type f \( -name '*.ts' -o -name '*.tsx' \) | sort)
export PATH="$WEB/node_modules/.bin:$PATH"
tsc --noEmit --noCheck --skipLibCheck --target ES2022 --module ESNext \
  --moduleResolution Bundler --jsx react-jsx --lib ES2022,DOM,DOM.Iterable \
  "$TMP/modules.d.ts" "$WEB/vite.config.ts" "${SOURCE_FILES[@]}"

tsc --skipLibCheck --target ES2022 --module commonjs --outDir "$TMP/out" \
  "$WEB/src/api/context.ts" "$WEB/src/auth/tokenStore.ts"

node - "$TMP/out" <<'JS'
const path = require("node:path");
const root = process.argv[2];
const context = require(path.join(root, "api/context.js"));
const token = require(path.join(root, "auth/tokenStore.js"));
const values = new Map();
const storage = {
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, value),
  removeItem: (key) => values.delete(key),
};
const saved = context.saveContext({tenantId: " tenant-a ", actorId: " actor-a "}, storage);
const loaded = context.loadContext(storage);
if (saved.tenantId !== "tenant-a" || loaded.actorId !== "actor-a") throw new Error("context roundtrip failed");
token.writeAccessToken("a.b.c", storage);
if (!token.hasUsableToken(token.readAccessToken(storage))) throw new Error("token helper failed");
token.clearAccessToken(storage);
if (token.readAccessToken(storage) !== null) throw new Error("token clear failed");
console.log(JSON.stringify({typescript_source_parse: "PASS", context_roundtrip: "PASS", token_helpers: "PASS"}));
JS
