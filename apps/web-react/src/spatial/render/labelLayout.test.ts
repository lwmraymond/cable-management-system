import { describe, expect, it } from "vitest";
import { visibleLabelIds, type LabelBox } from "./labelLayout";
const box = (id: string, changes: Partial<LabelBox> = {}): LabelBox => ({ id, x: 20, y: 20, width: 130, height: 25, priority: 10, depth: 5, ...changes });
describe("screen label placement", () => {
  it("keeps selection readable before competing room/rack labels regardless of input order", () => {
    const labels = [box("rack"), box("room", { priority: 100 }), box("selection", { priority: 1000 }), box("separate", { x: 300 })];
    expect([...visibleLabelIds(labels, 800, 600)]).toEqual(["selection", "separate"]);
    expect(visibleLabelIds(labels.reverse(), 800, 600)).toEqual(new Set(["selection", "separate"]));
  });
  it("rejects clipped, invalid and behind-camera labels, allowing a nearer readable label", () => {
    expect(visibleLabelIds([box("edge", { x: 0 }), box("bottom", { y: 580 }), box("invalid", { x: NaN }), box("behind", { depth: -1 }), box("far", { depth: 10 }), box("near", { depth: 2 })], 800, 600)).toEqual(new Set(["near"]));
  });
  it("does not mutate source geometry while decluttering dense scenes", () => {
    const labels = Array.from({ length: 120 }, (_, i) => box(String(i), { x: 10 + i % 12 * 50, y: 10 + Math.floor(i / 12) * 30 }));
    const before = structuredClone(labels);
    const accepted = visibleLabelIds(labels, 800, 600);
    expect(accepted.size).toBeLessThan(60);
    expect(accepted.size).toBeGreaterThan(0);
    expect(labels).toEqual(before);
  });
});


it("reserves length and measurement captions without counting them against object-label density", () => {
  expect(visibleLabelIds([box("room", { priority: 100 }), box("clear", { x: 300 })], 800, 600, [box("length")])).toEqual(new Set(["clear"]));
});
