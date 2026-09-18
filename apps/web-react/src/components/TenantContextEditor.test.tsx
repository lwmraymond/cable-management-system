import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TenantContextEditor } from "./TenantContextEditor";
import type { InfrastructureContext } from "../api/context";
import type { LocationRecord } from "../types";

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(cleanup);
afterAll(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const context: InfrastructureContext = {
  tenantId: "11111111-1111-4111-8111-111111111111",
  projectId: "22222222-2222-4222-8222-222222222222",
  actorId: "33333333-3333-4333-8333-333333333333",
  locationId: "44444444-4444-4444-8444-444444444444",
};
const locations: LocationRecord[] = [
  { id: context.locationId!, identifier: "RM-A", name: "A 机房", location_type: "room" },
  { id: "55555555-5555-4555-8555-555555555555", identifier: "RM-B", name: "B 机房", location_type: "room" },
];
const open = () => fireEvent.click(screen.getByRole("button", { name: "工作范围：选择工作范围" }));
const advanced = () => screen.getByRole("button", { name: /高级设置 · 租户与项目/ });
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function chooseSecondRoom() {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "工作位置" }));
  fireEvent.click(await screen.findByText("B 机房 · RM-B"));
}

describe("TenantContextEditor user actions", () => {
  it("keeps advanced fields hidden by default and preserves identity when applying a new location", async () => {
    const onChange = vi.fn();
    render(<TenantContextEditor value={context} onChange={onChange} locations={locations} />);
    open();
    expect(advanced()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("textbox", { name: "租户 ID" })).not.toBeInTheDocument();
    await chooseSecondRoom();
    fireEvent.click(screen.getByRole("button", { name: "应用范围" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledExactlyOnceWith({ ...context, locationId: locations[1].id }));
  });

  it("discards canceled edits and restores the current context on reopening", async () => {
    const onChange = vi.fn();
    render(<TenantContextEditor value={context} onChange={onChange} locations={locations} />);
    open();
    fireEvent.click(advanced());
    fill("租户 ID", "66666666-6666-4666-8666-666666666666");
    fill("项目 ID", "77777777-7777-4777-8777-777777777777");
    fill("演示用户 ID", "88888888-8888-4888-8888-888888888888");
    await chooseSecondRoom();
    fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    expect(onChange).not.toHaveBeenCalled();
    open();
    expect(screen.getByLabelText("租户 ID")).toHaveValue(context.tenantId);
    expect(screen.getByLabelText("项目 ID")).toHaveValue(context.projectId);
    expect(screen.getByLabelText("演示用户 ID")).toHaveValue(context.actorId);
    expect(screen.getByLabelText("位置 ID")).toHaveValue(context.locationId);
    fireEvent.click(screen.getByRole("button", { name: "应用范围" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledExactlyOnceWith(context));
  });

  it("reveals invalid hidden tenant settings on submit without applying them", async () => {
    const onChange = vi.fn();
    render(<TenantContextEditor value={{ ...context, tenantId: "invalid-tenant" }} onChange={onChange} locations={locations} />);
    open();
    expect(advanced()).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "应用范围" }));
    await waitFor(() => expect(advanced()).toHaveAttribute("aria-expanded", "true"));
    expect(await screen.findByText("请输入完整的 UUID")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "租户 ID" })).toBeVisible();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("normalizes a cleared work location to undefined while preserving tenant/project/actor", async () => {
    const onChange = vi.fn();
    render(<TenantContextEditor value={context} onChange={onChange} locations={locations} />);
    open();
    fireEvent.mouseDown(screen.getByRole("img", { name: "close-circle" }));
    fireEvent.click(screen.getByRole("button", { name: "应用范围" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledExactlyOnceWith({ ...context, locationId: undefined }));
  });

  it("restores originally omitted optional fields after editing and canceling", async () => {
    const onChange = vi.fn();
    render(<TenantContextEditor value={{ tenantId: context.tenantId }} onChange={onChange} locations={locations} />);
    open();
    fireEvent.click(advanced());
    fill("项目 ID", context.projectId!);
    fill("演示用户 ID", context.actorId!);
    await chooseSecondRoom();
    fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    expect(onChange).not.toHaveBeenCalled();
    open();
    expect(screen.getByLabelText("项目 ID")).toHaveValue("");
    expect(screen.getByLabelText("演示用户 ID")).toHaveValue("");
    expect(screen.getByLabelText("位置 ID")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "应用范围" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledExactlyOnceWith({ tenantId: context.tenantId, projectId: undefined, actorId: undefined, locationId: undefined }));
  });

  it("clears canceled optional values when reopening with a context that omits them", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<TenantContextEditor value={context} onChange={onChange} locations={locations} />);
    open();
    fireEvent.click(advanced());
    fill("项目 ID", "77777777-7777-4777-8777-777777777777");
    fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    rerender(<TenantContextEditor value={{ tenantId: context.tenantId }} onChange={onChange} locations={locations} />);
    open();
    expect(screen.getByLabelText("项目 ID")).toHaveValue("");
    expect(screen.getByLabelText("演示用户 ID")).toHaveValue("");
    expect(screen.getByLabelText("位置 ID")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "应用范围" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledExactlyOnceWith({ tenantId: context.tenantId, projectId: undefined, actorId: undefined, locationId: undefined }));
  });
});
