export type Side = "A" | "B";
export type ChannelMedium = "fiber" | "copper";
export type ChannelTopology = "simplex" | "duplex" | "quad" | "bundle" | "ethernet";
export type BreakoutMode = "fanout" | "fanin" | "passive";
export type OtdrEventType = "launch" | "connector" | "splice" | "bend" | "reflective" | "end" | "unknown";

export interface ChannelMemberInput {
  kind: "fiber_strand" | "copper_pair";
  resource_id: string;
  role: string;
}

export interface BreakoutLegInput {
  parent_strand_id: string;
  parent_side: Side;
  child_strand_id: string;
  child_side: Side;
  label?: string;
  loss_db: number;
}

export interface OtdrEventInput {
  event_type: OtdrEventType;
  distance_m: number;
  loss_db?: number;
  reflectance_db?: number;
  confidence: number;
  notes?: string;
}

export interface TopologyTrace {
  selected_cable: string;
  selected_identifier: string;
  trace_model: "generic-fiber" | "generic-copper";
  selected_strand: { id: string; number: number; channels: { id: string; identifier: string; role: string }[] } | null;
  selected_pair: { id: string; number: number; channels: { id: string; identifier: string; role: string }[] } | null;
  complete: boolean;
  hop_count: number;
  node_count?: number;
  edge_count?: number;
  cycle: boolean;
  branching: boolean;
  truncated: boolean;
  items: Record<string, unknown>[];
  otdr: { id: string; wavelength_nm: number; events: unknown[] }[];
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function requiredUuid(value: string, label: string): string {
  const clean = value.trim();
  if (!UUID.test(clean)) throw new Error(`${label} 必须是有效 UUID。`);
  return clean;
}

function requiredSide(value: string, label: string): Side {
  const clean = value.trim().toUpperCase();
  if (clean !== "A" && clean !== "B") throw new Error(`${label} 必须是 A 或 B。`);
  return clean;
}

function optionalFinite(value: string, label: string, low: number, high: number): number | undefined {
  const clean = value.trim();
  if (!clean) return undefined;
  const number = Number(clean);
  if (!Number.isFinite(number) || number < low || number > high) {
    throw new Error(`${label} 必须在 ${low}–${high} 之间。`);
  }
  return number;
}

function nonEmptyLines(value: string): string[] {
  return value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
}

export function parseChannelMembers(value: string): ChannelMemberInput[] {
  const result = nonEmptyLines(value).map((line, index) => {
    const [rawKind, rawId, ...rawRole] = line.split(",");
    const kind = rawKind?.trim();
    if (kind !== "fiber_strand" && kind !== "copper_pair") {
      throw new Error(`成员第 ${index + 1} 行类型必须是 fiber_strand 或 copper_pair。`);
    }
    const role = rawRole.join(",").trim() || "member";
    if (role.length > 80) throw new Error(`成员第 ${index + 1} 行 role 超过 80 字符。`);
    const member: ChannelMemberInput = {
      kind, resource_id: requiredUuid(rawId ?? "", `成员第 ${index + 1} 行资源`), role,
    };
    return member;
  });
  if (result.length < 1 || result.length > 600) throw new Error("Channel 必须包含 1–600 个成员。 ");
  return result;
}

export function parseBreakoutLegs(value: string): BreakoutLegInput[] {
  const result = nonEmptyLines(value).map((line, index) => {
    const [parentId, parentSide, childId, childSide, label = "", loss = "0"] = line.split(",");
    return {
      parent_strand_id: requiredUuid(parentId ?? "", `Breakout 第 ${index + 1} 行 parent strand`),
      parent_side: requiredSide(parentSide ?? "", `Breakout 第 ${index + 1} 行 parent side`),
      child_strand_id: requiredUuid(childId ?? "", `Breakout 第 ${index + 1} 行 child strand`),
      child_side: requiredSide(childSide ?? "", `Breakout 第 ${index + 1} 行 child side`),
      ...(label.trim() ? { label: label.trim() } : {}),
      loss_db: optionalFinite(loss, `Breakout 第 ${index + 1} 行 loss`, 0, 10) ?? 0,
    };
  });
  if (result.length < 1 || result.length > 576) throw new Error("Breakout 必须包含 1–576 条 leg。 ");
  return result;
}

export function parseOtdrEvents(value: string): OtdrEventInput[] {
  const allowed = new Set<OtdrEventType>(["launch", "connector", "splice", "bend", "reflective", "end", "unknown"]);
  let previousDistance = -1;
  const result = nonEmptyLines(value).map((line, index) => {
    const [distanceText, typeText, lossText = "", reflectanceText = "", confidenceText = "1", ...notes] = line.split(",");
    const eventType = typeText?.trim() as OtdrEventType;
    if (!allowed.has(eventType)) throw new Error(`OTDR 第 ${index + 1} 行事件类型无效。`);
    const distance = optionalFinite(distanceText ?? "", `OTDR 第 ${index + 1} 行距离`, 0, 1_000_000);
    if (distance === undefined) throw new Error(`OTDR 第 ${index + 1} 行缺少距离。`);
    if (distance < previousDistance) throw new Error("OTDR 事件必须按距离递增排列。 ");
    previousDistance = distance;
    const loss = optionalFinite(lossText, `OTDR 第 ${index + 1} 行损耗`, 0, 100);
    const reflectance = optionalFinite(reflectanceText, `OTDR 第 ${index + 1} 行反射`, -120, 20);
    const confidence = optionalFinite(confidenceText, `OTDR 第 ${index + 1} 行置信度`, 0, 1) ?? 1;
    const note = notes.join(",").trim();
    return {
      event_type: eventType,
      distance_m: distance,
      ...(loss === undefined ? {} : { loss_db: loss }),
      ...(reflectance === undefined ? {} : { reflectance_db: reflectance }),
      confidence,
      ...(note ? { notes: note } : {}),
    };
  });
  if (result.length > 10_000) throw new Error("OTDR 事件不能超过 10,000 条。 ");
  return result;
}

export function isoTimestamp(value: string): string {
  const clean = value.trim();
  if (!clean || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(clean)) {
    throw new Error("采集时间必须是包含时区的 ISO-8601 时间，例如 2026-09-04T10:00:00+08:00。 ");
  }
  const timestamp = new Date(clean);
  if (Number.isNaN(timestamp.valueOf())) throw new Error("采集时间格式无效。 ");
  return clean;
}

export function traceItemText(item: Record<string, unknown>): string {
  const kind = String(item.kind ?? "unknown");
  if (kind === "port") return `${String(item.identifier ?? item.id ?? "port")} / ${String(item.label ?? "")}`;
  if (kind === "fiber_endpoint") return `Strand #${String(item.number ?? "?")} ${String(item.side ?? "?")} / ${String(item.cable_identifier ?? item.cable_id ?? "")}`;
  if (kind === "fiber_strand") return `Strand #${String(item.number ?? "?")} / ${String(item.cable_identifier ?? item.cable_id ?? "")}`;
  if (kind === "cable") return `${String(item.identifier ?? item.id ?? "cable")} / ${String(item.media_type ?? "")}`;
  if (kind === "internal_mapping") return `${String(item.mapping_type ?? "mapping")} / lane ${String(item.lane ?? "-")}`;
  if (kind === "splice") return `${String(item.cassette ?? item.cassette_id ?? "cassette")} / Slot ${String(item.slot_number ?? "?")} / ${String(item.loss_db ?? 0)} dB`;
  if (kind === "breakout") return `${String(item.identifier ?? item.breakout_id ?? "breakout")} / Leg ${String(item.leg_number ?? "?")} / ${String(item.loss_db ?? 0)} dB`;
  if (kind === "fiber_termination") return `${String(item.connection_type ?? "termination")} / ${String(item.loss_db ?? 0)} dB`;
  return JSON.stringify(item);
}
