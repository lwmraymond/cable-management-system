import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FloorPlanPage } from "./FloorPlanPage";
import type { FloorPlan } from "./floorPlanUi";
import type { InfrastructureContext } from "../api/context";
const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
const planId = "11111111-2222-4333-8444-555555555555";
const otherPlanId = "22222222-2222-4333-8444-555555555555";
const rackId = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const extraRackId = "bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const revisionId = "cccccccc-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const context: InfrastructureContext = { tenantId: "tenant-a", actorId: "actor-a", projectId: "dddddddd-bbbb-4ccc-8ddd-eeeeeeeeeeee", locationId: "eeeeeeee-bbbb-4ccc-8ddd-eeeeeeeeeeee" };
const getContext = () => context;
function fixture(id = planId): FloorPlan {
  return {
    id, project_id: context.projectId!, location_id: context.locationId!, name: id === planId ? "机房 A 平面图" : "机房 B 平面图",
    units: "mm", canvas_width: 1000, canvas_height: 800, background_reference: null, status: "draft", version: 7,
    current_revision_number: 2, published_revision_number: 1, published_at: "2026-09-17T08:00:00Z",
    current_revision: { id: revisionId, revision_number: 2, checksum_sha256: "0123456789abcdef", change_summary: "初始布置", created_at: "2026-09-18T08:00:00Z", restored_from_revision_id: null },
    document: { schema_version: 1, grid_size: 10, paths: [], objects: [{ id: "rack-placement-a", object_type: "rack", object_id: rackId, x: 20, y: 30, width: 60, height: 100, rotation: 0, z_index: 0, locked: false, label: "Rack A" }] },
  };
}
let permissions: string[];
let stored: FloorPlan;
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("PointerEvent", MouseEvent);
  Object.defineProperty(SVGElement.prototype, "setPointerCapture", { configurable: true, value: vi.fn() });
});
beforeEach(() => {
  stored = fixture(); permissions = ["floor_plan:read", "floor_plan:write", "floor_plan:publish"];
  request.mockReset().mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === "/tenants/current") return { principal: { permissions } };
    if (path === `/floor-plans/${planId}/revisions`) return { items: [stored.current_revision] };
    if (path === `/floor-plans/${otherPlanId}/revisions`) return { items: [fixture(otherPlanId).current_revision] };
    if (path === `/floor-plans/${planId}/draft`) {
      const body = JSON.parse(String(options?.body));
      stored = { ...stored, document: body.document, version: 8, current_revision_number: 3 };
      return structuredClone(stored);
    }
    if (path === `/floor-plans/${planId}/publish`) {
      stored = { ...stored, version: 9, status: "published", published_revision_number: stored.current_revision_number };
      return structuredClone(stored);
    }
    if (path === `/floor-plans/${planId}`) return structuredClone(stored);
    if (path === `/floor-plans/${otherPlanId}`) return fixture(otherPlanId);
    throw new Error(`Unexpected request: ${path}`);
  });
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => {
  delete (SVGElement.prototype as unknown as { setPointerCapture?: unknown }).setPointerCapture;
  vi.unstubAllGlobals();
});
// Ant Design inserts spaces in two-character labels. Reading the button's own
// text also avoids repeated full-page style walks in these SVG/form flows.
function button(name: string) {
  return screen.getByText(actual => actual.replace(/\s/g, "") === name.replace(/\s/g, ""), { selector: "button > span" }).closest("button")!;
}
async function openPlan() {
  render(<FloorPlanPage getContext={getContext} />);
  await waitFor(() => expect(button("加载")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Floor Plan UUID"), { target: { value: planId } });
  fireEvent.click(button("加载"));
  await screen.findByText("Rack A");
  await waitFor(() => expect(button("加载")).toBeEnabled());
}
function canvas() { return screen.getByLabelText("Floor Plan canvas"); }
function rack() { return screen.getByText("Rack A").closest("g")!; }
function editRack() {
  fireEvent.pointerDown(rack(), { clientX: 20, clientY: 30, pointerId: 1 });
  fireEvent.pointerUp(canvas());
  fireEvent.change(screen.getByLabelText("Selected X"), { target: { value: "80" } });
  expect(rack()).toHaveAttribute("transform", "translate(80 30) rotate(0 30 50)");
}
function writes() { return request.mock.calls.filter(([, options]) => options?.method && options.method !== "GET"); }
function editedPlan(): FloorPlan {
  const original = fixture();
  return { ...original, version: 8, document: { ...original.document, objects: [{ ...original.document.objects[0], x: 80 }] } };
}
describe("floor plan editing safety", () => {
  it("blocks unsaved publishing, publishes the saved version and removes unload protection after saving", async () => {
    await openPlan(); editRack();
    expect(screen.getByText("画布有未保存改动，请先保存新 Revision，再发布。")).toBeInTheDocument();
    expect(button("发布当前 Revision")).toBeDisabled();
    fireEvent.click(button("发布当前 Revision"));
    expect(writes()).toEqual([]);
    expect(window.dispatchEvent(new Event("beforeunload", { cancelable: true }))).toBe(false);
    fireEvent.click(button("保存新 Revision"));
    await waitFor(() => expect(button("发布当前 Revision")).toBeEnabled());
    expect(request).toHaveBeenCalledWith(`/floor-plans/${planId}/draft`, {
      method: "PUT", body: JSON.stringify({ expected_version: 7, document: editedPlan().document, change_summary: "" }),
    });
    expect(window.dispatchEvent(new Event("beforeunload", { cancelable: true }))).toBe(true);
    fireEvent.click(button("发布当前 Revision"));
    await waitFor(() => expect(request).toHaveBeenCalledWith(`/floor-plans/${planId}/publish`, { method: "POST", body: JSON.stringify({ expected_version: 8 }) }));
    await waitFor(() => expect(button("发布当前 Revision")).toBeEnabled());
    expect(rack()).toHaveAttribute("transform", "translate(80 30) rotate(0 30 50)");
  }, 10000);
  it("lets a viewer select without dragging, changing coordinates or adding/removing resources", async () => {
    permissions = ["floor_plan:read"];
    await openPlan();
    fireEvent.pointerDown(rack(), { clientX: 20, clientY: 30, pointerId: 1 });
    fireEvent.pointerMove(canvas(), { clientX: 180, clientY: 200, pointerId: 1 });
    for (const label of ["Selected X", "Selected Y", "Physical resource UUID"]) expect(screen.getByLabelText(label)).toBeDisabled();
    expect(button("加入画布")).toBeDisabled(); expect(button("移除")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Selected X"), { target: { value: "160" } });
    fireEvent.click(button("移除"));
    expect(rack()).toHaveAttribute("transform", "translate(20 30) rotate(0 30 50)");
    expect(writes()).toEqual([]);
  });
  it("freezes canvas and form edits while saving so a response cannot discard newer edits", async () => {
    await openPlan(); editRack();
    let complete!: (result: FloorPlan) => void;
    request.mockImplementationOnce(() => new Promise<FloorPlan>(resolve => { complete = resolve; }));
    fireEvent.click(button("保存新 Revision"));
    await waitFor(() => expect(screen.getByLabelText("Selected X")).toBeDisabled());
    expect(button("移除")).toBeDisabled(); expect(screen.getByLabelText("Object width")).toBeDisabled();
    fireEvent.pointerDown(rack(), { clientX: 80, clientY: 30, pointerId: 1 });
    fireEvent.pointerMove(canvas(), { clientX: 250, clientY: 200, pointerId: 1 });
    fireEvent.change(screen.getByLabelText("Selected X"), { target: { value: "250" } });
    fireEvent.click(button("移除"));
    expect(rack()).toHaveAttribute("transform", "translate(80 30) rotate(0 30 50)");
    await act(async () => complete(editedPlan()));
    await waitFor(() => expect(button("保存新 Revision")).toBeEnabled());
    expect(rack()).toHaveAttribute("transform", "translate(80 30) rotate(0 30 50)");
    expect(writes()).toHaveLength(1);
  }, 10000);
  it("shows invalid resource IDs at the field and accepts correction without losing the drawing or writing", async () => {
    await openPlan();
    fireEvent.change(screen.getByLabelText("Physical resource UUID"), { target: { value: "not-a-resource" } });
    fireEvent.click(button("加入画布"));
    expect(screen.getByRole("alert")).toHaveTextContent("请输入有效的资源 UUID。");
    expect(screen.getByLabelText("Physical resource UUID")).toHaveAttribute("aria-invalid", "true");
    expect(rack()).toHaveAttribute("transform", "translate(20 30) rotate(0 30 50)");
    fireEvent.change(screen.getByLabelText("Physical resource UUID"), { target: { value: extraRackId } });
    fireEvent.change(screen.getByLabelText("Object label"), { target: { value: "Rack B" } });
    fireEvent.click(button("加入画布"));
    expect(screen.getByText("Rack B")).toBeInTheDocument();
    expect(screen.getByLabelText("Physical resource UUID")).toHaveAttribute("aria-invalid", "false");
    expect(writes()).toEqual([]);
  });
  it.each(["加载平面图", "恢复历史版本"])("keeps unsaved coordinates and sends no request after cancelling %s", async operation => {
    await openPlan(); editRack();
    const before = request.mock.calls.length;
    if (operation === "加载平面图") {
      fireEvent.change(screen.getByLabelText("Floor Plan UUID"), { target: { value: otherPlanId } });
      fireEvent.click(button("加载"));
    } else fireEvent.click(button("恢复"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(`${operation}将替换当前画布`);
    expect(request).toHaveBeenCalledTimes(before);
    fireEvent.click(within(dialog).getByRole("button", { name: "保留改动" }));
    expect(rack()).toHaveAttribute("transform", "translate(80 30) rotate(0 30 50)");
    expect(request).toHaveBeenCalledTimes(before);
    expect(button("发布当前 Revision")).toBeDisabled();
  });
  it("loads another plan only after explicit discard confirmation", async () => {
    await openPlan(); editRack();
    fireEvent.change(screen.getByLabelText("Floor Plan UUID"), { target: { value: otherPlanId } });
    fireEvent.click(button("加载"));
    const dialog = await screen.findByRole("dialog");
    expect(request).not.toHaveBeenCalledWith(`/floor-plans/${otherPlanId}`);
    fireEvent.click(within(dialog).getByRole("button", { name: "放弃改动并继续" }));
    await screen.findByText("机房 B 平面图 · r2 · v7");
    expect(rack()).toHaveAttribute("transform", "translate(20 30) rotate(0 30 50)");
    expect(writes()).toEqual([]);
  });
  it("preserves dirty drawing after a save failure and enables retry while publication stays blocked", async () => {
    await openPlan(); editRack();
    request.mockRejectedValueOnce(new Error("保存连接中断"));
    fireEvent.click(button("保存新 Revision"));
    await screen.findByText("保存连接中断");
    expect(button("保存新 Revision")).toBeEnabled(); expect(button("发布当前 Revision")).toBeDisabled();
    expect(screen.getByLabelText("Selected X")).toBeEnabled();
    expect(rack()).toHaveAttribute("transform", "translate(80 30) rotate(0 30 50)");
  });
});
