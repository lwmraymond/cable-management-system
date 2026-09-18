import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CablesPage } from "./CablesPage";
const { request, download } = vi.hoisted(() => ({ request: vi.fn(), download: vi.fn() }));
vi.mock("../api/client", () => ({ createApiClient: () => ({ request, download }) }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockReset(); download.mockReset(); });
afterEach(cleanup);
afterAll(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const context = { tenantId: "tenant-a", actorId: "owner-a", projectId: "project-a" };
const getContext = () => context;
const cable = { id: "cable-a", identifier: "CB-001", media_type: "Cat6A", installation_status: "planned" };
const preview = { ...cable, status: "planned", version: 2, action: "delete", allowed: true, blockers: [], endpoints: [], route_segment_count: 0 };
function open() { return render(<MemoryRouter><CablesPage getContext={getContext} /></MemoryRouter>); }

describe("cable list lifecycle entry", () => {
  it("previews a planned cable, deletes on confirmation and reloads the list", async () => {
    request.mockResolvedValueOnce([cable]).mockResolvedValueOnce(preview).mockResolvedValueOnce({ id: cable.id, action: "delete", version: 3 }).mockResolvedValueOnce([]);
    open(); fireEvent.click(await screen.findByRole("button", { name: "删除线缆 CB-001" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled());
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/cables", "/cables/cable-a/deletion-preview"]);
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await screen.findByText("No records in the selected tenant context");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/cables", "/cables/cable-a/deletion-preview", "/cables/cable-a?expected_version=2", "/cables"]);
  });
  it("uses removal for in-service cables and disables repeated removal for historical rows", async () => {
    request.mockResolvedValueOnce([{ ...cable, installation_status: "in_service" }, { ...cable, id: "old", identifier: "CB-OLD", installation_status: "removed" }])
      .mockResolvedValueOnce({ ...preview, action: "remove", status: "in_service" });
    open(); fireEvent.click(await screen.findByRole("button", { name: "拆除线缆 CB-001" }));
    await screen.findByLabelText("拆除原因（必填）");
    expect(screen.getByRole("button", { name: "拆除线缆 CB-OLD" })).toBeDisabled();
    expect(request).toHaveBeenCalledTimes(2);
  });
  it("clears selection and list when the actor changes within the same tenant", async () => {
    request.mockResolvedValueOnce([cable]).mockResolvedValueOnce(preview).mockResolvedValueOnce([]);
    const view = open(); fireEvent.click(await screen.findByRole("button", { name: "删除线缆 CB-001" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled());
    view.rerender(<MemoryRouter><CablesPage getContext={() => ({ ...context, actorId: "other" })} /></MemoryRouter>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("CB-001")).not.toBeInTheDocument();
    await screen.findByText("No records in the selected tenant context");
    expect(request.mock.calls.filter(([path]) => path === "/cables")).toHaveLength(2);
  });
  it("shows an export permission error without an unhandled rejection", async () => {
    request.mockResolvedValue([cable]); download.mockRejectedValueOnce(new Error("没有导出权限"));
    open(); await screen.findByText("CB-001");
    fireEvent.click(screen.getByRole("button", { name: /Cable Schedule CSV/ }));
    await screen.findByText("没有导出权限");
    expect(screen.getByRole("button", { name: /Cable Schedule CSV/ })).not.toBeDisabled();
  });
});
