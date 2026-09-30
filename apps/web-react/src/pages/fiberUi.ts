export type Side = "A" | "B";
export interface Bundle {
  id: string; cable_id: string; name: string; strand_count: number;
  strands: { id: string; number: number }[];
}
export interface Slot {
  id: string; number: number; version: number;
  splice_id: string | null; loss_db: number | null;
}
export interface Cassette {
  id: string; name: string; device_id: string; project_id: string;
  slot_count: number; slots: Slot[];
}
export interface Trace {
  steps: ({ kind: "strand"; strand_id: string; cable_id: string; number: number;
    entry_side: Side; exit_side: Side } | { kind: "splice"; splice_id: string;
    cassette_id: string; slot_number: number; loss_db: number })[];
  termination: "open" | "cycle" | "limit";
  truncated: boolean; cycle: boolean; total_splice_loss_db: number; max_hops: number;
}
export function contextKey(context: {
  tenantId: string; actorId?: string; projectId?: string; locationId?: string;
}): string {
  return JSON.stringify([context.tenantId, context.actorId ?? "",
    context.projectId ?? "", context.locationId ?? ""]);
}
export function resourceId(value: string): string {
  const clean = value.trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(clean)) {
    throw new Error("请输入有效的资源 UUID。");
  }
  return clean;
}
export function expectedVersion(slot: Slot): { expected_version: number } {
  if (!Number.isSafeInteger(slot.version) || slot.version < 1) {
    throw new Error("槽位版本无效，请重新加载。");
  }
  return { expected_version: slot.version };
}
export function fiberError(error: unknown): { message: string; conflict: boolean } {
  const status = typeof error === "object" && error !== null && "status" in error
    ? (error as { status: unknown }).status : undefined;
  if (status === 409) return { message: "资源已变更或端点已占用。请重新加载槽位并核对后再操作；本次不会自动重试。", conflict: true };
  if (status === 401) return { message: "登录已过期。请重新登录；不会自动重放写入。", conflict: false };
  if (status === 403) return { message: "当前账号不具备该资源实际项目和位置范围内的权限。", conflict: false };
  if (status === 404) return { message: "资源不存在、尚未初始化，或不属于当前租户。", conflict: false };
  return { message: error instanceof Error ? error.message : "操作失败，请核对连接与输入。", conflict: false };
}
