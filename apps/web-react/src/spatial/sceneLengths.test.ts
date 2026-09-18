import { describe, expect, it } from "vitest";
import { buildScene, type SpatialPayload, type SpatialSegment } from "./sceneData";
import { describePathLengths, formatLength, segmentGeometryLength } from "./sceneLengths";

function payload(): SpatialPayload {
  return {
    scope: { tenant_id: "tenant", project_id: null, location_id: "room" },
    locations: [{ id: "room", parent_id: null, identifier: "ROOM", name: "Room", location_type: "room", dimensions: { width_m: 8, depth_m: 6 }, coordinates: {}, transform_3d: {} }],
    racks: [], devices: [], ports: [], truncated: [],
    pathways: [{ id: "tray", identifier: "TRAY", name: "Tray", type: "basket_tray", location_id: "room", segments: [
      { id: "first", sequence: 1, name: "第一段", length_m: 4, coordinates: [{ x: 0, y: 0, z: 0 }, { x: 3, y: 0, z: 0 }] },
      { id: "second", sequence: 2, name: "第二段", length_m: 7, coordinates: [{ x: 3, y: 4, z: 0 }, { x: 3, y: 0, z: 0 }] },
    ] }],
    cables: [{ id: "cable", identifier: "CABLE", media_type: "copper", construction: "patch_cord", installation_status: "planned", length_m: 15, test_status: null, terminations: [], route_segment_ids: ["first", "second"], endpoint_scope: "none" }],
  };
}
const selectedCable = { kind: "cable", id: "cable" } as const;

describe("source-based pathway and cable lengths", () => {
  it("separates cable registration, segment registration and coordinates without requiring endpoint visibility", () => {
    const data = payload(), before = structuredClone(data);
    expect(describePathLengths(data, selectedCable)).toEqual({
      recordedLengthM: 15, segmentRecordedTotalM: 11, segmentGeometryTotalM: 7, loadedSegmentCount: 2, expectedSegmentCount: 2, complete: true,
      segments: [
        { id: "first", pathwayId: "tray", pathwayIdentifier: "TRAY", name: "第一段", recordedLengthM: 4, geometryLengthM: 3 },
        { id: "second", pathwayId: "tray", pathwayIdentifier: "TRAY", name: "第二段", recordedLengthM: 7, geometryLengthM: 4 },
      ], warnings: [expect.stringMatching(/登记.*估算.*已加载.*不代表实测或完整/)],
    });
    expect(data).toEqual(before);
  });
  it("preserves cable route order and sorts pathways by registered sequence", () => {
    const data = payload(); data.cables[0].route_segment_ids.reverse(); data.pathways[0].segments.reverse();
    const cable = describePathLengths(data, selectedCable);
    expect(cable.segments.map(segment => segment.id)).toEqual(["second", "first"]); expect(cable.complete).toBe(true);
    const pathway = describePathLengths(data, { kind: "pathway", id: "tray" });
    expect(pathway.segments.map(segment => segment.id)).toEqual(["first", "second"]);
    expect(pathway.recordedLengthM).toBeNull(); expect(pathway.segmentGeometryTotalM).toBe(7);
  });
  it.each(["missing", "duplicate", "disconnected", "invalid-coordinates", "zero-length", "invalid-record", "truncated"])("does not produce complete totals for %s segments", issue => {
    const data = payload();
    if (issue === "missing") data.cables[0].route_segment_ids.push("not-loaded");
    if (issue === "duplicate") data.cables[0].route_segment_ids.push("first");
    if (issue === "disconnected") data.pathways[0].segments[1].coordinates[1].x = 3.01;
    if (issue === "invalid-coordinates") data.pathways[0].segments[1].coordinates[1].x = Number.NaN;
    if (issue === "zero-length") data.pathways[0].segments[1].coordinates[1] = { ...data.pathways[0].segments[1].coordinates[0] };
    if (issue === "invalid-record") data.pathways[0].segments[1].length_m = Number.POSITIVE_INFINITY;
    if (issue === "truncated") data.truncated.push("coordinates");
    const result = describePathLengths(data, selectedCable);
    expect(result).toMatchObject({ complete: false, recordedLengthM: 15, segmentRecordedTotalM: null, segmentGeometryTotalM: null, loadedSegmentCount: 2, expectedSegmentCount: ["missing", "duplicate"].includes(issue) ? 3 : 2 });
    expect(result.warnings.join(" ")).toMatch(/未.*计算完整总长度/);
    expect(result.segments.map(segment => segment.id)).toEqual(["first", "second"]);
    if (issue === "truncated") expect(result.segments.every(segment => segment.geometryLengthM === null)).toBe(true);
  });
  it("uses the backend 1 mm join tolerance without adding gaps to source geometry", () => {
    const data = payload(); data.pathways[0].segments[1].coordinates = [{ x: 3.0005, y: 0 }, { x: 5.0005, y: 0 }];
    const result = describePathLengths(data, selectedCable);
    expect(result.complete).toBe(true); expect(result.segmentGeometryTotalM).toBeCloseTo(5, 12);
  });
  it("only sums local segments across spaces and never includes schematic layout distance", () => {
    const data = payload(), second = data.pathways[0].segments.pop()!;
    data.pathways.push({ ...data.pathways[0], id: "other-tray", location_id: "other-room", segments: [second] });
    data.locations.push({ ...data.locations[0], id: "other-room", name: "Other", dimensions: { width_m: 80, depth_m: 40 }, coordinates: { x: 2000, y: 3000 } });
    const result = describePathLengths(data, selectedCable);
    expect(result).toMatchObject({ complete: false, segmentRecordedTotalM: 11, segmentGeometryTotalM: 7 });
    expect(result.warnings.join(" ")).toMatch(/多个空间.*单段长度小计.*不包含空间间连接/);
    data.locations.reverse(); data.locations[0].dimensions.width_m = 800;
    expect(describePathLengths(data, selectedCable)).toEqual(result);
  });
  it("does not interpret absent routes or a zero value as complete wiring", () => {
    const data = payload(); data.cables[0].route_segment_ids = []; data.cables[0].length_m = 0;
    expect(describePathLengths(data, selectedCable)).toMatchObject({ complete: false, recordedLengthM: null, segmentRecordedTotalM: null, segmentGeometryTotalM: null, expectedSegmentCount: 0, loadedSegmentCount: 0, segments: [] });
    expect(describePathLengths(data, { kind: "pathway", id: "not-loaded" })).toMatchObject({ complete: false, segments: [], warnings: ["当前范围未加载选中对象。"] });
  });
  it("computes 3D lengths and formats display separately from the unrounded value", () => {
    const segment: SpatialSegment = { id: "3d", sequence: 1, name: "3D", length_m: 90, coordinates: [{ x: 0, y: 0 }, { x: 3, y: 4, z: 12 }] };
    expect(segmentGeometryLength(segment)).toBe(13);
    segment.coordinates = [{ x: 0, y: 0 }, { x: 1, y: 1 }];
    expect(segmentGeometryLength(segment)).toBe(Math.SQRT2); expect(formatLength(segmentGeometryLength(segment)!)).toBe("1.414");
    expect(formatLength(2)).toBe("2.00"); expect(formatLength(Number.NaN)).toBe("—");
    segment.coordinates[1].x = Number.POSITIVE_INFINITY; expect(segmentGeometryLength(segment)).toBeNull();
    segment.coordinates = [{ x: 1, y: 1 }]; expect(segmentGeometryLength(segment)).toBeNull();
  });
});

describe("renderer dimension provenance", () => {
  it("attaches coordinate dimensions to registered tray and cable segments", () => {
    expect(buildScene(payload(), { showPathways: true }).data.paths.map(path => ({ kind: path.kind, dimension: path.dimension }))).toEqual([
      { kind: "pathway", dimension: { label: "第一段 · 坐标 3.00 m", lengthM: 3, basis: "coordinates" } },
      { kind: "pathway", dimension: { label: "第二段 · 坐标 4.00 m", lengthM: 4, basis: "coordinates" } },
      { kind: "cable", dimension: { label: "第一段 · 坐标 3.00 m", lengthM: 3, basis: "coordinates" } },
      { kind: "cable", dimension: { label: "第二段 · 坐标 4.00 m", lengthM: 4, basis: "coordinates" } },
    ]);
  });
  it("does not label a truncated coordinate prefix as a full segment length", () => {
    const data = payload(); data.truncated.push("coordinates"); const paths = buildScene(data, { showPathways: true }).data.paths;
    expect(paths).toHaveLength(4); expect(paths.every(path => path.dimension === undefined)).toBe(true);
  });
});


describe("partial route length coverage", () => {
  it("does not describe outside registered paths as unregistered or infer a complete length", () => {
    const data = payload();
    data.cables[0].route_scope = "partial";
    data.cables[0].route_segment_ids = [];
    const result = describePathLengths(data, selectedCable);
    expect(result).toMatchObject({ recordedLengthM: 15, complete: false, loadedSegmentCount: 0, expectedSegmentCount: 0, segmentRecordedTotalM: null, segmentGeometryTotalM: null });
    expect(result.warnings.join(" ")).toMatch(/部分登记路径.*楼层\/房间之外.*已加载小计/);
    expect(result.warnings.join(" ")).not.toMatch(/未登记路径段/);
  });

  it.each(["connected", "disconnected"] as const)("reports only loaded subtotals for %s parts of a partial registered route", geometry => {
    const data = payload();
    data.cables[0].route_scope = "partial";
    if (geometry === "disconnected") data.pathways[0].segments[1].coordinates = [{ x: 7, y: 0 }, { x: 7, y: 4 }];
    const before = structuredClone(data);
    const result = describePathLengths(data, selectedCable);
    expect(result).toMatchObject({ complete: false, segmentRecordedTotalM: 11, segmentGeometryTotalM: 7, loadedSegmentCount: 2, expectedSegmentCount: 2 });
    expect(result.warnings.join(" ")).toMatch(/部分登记路径.*已加载小计/);
    expect(data).toEqual(before);
  });

  it.each(["disconnected", "invalid", "truncated"] as const)("still rejects %s geometry when all registered references were returned", issue => {
    const data = payload();
    data.cables[0].route_scope = "complete";
    if (issue === "disconnected") data.pathways[0].segments[1].coordinates[1].x += 1;
    if (issue === "invalid") data.pathways[0].segments[1].coordinates[0].z = Number.NaN;
    if (issue === "truncated") data.truncated = ["coordinates"];
    expect(describePathLengths(data, selectedCable)).toMatchObject({ complete: false, segmentGeometryTotalM: null });
  });

  it("preserves the no-registration explanation for an explicit none route scope", () => {
    const data = payload();
    data.cables[0].route_scope = "none";
    data.cables[0].route_segment_ids = [];
    const result = describePathLengths(data, selectedCable);
    expect(result.complete).toBe(false);
    expect(result.warnings.join(" ")).toMatch(/未登记路径段/);
    expect(result.warnings.join(" ")).not.toMatch(/部分登记路径/);
  });
});


describe("saved portions in lengths and 3D", () => {
  const portions = () => [
    { segment_id: "first", start_offset_m: 1, end_offset_m: 3, geometry_hash: "a".repeat(64), valid: true },
    { segment_id: "second", start_offset_m: 4, end_offset_m: 2, geometry_hash: "b".repeat(64), valid: true },
  ];
  it("uses the same clipped geometry for preview and persisted cables while retaining full trays", () => {
    const data = payload(); data.cables[0].route_portions = portions();
    const before = structuredClone(data), result = describePathLengths(data, selectedCable);
    expect(result.complete).toBe(true);
    expect(result.segmentGeometryTotalM).toBe(4);
    expect(result.segmentRecordedTotalM).toBeCloseTo(8 / 3 + 3.5);
    expect(result.segments.map(s => s.range)).toEqual([{ startM: 1, endM: 3, fullRecordedM: 4 }, { startM: 4, endM: 2, fullRecordedM: 7 }]);
    const paths = buildScene(data, { showPathways: true }).data.paths;
    expect(paths.filter(p => p.kind === "pathway").map(p => p.dimension?.lengthM)).toEqual([3, 4]);
    const cablePaths = paths.filter(p => p.kind === "cable");
    expect(cablePaths.map(p => p.dimension?.lengthM)).toEqual([2, 2]);
    expect(cablePaths.every(p => p.dimension?.label.includes("局部坐标"))).toBe(true);
    const preview = structuredClone(data); preview.cables[0].route_portions?.forEach(p => { delete p.valid; });
    expect(buildScene(preview, { showPathways: true }).data.paths).toEqual(paths);
    expect(data).toEqual(before);
  });
  it("does not fabricate a valid current length or full cable path from stale geometry", () => {
    const data = payload(); data.cables[0].route_portions = portions(); data.cables[0].route_portions.forEach(p => { p.valid = false; });
    expect(describePathLengths(data, selectedCable)).toMatchObject({ complete: false, segmentRecordedTotalM: null, segmentGeometryTotalM: null });
    const scene = buildScene(data, { showPathways: true });
    expect(scene.data.paths.filter(p => p.kind === "cable")).toHaveLength(0);
    expect(scene.warnings.join(" ")).toMatch(/几何已变化/);
  });
  it("does not omit a zero-length valid tray portion from totals", () => {
    const data = payload(); data.cables[0].route_segment_ids = ["first"];
    data.cables[0].route_portions = [{ ...portions()[0], end_offset_m: 1 }];
    expect(describePathLengths(data, selectedCable)).toMatchObject({ complete: true, segmentRecordedTotalM: 0, segmentGeometryTotalM: 0 });
  });
});
