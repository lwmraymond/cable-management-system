import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RackPlacementDialog } from "./RackPlacementDialog";
import type { SpatialLocation, SpatialRack } from "./sceneData";
const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockReset(); });
afterEach(cleanup);
afterAll(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const rack: SpatialRack = { id: "rack-b", location_id: "room-a", rack_identifier: "RACK-B", name: "B 机柜", height_u: 42, width_mm: 600, depth_mm: 1000, position_x: 4, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active", version: 7 };
const room: SpatialLocation = { id: "room-a", parent_id: null, identifier: "ROOM-A", name: "A 机房", location_type: "room", dimensions: { width_m: 10, depth_m: 8, height_m: 3.6 }, coordinates: {}, transform_3d: {} };
const getContext = () => ({ tenantId: "tenant-a", actorId: "owner-a" });

describe("rack position save boundary", () => {
  it("submits edited coordinates and the last loaded version before refreshing", async () => {
    const onSaved = vi.fn(), onClose = vi.fn();
    request.mockResolvedValue({ ...rack, position_x: 5.5, rotation: 90, version: 8 });
    render(<RackPlacementDialog rack={rack} room={room} getContext={getContext} onSaved={onSaved} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("X 坐标（米）"), { target: { value: "5.5" } });
    fireEvent.change(screen.getByLabelText("朝向（度）"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/scene/racks/rack-b", { method: "PATCH", body: JSON.stringify({ position_x: 5.5, position_y: 1, position_z: 0, rotation: 90, expected_version: 7 }) }));
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("preserves the proposed position and open dialog after a collision rejection", async () => {
    const onSaved = vi.fn(), onClose = vi.fn();
    request.mockRejectedValue(new Error("Rack footprint overlaps an existing or requested rack"));
    render(<RackPlacementDialog rack={rack} room={room} getContext={getContext} onSaved={onSaved} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("X 坐标（米）"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));
    await screen.findByText("Rack footprint overlaps an existing or requested rack");
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(Number((screen.getByLabelText("X 坐标（米）") as HTMLInputElement).value)).toBe(1);
    expect(onSaved).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled();
    expect(rack.position_x).toBe(4);
  });
});
