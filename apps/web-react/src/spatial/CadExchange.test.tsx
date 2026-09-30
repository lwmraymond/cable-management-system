import { App } from "antd";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { CadExchange } from "./CadExchange";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request, download: vi.fn() }) }));
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockResolvedValue({ dxf: { available: true }, ifc: { available: true }, dwg: { available: false } }); });
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());
const context = () => ({ tenantId: "tenant", actorId: "owner" });
it("does not treat a floor ID as an exportable room", async () => {
  render(<App><CadExchange getContext={context} rooms={[{ id: "a", name: "Room A" }, { id: "b", name: "Room B" }]} initialRoom="floor" onClose={vi.fn()} onApplied={vi.fn()} /></App>);
  await waitFor(() => expect(request).toHaveBeenCalled());
  expect(screen.getByText("选择单个房间")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出 DXF" })).toBeDisabled();
  expect(screen.queryByText("floor")).not.toBeInTheDocument();
});
it("selects the only valid room and truthfully disables DWG", async () => {
  render(<App><CadExchange getContext={context} rooms={[{ id: "a", name: "Room A" }]} initialRoom="floor" onClose={vi.fn()} onApplied={vi.fn()} /></App>);
  await waitFor(() => expect(screen.getByRole("button", { name: "导出 DXF" })).toBeEnabled());
  expect(screen.getByText("Room A")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "DWG 未启用" })).toBeDisabled();
});
