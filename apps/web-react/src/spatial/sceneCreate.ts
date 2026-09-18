import type { InfrastructureContext } from "../api/context";
import type { SpatialPayload, SpatialPort } from "./sceneData";

export type SceneCreateKind = "room" | "rack" | "device" | "pathway" | "cable";
export type PathPoint = { x: number; y: number; z: number };
export type DeviceTemplate = { id: string; manufacturer: string; model: string; device_type: string; rack_units: number };
export type SceneCreateValues = {
  identifier: string; name: string; parent_id?: string; kind: "server_room" | "room";
  width_m: number; depth_m: number; height_m: number; location_id: string;
  identifier_prefix: string; name_prefix: string; count: number; columns: number;
  position_x: number; position_y: number; rotation: number; gap_m: number;
  width_mm: number; depth_mm: number; height_u: number;
  rack_id: string; template_id: string; start_u: number; face: "front" | "rear";
  pathway_type: string; capacity_area_mm2?: number; points: PathPoint[];
  device_a_id?: string; device_b_id?: string; port_a_id: string; port_b_id: string;
  media_type: string; construction: string; color?: string; length_m?: number; route_segment_ids?: string[];
};

export function pathLength(points: PathPoint[]): number {
  if (points.length < 2) throw new Error("线槽至少需要两个坐标点。");
  if (points.some(point => ![point.x, point.y, point.z].every(Number.isFinite))) throw new Error("请完整填写每个点的 X、Y 和高度。");
  let length = 0;
  for (let index = 1; index < points.length; index++) length += Math.hypot(points[index].x - points[index - 1].x, points[index].y - points[index - 1].y, points[index].z - points[index - 1].z);
  if (!length) throw new Error("线槽坐标不能全部重合。");
  return Math.round(length * 1000) / 1000;
}

export function mediaFamily(value: string): string {
  if (/cat|copper|rj45|punchdown/i.test(value)) return "copper";
  if (/fiber|os[12]|om[1-5]|lc|sc|mpo|mtp/i.test(value)) return "fiber";
  return value.toLowerCase();
}
/** Policy admission is separate from physical occupancy and port status. */
export function portBlockedReason(payload: SpatialPayload, port: SpatialPort): string | undefined {
  const policy = payload.devices.find(device => device.id === port.device_id)?.cable_policy;
  if (!policy) return;
  if (!policy.allows_cables) return "设备布线策略禁止连接线缆。";
  const family = mediaFamily(port.media_type ?? port.connector_type);
  if (!policy.allowed_media.includes(family)) {
    const label = family === "copper" ? "铜缆" : family === "fiber" ? "光纤" : `介质 ${family}`;
    return `设备布线策略不允许${label}。`;
  }
}

export function availablePorts(payload: SpatialPayload, deviceId: string | undefined, mediaType: string, otherPort?: string): SpatialPort[] {
  const used = new Set(payload.cables.flatMap(cable => cable.terminations.map(terminal => terminal.port_id)));
  return payload.ports.filter(port => {
    const details = port as SpatialPort & { media_type?: string; occupied?: boolean };
    const media = details.media_type ?? port.connector_type;
    return port.device_id === deviceId && port.id !== otherPort && port.status === "available" && !details.occupied && !used.has(port.id) && !portBlockedReason(payload, port) && mediaFamily(media) === mediaFamily(mediaType);
  }).sort((a, b) => a.front_or_rear.localeCompare(b.front_or_rear) || a.position_index - b.position_index);
}

export function createSceneRequest(kind: SceneCreateKind, values: SceneCreateValues, context: InfrastructureContext) {
  const identifier = values.identifier?.trim(), name = values.name?.trim();
  let body: Record<string, unknown>;
  switch (kind) {
    case "room": body = { parent_id: values.parent_id || null, identifier, name, kind: values.kind, width_m: values.width_m, depth_m: values.depth_m, height_m: values.height_m }; break;
    case "rack": body = { location_id: values.location_id, identifier_prefix: values.identifier_prefix.trim(), name_prefix: values.name_prefix.trim(), count: values.count, columns: values.columns, position_x: values.position_x, position_y: values.position_y, rotation: values.rotation, gap_m: values.gap_m, width_mm: values.width_mm, depth_mm: values.depth_mm, height_u: values.height_u }; break;
    case "device": body = { rack_id: values.rack_id, template_id: values.template_id, identifier, name, start_u: values.start_u, face: values.face }; break;
    case "pathway": body = { location_id: values.location_id, identifier, name, pathway_type: values.pathway_type, ...(values.capacity_area_mm2 == null ? {} : { capacity_area_mm2: values.capacity_area_mm2 }), segments: [{ name: `${name} · 01`, sequence: 1, length_m: pathLength(values.points), coordinates: values.points.map(({ x, y, z }) => ({ x, y, z })) }] }; break;
    case "cable": {
      if (values.port_a_id === values.port_b_id) throw new Error("A 端和 B 端必须选择不同端口。");
      body = { identifier, media_type: values.media_type, construction: values.construction, port_a_id: values.port_a_id, port_b_id: values.port_b_id, ...(context.projectId ? { project_id: context.projectId } : {}), ...(values.color?.trim() ? { color: values.color.trim() } : {}), ...(values.length_m == null ? {} : { length_m: values.length_m }), route_segment_ids: values.route_segment_ids ?? [] };
      break;
    }
  }
  return { path: `/scene/${{ room: "rooms", rack: "racks", device: "devices", pathway: "pathways", cable: "cables" }[kind]}`, body };
}

export function creationError(reason: unknown): string {
  const detail = (reason as { body?: { detail?: unknown } })?.body?.detail;
  if (Array.isArray(detail)) return detail.map(item => `${Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== "body").join(" / ") : "字段"}：${item.msg ?? "输入无效"}`).join("；");
  const message = reason instanceof Error ? reason.message : "请求未完成，请重试。";
  if (/Tray geometry changed/i.test(message)) return "线槽坐标或登记长度已变化，请重新计算或校验走线后再保存。";
  if (/already physically terminated|already.*occupied|port.*claimed/i.test(message)) return "端口已被占用，请关闭抽屉、刷新场景后重新选择。";
  if (/overlaps an occupied rack U|reserved.*unit/i.test(message)) return "所选 U 位已占用或被预留，请调整设备起始 U 位。";
  if (/Rack footprint or height extends outside/i.test(message)) return "机柜超出房间边界或净高，请调整坐标和规格。";
  if (/Rack footprint overlaps/i.test(message)) return "机柜位置与现有机柜重叠，请调整起始坐标或净间距。";
  if (/Room needs finite positive/i.test(message)) return "房间尚未录入有效的宽度、进深和净高，暂时无法进行空间编辑。";
  if (/duplicate|already exists|unique/i.test(message)) return "编号已存在，请使用另一个编号。";
  return message;
}
