import type { SpatialPayload, SpatialSegment } from "./sceneData";
import { segmentForCable } from "./routePortions";

type LengthSegment = {
  id: string; pathwayId: string; pathwayIdentifier: string; name: string;
  recordedLengthM: number | null; geometryLengthM: number | null;
  range?: { startM: number; endM: number; fullRecordedM: number | null };
};
export type PathLengthSummary = {
  recordedLengthM: number | null; segmentRecordedTotalM: number | null; segmentGeometryTotalM: number | null;
  loadedSegmentCount: number; expectedSegmentCount: number; complete: boolean;
  segments: LengthSegment[]; warnings: string[];
};
type Point = [number, number, number];
const finiteCoordinate = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && Math.abs(value) < 100_000;
const positiveLength = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/** Display only: callers retain the unrounded metre value for arithmetic. */
export function formatLength(value: number): string {
  return Number.isFinite(value) && value >= 0 ? value.toLocaleString("en-US", { useGrouping: false, minimumFractionDigits: 2, maximumFractionDigits: 3 }) : "—";
}

function segmentCoordinates(segment: SpatialSegment): Point[] | null {
  if (segment.coordinates.length < 2 || segment.coordinates.some(point => !finiteCoordinate(point.x) || !finiteCoordinate(point.y) || !finiteCoordinate(point.z ?? 0))) return null;
  return segment.coordinates.map(point => [point.x, point.y, point.z ?? 0]);
}

/** Source coordinates are metres, before any schematic room or rack placement. */
export function segmentGeometryLength(segment: SpatialSegment): number | null {
  const points = segmentCoordinates(segment);
  if (!points) return null;
  return positiveLength(points.slice(1).reduce((sum, point, index) => sum + distance(points[index], point), 0));
}

function continuous(parts: Point[][]): boolean {
  if (!parts.length) return false;
  let ends = [parts[0][0], parts[0][parts[0].length - 1]];
  for (const points of parts.slice(1)) {
    const first = points[0], last = points[points.length - 1];
    const next: Point[] = [];
    for (const end of ends) {
      if (distance(end, first) <= 0.001) next.push(last);
      if (distance(end, last) <= 0.001) next.push(first);
    }
    // At most two distinct end coordinates survive each ordered segment.
    ends = next.filter((point, index) => next.findIndex(other => distance(point, other) === 0) === index);
    if (!ends.length) return false;
  }
  return true;
}

/** Completeness also requires usable, continuous geometry; returned references alone are insufficient. */
export function describePathLengths(payload: SpatialPayload, selection: { kind: "cable" | "pathway"; id: string }): PathLengthSummary {
  const result: PathLengthSummary = { recordedLengthM: null, segmentRecordedTotalM: null, segmentGeometryTotalM: null, loadedSegmentCount: 0, expectedSegmentCount: 0, complete: false, segments: [], warnings: [] };
  const cable = selection.kind === "cable" ? payload.cables.find(item => item.id === selection.id) : undefined;
  const pathway = selection.kind === "pathway" ? payload.pathways.find(item => item.id === selection.id) : undefined;
  if (!cable && !pathway) { result.warnings.push("当前范围未加载选中对象。"); return result; }
  result.recordedLengthM = positiveLength(cable?.length_m);
  if (cable?.route_portions) result.warnings.push("以下长度仅计本线路实际经过的线槽部分；采用长度按几何占比折算整段登记值，不含接入或预留。" );
  if (cable) result.warnings.push("线缆登记长度可能来自估算；路径段仅含当前已加载范围，不代表实测或完整敷设线路。");
  else result.warnings.push("登记长度与坐标长度来源不同，均不代表已经现场实测。");
  const partialRoute = cable?.route_scope === "partial";
  if (partialRoute) result.warnings.push("部分登记路径在当前楼层/房间之外或尚未加载，仅显示已加载小计。");
  const refs = cable ? cable.route_segment_ids : [...pathway!.segments].sort((a, b) => a.sequence - b.sequence || a.id.localeCompare(b.id)).map(segment => segment.id);
  result.expectedSegmentCount = refs.length;
  if (!refs.length) {
    result.warnings.push(partialRoute ? "当前没有可显示的已登记路径段；未推算完整敷设长度。" : "未登记路径段；端口连接示意不用于计算布放长度。");
    return result;
  }
  const catalog = new Map(payload.pathways.flatMap(item => item.segments.map(segment => [segment.id, { segment, pathway: item }] as const)));
  const seen = new Set<string>(), locations = new Set<string>();
  const geometry: Point[][] = [];
  let invalid = false;
  for (const id of refs) {
    if (seen.has(id)) { result.warnings.push("登记路线包含重复路径段；未重复累计，也未计算完整总长度。"); invalid = true; continue; }
    seen.add(id);
    const item = catalog.get(id);
    if (!item) { result.warnings.push("部分登记路径段未在当前范围加载；未计算完整总长度。"); invalid = true; continue; }
    const portion = cable?.route_portions?.find(part => part.segment_id === id);
    const segment = cable ? segmentForCable(cable, item.segment) : item.segment;
    const recordedLengthM = segment ? portion ? segment.length_m : positiveLength(segment.length_m) : null;
    const geometryLengthM = !segment || payload.truncated.includes("coordinates") ? null : portion ? Math.abs(portion.end_offset_m - portion.start_offset_m) : segmentGeometryLength(segment);
    if (!segment) result.warnings.push("线槽几何已变化或接入范围无效，旧的局部路径不能继续作为当前线路长度，请重新核对线路。");
    result.segments.push({ id, pathwayId: item.pathway.id, pathwayIdentifier: item.pathway.identifier, name: item.segment.name, recordedLengthM, geometryLengthM,
      ...(portion && segment ? { range: { startM: portion.start_offset_m, endM: portion.end_offset_m, fullRecordedM: positiveLength(item.segment.length_m) } } : {}) });
    locations.add(item.pathway.location_id);
    if (recordedLengthM === null || geometryLengthM === null) {
      result.warnings.push("部分路径段的登记长度或空间坐标无效；未计算完整总长度。"); invalid = true;
    }
    if (geometryLengthM !== null && segment) geometry.push(segmentCoordinates(segment)!);
  }
  result.loadedSegmentCount = result.segments.length;
  const truncated = payload.truncated.some(kind => ["pathways", "segments", "coordinates", ...(cable ? ["routes"] : [])].includes(kind));
  if (truncated) { result.warnings.push("场景路径或坐标数据已截断；未计算完整总长度。"); invalid = true; }
  const multiSpace = locations.size > 1;
  if (multiSpace) result.warnings.push("路径涉及多个空间；仅显示已加载单段长度小计，不包含空间间连接，路线完整性未知。");
  else if (!partialRoute && !invalid && !continuous(geometry)) { result.warnings.push("登记顺序中的路径段未连续相接（容差 1 mm）；未跨越断点计算完整总长度。"); invalid = true; }
  if (!invalid) {
    result.segmentRecordedTotalM = result.segments.reduce((sum, segment) => sum + segment.recordedLengthM!, 0);
    result.segmentGeometryTotalM = result.segments.reduce((sum, segment) => sum + segment.geometryLengthM!, 0);
    result.complete = !multiSpace && !partialRoute;
  }
  result.warnings = [...new Set(result.warnings)];
  return result;
}
