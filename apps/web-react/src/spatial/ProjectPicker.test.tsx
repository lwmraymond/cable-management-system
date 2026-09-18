import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ProjectPicker } from "./ProjectPicker";
import { ApiError } from "../api/client";
const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", async importOriginal => ({ ...await importOriginal<typeof import("../api/client")>(), createApiClient: () => ({ request }) }));
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => request.mockReset());
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());
const getContext = () => ({ tenantId: "workspace", actorId: "owner" });
it("auto-selects the only active project while keeping creation unavailable to a viewer", async () => {
  request.mockResolvedValueOnce({ projects: [{ id: "p", project_number: "P-01", name: "Project", status: "active" }], can_create: false, truncated: false });
  const onChange = vi.fn();
  render(<ProjectPicker getContext={getContext} value="" onChange={onChange} disabled={false} />);
  await screen.findByText(/请联系工作区管理员创建项目/);
  expect(onChange).toHaveBeenCalledExactlyOnceWith("p");
  expect(screen.queryByRole("button", { name: "新建项目" })).not.toBeInTheDocument();
});
it("keeps failed creation visible and never selects a project that was not created", async () => {
  request.mockResolvedValueOnce({ projects: [], can_create: true, truncated: false }).mockRejectedValueOnce(new ApiError("duplicate", 409));
  const onChange = vi.fn();
  render(<ProjectPicker getContext={getContext} value="" onChange={onChange} disabled={false} />);
  fireEvent.click(await screen.findByRole("button", { name: "新建项目" }));
  fireEvent.change(screen.getByLabelText("项目编号"), { target: { value: "HPC-NET-01" } });
  fireEvent.change(screen.getByLabelText("项目名称"), { target: { value: "HPC" } });
  fireEvent.click(screen.getByRole("button", { name: "创建并选择" }));
  await screen.findByText(/项目编号已存在/);
  expect(screen.getByLabelText("项目编号")).toHaveValue("HPC-NET-01");
  expect(onChange.mock.calls.every(call => call[0] === "")).toBe(true);
});
