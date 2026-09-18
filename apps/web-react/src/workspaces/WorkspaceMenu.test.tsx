import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { WorkspaceMenu, type WorkspaceMenuProps } from "./WorkspaceMenu";
import type { BrowserSession, Workspace, WorkspaceMember } from "./types";
import { ApiError } from "../api/client";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", async importOriginal => ({ ...await importOriginal<typeof import("../api/client")>(), createApiClient: () => ({ request }) }));
const personal: Workspace = { id: "personal-a", name: "个人规划", kind: "personal", role: "owner", can_manage: true, permissions: ["*"], scopes: [{ project_id: null, location_id: null }] };
const shared: Workspace = { id: "shared-a", name: "团队布线", kind: "shared", role: "viewer", can_manage: false, permissions: ["location:read"], scopes: [{ project_id: "project-a", location_id: "room-a" }] };
const owner: WorkspaceMember = { user_id: "owner-a", email: "owner@example.com", display_name: "林工程师", role: "owner", active: true, is_owner: true };
const administrator: WorkspaceMember = { user_id: "admin-b", email: "admin@example.com", display_name: "工作区管理员", role: "admin", active: true, is_owner: true };
const existing: WorkspaceMember = { user_id: "editor-a", email: "editor@example.com", display_name: "现场运维", role: "editor", active: true, is_owner: false };
const session: BrowserSession = { user: { id: owner.user_id, email: owner.email, display_name: owner.display_name }, auth_method: "cookie", workspaces: [personal, shared], csrf_header_name: "X-CSRF-Token", csrf_cookie_name: "sim_csrf", can_create_workspaces: true };
function props(overrides: Partial<WorkspaceMenuProps> = {}): WorkspaceMenuProps {
  return { session, context: { tenantId: personal.id, actorId: owner.user_id, projectId: "project-old", locationId: "room-old" }, onSelect: vi.fn(), onRefresh: vi.fn().mockResolvedValue(undefined), onSignOut: vi.fn(), ...overrides };
}
function open(input: WorkspaceMenuProps) {
  const rendered = render(<WorkspaceMenu {...input} />);
  fireEvent.click(screen.getByRole("button", { name: "管理账户和工作区" }));
  return rendered;
}
function section(name: string) { return within(screen.getByRole("region", { name })); }
function writes() { return request.mock.calls.filter(([, options]) => options?.method && options.method !== "GET"); }
function startCreation(name = "独立规划") {
  const create = section("创建工作区");
  fireEvent.click(create.getByRole("button", { name: "新建工作区" }));
  fireEvent.change(create.getByRole("textbox", { name: "新工作区名称" }), { target: { value: name } });
  return create.getByRole("button", { name: "创建个人工作区" });
}
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => {
  request.mockReset().mockImplementation((path: string, options?: RequestInit) => {
    if (path.endsWith("/members") && !options?.method) return Promise.resolve([owner, administrator, existing]);
    throw new Error(`Unexpected ${options?.method ?? "GET"} ${path}`);
  });
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => vi.unstubAllGlobals());

describe("account workspace menu", () => {
  it("labels the real demo identity, hides owner controls for viewers and delegates switching without sending mutations", async () => {
    const input = props({ session: { ...session, auth_method: "demo", can_create_workspaces: false }, context: { tenantId: shared.id, actorId: owner.user_id } });
    render(<WorkspaceMenu {...input} />);
    expect(screen.getByText("演示身份")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "管理账户和工作区" }));
    expect(section("当前账户").getByText(owner.email)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "管理当前工作区" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "工作区成员" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "创建工作区" })).not.toBeInTheDocument();
    fireEvent.click(section("可用工作区").getByRole("button", { name: "切换到工作区 个人规划" }));
    expect(input.onSelect).toHaveBeenCalledExactlyOnceWith(personal);
    expect(request).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "管理账户和工作区" }));
    fireEvent.click(screen.getByRole("button", { name: "退出演示身份" }));
    expect(input.onSignOut).toHaveBeenCalledTimes(1);
  });

  it("creates only a personal workspace after explicit submission and selects the returned workspace after refresh", async () => {
    const created = { ...personal, id: "personal-new", name: "独立规划" };
    request.mockResolvedValueOnce(created);
    const input = props();
    open(input);
    const submit = startCreation("  独立规划  ");
    expect(writes()).toHaveLength(0);
    fireEvent.click(submit);
    await waitFor(() => expect(input.onSelect).toHaveBeenCalledExactlyOnceWith(created));
    expect(writes()).toEqual([["/workspaces", { method: "POST", body: JSON.stringify({ name: "独立规划", kind: "personal" }) }]]);
    expect(input.onRefresh).toHaveBeenCalledTimes(1);
  });

  it("keeps a failed creation form and does not switch until a retry succeeds", async () => {
    const input = props();
    request.mockRejectedValueOnce(new Error("工作区暂时无法创建")).mockResolvedValueOnce({ ...personal, id: "personal-new", name: "独立规划" });
    open(input);
    fireEvent.click(startCreation());
    expect(await screen.findByRole("alert")).toHaveTextContent("工作区暂时无法创建");
    expect(section("创建工作区").getByRole("textbox", { name: "新工作区名称" })).toHaveValue("独立规划");
    expect(input.onSelect).not.toHaveBeenCalled();
    fireEvent.click(section("创建工作区").getByRole("button", { name: "创建个人工作区" }));
    await waitFor(() => expect(input.onSelect).toHaveBeenCalledTimes(1));
    expect(writes()).toHaveLength(2);
  });

  it("retries only the refresh after creation succeeded but the workspace list could not reload", async () => {
    const created = { ...personal, id: "personal-new", name: "独立规划" };
    const input = props({ onRefresh: vi.fn().mockRejectedValueOnce(new Error("列表服务不可用")).mockResolvedValueOnce(undefined) });
    request.mockResolvedValueOnce(created);
    open(input);
    fireEvent.click(startCreation());
    expect(await screen.findByRole("alert")).toHaveTextContent("更改已保存，但列表刷新失败");
    expect(input.onSelect).not.toHaveBeenCalled();
    expect(section("创建工作区").getByRole("button", { name: "新建工作区" })).toBeDisabled();
    fireEvent.click(section("可用工作区").getByRole("button", { name: "刷新工作区列表" }));
    await waitFor(() => expect(input.onSelect).toHaveBeenCalledExactlyOnceWith(created));
    expect(writes()).toHaveLength(1);
    expect(input.onRefresh).toHaveBeenCalledTimes(2);
  });

  it("requires both sharing acknowledgement and the sharing button, then exposes member management only after the session confirms sharing", async () => {
    const updated: Workspace = { ...personal, kind: "shared" };
    const input = props();
    const view = open(input);
    const management = section("管理当前工作区");
    const enable = management.getByRole("button", { name: "启用共享" });
    expect(enable).toBeDisabled();
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(management.getByRole("checkbox", { name: "我确认将此个人工作区改为共享工作区" }));
    expect(writes()).toHaveLength(0);
    request.mockResolvedValueOnce(updated);
    fireEvent.click(enable);
    await waitFor(() => expect(input.onRefresh).toHaveBeenCalledTimes(1));
    expect(writes()).toEqual([["/workspaces/personal-a", { method: "PATCH", body: JSON.stringify({ kind: "shared" }) }]]);
    expect(screen.queryByRole("region", { name: "工作区成员" })).not.toBeInTheDocument();
    view.rerender(<WorkspaceMenu {...input} session={{ ...session, workspaces: [updated, shared] }} />);
    const members = section("工作区成员");
    await members.findByText(existing.email);
    expect(members.getByRole("button", { name: "保存成员权限" })).not.toBeDisabled();
    expect(writes()).toHaveLength(1);
  });

  it("preserves a rejected rename and submits only the new name on retry", async () => {
    const input = props();
    open(input);
    const management = section("管理当前工作区");
    fireEvent.change(management.getByRole("textbox", { name: "工作区名称" }), { target: { value: "修订后的规划" } });
    request.mockRejectedValueOnce(new Error("名称暂时无法更新")).mockResolvedValueOnce({ ...personal, name: "修订后的规划" });
    fireEvent.click(management.getByRole("button", { name: "保存名称" }));
    await management.findByRole("alert");
    expect(management.getByRole("textbox", { name: "工作区名称" })).toHaveValue("修订后的规划");
    expect(input.onRefresh).not.toHaveBeenCalled();
    fireEvent.click(management.getByRole("button", { name: "保存名称" }));
    await waitFor(() => expect(input.onRefresh).toHaveBeenCalledTimes(1));
    expect(writes().map(([, options]) => JSON.parse(options.body))).toEqual([{ name: "修订后的规划" }, { name: "修订后的规划" }]);
  });

  it("keeps rejected member input, applies the explicit role and requires confirmation before revoking a non-owner", async () => {
    const member: WorkspaceMember = { user_id: "member-new", email: "colleague@example.com", display_name: "新同事", role: "editor", active: true, is_owner: false };
    const managed = { ...shared, can_manage: true, role: "owner" };
    let storedMembers = [owner, administrator, existing];
    let grants = 0;
    request.mockImplementation((path: string, options?: RequestInit) => {
      if (!options?.method) return Promise.resolve(storedMembers);
      if (options.method === "PUT") {
        if (++grants === 1) return Promise.reject(new Error("账号不存在或尚未启用"));
        storedMembers = [...storedMembers, member]; return Promise.resolve(storedMembers);
      }
      if (options.method === "DELETE" && path.includes(`/members/${member.user_id}?`)) { storedMembers = storedMembers.filter(item => item.user_id !== member.user_id); return Promise.resolve(undefined); }
      throw new Error(`Unexpected ${options.method} ${path}`);
    });
    const input = props({ session: { ...session, workspaces: [personal, managed] }, context: { tenantId: shared.id, actorId: owner.user_id } });
    open(input);
    const members = section("工作区成员");
    await members.findByText(existing.email);
    expect(members.queryByRole("button", { name: `撤销 ${owner.email} 的访问权限` })).not.toBeInTheDocument();
    expect(members.queryByRole("button", { name: `撤销 ${administrator.email} 的访问权限` })).not.toBeInTheDocument();
    fireEvent.change(members.getByRole("textbox", { name: "成员邮箱" }), { target: { value: member.email } });
    fireEvent.mouseDown(members.getByRole("combobox", { name: "访问角色" }));
    fireEvent.click(screen.getByText("编辑者 · 可修改"));
    fireEvent.click(members.getByRole("button", { name: "保存成员权限" }));
    expect(await members.findByRole("alert")).toHaveTextContent("账号不存在或尚未启用");
    expect(members.getByRole("textbox", { name: "成员邮箱" })).toHaveValue(member.email);
    fireEvent.click(members.getByRole("button", { name: "保存成员权限" }));
    await members.findByText(member.email);
    expect(writes().map(([path, options]) => ({ path, method: options.method, body: JSON.parse(options.body) }))).toEqual([
      { path: "/workspaces/shared-a/members", method: "PUT", body: { email: member.email, role: "editor" } },
      { path: "/workspaces/shared-a/members", method: "PUT", body: { email: member.email, role: "editor" } },
    ]);
    fireEvent.click(members.getByRole("button", { name: `撤销 ${member.email} 的访问权限` }));
    expect(writes()).toHaveLength(2);
    fireEvent.click(members.getByRole("button", { name: "取消撤销" }));
    expect(writes()).toHaveLength(2);
    fireEvent.click(members.getByRole("button", { name: `撤销 ${member.email} 的访问权限` }));
    fireEvent.click(members.getByRole("button", { name: "确认撤销访问" }));
    await waitFor(() => expect(members.queryByText(member.email)).not.toBeInTheDocument());
    expect(writes()[2]).toEqual(["/workspaces/shared-a/members/member-new?revoke_scoped_access=true", { method: "DELETE" }]);
    expect(input.onRefresh).toHaveBeenCalledTimes(2);
  }, 10000);

  it("blocks grants when member loading fails and enables them after retry", async () => {
    const managed = { ...shared, can_manage: true, role: "owner" };
    request.mockRejectedValueOnce(new Error("无法读取成员"));
    open(props({ session: { ...session, workspaces: [personal, managed] }, context: { tenantId: shared.id, actorId: owner.user_id } }));
    const members = section("工作区成员");
    expect(await members.findByRole("alert")).toHaveTextContent("成员读取失败");
    expect(members.getByRole("button", { name: "保存成员权限" })).toBeDisabled();
    fireEvent.click(members.getByRole("button", { name: "刷新成员" }));
    await members.findByText(existing.email);
    expect(members.getByRole("button", { name: "保存成员权限" })).not.toBeDisabled();
    expect(writes()).toHaveLength(0);
  });
});


describe("shared workspace lifecycle", () => {
  it("explains private data isolation without offering self-removal to the owner", () => {
    open(props());
    expect(section("当前访问范围").getByText("个人内容 · 仅本人可见")).toBeVisible();
    expect(screen.queryByRole("region", { name: "退出共享工作区" })).not.toBeInTheDocument();
  });
  it("prepares an existing member role edit without writing and includes the inspected version on save", async () => {
    const versioned = { ...existing, version: 4 };
    request.mockResolvedValueOnce([owner, versioned]);
    const managed = { ...shared, is_owner: true, role: "owner", can_manage: true, member_count: 2 };
    open(props({ session: { ...session, workspaces: [managed] }, context: { tenantId: managed.id, actorId: owner.user_id } }));
    const members = section("工作区成员");
    await members.findByText(existing.email);
    fireEvent.click(members.getByRole("button", { name: `修改 ${existing.email} 的角色` }));
    expect(members.getByRole("textbox", { name: "成员邮箱" })).toHaveValue(existing.email);
    expect(writes()).toHaveLength(0);
    fireEvent.mouseDown(members.getByRole("combobox", { name: "访问角色" }));
    fireEvent.click(screen.getByText("查看者 · 只读"));
    request.mockResolvedValueOnce([owner, { ...versioned, role: "viewer", version: 5 }]);
    fireEvent.click(members.getByRole("button", { name: "保存成员权限" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(JSON.parse(writes()[0][1].body)).toEqual({ email: existing.email, role: "viewer", expected_version: 4 });
  });
  it("blocks repeat role writes after a conflict until permissions are refreshed", async () => {
    request.mockResolvedValueOnce([owner, { ...existing, version: 2 }]);
    const managed = { ...shared, can_manage: true, role: "owner" };
    const input = props({ session: { ...session, workspaces: [managed] }, context: { tenantId: managed.id, actorId: owner.user_id } });
    open(input);
    const members = section("工作区成员");
    await members.findByText(existing.email);
    fireEvent.click(members.getByRole("button", { name: `修改 ${existing.email} 的角色` }));
    request.mockRejectedValueOnce(new ApiError("Member permissions changed; refresh the member list before retrying", 409));
    fireEvent.click(members.getByRole("button", { name: "保存成员权限" }));
    expect(await members.findByRole("alert")).toHaveTextContent("成员权限已被其他管理员修改");
    expect(members.getByRole("button", { name: "保存成员权限" })).toBeDisabled();
    expect(writes()).toHaveLength(1);
    fireEvent.click(section("可用工作区").getByRole("button", { name: "刷新工作区列表" }));
    await waitFor(() => expect(members.getByRole("button", { name: "保存成员权限" })).toBeEnabled());
    expect(writes()).toHaveLength(1);
  });
  it("requires acknowledgement before leaving and never deletes shared resources", async () => {
    const joined = { ...shared, can_leave: true };
    const input = props({ session: { ...session, workspaces: [joined] }, context: { tenantId: joined.id, actorId: owner.user_id } });
    open(input);
    const exit = section("退出共享工作区");
    const confirm = exit.getByRole("button", { name: "确认退出共享工作区" });
    expect(confirm).toBeDisabled();
    fireEvent.click(exit.getByRole("checkbox", { name: `我确认退出“${joined.name}”` }));
    expect(writes()).toHaveLength(0);
    request.mockResolvedValueOnce(undefined);
    fireEvent.click(confirm);
    await waitFor(() => expect(input.onRefresh).toHaveBeenCalledOnce());
    expect(writes()).toEqual([["/workspaces/shared-a/leave", { method: "POST" }]]);
  });
  it("shows return-to-private only for the actual owner and requires explicit acknowledgement", async () => {
    const managed = { ...shared, can_manage: true, role: "owner", is_owner: true, member_count: 1 };
    const input = props({ session: { ...session, workspaces: [managed] }, context: { tenantId: managed.id, actorId: owner.user_id } });
    open(input);
    const management = section("管理当前工作区");
    expect(management.getByRole("button", { name: "恢复为个人工作区" })).toBeDisabled();
    fireEvent.click(management.getByRole("checkbox", { name: "我确认恢复为仅本人可见的个人工作区" }));
    request.mockResolvedValueOnce({ ...managed, kind: "personal" });
    fireEvent.click(management.getByRole("button", { name: "恢复为个人工作区" }));
    await waitFor(() => expect(input.onRefresh).toHaveBeenCalledOnce());
    expect(writes()).toEqual([["/workspaces/shared-a", { method: "PATCH", body: JSON.stringify({ kind: "personal" }) }]]);
  });
});
