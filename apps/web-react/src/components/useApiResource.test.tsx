import { StrictMode, Suspense, startTransition, useLayoutEffect, useState } from "react";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApiResource } from "./useApiResource";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function queuedLoader() {
  const requests: Array<ReturnType<typeof deferred<string>> & { signal?: AbortSignal }> = [];
  const loader = vi.fn((signal?: AbortSignal) => {
    const request = { ...deferred<string>(), signal };
    requests.push(request);
    return request.promise;
  });
  return { loader, requests };
}

afterEach(cleanup);

describe("useApiResource request ownership", () => {
  it("supports legacy zero-argument loaders and stable dependency values", async () => {
    const loader = vi.fn(async () => "loaded");
    const { result, rerender } = renderHook(() => useApiResource(loader, ["tenant-a"]));
    expect(result.current).toMatchObject({ data: undefined, error: undefined, loading: true });
    await act(async () => {});
    expect(result.current).toMatchObject({ data: "loaded", error: undefined, loading: false });
    rerender();
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("aborts the previous context and ignores its late success after the new context succeeds", async () => {
    const { loader, requests } = queuedLoader();
    const { result, rerender } = renderHook(({ tenant }) => useApiResource(loader, [tenant]), { initialProps: { tenant: "a" } });
    rerender({ tenant: "b" });
    expect(requests[0].signal?.aborted).toBe(true);
    expect(requests[1].signal?.aborted).toBe(false);
    await act(async () => { requests[1].resolve("tenant-b"); });
    await act(async () => { requests[0].resolve("tenant-a"); });
    expect(result.current).toMatchObject({ data: "tenant-b", error: undefined, loading: false });
  });

  it("does not let an old failure finish the new request's loading or set its error", async () => {
    const { loader, requests } = queuedLoader();
    const { result, rerender } = renderHook(({ tenant }) => useApiResource(loader, [tenant]), { initialProps: { tenant: "a" } });
    rerender({ tenant: "b" });
    await act(async () => { requests[0].reject(new Error("old failure")); });
    expect(result.current).toMatchObject({ data: undefined, error: undefined, loading: true });
    await act(async () => { requests[1].resolve("tenant-b"); });
    expect(result.current).toMatchObject({ data: "tenant-b", error: undefined, loading: false });
  });

  it("does not let an old success erase the latest request's error", async () => {
    const { loader, requests } = queuedLoader();
    const { result, rerender } = renderHook(({ tenant }) => useApiResource(loader, [tenant]), { initialProps: { tenant: "a" } });
    rerender({ tenant: "b" });
    await act(async () => { requests[1].reject(new Error("new failure")); });
    await act(async () => { requests[0].resolve("tenant-a"); });
    expect(result.current).toMatchObject({ data: undefined, error: new Error("new failure"), loading: false });
  });

  it("hides old data and errors during the dependency-change render, before layout/passive effects", async () => {
    const { loader, requests } = queuedLoader();
    const renders: Array<{ tenant: string; data?: string; error?: Error; loading: boolean }> = [];
    const layoutViews: typeof renders = [];
    const { result, rerender } = renderHook(({ tenant }) => {
      const value = useApiResource(loader, [tenant]);
      const view = { tenant, data: value.data, error: value.error, loading: value.loading };
      renders.push(view);
      useLayoutEffect(() => { layoutViews.push(view); });
      return value;
    }, { initialProps: { tenant: "a" } });
    await act(async () => { requests[0].resolve("private tenant-a data"); });
    let refresh!: Promise<void>;
    act(() => { refresh = result.current.reload(); });
    await act(async () => { requests[1].reject(new Error("tenant-a error")); await refresh; });
    expect(result.current.data).toBe("private tenant-a data");
    expect(result.current.error).toBeDefined();
    rerender({ tenant: "b" });
    const expected = { tenant: "b", data: undefined, error: undefined, loading: true };
    expect(renders.find(view => view.tenant === "b")).toEqual(expected);
    expect(layoutViews.find(view => view.tenant === "b")).toEqual(expected);
    await act(async () => { requests[2].resolve("tenant-b data"); });
    expect(result.current.data).toBe("tenant-b data");
  });

  it("preserves same-context data during competing retries and accepts only the latest completion", async () => {
    const { loader, requests } = queuedLoader();
    const { result } = renderHook(() => useApiResource(loader));
    await act(async () => { requests[0].resolve("cached"); });
    let first!: Promise<void>, second!: Promise<void>;
    act(() => { first = result.current.reload(); });
    act(() => { second = result.current.reload(); });
    expect(requests[1].signal?.aborted).toBe(true);
    expect(result.current).toMatchObject({ data: "cached", error: undefined, loading: true });
    await act(async () => { requests[2].resolve("fresh"); await second; });
    await act(async () => { requests[1].reject(new Error("superseded retry")); await first; });
    expect(result.current).toMatchObject({ data: "fresh", error: undefined, loading: false });
  });

  it("retains successful data on refresh failure and clears the error on a subsequent retry", async () => {
    const { loader, requests } = queuedLoader();
    const { result } = renderHook(() => useApiResource(loader));
    await act(async () => { requests[0].resolve("cached"); });
    let refresh!: Promise<void>;
    act(() => { refresh = result.current.reload(); });
    await act(async () => { requests[1].reject("network unavailable"); await refresh; });
    expect(result.current).toMatchObject({ data: "cached", error: new Error("network unavailable"), loading: false });
    act(() => { refresh = result.current.reload(); });
    expect(result.current).toMatchObject({ data: "cached", error: undefined, loading: true });
    await act(async () => { requests[2].resolve("fresh"); await refresh; });
    expect(result.current).toMatchObject({ data: "fresh", error: undefined, loading: false });
  });

  it.each(["resolve", "reject"] as const)("aborts on unmount, ignores a late %s and makes a saved reload inert", async outcome => {
    const { loader, requests } = queuedLoader();
    const rendered = vi.fn();
    const { result, unmount } = renderHook(() => { rendered(); return useApiResource(loader); });
    const reload = result.current.reload;
    unmount();
    const renderCount = rendered.mock.calls.length;
    expect(requests[0].signal?.aborted).toBe(true);
    await act(async () => {
      if (outcome === "resolve") requests[0].resolve("late");
      else requests[0].reject(new Error("late"));
      await reload();
    });
    expect(loader).toHaveBeenCalledTimes(1);
    expect(rendered).toHaveBeenCalledTimes(renderCount);
  });

  it("uses the latest committed loader on retry without refetching merely for a new function identity", async () => {
    const original = vi.fn(async () => "original"), replacement = vi.fn(async () => "replacement");
    const { result, rerender } = renderHook(({ loader }) => useApiResource(loader, ["same-context"]), { initialProps: { loader: original } });
    await act(async () => {});
    const reload = result.current.reload;
    rerender({ loader: replacement });
    expect(replacement).not.toHaveBeenCalled();
    expect(result.current.reload).toBe(reload);
    await act(async () => { await reload(); });
    expect(replacement).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe("replacement");
  });

  it("does not allow a saved old-context reload to cancel or restart the current context", async () => {
    const { loader, requests } = queuedLoader();
    const { result, rerender } = renderHook(({ tenant }) => useApiResource(loader, [tenant]), { initialProps: { tenant: "a" } });
    const oldReload = result.current.reload;
    rerender({ tenant: "b" });
    await act(async () => { await oldReload(); });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(requests[1].signal?.aborted).toBe(false);
    await act(async () => { requests[1].resolve("b"); });
    expect(result.current.data).toBe("b");
  });

  it("survives StrictMode effect cleanup/replay without accepting the replayed request's predecessor", async () => {
    const { loader, requests } = queuedLoader();
    const { result } = renderHook(() => useApiResource(loader), { wrapper: StrictMode });
    expect(requests).toHaveLength(2);
    expect(requests[0].signal?.aborted).toBe(true);
    await act(async () => { requests[1].resolve("current"); });
    await act(async () => { requests[0].reject(new Error("aborted first mount")); });
    expect(result.current).toMatchObject({ data: "current", error: undefined, loading: false });
  });

  it("does not let an uncommitted suspended context interfere with the still-visible context", async () => {
    const { loader, requests } = queuedLoader();
    const suspended = new Promise<never>(() => {});
    let changeContext!: (tenant: string) => void;
    let visibleReload!: () => Promise<void>;
    function Resource({ tenant }: { tenant: string }) {
      const value = useApiResource(signal => loader(signal), [tenant]);
      useLayoutEffect(() => { visibleReload = value.reload; });
      if (tenant === "b") throw suspended;
      return <span>{value.data ?? "loading-a"}</span>;
    }
    function Host() {
      const [tenant, setTenant] = useState("a");
      changeContext = setTenant;
      return <Suspense fallback={<span>loading-b</span>}><Resource tenant={tenant} /></Suspense>;
    }
    render(<Host />);
    act(() => { startTransition(() => { changeContext("b"); }); });
    expect(screen.getByText("loading-a")).toBeTruthy();
    expect(loader).toHaveBeenCalledTimes(1);
    expect(requests[0].signal?.aborted).toBe(false);
    await act(async () => { requests[0].resolve("committed-a"); });
    expect(screen.getByText("committed-a")).toBeTruthy();
    let refresh!: Promise<void>;
    act(() => { refresh = visibleReload(); });
    await act(async () => { requests[1].resolve("refreshed-a"); await refresh; });
    expect(screen.getByText("refreshed-a")).toBeTruthy();
  });
});
