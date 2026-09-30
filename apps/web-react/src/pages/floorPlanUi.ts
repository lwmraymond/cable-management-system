export type FloorObjectType = "location" | "rack" | "device" | "pathway";

export interface FloorPlanObject {
  id: string;
  object_type: FloorObjectType;
  object_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  z_index: number;
  locked: boolean;
  label: string | null;
}

export interface FloorPlanPath {
  id: string;
  pathway_id: string;
  points: { x: number; y: number }[];
  width: number;
  label: string;
}

export interface FloorPlanDocument {
  schema_version: 1;
  grid_size: number;
  objects: FloorPlanObject[];
  paths: FloorPlanPath[];
}

export interface FloorPlan {
  id: string;
  project_id: string;
  location_id: string;
  name: string;
  units: "mm" | "m" | "ft";
  canvas_width: number;
  canvas_height: number;
  background_reference: string | null;
  status: "draft" | "published" | "archived";
  version: number;
  current_revision_number: number;
  published_revision_number: number | null;
  published_at: string | null;
  document: FloorPlanDocument;
  current_revision: {
    id: string;
    revision_number: number;
    checksum_sha256: string;
    change_summary: string;
    created_at: string;
    restored_from_revision_id: string | null;
  };
}

export function contextKey(context: {
  tenantId: string;
  actorId?: string;
  projectId?: string;
  locationId?: string;
}): string {
  return JSON.stringify([
    context.tenantId,
    context.actorId ?? "",
    context.projectId ?? "",
    context.locationId ?? "",
  ]);
}

export function uuid(value: string): string {
  const clean = value.trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(clean)) {
    throw new Error("请输入有效的资源 UUID。");
  }
  return clean;
}

export function finite(value: number, name: string): number {
  if (!Number.isFinite(value)) throw new Error(`${name} 必须是有限数字。`);
  return value;
}

export function snap(value: number, gridSize: number): number {
  finite(value, "坐标");
  if (!Number.isFinite(gridSize) || gridSize <= 0) throw new Error("网格大小必须大于 0。");
  return Math.round(value / gridSize) * gridSize;
}

export function clampPlacement(
  object: FloorPlanObject,
  canvasWidth: number,
  canvasHeight: number,
  gridSize: number,
): FloorPlanObject {
  const width = Math.max(gridSize, Math.min(finite(object.width, "宽度"), canvasWidth));
  const height = Math.max(gridSize, Math.min(finite(object.height, "高度"), canvasHeight));
  const x = Math.min(Math.max(0, snap(object.x, gridSize)), canvasWidth - width);
  const y = Math.min(Math.max(0, snap(object.y, gridSize)), canvasHeight - height);
  return { ...object, x, y, width, height };
}

export function moveObject(
  document: FloorPlanDocument,
  objectId: string,
  x: number,
  y: number,
  canvasWidth: number,
  canvasHeight: number,
): FloorPlanDocument {
  return {
    ...document,
    objects: document.objects.map(object => object.id === objectId && !object.locked
      ? clampPlacement({ ...object, x, y }, canvasWidth, canvasHeight, document.grid_size)
      : object),
  };
}

export function upsertObject(
  document: FloorPlanDocument,
  object: FloorPlanObject,
  canvasWidth: number,
  canvasHeight: number,
): FloorPlanDocument {
  uuid(object.object_id);
  if (!["location", "rack", "device", "pathway"].includes(object.object_type)) {
    throw new Error("不支持的对象类型。");
  }
  if (!object.id.trim()) throw new Error("绘图对象 ID 不能为空。");
  const normalized = clampPlacement(object, canvasWidth, canvasHeight, document.grid_size);
  const existing = document.objects.find(row => row.id === object.id);
  const physicalCollision = document.objects.find(row =>
    row.id !== object.id
    && row.object_type === object.object_type
    && row.object_id === object.object_id
  );
  if (physicalCollision) throw new Error("同一物理资源不能重复放置。");
  return {
    ...document,
    objects: existing
      ? document.objects.map(row => row.id === object.id ? normalized : row)
      : [...document.objects, normalized],
  };
}

export function removeObject(
  document: FloorPlanDocument,
  objectId: string,
): FloorPlanDocument {
  return {
    ...document,
    objects: document.objects.filter(object => object.id !== objectId || object.locked),
  };
}

export function newDocument(gridSize = 25): FloorPlanDocument {
  if (!Number.isFinite(gridSize) || gridSize <= 0) throw new Error("网格大小必须大于 0。");
  return { schema_version: 1, grid_size: gridSize, objects: [], paths: [] };
}

export function zoomLevel(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(4, Math.max(0.25, value));
}

export function floorPlanError(error: unknown): {
  message: string;
  conflict: boolean;
} {
  const status = typeof error === "object" && error !== null && "status" in error
    ? (error as { status: unknown }).status
    : undefined;
  if (status === 409) {
    return {
      message: "平面图已被其他会话修改。请重新加载并比较版本；不会自动覆盖。",
      conflict: true,
    };
  }
  if (status === 401) return { message: "登录已过期，请重新登录。", conflict: false };
  if (status === 403) return { message: "当前账号没有该项目/位置范围的平面图权限。", conflict: false };
  if (status === 404) return { message: "平面图或其引用资源不存在。", conflict: false };
  return {
    message: error instanceof Error ? error.message : "Floor Plan 操作失败。",
    conflict: false,
  };
}
