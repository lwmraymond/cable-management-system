import { useEffect, useState, type SetStateAction } from "react";
import type { InfrastructureContext } from "../api/context";

const defaults = { labels: true, shell: false, pathways: true, dimensions: true };
type Layers = typeof defaults;
function read(key: string): Layers {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? "null");
    return Object.fromEntries(Object.entries(defaults).map(([name, fallback]) => [name, value && typeof value === "object" && typeof (value as Record<string, unknown>)[name] === "boolean" ? (value as Record<string, boolean>)[name] : fallback])) as Layers;
  } catch { return { ...defaults }; }
}
export function useSpatialLayers(context: InfrastructureContext): [Layers, (next: SetStateAction<Layers>) => void] {
  const key = `sim.spatial-layers.v1:${context.tenantId}:${context.actorId ?? "session"}`;
  const [state, setState] = useState(() => ({ key, value: read(key) }));
  const layers = state.key === key ? state.value : read(key);
  useEffect(() => {
    if (state.key !== key) return;
    try { localStorage.setItem(key, JSON.stringify(state.value)); } catch { /* Private/storage-limited sessions still work. */ }
  }, [key, state]);
  return [layers, next => setState(previous => {
    const value = previous.key === key ? previous.value : read(key);
    return { key, value: typeof next === "function" ? next(value) : next };
  })];
}
