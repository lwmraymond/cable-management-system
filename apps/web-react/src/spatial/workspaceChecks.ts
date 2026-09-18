import type { Selection } from "./render/sceneRenderer";
import { sceneLocations, type CablePolicy, type SpatialLocation, type SpatialPayload } from "./sceneData";
import { mediaFamily } from "./sceneCreate";
import { describePathLengths, segmentGeometryLength } from "./sceneLengths";

export type WorkspaceCheckIssue = {
  id: string; code: string; objectLabel: string; severity: "warning" | "info"; title: string; detail: string;
  selection: Selection; action: "view" | "entrances" | "rack-position" | "properties"; actionLabel: string;
};
export type WorkspaceCheckResult = {
  issues: WorkspaceCheckIssue[]; partial: boolean;
  checked: { rooms: number; racks: number; devices: number; cables: number; pathways: number };
  omittedCount: number;
};
const ROOM_TYPES = new Set(["room", "tr", "er", "mdf", "mmr", "data_hall", "entrance_facility"]);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const positive = (value: unknown): value is number => finite(value) && value > 0;
const renderNumber = (value: unknown): value is number => finite(value) && Math.abs(value) < 100_000;
const validDimensions = (room: SpatialLocation) => [room.dimensions.width_m, room.dimensions.depth_m, room.dimensions.height_m].every(value => positive(value) && renderNumber(value));
const blocked = (policy: CablePolicy | undefined, family: string) => Boolean(policy && (!policy.allows_cables || !policy.allowed_media.includes(family)));

/** Advisory checks on the supplied visible inventory; never fetches or changes records. */
export function checkWorkspace(payload: SpatialPayload, locationId?: string): WorkspaceCheckResult {
  const inSpace = (id: string) => !locationId || id === locationId;
  const rooms = sceneLocations(payload).filter(room => inSpace(room.id));
  const racks = payload.racks.filter(rack => inSpace(rack.location_id));
  const devices = payload.devices.filter(device => inSpace(device.location_id));
  const pathways = payload.pathways.filter(pathway => inSpace(pathway.location_id));
  const localPathwayIds = new Set(pathways.map(pathway => pathway.id));
  const segmentMap = new Map(payload.pathways.flatMap(pathway => pathway.segments.map(segment => [segment.id, { segment, pathway }] as const)));
  const cables = payload.cables.filter(cable => !locationId || cable.terminations.some(term => term.location_id === locationId) || cable.route_segment_ids.some(id => {
    const entry = segmentMap.get(id); return entry && localPathwayIds.has(entry.pathway.id);
  }));
  const locations = new Map(payload.locations.map(room => [room.id, room]));
  const allRacks = new Map(payload.racks.map(rack => [rack.id, rack]));
  const allDevices = new Map(payload.devices.map(device => [device.id, device]));
  const findings = new Map<string, WorkspaceCheckIssue>();
  let partial = Boolean(payload.truncated.length || locationId && !locations.has(locationId));
  const add = (code: string, severity: WorkspaceCheckIssue["severity"], selection: Selection, title: string, detail: string, action: WorkspaceCheckIssue["action"] = "view", actionLabel = "查看详情") => {
    const id = `${code}:${selection.kind}:${selection.id}`;
    const entity: { name?: string; identifier?: string; rack_identifier?: string } | undefined = selection.kind === "room" ? locations.get(selection.id) : selection.kind === "rack" ? allRacks.get(selection.id) : selection.kind === "device" ? allDevices.get(selection.id) : selection.kind === "pathway" ? payload.pathways.find(item => item.id === selection.id) : selection.kind === "cable" ? payload.cables.find(item => item.id === selection.id) : payload.ports.find(item => item.id === selection.id);
    const objectLabel = [...new Set([entity?.name, entity?.identifier ?? entity?.rack_identifier].map(value => value?.trim()).filter(Boolean))].join(" · ") || selection.id;
    if (!findings.has(id)) findings.set(id, { id, code, objectLabel, severity, title, detail, selection, action, actionLabel });
  };

  for (const room of rooms) {
    const selection: Selection = { kind: "room", id: room.id };
    const dimensionsValid = validDimensions(room);
    if (!dimensionsValid) add("space_dimensions", "info", selection, "空间尺寸待完善", "宽度、进深或净高尚未有效登记；当前底板可能采用示意尺寸，不能据此核对实际空间边界。");
    if (!ROOM_TYPES.has(room.location_type)) continue;
    const entrances = room.dimensions.entrances;
    if (!Array.isArray(entrances) || !entrances.length) {
      add("room_entrance", "info", selection, "出入口资料待完善", "当前房间尚未登记出入口；这不表示现场没有出口。", dimensionsValid ? "entrances" : "view", dimensionsValid ? "配置出入口" : "查看空间");
      continue;
    }
    const ids = new Set<string>();
    const walls = new Map<string, { start: number; end: number }[]>();
    let invalid = false;
    for (const entrance of entrances) {
      if (!entrance || typeof entrance !== "object" || typeof entrance.id !== "string" || !entrance.id.trim() || ids.has(entrance.id) || typeof entrance.name !== "string" || !entrance.name.trim() || !["north", "south", "east", "west"].includes(entrance.wall) || !finite(entrance.offset_m) || entrance.offset_m < 0 || !positive(entrance.width_m) || !positive(entrance.height_m)) { invalid = true; continue; }
      ids.add(entrance.id);
      const end = entrance.offset_m + entrance.width_m;
      const length = entrance.wall === "north" || entrance.wall === "south" ? room.dimensions.width_m : room.dimensions.depth_m;
      if (dimensionsValid && (end > Number(length) + 1e-8 || entrance.height_m > Number(room.dimensions.height_m) + 1e-8)) invalid = true;
      const openings = walls.get(entrance.wall) ?? [];
      if (openings.some(other => entrance.offset_m < other.end - 1e-8 && end > other.start + 1e-8)) invalid = true;
      openings.push({ start: entrance.offset_m, end }); walls.set(entrance.wall, openings);
    }
    if (invalid) add("room_entrance", "warning", selection, "出入口记录需要核对", "已登记的入口字段无效、相互重叠或超出有效空间尺寸；请核对记录，不据此推断现场通行情况。", dimensionsValid ? "entrances" : "view", dimensionsValid ? "配置出入口" : "查看空间");
  }

  for (const rack of racks) {
    const selection: Selection = { kind: "rack", id: rack.id };
    const validSpecs = [rack.width_mm, rack.depth_mm, rack.height_u].every(value => positive(value) && renderNumber(value)) && Number.isInteger(rack.height_u);
    if (![rack.position_x, rack.position_y, rack.position_z, rack.rotation].every(renderNumber) || !validSpecs) {
      add("rack_placement", "info", selection, "机柜摆位资料无效", "已登记坐标、旋转或规格不完整，无法核对机柜边界；请先查看并确认记录。", validSpecs ? "rack-position" : "view", validSpecs ? "调整位置" : "查看规格"); continue;
    }
    const room = locations.get(rack.location_id);
    if (!room) { partial = true; add("rack_placement", "info", selection, "机柜所属空间未加载", "当前数据不足以核对机柜位置与房间边界。"); continue; }
    if (!validDimensions(room)) continue;
    const angle = rack.rotation * Math.PI / 180;
    const width = Math.max(0.5, rack.width_mm / 1000), depth = Math.max(0.45, rack.depth_mm / 1000);
    const halfX = (Math.abs(Math.cos(angle)) * width + Math.abs(Math.sin(angle)) * depth) / 2;
    const halfY = (Math.abs(Math.sin(angle)) * width + Math.abs(Math.cos(angle)) * depth) / 2;
    if (rack.position_x - halfX < -1e-8 || rack.position_y - halfY < -1e-8 || rack.position_x + halfX > Number(room.dimensions.width_m) + 1e-8 || rack.position_y + halfY > Number(room.dimensions.depth_m) + 1e-8 || rack.position_z < -1e-8 || rack.position_z + rack.height_u * 0.04445 + 0.1925 > Number(room.dimensions.height_m) + 1e-8) {
      add("rack_placement", "warning", selection, "机柜记录超出空间边界", "按已登记尺寸与旋转计算，机柜外框或顶部超出所属空间；请核对房间尺寸和摆位。", "rack-position", "调整位置");
    }
  }

  for (const device of devices) {
    const selection: Selection = { kind: "device", id: device.id };
    if (!device.rack_id) add("device_unracked", "info", selection, "设备尚未入柜显示", "该设备未关联机柜，当前三维机柜视图不会显示它；工作区插座等非机柜设备可能属于正常配置。");
    else if (!allRacks.has(device.rack_id)) { partial = true; add("device_unracked", "info", selection, "设备关联机柜未加载", "当前范围无法核对安装位置；不能据此判断设备未入柜。"); }
  }

  for (const pathway of pathways) {
    const selection: Selection = { kind: "pathway", id: pathway.id };
    if (pathway.segments.some(segment => !positive(segment.length_m))) add("length_unregistered", "info", selection, "路径段长度待登记", "部分已加载路径段没有有效的登记长度；坐标长度与登记长度来源不同，不能替代现场核对。");
    if (!payload.truncated.includes("coordinates") && (!pathway.segments.length || pathway.segments.some(segment => segmentGeometryLength(segment) === null))) add("route_geometry", "info", selection, "路径坐标资料不足", "当前已加载路径段缺少有效坐标，无法完整展示或检查走线；这不表示现场线槽中断。");
  }

  for (const cable of cables) {
    const selection = { kind: "cable", id: cable.id } as const;
    const partialRoute = cable.route_scope === "partial";
    if (partialRoute) {
      partial = true;
      add("route_coverage", "info", selection, "登记路由仅部分可见", "部分登记路径在当前楼层/房间之外或尚未加载；仅核对可见路径段和布线策略，不检查整条路由的连续性，也不据此推断断线。");
    }
    if (cable.endpoint_scope !== "complete" || locationId && cable.terminations.some(term => term.location_id !== locationId)) partial = true;
    if (!positive(cable.length_m)) add("length_unregistered", "info", selection, "线缆长度待登记", "当前记录没有有效长度；登记长度也可能来自估算，不等于现场实测。");
    if (!cable.route_segment_ids.length && !partialRoute) add("cable_route_unloaded", "info", selection, "当前范围没有登记路由", "可能是直连跳线或路径尚未在当前范围加载；不据此判断断线，也不从三维示意连线推算敷设长度。");
    else if (cable.route_segment_ids.length) {
      const entries = cable.route_segment_ids.map(id => segmentMap.get(id));
      const missing = entries.some(entry => !entry);
      const outside = Boolean(locationId && entries.some(entry => entry && entry.pathway.location_id !== locationId));
      if (missing || outside) partial = true;
      if (missing) add("route_geometry", "info", selection, "线缆路由资料未完整加载", "部分登记路径段未加载，无法检查其连接关系；不会跨越缺失数据推断线路故障。");
      const relevantIds = cable.route_segment_ids.filter(id => {
        const entry = segmentMap.get(id); return entry && inSpace(entry.pathway.location_id);
      });
      if (new Set(relevantIds).size !== relevantIds.length) add("route_geometry", "warning", selection, "登记路由存在重复路径段", "当前空间关联的登记路线重复引用同一路径段；请查看长度明细并核对登记顺序。");
      if (!partialRoute && !missing && !outside && !payload.truncated.some(kind => ["pathways", "segments", "coordinates", "routes"].includes(kind))) {
        const multiSpace = new Set(entries.map(entry => entry!.pathway.location_id)).size > 1;
        if (multiSpace) { partial = true; add("route_geometry", "info", selection, "跨空间路由尚不能完整核对", "多个空间的坐标不能直接拼接；当前只核对可见单段，不推算空间间的连接距离。"); }
        else if (entries.every(entry => positive(entry!.segment.length_m) && segmentGeometryLength(entry!.segment) !== null) && !describePathLengths(payload, selection).complete) add("route_geometry", "warning", selection, "登记路径段未连续相接", "按当前登记顺序和坐标，段端点未在 1 mm 容差内连续相接；请核对记录，不据此判定现场断线。");
      }
    }
    const family = mediaFamily(cable.media_type);
    if (!["copper", "fiber"].includes(family)) { partial = true; continue; }
    for (const term of cable.terminations) {
      const device = allDevices.get(term.device_id);
      if (!device) { partial = true; continue; }
      if (inSpace(device.location_id) && blocked(device.cable_policy, family)) add("existing_policy_conflict", "warning", { kind: "device", id: device.id }, "设备策略与可见连接不一致", `可见线缆 ${cable.identifier} 的介质被设备当前布线策略禁止；请核对连接或策略，不会自动放开限制。`, "properties", "查看布线属性");
    }
    for (const id of cable.route_segment_ids) {
      const pathway = segmentMap.get(id)?.pathway;
      if (pathway && inSpace(pathway.location_id) && blocked(pathway.cable_policy, family)) add("existing_policy_conflict", "warning", { kind: "pathway", id: pathway.id }, "线槽策略与可见连接不一致", `可见线缆 ${cable.identifier} 使用了当前策略禁止其介质的线槽；请核对连接或策略，不会自动放开限制。`, "properties", "查看布线属性");
    }
  }
  const issues = [...findings.values()].sort((a, b) => (a.severity === "warning" ? 0 : 1) - (b.severity === "warning" ? 0 : 1) || a.id.localeCompare(b.id));
  return { issues: issues.slice(0, 100), omittedCount: Math.max(0, issues.length - 100), partial, checked: { rooms: rooms.length, racks: racks.length, devices: devices.length, cables: cables.length, pathways: pathways.length } };
}
