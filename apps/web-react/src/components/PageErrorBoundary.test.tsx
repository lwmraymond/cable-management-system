import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { lazy, Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageErrorBoundary } from "./PageErrorBoundary";

beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function BrokenPage(): never { throw new Error("internal token=do-not-display"); }

describe("page recovery", () => {
  it("keeps sibling navigation available without exposing internal error details", () => {
    render(<><nav><a href="/app-next/locations">位置管理</a></nav><PageErrorBoundary><BrokenPage /></PageErrorBoundary></>);
    expect(screen.getByRole("alert")).toHaveTextContent("页面暂时无法显示");
    expect(screen.getByRole("link", { name: "位置管理" })).toBeVisible();
    expect(screen.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/app-next/");
    expect(screen.getByRole("button", { name: "重新加载页面" })).toBeEnabled();
    expect(screen.queryByText(/do-not-display/)).not.toBeInTheDocument();
  });
  it("recovers on navigation to a different page key", () => {
    const page = render(<PageErrorBoundary key="old"><BrokenPage /></PageErrorBoundary>);
    page.rerender(<PageErrorBoundary key="new"><h1>新页面</h1></PageErrorBoundary>);
    expect(screen.getByRole("heading", { name: "新页面" })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("catches a failed deferred page instead of leaving the loading state forever", async () => {
    const OfflinePage = lazy(() => Promise.reject(new Error("Failed to fetch dynamically imported module")));
    render(<PageErrorBoundary><Suspense fallback={<p role="status">加载中</p>}><OfflinePage /></Suspense></PageErrorBoundary>);
    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
