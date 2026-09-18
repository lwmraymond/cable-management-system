import type { SpatialCable, SpatialSegment } from "./sceneData";

export type RoutePortion = { segment_id: string; start_offset_m: number; end_offset_m: number; geometry_hash: string; valid?: boolean };

/** Clip source Z-up coordinates, preserving bends, orientation and the original tray. */
export function segmentForCable(cable: Pick<SpatialCable, "route_portions">, segment: SpatialSegment): SpatialSegment | null {
  if (!cable.route_portions) return segment;
  const matches = cable.route_portions.filter(portion => portion.segment_id === segment.id);
  if (matches.length !== 1) return null;
  const portion = matches[0];
  if (portion.valid === false || ![portion.start_offset_m, portion.end_offset_m].every(value => Number.isFinite(value) && value >= 0)) return null;
  const points = segment.coordinates.map(point => ({ x: point.x, y: point.y, z: point.z ?? 0 }));
  if (points.length < 2 || points.length > 512 || points.some(point => ![point.x, point.y, point.z].every(value => Number.isFinite(value) && Math.abs(value) < 100000))) return null;
  const offsets = [0];
  for (let i = 1; i < points.length; i++) offsets.push(offsets[i - 1] + Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y, points[i].z - points[i - 1].z));
  const total = offsets[offsets.length - 1];
  if (!Number.isFinite(total) || total <= 0 || Math.max(portion.start_offset_m, portion.end_offset_m) > total + 1e-7) return null;
  const low = Math.min(portion.start_offset_m, portion.end_offset_m), high = Math.min(total, Math.max(portion.start_offset_m, portion.end_offset_m));
  const at = (offset: number) => {
    for (let i = 1; i < points.length; i++) {
      const span = offsets[i] - offsets[i - 1];
      if (span > 0 && offset <= offsets[i]) {
        const ratio = (offset - offsets[i - 1]) / span, a = points[i - 1], b = points[i];
        return { x: a.x + ratio * (b.x - a.x), y: a.y + ratio * (b.y - a.y), z: a.z + ratio * (b.z - a.z) };
      }
    }
    return points[points.length - 1];
  };
  const coordinates = [at(low), ...points.filter((_, index) => offsets[index] > low && offsets[index] < high), at(high)];
  if (portion.start_offset_m > portion.end_offset_m) coordinates.reverse();
  const recorded = Number.isFinite(segment.length_m) && segment.length_m > 0 ? segment.length_m : total;
  return { ...segment, coordinates, length_m: (high - low) / total * recorded };
}
