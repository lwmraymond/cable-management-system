import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FiberChannelBreakoutPanel } from "./FiberChannelBreakoutPanel";
import { FiberOtdrPanel } from "./FiberOtdrPanel";
import type { FiberTopologySession } from "./fiberTopologySession";

const project = "11111111-2222-4333-8444-555555555555";
const resource = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());
function session(result: unknown) {
  const post = vi.fn().mockResolvedValue(result);
  const errors: unknown[] = [];
  const value: FiberTopologySession = {
    api: { request: vi.fn(), download: vi.fn() }, busy: false, notice: "", canRead: true, canWrite: true,
    post, run: operation => { void operation().catch(error => errors.push(error)); },
  };
  return { value, post, errors };
}
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }

describe("advanced topology forms", () => {
  it("creates a channel with parsed members and releases the returned version", async () => {
    const api = session({ id: resource, version: 3 });
    render(<FiberChannelBreakoutPanel session={api.value} initialProjectId={project} />);
    fill("Channel 标识", "CH-1"); fill("Channel 名称", "Test channel");
    fill("成员（每行：fiber_strand 或 copper_pair,资源 UUID,角色）", `fiber_strand,${resource},tx`);
    fireEvent.click(screen.getByRole("button", { name: "创建 Channel" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/fiber/channels", {
      project_id: project, identifier: "CH-1", name: "Test channel", medium: "fiber", topology: "simplex",
      members: [{ kind: "fiber_strand", resource_id: resource, role: "tx" }],
    }));
    fireEvent.click(screen.getByRole("button", { name: "释放 Channel" }));
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith(`/fiber/channels/${resource}/release`, { expected_version: 3 }));
    expect(api.errors).toEqual([]);
  });

  it("submits a timezone-aware OTDR measurement and parsed events", async () => {
    const api = session({ id: resource, events: [] });
    render(<FiberOtdrPanel session={api.value} initialProjectId={project} />);
    fill("OTDR 线缆 UUID", resource); fill("测量来源名称", "test.sor");
    fill("采集时间（含时区）", "2026-09-17T10:00:00+08:00");
    fill("事件（每行：距离 m,类型,损耗 dB,反射 dB,置信度,备注）", "0,launch,0,,1,start\n100,end,,,1,end");
    fireEvent.click(screen.getByRole("button", { name: "保存 OTDR 记录" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/fiber/otdr-records", {
      project_id: project, cable_id: resource, direction: "A", wavelength_nm: 1550,
      acquired_at: "2026-09-17T10:00:00+08:00", source_name: "test.sor",
      events: [
        { event_type: "launch", distance_m: 0, loss_db: 0, confidence: 1, notes: "start" },
        { event_type: "end", distance_m: 100, confidence: 1, notes: "end" },
      ],
    }));
    expect(api.errors).toEqual([]);
  });
});
