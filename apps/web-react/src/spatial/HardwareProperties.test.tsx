import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HardwareProperties } from "./HardwareProperties";
import type { SpatialPayload } from "./sceneData";
const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
beforeAll(() => vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))));
beforeEach(() => { request.mockReset(); });
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());
const getContext = () => ({ tenantId: "tenant-a", actorId: "owner-a" });
function payload(version: number | undefined = 4): SpatialPayload {
  return { scope: { tenant_id: "tenant-a", project_id: null, location_id: null }, locations: [], racks: [], devices: [], ports: [], cables: [], truncated: [], pathways: [{ id: "path-a", location_id: "room-a", identifier: "TRAY-A", name: "顶部桥架", type: "basket_tray", segments: [], version, cable_policy: { allows_cables: true, allowed_media: ["copper", "fiber"] } }] };
}
describe("hardware cable policy", () => {
  it("updates allowed media with the loaded version and refreshes only after success", async () => {
    const onSaved = vi.fn(); request.mockResolvedValue({ version: 5 });
    render(<HardwareProperties selection={{ kind: "pathway", id: "path-a" }} payload={payload()} getContext={getContext} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "铜缆" }));
    fireEvent.click(screen.getByRole("button", { name: "保存布线属性" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/scene/pathways/path-a/cable-policy", { method: "PATCH", body: JSON.stringify({ expected_version: 4, allows_cables: true, allowed_media: ["fiber"] }) }));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });
  it("retains attempted settings after a version conflict without reporting a save", async () => {
    const onSaved = vi.fn(); request.mockRejectedValue(new Error("Resource version has changed; reload before retrying"));
    render(<HardwareProperties selection={{ kind: "pathway", id: "path-a" }} payload={payload()} getContext={getContext} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "允许布放线缆" }));
    fireEvent.click(screen.getByRole("button", { name: "保存布线属性" }));
    await screen.findByText("Resource version has changed; reload before retrying");
    expect(screen.getByRole("checkbox", { name: "允许布放线缆" })).not.toBeChecked();
    expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ expected_version: 4, allows_cables: false, allowed_media: [] });
    expect(onSaved).not.toHaveBeenCalled();
  });
  it("refreshes the parent after a completed write even if selection changed during saving", async () => {
    const onSaved = vi.fn(); let resolve!: (value: unknown) => void;
    request.mockReturnValue(new Promise(value => { resolve = value; }));
    const mounted = render(<HardwareProperties selection={{ kind: "pathway", id: "path-a" }} payload={payload()} getContext={getContext} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole("button", { name: "保存布线属性" }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    mounted.unmount();
    resolve({ version: 5 });
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  });
  it("does not submit without a loaded version", () => {
    const data = payload(); data.pathways[0].version = undefined;
    render(<HardwareProperties selection={{ kind: "pathway", id: "path-a" }} payload={data} getContext={getContext} onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: "保存布线属性" })).toBeDisabled();
    expect(screen.getByText("请刷新场景后再保存属性。")).toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });
});
