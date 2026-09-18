import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSpatialLayers } from "./useSpatialLayers";
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
it("persists preferences per workspace and actor without carrying them across identities", () => {
  const first = { tenantId: "a", actorId: "alice" };
  const hook = renderHook(({ context }) => useSpatialLayers(context), { initialProps: { context: first } });
  act(() => hook.result.current[1](v => ({ ...v, labels: false })));
  hook.rerender({ context: { tenantId: "b", actorId: "alice" } });
  expect(hook.result.current[0].labels).toBe(true);
  hook.rerender({ context: { tenantId: "a", actorId: "bob" } });
  expect(hook.result.current[0].labels).toBe(true);
  hook.rerender({ context: first });
  expect(hook.result.current[0].labels).toBe(false);
  hook.unmount();
  expect(renderHook(() => useSpatialLayers(first)).result.current[0].labels).toBe(false);
});
it("works when browser storage is unavailable", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  const hook = renderHook(() => useSpatialLayers({ tenantId: "a" }));
  act(() => hook.result.current[1](v => ({ ...v, labels: false })));
  expect(hook.result.current[0].labels).toBe(false);
});
