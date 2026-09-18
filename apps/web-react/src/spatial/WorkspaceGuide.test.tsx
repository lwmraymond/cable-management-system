import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkspaceGuide } from "./WorkspaceGuide";

beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => {
  window.localStorage.clear();
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => vi.unstubAllGlobals());
function props() { return { open: true, onClose: vi.fn(), onAction: vi.fn(), storageKey: "guide:project-a:user-a" }; }
function search(value: string) { fireEvent.change(screen.getByRole("textbox", { name: "搜索教程" }), { target: { value } }); }

describe("3D workspace guide", () => {
  it("searches actual instructions, delegates the requested tool and keeps read progress independent of opening it", () => {
    const input = props();
    const view = render(<WorkspaceGuide {...input} />);
    search("预留长度");
    expect(screen.getByRole("heading", { name: "选择 A/B 端并确认走线" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "建立房间与出入口" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "连接光纤" }));
    expect(input.onAction).toHaveBeenCalledExactlyOnceWith("fiber");
    expect(input.onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "标记已读：选择 A/B 端并确认走线" })).not.toBeChecked();
    expect(window.localStorage.getItem(input.storageKey)).toBeNull();
    view.rerender(<WorkspaceGuide {...input} disabled />);
    expect(screen.getByRole("button", { name: "连接铜缆" })).toBeDisabled();
    search("Observium");
    expect(screen.getByRole("region", { name: "未来发现数据" })).toHaveTextContent("Observium 尚未接入");
    search("no-such-guide-term");
    expect(screen.getByRole("status")).toHaveTextContent("没有匹配的教程");
    search("");
    expect(screen.getByRole("heading", { name: "建立房间与出入口" })).toBeInTheDocument();
  });

  it("restores valid read IDs, persists only steps and isolates progress when the storage key changes", () => {
    const input = props();
    window.localStorage.setItem(input.storageKey, JSON.stringify(["scope", "connect", "unknown-step", "scope", 12]));
    window.localStorage.setItem("guide:project-b:user-a", JSON.stringify(["rack"]));
    const view = render(<WorkspaceGuide {...input} />);
    expect(screen.getByText("阅读进度：已读 2 / 7 步")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "标记已读：选择项目与空间范围" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "标记已读：选择 A/B 端并确认走线" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "标记已读：测距并转为线槽" }));
    expect(JSON.parse(window.localStorage.getItem(input.storageKey)!)).toEqual(["scope", "connect", "measure"]);
    expect(screen.getByText("阅读进度：已读 3 / 7 步")).toBeInTheDocument();
    view.rerender(<WorkspaceGuide {...input} storageKey="guide:project-b:user-a" />);
    expect(screen.getByText("阅读进度：已读 1 / 7 步")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "标记已读：批量布置机柜" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "标记已读：选择项目与空间范围" })).not.toBeChecked();
    expect(JSON.parse(window.localStorage.getItem(input.storageKey)!)).toEqual(["scope", "connect", "measure"]);
    expect(input.onAction).not.toHaveBeenCalled();
  });

  it("continues in memory when browser storage is unavailable and allows the read mark to be undone", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Storage denied", "SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Storage denied", "SecurityError"); });
    const input = props();
    render(<WorkspaceGuide {...input} />);
    expect(screen.getByRole("status")).toHaveTextContent("本次阅读仍可继续");
    search("出入口");
    const read = screen.getByRole("checkbox", { name: "标记已读：建立房间与出入口" });
    fireEvent.click(read);
    expect(read).toBeChecked();
    expect(screen.getByText("阅读进度：已读 1 / 7 步")).toBeInTheDocument();
    fireEvent.click(read);
    expect(read).not.toBeChecked();
    expect(screen.getByText("阅读进度：已读 0 / 7 步")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建房间" }));
    expect(input.onAction).toHaveBeenCalledExactlyOnceWith("room");
  });

  it("stays closed until requested and contains Escape so closing help does not exit the underlying workspace tool", async () => {
    const input = props(), onWorkspaceKey = vi.fn();
    const view = render(<div onKeyDown={onWorkspaceKey}><button>原工作区入口</button><WorkspaceGuide {...input} open={false} /></div>);
    screen.getByRole("button", { name: "原工作区入口" }).focus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(input.onAction).not.toHaveBeenCalled();
    view.rerender(<div onKeyDown={onWorkspaceKey}><button>原工作区入口</button><WorkspaceGuide {...input} /></div>);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "搜索教程" }), { key: "Escape", keyCode: 27 });
    await waitFor(() => expect(input.onClose).toHaveBeenCalledTimes(1));
    expect(onWorkspaceKey).not.toHaveBeenCalled();
    expect(input.onAction).not.toHaveBeenCalled();
  });
});
