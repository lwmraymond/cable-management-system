import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { LocationRecord } from "../types";
import type { SceneCreateDrawer, SceneCreated } from "./SceneCreateDrawer";
import { SpatialDirectoryRoomCreate } from "./SpatialDirectoryRoomCreate";

const api = vi.hoisted(() => ({ create: vi.fn(), request: vi.fn() }));
const drawer = vi.hoisted(() => ({ render: vi.fn(), result: { kind: "room", locationId: "created-room", id: "fallback-id" } as SceneCreated }));
vi.mock("../api/client", () => ({ createApiClient: api.create }));
vi.mock("./SceneCreateDrawer", () => ({ SceneCreateDrawer: (props: ComponentProps<typeof SceneCreateDrawer>) => {
  drawer.render(props);
  return <section role="dialog" aria-label="新建房间">
    <button onClick={() => { props.onCreated(drawer.result); props.onClose(); }}>模拟保存成功</button>
    <button onClick={props.onClose}>取消</button>
  </section>;
} }));

beforeEach(() => {
  vi.clearAllMocks();
  api.create.mockReturnValue({ request: api.request });
  drawer.result = { kind: "room", locationId: "created-room", id: "fallback-id" };
});
afterEach(cleanup);

const getContext = () => ({ tenantId: "tenant-a", projectId: "project-a", locationId: "old-room" });
const locations: LocationRecord[] = [
  { id: "building-a", name: "办公楼", identifier: "BLD-A", location_type: "building" },
  { id: "room-a", name: "机房", identifier: "RM-A", location_type: "room", parent_id: "building-a" },
];
function openDrawer() { fireEvent.click(screen.getByRole("button", { name: "创建房间" })); }
function drawerProps(): ComponentProps<typeof SceneCreateDrawer> { return drawer.render.mock.lastCall![0]; }

describe("room creation from the location directory", () => {
  it("opens an empty workspace's room form without preloading a scene and reports only the saved location", () => {
    const onCreated = vi.fn();
    render(<SpatialDirectoryRoomCreate locations={[]} getContext={getContext} onCreated={onCreated} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    openDrawer();
    expect(drawerProps()).toMatchObject({
      kind: "room", locationId: "", getContext,
      payload: {
        scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null },
        locations: [], racks: [], devices: [], ports: [], pathways: [], cables: [], truncated: [],
      },
    });
    expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "模拟保存成功" }));
    expect(onCreated).toHaveBeenCalledExactlyOnceWith("created-room");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.create).not.toHaveBeenCalled();
    expect(api.request).not.toHaveBeenCalled();
  });

  it("uses an existing building as the parent without inserting a floor or inventing geometry", () => {
    const before = structuredClone(locations);
    render(<SpatialDirectoryRoomCreate locations={locations} parentId="building-a" getContext={getContext} onCreated={vi.fn()} />);
    openDrawer();
    expect(drawerProps().locationId).toBe("building-a");
    expect(drawerProps().payload.locations).toEqual(locations.map(location => ({
      ...location, parent_id: location.parent_id ?? null, dimensions: {}, coordinates: {}, transform_3d: {},
    })));
    expect(locations).toEqual(before);
    expect(api.request).not.toHaveBeenCalled();
  });

  it.each([undefined, "missing-parent"])("does not inherit the old scope when parent %s is absent from the directory", parentId => {
    render(<SpatialDirectoryRoomCreate locations={locations} parentId={parentId} getContext={getContext} onCreated={vi.fn()} />);
    openDrawer();
    expect(drawerProps().locationId).toBe("");
    expect(drawerProps().payload.scope.location_id).toBeNull();
  });

  it("closes on cancel without writing or reporting a new location", () => {
    const onCreated = vi.fn();
    render(<SpatialDirectoryRoomCreate locations={locations} getContext={getContext} onCreated={onCreated} />);
    openDrawer();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
    expect(api.create).not.toHaveBeenCalled();
    expect(api.request).not.toHaveBeenCalled();
  });

  it("keeps creation disabled while directory data is unavailable", () => {
    render(<SpatialDirectoryRoomCreate locations={[]} disabled getContext={getContext} onCreated={vi.fn()} />);
    expect(screen.getByRole("button", { name: "创建房间" })).toBeDisabled();
    openDrawer();
    expect(drawer.render).not.toHaveBeenCalled();
  });

  it.each([
    [{ kind: "room", id: "real-response-id" }, "real-response-id"],
    [{ kind: "room" }, undefined],
  ] as const)("forwards a response id when present without inventing one", (result, expected) => {
    drawer.result = result;
    const onCreated = vi.fn();
    render(<SpatialDirectoryRoomCreate locations={[]} getContext={() => ({ tenantId: "tenant-a" })} onCreated={onCreated} />);
    openDrawer();
    expect(drawerProps().payload.scope.project_id).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "模拟保存成功" }));
    if (expected) expect(onCreated).toHaveBeenCalledExactlyOnceWith(expected);
    else expect(onCreated).not.toHaveBeenCalled();
  });
});
