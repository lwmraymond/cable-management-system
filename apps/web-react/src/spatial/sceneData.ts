import type { SceneData, ScenePath, SceneRack } from "./render/sceneRenderer";
import { portBlockedReason } from "./sceneCreate";
import { formatLength, segmentGeometryLength } from "./sceneLengths";
import { segmentForCable, type RoutePortion } from "./routePortions";

export interface SpatialLocation { id: string; parent_id: string | null; identifier: string; name: string; location_type: string; dimensions: Record<string, unknown>; coordinates: Record<string, unknown>; transform_3d: Record<string, unknown>; version?: number }
export interface SpatialRack { id: string; location_id: string; rack_identifier: string; name: string; height_u: number; width_mm: number; depth_mm: number; position_x: number; position_y: number; position_z: number; rotation: number; reserved_units: number[]; status: string; version?: number }
export interface CablePolicy { allows_cables: boolean; allowed_media: string[] }
export interface RoomEntrance { id: string; name: string; wall: "north" | "south" | "east" | "west"; offset_m: number; width_m: number; height_m: number }
export interface SpatialDevice { id: string; rack_id: string | null; location_id: string; identifier: string; name: string; device_type: string; start_u: number; rack_units: number; face: string; status: string; cable_policy?: CablePolicy; version?: number }
export interface SpatialPort { id: string; device_id: string; identifier: string; label: string; connector_type: string; front_or_rear: string; position_index: number; status: string; occupied?: boolean; media_type?: string }
export interface SpatialSegment { id: string; sequence: number; name: string; length_m: number; coordinates: { x: number; y: number; z?: number }[] }
export interface SpatialPathway { id: string; location_id: string; identifier: string; name: string; type: string; segments: SpatialSegment[]; cable_policy?: CablePolicy; version?: number }
export interface SpatialCable { id: string; identifier: string; media_type: string; construction: string; installation_status: string; length_m: number | null; test_status: string | null; terminations: { side: string; port_id: string; device_id: string; rack_id: string | null; location_id: string }[]; route_segment_ids: string[]; route_portions?: RoutePortion[]; route_scope?: "complete" | "partial" | "none"; endpoint_scope: "complete" | "partial" | "none" }
export interface SpatialPayload {
  scope: { tenant_id: string; project_id: string | null; location_id: string | null };
  locations: SpatialLocation[]; racks: SpatialRack[]; devices: SpatialDevice[]; ports: SpatialPort[];
  pathways: SpatialPathway[]; cables: SpatialCable[]; truncated: string[];
  port_page?: { total: number; next_cursor: string | null };
}
export type Point = [number, number, number];
export type RouteKind = "none" | "recorded" | "schematic";
const ROOM_TYPES = new Set(["room", "tr", "er", "mdf", "mmr", "data_hall", "entrance_facility"]);
export function sceneLocations(payload: SpatialPayload): SpatialLocation[] {
  const used = new Set([...payload.racks.map(rack => rack.location_id), ...payload.pathways.map(pathway => pathway.location_id)]);
  return payload.locations.filter(location => ROOM_TYPES.has(location.location_type) || used.has(location.id));
}
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && Math.abs(value) < 100_000;
const dimension = (value: unknown, fallback: number) => finite(value) && value > 0 ? value : fallback;

export function roomEntrances(room?: SpatialLocation): RoomEntrance[] {
  const entries = room?.dimensions.entrances;
  if (!Array.isArray(entries)) return [];
  return entries.filter((entry): entry is RoomEntrance => Boolean(entry && typeof entry === "object" && typeof entry.id === "string" && typeof entry.name === "string" && ["north", "south", "east", "west"].includes(entry.wall) && [entry.offset_m, entry.width_m, entry.height_m].every(finite) && entry.offset_m >= 0 && entry.width_m > 0 && entry.height_m > 0));
}

export function rackCapacity(rack: SpatialRack, devices: SpatialDevice[]) {
  const occupied = new Set<number>();
  for (const device of devices.filter(item => item.rack_id === rack.id)) {
    for (let u = device.start_u; u < device.start_u + device.rack_units; u++) if (u >= 1 && u <= rack.height_u) occupied.add(u);
  }
  const reserved = new Set(rack.reserved_units.filter(u => u >= 1 && u <= rack.height_u && !occupied.has(u)));
  return { used: occupied.size, reserved: reserved.size, free: Math.max(0, rack.height_u - occupied.size - reserved.size), percent: Math.round(occupied.size / rack.height_u * 100) };
}

/** Backend is Z-up in metres; Three.js uses Y-up. Invalid segments are never bridged. */
export function segmentPoints(segment: SpatialSegment, offset: [number, number] = [0, 0]): Point[] {
  if (segment.coordinates.length < 2 || segment.coordinates.some(p => !finite(p.x) || !finite(p.y) || !finite(p.z ?? 0))) return [];
  return segment.coordinates.map(p => [p.x + offset[0], p.z ?? 0, p.y + offset[1]]);
}

const pointDistance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

function routeEnds(parts: Point[][], directed = false): [Point, Point] | undefined {
  if (directed) {
    if (!parts.length || parts.some(part => part.length < 2) || parts.slice(1).some((part, index) => pointDistance(parts[index][parts[index].length - 1], part[0]) > 0.001)) return;
    return [parts[0][0], parts[parts.length - 1][parts[parts.length - 1].length - 1]];
  }
  if (!parts.length || parts.some(part => part.length < 2 || !part.some(point => pointDistance(point, part[0]) > 0.0001))) return;
  for (const reverse of [false, true]) {
    const start = parts[0][reverse ? parts[0].length - 1 : 0];
    let end = parts[0][reverse ? 0 : parts[0].length - 1];
    const connected = parts.slice(1).every(part => {
      if (pointDistance(end, part[0]) <= 0.001) { end = part[part.length - 1]; return true; }
      if (pointDistance(end, part[part.length - 1]) <= 0.001) { end = part[0]; return true; }
      return false;
    });
    if (connected) return [start, end];
  }
}

export function buildScene(payload: SpatialPayload, options: { locationId?: string; rackId?: string; cableId?: string; showPathways?: boolean; portDetailRackIds?: string[] } = {}) {
  const usedPortIds = new Set(payload.cables.flatMap(cable => cable.terminations.map(terminal => terminal.port_id)));
  const portsByDevice = new Map<string, SpatialPort[]>();
  for (const port of payload.ports) {
    const group = portsByDevice.get(port.device_id) ?? [];
    group.push(port); portsByDevice.set(port.device_id, group);
  }
  const racks = payload.racks.filter(rack => (!options.locationId || rack.location_id === options.locationId) && (!options.rackId || rack.id === options.rackId));
  const roomIds = options.rackId ? [...new Set(racks.map(rack => rack.location_id))]
    : options.locationId ? [options.locationId] : sceneLocations(payload).map(location => location.id);
  const rooms: SceneData["rooms"] = [];
  const rendered: SceneRack[] = [];
  const offsets = new Map<string, [number, number]>();
  const warnings = new Set<string>();
  let nextX = 0;
  if (roomIds.length > 1) warnings.add("多房间按分区示意排列，不代表房间之间的实测距离。");
  for (const roomId of roomIds) {
    const location = payload.locations.find(item => item.id === roomId);
    const localRacks = racks.filter(rack => rack.location_id === roomId);
    const duplicate = localRacks.some((a, index) => localRacks.slice(index + 1).some(b => a.position_x === b.position_x && a.position_y === b.position_y && a.position_z === b.position_z));
    const invalid = localRacks.some(rack => !finite(rack.position_x) || !finite(rack.position_y) || !finite(rack.position_z));
    const arranged = duplicate || invalid;
    if (localRacks.some(rack => !finite(rack.rotation))) warnings.add("无效机架旋转角采用零角度示意，原始姿态未修改。");
    if (arranged) warnings.add("重叠或未配置的机架坐标采用示意摆位，原始数据未修改。");
    const width = dimension(location?.dimensions.width_m, Math.max(4, localRacks.length * 1.2 + 1));
    const depth = dimension(location?.dimensions.depth_m, 4);
    if (!finite(location?.dimensions.width_m) || Number(location?.dimensions.width_m) <= 0 || !finite(location?.dimensions.depth_m) || Number(location?.dimensions.depth_m) <= 0) warnings.add("部分房间未录入尺寸，底板为示意范围。");
    const offset: [number, number] = [nextX, 0];
    offsets.set(roomId, offset);
    rooms.push({ id: roomId, label: location?.name ?? "机架区域", center: [nextX + width / 2, depth / 2], width, depth, height: dimension(location?.dimensions.height_m, 3.2), entrances: roomEntrances(location) });
    for (const [index, rack] of localRacks.entries()) {
      const devices = payload.devices.filter(item => item.rack_id === rack.id);
      rendered.push({
        id: rack.id, locationId: rack.location_id, identifier: rack.rack_identifier, name: rack.name,
        position: [nextX + (arranged ? 0.8 + index * 1.2 : rack.position_x), arranged ? 0 : rack.position_z, arranged ? 1 : rack.position_y],
        rotation: (finite(rack.rotation) ? rack.rotation : 0) * Math.PI / 180,
        width: dimension(rack.width_mm, 600) / 1000, depth: dimension(rack.depth_mm, 1000) / 1000, heightU: dimension(rack.height_u, 42),
        devices: devices.map(device => ({ id: device.id, identifier: device.identifier, name: device.name, deviceType: device.device_type, startU: device.start_u, units: device.rack_units, face: device.face,
          portCount: (portsByDevice.get(device.id) ?? []).filter(port => port.front_or_rear === device.face).length,
          ports: (options.portDetailRackIds && !options.portDetailRackIds.includes(rack.id) ? [] : portsByDevice.get(device.id) ?? []).map(port => ({ id: port.id, identifier: port.identifier, mediaType: port.media_type ?? port.connector_type, face: port.front_or_rear, positionIndex: port.position_index, occupied: Boolean(port.occupied || port.status !== "available" || usedPortIds.has(port.id)), ...(portBlockedReason(payload, port) ? { blocked: true } : {}) })),
        })),
      });
    }
    nextX += width + 2;
  }
  const paths: ScenePath[] = [];
  const segmentDimension = (segment: SpatialSegment, partial = false) => {
    const lengthM = payload.truncated.includes("coordinates") ? null : segmentGeometryLength(segment);
    return lengthM === null ? {} : { dimension: { label: `${segment.name} · ${partial ? "局部坐标" : "坐标"} ${formatLength(lengthM)} m`, lengthM, basis: "coordinates" as const } };
  };
  const segments = new Map(payload.pathways.flatMap(pathway => pathway.segments.map(segment => [segment.id, { segment, pathway }] as const)));
  if (options.showPathways && !options.rackId) for (const pathway of payload.pathways) {
    const offset = offsets.get(pathway.location_id);
    if (!offset) continue;
    for (const segment of pathway.segments) {
      const points = segmentPoints(segment, offset);
      if (points.length) paths.push({ id: pathway.id, identifier: pathway.identifier, kind: "pathway", points, pathwayType: pathway.type, ...segmentDimension(segment) });
    }
  }
  const routeGeometryTruncated = payload.truncated.some(kind => ["pathways", "segments", "coordinates", "routes"].includes(kind));
  const routeKinds: Record<string, RouteKind> = {};
  const leadInCableIds: string[] = [];
  for (const cable of payload.cables) {
    let routeKind: RouteKind = "none";
    const registered: Point[][] = [];
    if (!options.rackId) for (const id of cable.route_segment_ids) {
      const entry = segments.get(id);
      const offset = entry && offsets.get(entry.pathway.location_id);
      if (!entry || !offset) continue;
      const segment = segmentForCable(cable, entry.segment);
      if (!segment || payload.truncated.includes("coordinates") && cable.route_portions) { warnings.add("部分线路的线槽几何已变化或资料不完整；未补画旧接入范围，请重新核对线路。"); continue; }
      const points = segmentPoints(segment, offset);
      if (points.length) { paths.push({ id: cable.id, identifier: cable.identifier, kind: "cable", points, ...segmentDimension(segment, Boolean(cable.route_portions)) }); registered.push(points); routeKind = "recorded"; }
    }
    const endpoints: { point: Point; front: Point }[] = [];
    if (cable.route_scope !== "partial" && cable.endpoint_scope === "complete" && cable.terminations.length === 2) for (const side of ["A", "B"]) {
      const terminal = cable.terminations.find(t => t.side === side);
      const device = terminal && payload.devices.find(item => item.id === terminal.device_id);
      const rack = device && rendered.find(item => item.id === device.rack_id);
      const sourceRack = rack && payload.racks.find(item => item.id === rack.id);
      if (!terminal || !device || !rack || !sourceRack || terminal.rack_id !== rack.id || terminal.location_id !== device.location_id || device.location_id !== sourceRack.location_id) continue;
      const port = payload.ports.find(item => item.id === terminal.port_id && item.device_id === device.id);
      if (!port || !["front", "rear"].includes(port.front_or_rear) || !finite(device.start_u) || !finite(device.rack_units)) continue;
      const localPorts = payload.ports.filter(item => item.device_id === device.id && item.front_or_rear === port.front_or_rear).sort((a, b) => a.position_index - b.position_index);
      const index = Math.max(0, localPorts.findIndex(item => item.id === terminal.port_id));
      const x = ((index + 0.5) / Math.max(1, localPorts.length) - 0.5) * rack.width * 0.7;
      const sign = port.front_or_rear === "rear" ? -1 : 1;
      const z = sign * (rack.depth / 2 - 0.047);
      const point: Point = [rack.position[0] + x * Math.cos(rack.rotation) + z * Math.sin(rack.rotation), rack.position[1] + 0.115 + (device.start_u - 1) * 0.04445 + (device.rack_units * 0.04445 - 0.003) / 2, rack.position[2] - x * Math.sin(rack.rotation) + z * Math.cos(rack.rotation)];
      endpoints.push({ point, front: [point[0] + sign * Math.sin(rack.rotation) * 0.2, point[1], point[2] + sign * Math.cos(rack.rotation) * 0.2] });
    }
    if (endpoints.length === 2) {
      const [a, b] = endpoints;
      const ends = !routeGeometryTruncated && registered.length === cable.route_segment_ids.length && new Set(cable.route_segment_ids).size === registered.length ? routeEnds(registered, Boolean(cable.route_portions)) : undefined;
      if (ends) {
        const reverse = !cable.route_portions && pointDistance(a.point, ends[1]) + pointDistance(b.point, ends[0]) < pointDistance(a.point, ends[0]) + pointDistance(b.point, ends[1]);
        const [start, end] = reverse ? [ends[1], ends[0]] : ends;
        paths.push({ id: cable.id, identifier: cable.identifier, kind: "cable", points: [a.point, a.front, [a.front[0], start[1], a.front[2]], start] },
          { id: cable.id, identifier: cable.identifier, kind: "cable", points: [end, [b.front[0], end[1], b.front[2]], b.front, b.point] });
        leadInCableIds.push(cable.id);
      } else if (routeKind === "none" && (!cable.route_segment_ids.length || options.rackId)) {
        paths.push({ id: cable.id, identifier: cable.identifier, kind: "cable", points: [a.point, a.front, b.front, b.point] });
        routeKind = "schematic";
      }
    }
    routeKinds[cable.id] = routeKind;
  }
  return { data: { racks: rendered, rooms, paths, ...(options.rackId ? { focusRackId: options.rackId } : {}) }, warnings: [...warnings], routeKind: routeKinds[options.cableId ?? ""] ?? "none", routeKinds, leadInCableIds };
}
