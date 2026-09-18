import type { Selection } from "./render/sceneRenderer";
import type { SpatialPayload } from "./sceneData";

export type SceneMode = "overview" | "rack";
export type SelectionView = { locationId: string; mode: SceneMode; selection: Selection };

/** Adjust only the UI filter inside the already-authorized scene response. */
export function selectionView(payload: SpatialPayload, selection: Selection, current: { locationId: string; mode: SceneMode }): SelectionView | null {
  let locationId = current.locationId;
  let mode = current.mode;
  let targetLocation: string | undefined;
  if (selection.kind === "room") {
    const room = payload.locations.find(item => item.id === selection.id);
    if (!room) return null;
    locationId = room.id;
    mode = "overview";
  } else if (selection.kind === "rack") {
    const rack = payload.racks.find(item => item.id === selection.id);
    if (!rack) return null;
    targetLocation = rack.location_id;
  } else if (selection.kind === "device" || selection.kind === "port") {
    const deviceId = selection.kind === "port" ? payload.ports.find(port => port.id === selection.id)?.device_id : selection.id;
    const device = payload.devices.find(item => item.id === deviceId);
    if (!device) return null;
    targetLocation = payload.racks.find(item => item.id === device.rack_id)?.location_id ?? device.location_id;
  } else if (selection.kind === "cable") {
    const cable = payload.cables.find(item => item.id === selection.id);
    if (!cable) return null;
    mode = "overview";
    const endpointRooms = ["A", "B"].map(side => {
      const terminal = cable.terminations.find(item => item.side === side);
      const device = terminal && payload.devices.find(item => item.id === terminal.device_id);
      const port = terminal && payload.ports.find(item => item.id === terminal.port_id && item.device_id === device?.id);
      return port && payload.racks.find(item => item.id === device?.rack_id)?.location_id;
    });
    if (endpointRooms.every((room): room is string => Boolean(room))) {
      if (new Set(endpointRooms).size > 1) locationId = "";
      else targetLocation = endpointRooms[0];
    }
  } else {
    const pathway = payload.pathways.find(item => item.id === selection.id);
    if (!pathway) return null;
    targetLocation = pathway.location_id;
  }
  if (locationId && targetLocation && locationId !== targetLocation) locationId = targetLocation;
  return { locationId, mode, selection };
}
