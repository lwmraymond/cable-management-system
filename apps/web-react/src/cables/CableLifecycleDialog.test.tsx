import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CableLifecycleDialog, type CableLifecyclePreview } from "./CableLifecycleDialog";
const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => request.mockReset());
afterEach(cleanup);
afterAll(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const cable = { id: "cable-a", identifier: "CB-001" };
const context = { tenantId: "tenant-a", actorId: "owner-a", projectId: "project-a", locationId: "floor-a" };
const getContext = () => context;
function preview(overrides: Partial<CableLifecyclePreview> = {}): CableLifecyclePreview {
  return { ...cable, version: 3, action: "delete", status: "planned", allowed: true, blockers: [], endpoints: [{ side: "A", device_name: "交换机 A", port_label: "Gi1" }, { side: "B", device_name: "配线架 B", port_label: "01" }], route_segment_count: 2, ...overrides };
}
function pending<T>() { let resolve!: (value: T) => void; return { promise: new Promise<T>(done => { resolve = done; }), resolve: (value: T) => resolve(value) }; }
function open() { const onCompleted = vi.fn(), onClose = vi.fn(); render(<CableLifecycleDialog cable={cable} getContext={getContext} onCompleted={onCompleted} onClose={onClose} />); return { onCompleted, onClose }; }

describe("cable lifecycle confirmation", () => {
  it("loads impact before enabling deletion and submits exactly the previewed version once", async () => {
    const load = pending<CableLifecyclePreview>(), save = pending<unknown>();
    request.mockReturnValueOnce(load.promise).mockReturnValueOnce(save.promise);
    const { onCompleted, onClose } = open();
    expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
    expect(request).toHaveBeenCalledWith("/cables/cable-a/deletion-preview", { signal: expect.any(AbortSignal) });
    await act(async () => load.resolve(preview()));
    expect(screen.getByText("A 端：交换机 A / Gi1")).toBeInTheDocument();
    expect(screen.getByText("2 个登记路径段")).toBeInTheDocument();
    expect(screen.getByText(/历史及审计记录保留/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认删除" });
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(request).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenLastCalledWith("/cables/cable-a?expected_version=3", { method: "DELETE" });
    expect(screen.getByRole("button", { name: /取\s*消/ })).toBeDisabled();
    await act(async () => save.resolve({ id: cable.id, action: "delete", version: 4 }));
    expect(onCompleted).toHaveBeenCalledOnce(); expect(onClose).not.toHaveBeenCalled();
  });
  it("requires a trimmed reason and fresh preview after conflict while retaining the input", async () => {
    request.mockResolvedValueOnce(preview({ action: "remove", status: "in_service" })).mockRejectedValueOnce(new Error("线缆版本已改变"))
      .mockResolvedValueOnce(preview({ action: "remove", status: "in_service", version: 8 })).mockResolvedValueOnce({ id: cable.id, action: "remove", version: 9 });
    const { onCompleted } = open();
    const reason = await screen.findByLabelText("拆除原因（必填）");
    expect(screen.getByRole("button", { name: "确认拆除" })).toBeDisabled();
    fireEvent.change(reason, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "确认拆除" })).toBeDisabled();
    fireEvent.change(reason, { target: { value: "  设备迁移，现场已拆除  " } });
    fireEvent.click(screen.getByRole("button", { name: "确认拆除" }));
    await screen.findByText("线缆版本已改变");
    expect(request).toHaveBeenLastCalledWith("/cables/cable-a/remove", { method: "POST", body: JSON.stringify({ expected_version: 3, reason: "设备迁移，现场已拆除" }) });
    expect(screen.getByLabelText("拆除原因（必填）")).toHaveValue("  设备迁移，现场已拆除  ");
    expect(screen.getByRole("button", { name: "确认拆除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认拆除" })); expect(request).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "刷新影响预览" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认拆除" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认拆除" }));
    await waitFor(() => expect(onCompleted).toHaveBeenCalledWith({ id: cable.id, action: "remove", version: 9 }));
    expect(request).toHaveBeenLastCalledWith("/cables/cable-a/remove", { method: "POST", body: JSON.stringify({ expected_version: 8, reason: "设备迁移，现场已拆除" }) });
  });
  it.each(["没有删除权限", "关联光纤接续，请先处理依赖"])("shows blockers without issuing a write: %s", async message => {
    request.mockResolvedValue(preview({ allowed: false, blockers: [{ code: "blocked", message }] }));
    open(); await screen.findByText(message);
    expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" })); expect(request).toHaveBeenCalledTimes(1);
  });
  it("retries preview errors and rejects mismatched cable data", async () => {
    request.mockRejectedValueOnce(new Error("预览暂时不可用")).mockResolvedValueOnce(preview({ id: "other-cable" })).mockResolvedValueOnce(preview());
    open(); await screen.findByText("预览暂时不可用");
    expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "刷新影响预览" }));
    await screen.findByText("线缆预览无效，请重新读取后核对。");
    expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "刷新影响预览" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled());
  });
  it("discards a late preview after selecting a different cable", async () => {
    const first = pending<CableLifecyclePreview>();
    request.mockReturnValueOnce(first.promise).mockResolvedValueOnce(preview({ id: "cable-b", identifier: "CB-002", allowed: false, blockers: [{ code: "denied", message: "B 不允许删除" }] }));
    const props = { getContext, onClose: vi.fn(), onCompleted: vi.fn() };
    const view = render(<CableLifecycleDialog cable={cable} {...props} />);
    view.rerender(<CableLifecycleDialog cable={{ id: "cable-b", identifier: "CB-002" }} {...props} />);
    await screen.findByText("B 不允许删除"); await act(async () => first.resolve(preview()));
    expect(screen.queryByText("CB-001")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
  });
  it("never delivers a late successful write to another account or workspace", async () => {
    const save = pending<unknown>();
    request.mockResolvedValueOnce(preview()).mockReturnValueOnce(save.promise).mockResolvedValueOnce(preview({ allowed: false, blockers: [{ code: "denied", message: "新账号无权限" }] }));
    const props = { cable, onClose: vi.fn(), onCompleted: vi.fn() };
    const view = render(<CableLifecycleDialog {...props} getContext={getContext} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    view.rerender(<CableLifecycleDialog {...props} getContext={() => ({ ...context, actorId: "other" })} />);
    await screen.findByText("新账号无权限"); await act(async () => save.resolve({ id: cable.id, action: "delete", version: 4 }));
    expect(props.onCompleted).not.toHaveBeenCalled(); expect(props.onClose).not.toHaveBeenCalled();
  });
  it("cancels without modifying the cable", async () => {
    request.mockResolvedValue(preview()); const { onClose, onCompleted } = open();
    await waitFor(() => expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    expect(onClose).toHaveBeenCalledOnce(); expect(onCompleted).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(1);
  });
});
