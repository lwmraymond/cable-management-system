import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "./DashboardPage";
import type { InfrastructureContext } from "../api/context";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
const getContext = (): InfrastructureContext => ({ tenantId: "tenant-1", projectId: "project-1", locationId: "room-1", actorId: "actor-1" });
const response = {
  counts: { buildings: 3, telecom_rooms: 6, racks: 14, devices: 21, active_cables: 18, open_work_orders: 0, failed_tests: 0, expiring_access: 1 },
  recent_work_orders: [{ id: "order-1", number: "WO-001", title: "核对二楼链路", status: "completed", due_at: "2026-10-01T10:00:00" }],
};
function renderPage(context = getContext) { return render(<MemoryRouter><DashboardPage getContext={context} /></MemoryRouter>); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockReset(); });
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());

describe("DashboardPage", () => {
  it("shows the backend counts with accurate tenant, cable, test and grant meanings", async () => {
    request.mockResolvedValue(response);
    renderPage();
    expect(await screen.findByRole("article", { name: "未拆除线缆" })).toHaveTextContent("18");
    expect(screen.getByRole("article", { name: "建筑" })).toHaveTextContent("3栋");
    expect(screen.getByRole("article", { name: "建筑" })).toHaveTextContent("通信间 6 · TR / MDF / ER / MMR");
    expect(screen.getByRole("article", { name: "机柜" })).toHaveTextContent("14台");
    expect(screen.getByRole("article", { name: "设备" })).toHaveTextContent("21台");
    expect(screen.getByText(/限定范围协作方只统计获授权资料/)).toBeInTheDocument();
    expect(screen.getByText("累计失败记录，不代表待处理数量")).toBeInTheDocument();
    expect(screen.getByText("状态为有效，未按到期时间筛选")).toBeInTheDocument();
    expect(screen.queryByText(/到期授权|健康率|在线率/)).not.toBeInTheDocument();
    expect(screen.getByText("WO-001")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("核对二楼链路")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith("/dashboard", { signal: expect.any(AbortSignal) });
  });

  it("distinguishes real zero counts from absent, invalid, negative and noninteger counts", async () => {
    request.mockResolvedValue({ counts: { buildings: 0, telecom_rooms: 0, racks: null, devices: -1, active_cables: 2.5, open_work_orders: 0, failed_tests: Number.NaN, expiring_access: "1" } });
    renderPage();
    const building = await screen.findByRole("article", { name: "建筑" });
    expect(building).toHaveTextContent("0栋");
    expect(building).toHaveTextContent("通信间 0");
    expect(within(building).queryByText("未返回")).not.toBeInTheDocument();
    for (const title of ["机柜", "设备", "未拆除线缆"]) expect(within(screen.getByRole("article", { name: title })).getByText("未返回")).toBeInTheDocument();
    expect(screen.getByText("近期工单数据未返回。")).toBeInTheDocument();
    expect(screen.getByText(/部分统计未返回/)).toBeInTheDocument();
    expect(screen.queryByText("暂无工单记录。")).not.toBeInTheDocument();
  });

  it("keeps real work entry links available while loading without displaying invented zeros", () => {
    request.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载工作台数据");
    expect(screen.queryByRole("article", { name: "机柜" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新数据" })).toBeDisabled();
    const links = { "进入三维工作区": "/3d", "空间台账": "/locations", "平面图规划": "/floor-plans", "机柜与设备": "/racks", "线缆查询": "/cables", "光纤熔接": "/fiber", "光纤拓扑": "/fiber-topology" };
    for (const [label, href] of Object.entries(links)) expect(screen.getByRole("link", { name: new RegExp(label) })).toHaveAttribute("href", href);
  });

  it("allows an initial request failure to be retried and shows only the successful response", async () => {
    request.mockRejectedValueOnce(new Error("没有工作台读取权限")).mockResolvedValueOnce(response);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("没有工作台读取权限");
    expect(screen.queryByRole("article", { name: "建筑" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试加载" }));
    expect(await screen.findByRole("article", { name: "机柜" })).toHaveTextContent("14台");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("does not present old counts or recent records as current after a refresh failure", async () => {
    request.mockResolvedValueOnce(response).mockRejectedValueOnce(new Error("网络连接中断"));
    renderPage();
    await screen.findByText("WO-001");
    fireEvent.click(screen.getByRole("button", { name: "刷新数据" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("网络连接中断");
    expect(screen.queryByRole("article", { name: "机柜" })).not.toBeInTheDocument();
    expect(screen.queryByText("WO-001")).not.toBeInTheDocument();
    expect(screen.getByText("记录暂不可用，请重试加载。")).toBeInTheDocument();
  });

  it("reloads when project, location or actor changes within the same tenant and hides previous records", async () => {
    const pending = deferred<typeof response>();
    request.mockResolvedValueOnce(response).mockReturnValueOnce(pending.promise).mockResolvedValue(response);
    const view = renderPage();
    await screen.findByText("WO-001");
    const next = () => ({ ...getContext(), locationId: "room-2" });
    view.rerender(<MemoryRouter><DashboardPage getContext={next} /></MemoryRouter>);
    expect(screen.queryByText("WO-001")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载工作台数据");
    await act(async () => pending.resolve({ ...response, counts: { ...response.counts, racks: 2 }, recent_work_orders: [] }));
    expect(screen.getByRole("article", { name: "机柜" })).toHaveTextContent("2台");
    expect(screen.getByText("暂无工单记录。")).toBeInTheDocument();
    const projectChanged = () => ({ ...next(), projectId: "project-2" });
    view.rerender(<MemoryRouter><DashboardPage getContext={projectChanged} /></MemoryRouter>);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    const actorChanged = () => ({ ...projectChanged(), actorId: "actor-2" });
    view.rerender(<MemoryRouter><DashboardPage getContext={actorChanged} /></MemoryRouter>);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
  });

  it("handles a missing response shape and an explicitly empty work-order list separately", async () => {
    request.mockResolvedValueOnce({ racks: 900 }).mockResolvedValueOnce({ counts: response.counts, recent_work_orders: [] });
    renderPage();
    const metric = await screen.findByRole("article", { name: "机柜" });
    expect(metric).toHaveTextContent("未返回");
    expect(screen.queryByText("900")).not.toBeInTheDocument();
    expect(screen.getByText("近期工单数据未返回。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新数据" }));
    expect(await screen.findByText("暂无工单记录。")).toBeInTheDocument();
  });

  it("shows unknown work-order fields without inventing titles, state or links", async () => {
    request.mockResolvedValue({ ...response, recent_work_orders: [{ id: "order-2", status: "__proto__" }, null] });
    renderPage();
    expect(await screen.findAllByText("工单标题未返回")).toHaveLength(2);
    expect(screen.getAllByText("状态未知")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: /工单/ })).not.toBeInTheDocument();
  });
});
