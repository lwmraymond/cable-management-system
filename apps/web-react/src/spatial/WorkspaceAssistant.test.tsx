import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { WorkspaceAssistant } from "./WorkspaceAssistant";
import type { WorkspaceCheckResult } from "./workspaceChecks";

beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());
function checkResult(count = 26): WorkspaceCheckResult {
  return {
    checked: { rooms: 2, racks: 4, devices: 8, cables: 3, pathways: count },
    partial: false, omittedCount: 0,
    issues: Array.from({ length: count }, (_, index) => ({
      id: `pathway-length:${index + 1}`, code: "pathway_length",
      objectLabel: `线槽 ${index + 1} · TRAY-${String(index + 1).padStart(2, "0")}`,
      severity: index % 2 === 0 ? "warning" as const : "info" as const,
      title: index % 2 === 0 ? "线槽长度记录需要核对" : "线槽长度资料待完善",
      detail: "请核对该线槽的长度记录与坐标资料。",
      selection: { kind: "pathway" as const, id: `pathway-${index + 1}` },
      action: "view" as const, actionLabel: "查看详情",
    })),
  };
}
function props() {
  return { result: checkResult(), scopeName: "当前项目 · 全部房间", loading: false, onAction: vi.fn(), onIssue: vi.fn(), onRefresh: vi.fn(), onGuide: vi.fn() };
}
function checks() { return within(screen.getByRole("region", { name: "自动检查结果" })); }

describe("workspace assistant", () => {
  it("paginates checks, searches object identifiers and combines severity with search while resetting the visible page", () => {
    const input = props();
    render(<WorkspaceAssistant {...input} />);
    const results = checks();
    expect(results.getAllByRole("listitem")).toHaveLength(12);
    fireEvent.click(results.getByRole("button", { name: "再显示 12 项" }));
    expect(results.getAllByRole("listitem")).toHaveLength(24);
    fireEvent.click(results.getByRole("button", { name: "再显示 2 项" }));
    expect(results.getAllByRole("listitem")).toHaveLength(26);
    expect(results.queryByRole("button", { name: /^再显示/ })).not.toBeInTheDocument();

    fireEvent.click(results.getByRole("radio", { name: "提示" }));
    expect(results.getAllByRole("listitem")).toHaveLength(12);
    expect(results.getByRole("button", { name: "再显示 1 项" })).toBeInTheDocument();
    fireEvent.change(results.getByRole("textbox", { name: "搜索检查结果" }), { target: { value: "  tray-26  " } });
    expect(results.getAllByRole("listitem")).toHaveLength(1);
    expect(results.getByText("线槽 26 · TRAY-26")).toBeInTheDocument();
    fireEvent.click(results.getByRole("radio", { name: "待核对" }));
    expect(results.queryAllByRole("listitem")).toHaveLength(0);
    expect(results.getByText("没有匹配的检查结果。")).toBeInTheDocument();
    fireEvent.click(results.getByRole("radio", { name: "全部" }));
    fireEvent.click(results.getByRole("button", { name: "查看详情" }));
    expect(input.onIssue).toHaveBeenCalledExactlyOnceWith(input.result.issues[25]);
    expect(input.onAction).not.toHaveBeenCalled();
    expect(input.onRefresh).not.toHaveBeenCalled();
  });

  it("blocks stale planning, operations and issue actions until fresh data arrives while keeping guide and retry available", () => {
    const input = { ...props(), result: checkResult(1) };
    const view = render(<WorkspaceAssistant {...input} stale />);
    expect(screen.getByRole("alert")).toHaveTextContent("刷新未成功");
    const planning = within(screen.getByRole("button", { name: /创建房间/ }).closest("ol")!);
    for (const button of planning.getAllByRole("button")) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    const issueButton = checks().getByRole("button", { name: "查看详情" });
    expect(issueButton).toBeDisabled();
    fireEvent.click(issueButton);
    fireEvent.click(screen.getByRole("radio", { name: "日常运维" }));
    for (const label of ["查找设备与线缆", "新建光纤连接", "新建铜缆连接", "测距与线槽规划"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(input.onAction).not.toHaveBeenCalled();
    expect(input.onIssue).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /打开教学指南/ }));
    fireEvent.click(checks().getByRole("button", { name: "刷新并重新检查" }));
    expect(input.onGuide).toHaveBeenCalledTimes(1);
    expect(input.onRefresh).toHaveBeenCalledTimes(1);

    view.rerender(<WorkspaceAssistant {...input} stale={false} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建光纤连接" }));
    fireEvent.click(checks().getByRole("button", { name: "查看详情" }));
    expect(input.onAction).toHaveBeenCalledExactlyOnceWith("fiber");
    expect(input.onIssue).toHaveBeenCalledExactlyOnceWith(input.result.issues[0]);
  });
});
