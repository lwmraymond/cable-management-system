import type { SpatialDevice, SpatialPayload, SpatialRack } from "./sceneData";

export type SuggestedCreationKind = "room" | "device" | "pathway";

/** Visible-data suggestions only; the server still enforces identifier uniqueness. */
export function suggestCreationIdentity(kind: SuggestedCreationKind, payload: Pick<SpatialPayload, "locations" | "devices" | "pathways">): { identifier: string; name: string } {
  const objects = kind === "room" ? payload.locations : kind === "device" ? payload.devices : payload.pathways;
  const used = new Set(objects.map(object => object.identifier.trim().toUpperCase()));
  const [prefix, name] = { room: ["ROOM", "房间"], device: ["DEVICE", "设备"], pathway: ["TRAY", "线槽"] }[kind];
  let number = 1;
  while (used.has(`${prefix}-${String(number).padStart(3, "0")}`)) number++;
  const suffix = String(number).padStart(3, "0");
  return { identifier: `${prefix}-${suffix}`, name: `${name} ${suffix}` };
}

export type RackSlotSuggestion = { startU: number; endU: number } | { reason: string };

export function suggestRackSlot(rack: SpatialRack | undefined, devices: SpatialDevice[], units: number | undefined, face: string | undefined, complete = true): RackSlotSuggestion {
  if (!rack) return { reason: "请先选择安装机柜。" };
  if (!Number.isInteger(units) || units! < 1) return { reason: "请先选择具有有效 U 高度的设备模板。" };
  if (face !== "front" && face !== "rear") return { reason: "请先选择前侧或后侧安装面。" };
  if (!complete) return { reason: "当前设备列表不完整，无法可靠判断空闲 U 位；请缩小空间范围或刷新后重试。" };
  if (!Number.isInteger(rack.height_u) || rack.height_u < 1) return { reason: "机柜 U 高度无效，请先核对机柜资料。" };
  if (units! > rack.height_u) return { reason: `模板需要 ${units}U，超过机柜的 ${rack.height_u}U 容量。` };
  if (rack.reserved_units.some(unit => !Number.isInteger(unit) || unit < 1 || unit > rack.height_u)) return { reason: "机柜预留 U 位数据无效，请先核对机柜资料。" };
  const occupied = new Set(rack.reserved_units);
  for (const device of devices.filter(device => device.rack_id === rack.id && device.face === face)) {
    if (!Number.isInteger(device.start_u) || !Number.isInteger(device.rack_units) || device.start_u < 1 || device.rack_units < 1 || device.start_u + device.rack_units - 1 > rack.height_u) return { reason: "机柜已有设备的 U 位数据无效，请先核对设备资料。" };
    for (let unit = device.start_u; unit < device.start_u + device.rack_units; unit++) occupied.add(unit);
  }
  let free = 0;
  for (let unit = 1; unit <= rack.height_u; unit++) {
    free = occupied.has(unit) ? 0 : free + 1;
    if (free === units) return { startU: unit - units + 1, endU: unit };
  }
  return { reason: `${face === "front" ? "前侧" : "后侧"}没有连续 ${units}U 的空闲位置；请换机柜、安装面或设备模板。` };
}
