import { describe, expect, it } from "vitest";
import type { SpatialSegment } from "./sceneData";
import { segmentForCable, type RoutePortion } from "./routePortions";

const source: SpatialSegment = { id: "tray", sequence: 1, name: "L tray", length_m: 18, coordinates: [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 3 }, { x: 4, y: 3, z: 2 }] };
const portion = (start = 2, end = 8): RoutePortion => ({ segment_id: "tray", start_offset_m: start, end_offset_m: end, geometry_hash: "a".repeat(64) });

describe("clipping a cable to its saved tray range", () => {
  it("preserves bends, vertical geometry and the original tray in either direction", () => {
    const before = structuredClone(source);
    const forward = segmentForCable({ route_portions: [portion()] }, source)!;
    expect(forward.coordinates).toEqual([{ x: 2, y: 0, z: 0 }, { x: 4, y: 0, z: 0 }, { x: 4, y: 3, z: 0 }, { x: 4, y: 3, z: 1 }]);
    expect(forward.length_m).toBe(12);
    expect(segmentForCable({ route_portions: [portion(8, 2)] }, source)?.coordinates).toEqual([...forward.coordinates].reverse());
    expect(source).toEqual(before);
  });
  it("retains coincident entry/exit as a valid zero tray length", () => {
    const clipped = segmentForCable({ route_portions: [portion(2, 2)] }, source)!;
    expect(clipped.coordinates).toEqual([{ x: 2, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }]);
    expect(clipped.length_m).toBe(0);
  });
  it.each(["stale", "missing", "duplicate", "negative", "outside", "non-finite"])("rejects %s portions without drawing the full tray as cable", issue => {
    const part = portion();
    if (issue === "stale") part.valid = false;
    if (issue === "negative") part.start_offset_m = -1;
    if (issue === "outside") part.end_offset_m = 10;
    if (issue === "non-finite") part.start_offset_m = Number.NaN;
    const portions = issue === "missing" ? [] : issue === "duplicate" ? [part, part] : [part];
    expect(segmentForCable({ route_portions: portions }, source)).toBeNull();
  });
  it("keeps legacy whole-segment routes unchanged and uses geometry when registration is absent", () => {
    expect(segmentForCable({}, source)).toBe(source);
    expect(segmentForCable({ route_portions: [portion()] }, { ...source, length_m: 0 })?.length_m).toBe(6);
  });
});
