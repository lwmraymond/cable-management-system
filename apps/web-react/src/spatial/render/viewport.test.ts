import { describe, expect, it } from "vitest";
import { shouldRefitViewport } from "./viewport";

describe("focused scene resizing", () => {
  it("refits when the connection sidebar opens or closes", () => {
    expect(shouldRefitViewport([950, 600], [595, 600])).toBe(true);
    expect(shouldRefitViewport([595, 600], [950, 600])).toBe(true);
  });
  it("preserves zoom for small resizes and proportional window scaling", () => {
    expect(shouldRefitViewport([950, 600], [915, 590])).toBe(false);
    expect(shouldRefitViewport([950, 600], [760, 480])).toBe(false);
  });
  it("compares an animated transition with the last fit instead of only its last small step", () => {
    const lastFit = [950, 600] as const;
    expect([930, 910, 890, 870, 850].map(width => shouldRefitViewport(lastFit, [width, 600]))).toEqual([false, false, false, false, true]);
    expect(shouldRefitViewport([850, 600], [850, 600])).toBe(false);
  });
  it("ignores hidden or uninitialized viewports", () => {
    expect(shouldRefitViewport([0, 0], [595, 600])).toBe(false);
    expect(shouldRefitViewport([950, 600], [0, 0])).toBe(false);
  });
});
