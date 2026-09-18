import { CheckOutlined, DownOutlined, LockOutlined, LogoutOutlined, PlusOutlined, ReloadOutlined, TeamOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Checkbox, Drawer, Empty, Input, Select, Spin, Tag } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError, createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import type { BrowserSession, Workspace, WorkspaceMember } from "./types";
import "./workspaces.css";

export type WorkspaceMenuProps = {
  session: BrowserSession;
  context: InfrastructureContext;
  onSelect: (workspace: Workspace) => void;
  onRefresh: () => Promise<void>;
  onSignOut: () => void;
};
const roleLabels: Record<string, string> = { owner: "所有者", admin: "管理员", administrator: "管理员", editor: "编辑者", viewer: "查看者", "Tenant Owner": "工作区所有者", "Workspace Owner": "工作区所有者", "Infrastructure Manager": "基础设施管理员", "Scoped Contractor": "限定范围协作方" };
const roleLabel = (role: string) => roleLabels[role] ?? role;
const reasonText = (reason: unknown) => {
  const text = reason instanceof Error ? reason.message : "请求未成功，请稍后重试。";
  if (text.includes("Member permissions changed")) return "成员权限已被其他管理员修改。请刷新工作区列表，核对最新角色后再操作。";
  if (text.includes("Remove other members and revoke access grants")) return "请先撤销其他成员及独立项目授权，再恢复为个人工作区。";
  if (text.includes("active provisioned account")) return "未找到已启用的账号，请核对成员邮箱。";
  return text;
};

export function WorkspaceMenu(props: WorkspaceMenuProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const workspace = props.session.workspaces.find(item => item.id === props.context.tenantId);
  const displayName = props.session.user.display_name || props.session.user.email;
  return <div className="workspace-menu" onKeyDown={event => { if (open && event.key === "Escape") { event.stopPropagation(); event.preventDefault(); if (!busy) setOpen(false); } }}>
    <Button type="text" className="workspace-menu-trigger" aria-label="管理账户和工作区" aria-expanded={open} onClick={() => setOpen(true)}>
      <UserOutlined aria-hidden="true" /><span className="workspace-menu-trigger-label"><strong>{workspace?.name ?? "选择工作区"}</strong><small>{displayName}{workspace ? ` · ${workspace.kind === "personal" ? "个人" : "共享"}` : ""}</small></span>{props.session.auth_method === "demo" && <Tag className="workspace-demo-badge">演示身份</Tag>}<DownOutlined aria-hidden="true" />
    </Button>
    <Drawer rootClassName="workspace-account-drawer" title="账户与工作区" size="min(520px, 100vw)" open={open} onClose={() => { if (!busy) setOpen(false); }} closable={!busy ? { "aria-label": "关闭账户与工作区" } : false} mask={{ closable: !busy }} keyboard={!busy} destroyOnHidden>
      <WorkspacePanel key={`${props.session.user.id}:${props.context.tenantId}`} {...props} open={open} onBusyChange={setBusy} onClose={() => setOpen(false)} />
    </Drawer>
  </div>;
}

type Area = "refresh" | "create" | "rename" | "share" | "members" | "leave";
function WorkspacePanel({ session, context, onSelect, onRefresh, onSignOut, onClose, onBusyChange, open }: WorkspaceMenuProps & { open: boolean; onClose: () => void; onBusyChange: (busy: boolean) => void }) {
  const getContext = useCallback(() => context, [context.tenantId, context.actorId, context.projectId, context.locationId]);
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const current = session.workspaces.find(item => item.id === context.tenantId);
  const [busy, setBusy] = useState<Area>();
  const [error, setError] = useState<{ area: Area; text: string }>();
  const [notice, setNotice] = useState<{ area: Area; text: string }>();
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [name, setName] = useState(current?.name ?? "");
  const [shareConfirmed, setShareConfirmed] = useState(false);
  const [privateConfirmed, setPrivateConfirmed] = useState(false);
  const [leaveConfirmed, setLeaveConfirmed] = useState(false);
  const [pendingWorkspace, setPendingWorkspace] = useState<Workspace>();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [members, setMembers] = useState<WorkspaceMember[]>();
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersError, setMembersError] = useState("");
  const [memberRevision, setMemberRevision] = useState(0);
  const [revoke, setRevoke] = useState<string>();
  const alive = useRef(true);
  const saving = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; onBusyChange(false); }; }, [onBusyChange]);
  useEffect(() => { onBusyChange(Boolean(busy)); }, [busy, onBusyChange]);
  useEffect(() => { setName(current?.name ?? ""); }, [current?.name]);
  useEffect(() => {
    const controller = new AbortController();
    setMembers(undefined); setMembersError("");
    if (!open || !current?.can_manage || current.kind !== "shared") { setMembersLoading(false); return () => controller.abort(); }
    setMembersLoading(true);
    api.request<WorkspaceMember[]>(`/workspaces/${encodeURIComponent(current.id)}/members`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setMembers(value); })
      .catch(reason => { if (!controller.signal.aborted) setMembersError(reasonText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setMembersLoading(false); });
    return () => controller.abort();
  }, [api, open, current?.id, current?.can_manage, current?.kind, memberRevision]);

  const disabled = Boolean(busy) || needsRefresh;
  const feedback = (area: Area) => <>{notice?.area === area && <p className="workspace-operation-notice" role="status">{notice.text}</p>}{error?.area === area && <Alert type="error" showIcon role="alert" title={error.text} />}</>;
  const selectWorkspace = (workspace: Workspace) => { if (!busy) { onSelect(workspace); onClose(); } };
  const refresh = async () => {
    if (saving.current) return;
    saving.current = true; setBusy("refresh"); setError(undefined);
    try {
      await onRefresh();
      if (!alive.current) return;
      setNeedsRefresh(false); setMemberRevision(value => value + 1);
      if (pendingWorkspace) selectWorkspace(pendingWorkspace);
    } catch (reason) { if (alive.current) setError({ area: "refresh", text: `工作区列表刷新失败：${reasonText(reason)}` }); }
    finally { saving.current = false; if (alive.current) setBusy(undefined); }
  };
  const mutate = async <T,>(area: Area, write: () => Promise<T>, message: string, saved?: (value: T) => void, afterRefresh?: (value: T) => void) => {
    if (saving.current || needsRefresh) return;
    saving.current = true; setBusy(area); setError(undefined); setNotice(undefined);
    try {
      const value = await write();
      if (!alive.current) return;
      saved?.(value); setNotice({ area, text: message });
      try { await onRefresh(); if (alive.current) afterRefresh?.(value); }
      catch (reason) { if (alive.current) { setNeedsRefresh(true); setError({ area, text: `更改已保存，但列表刷新失败。请刷新工作区列表后继续。${reasonText(reason)}` }); } }
    } catch (reason) {
      if (alive.current) {
        setError({ area, text: reasonText(reason) });
        if (reason instanceof ApiError && reason.status === 409 && reason.message.includes("Member permissions changed")) setNeedsRefresh(true);
      }
    }
    finally { saving.current = false; if (alive.current) setBusy(undefined); }
  };
  const createWorkspace = (event: FormEvent) => {
    event.preventDefault();
    if (!newName.trim()) { setError({ area: "create", text: "请输入工作区名称。" }); return; }
    void mutate("create", () => api.request<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name: newName.trim(), kind: "personal" }) }), "个人工作区已创建。", value => { setPendingWorkspace(value); setNewName(""); setShowCreate(false); }, value => { onSelect(value); onClose(); });
  };
  const rename = (event: FormEvent) => {
    event.preventDefault();
    if (!current?.can_manage) return;
    if (!name.trim()) { setError({ area: "rename", text: "请输入工作区名称。" }); return; }
    void mutate("rename", () => api.request<Workspace>(`/workspaces/${encodeURIComponent(current.id)}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) }), "工作区名称已保存。");
  };
  const share = () => {
    if (!current?.can_manage || current.kind !== "personal" || !shareConfirmed) return;
    void mutate("share", () => api.request<Workspace>(`/workspaces/${encodeURIComponent(current.id)}`, { method: "PATCH", body: JSON.stringify({ kind: "shared" }) }), "已启用共享。只有明确授权的成员才能访问。", () => setShareConfirmed(false));
  };
  const makePrivate = () => {
    if (!current?.is_owner || current.kind !== "shared" || !privateConfirmed) return;
    void mutate("share", () => api.request<Workspace>(`/workspaces/${encodeURIComponent(current.id)}`, { method: "PATCH", body: JSON.stringify({ kind: "personal" }) }), "已恢复为个人工作区，资料仅本人可见。", () => setPrivateConfirmed(false));
  };
  const leave = () => {
    if (!current?.can_leave || !leaveConfirmed) return;
    void mutate("leave", () => api.request<void>(`/workspaces/${encodeURIComponent(current.id)}/leave`, { method: "POST" }), "已退出共享工作区。", undefined, () => onClose());
  };
  const grant = (event: FormEvent) => {
    event.preventDefault();
    if (!current?.can_manage || current.kind !== "shared" || !members) return;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setError({ area: "members", text: "请输入有效的成员邮箱。" }); return; }
    const existing = members.find(member => member.email.toLowerCase() === email.trim().toLowerCase());
    void mutate("members", () => api.request<WorkspaceMember[]>(`/workspaces/${encodeURIComponent(current.id)}/members`, { method: "PUT", body: JSON.stringify({ email: email.trim(), role, ...(existing?.version !== undefined ? { expected_version: existing.version } : {}) }) }), "成员权限已保存。", value => { setMembers(value); setEmail(""); });
  };
  const remove = (member: WorkspaceMember) => {
    if (!current?.can_manage || member.is_owner || member.user_id === session.user.id || revoke !== member.user_id) return;
    const query = new URLSearchParams({ revoke_scoped_access: "true" });
    if (member.version !== undefined) query.set("expected_version", String(member.version));
    void mutate("members", () => api.request<void>(`/workspaces/${encodeURIComponent(current.id)}/members/${encodeURIComponent(member.user_id)}?${query}`, { method: "DELETE" }), "已撤销该成员的工作区访问权限。", () => { setRevoke(undefined); setMemberRevision(value => value + 1); });
  };

  return <div className="workspace-account-content">
    <section className="workspace-account-identity" aria-label="当前账户"><span className="workspace-account-avatar"><UserOutlined aria-hidden="true" /></span><div><h2>{session.user.display_name || session.user.email}</h2>{session.user.email && <p>{session.user.email}</p>}{session.auth_method === "demo" && <p className="workspace-demo-notice">演示身份 · 当前使用预设演示账户</p>}</div></section>
    {current && <section className="workspace-account-section workspace-access-summary" aria-label="当前访问范围">
      <div className="workspace-section-heading"><h3>{current.kind === "personal" ? "个人内容 · 仅本人可见" : "共享内容 · 仅授权成员可见"}</h3><Tag>{roleLabel(current.role)}</Tag></div>
      <p>{current.kind === "personal" ? "房间、机柜、设备、线缆及图纸保存在独立空间。其他账号无法通过链接直接访问。" : current.scopes?.length ? "你只可访问获授权的项目与位置；受限内容不会因加入其他空间而开放。" : "此空间的资料在成员间协作维护；个人空间的资料不会自动加入共享。"}</p>
      {current.member_count != null && <small>当前空间 {current.member_count} 位成员</small>}
      <div className="workspace-role-guide"><span>查看者：查阅和导出</span><span>编辑者：规划、编辑和测试</span><span>所有者／管理员：管理共享与成员</span></div>
    </section>}
    <section className="workspace-account-section" aria-label="可用工作区"><div className="workspace-section-heading"><h3>工作区</h3><Button icon={<ReloadOutlined aria-hidden="true" />} size="small" loading={busy === "refresh"} disabled={Boolean(busy)} onClick={() => void refresh()}>刷新工作区列表</Button></div>{feedback("refresh")}
      <p className="workspace-account-help">每个工作区独立保存资料。切换后将使用该工作区授予的访问范围。</p>
      {session.workspaces.length ? <ul className="workspace-switch-list">{session.workspaces.map(workspace => <li key={workspace.id}><button className="workspace-switch-item" aria-label={`切换到工作区 ${workspace.name}`} aria-pressed={workspace.id === current?.id} disabled={Boolean(busy) || workspace.id === current?.id} onClick={() => selectWorkspace(workspace)}><span className="workspace-kind-icon">{workspace.kind === "personal" ? <LockOutlined aria-hidden="true" /> : <TeamOutlined aria-hidden="true" />}</span><span className="workspace-switch-name"><strong>{workspace.name}</strong><small>{workspace.kind === "personal" ? "个人" : "共享"} · {roleLabel(workspace.role)}</small></span>{workspace.id === current?.id && <span className="workspace-current-label"><CheckOutlined aria-hidden="true" />当前</span>}</button></li>)}</ul> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前账户暂无可用工作区" />}
    </section>
    {session.can_create_workspaces && <section className="workspace-account-section" aria-label="创建工作区"><div className="workspace-section-heading"><h3>创建个人工作区</h3>{!showCreate && <Button icon={<PlusOutlined aria-hidden="true" />} size="small" disabled={disabled} onClick={() => { setShowCreate(true); setError(undefined); }}>新建工作区</Button>}</div><p className="workspace-account-help">新工作区默认为个人使用。需要协作时，再明确启用共享并逐一授权成员。</p>{feedback("create")}{showCreate && <form onSubmit={createWorkspace}><label htmlFor="workspace-new-name">新工作区名称</label><Input id="workspace-new-name" maxLength={180} value={newName} disabled={disabled} onChange={event => setNewName(event.target.value)} placeholder="例如：个人布线规划" /><div className="workspace-form-actions"><Button disabled={Boolean(busy)} onClick={() => { setShowCreate(false); setError(undefined); }}>取消创建</Button><Button type="primary" htmlType="submit" loading={busy === "create"} disabled={disabled}>创建个人工作区</Button></div></form>}</section>}
    {current?.can_manage && <section className="workspace-account-section" aria-label="管理当前工作区"><div className="workspace-section-heading"><h3>管理当前工作区</h3><span className="workspace-kind-label">{current.kind === "personal" ? "个人" : "共享"}</span></div>
      <form onSubmit={rename}><label htmlFor="workspace-rename">工作区名称</label><div className="workspace-inline-form"><Input id="workspace-rename" maxLength={180} value={name} disabled={disabled} onChange={event => setName(event.target.value)} /><Button htmlType="submit" loading={busy === "rename"} disabled={disabled || name.trim() === current.name}>保存名称</Button></div></form>{feedback("rename")}
      {current.kind === "personal" ? <div className="workspace-sharing"><h4>开启协作</h4><p>个人工作区尚未共享。启用共享后，仍需通过成员邮箱明确授予访问权限；不会自动邀请或公开资料。</p><Checkbox checked={shareConfirmed} disabled={disabled} onChange={event => setShareConfirmed(event.target.checked)}>我确认将此个人工作区改为共享工作区</Checkbox><Button disabled={disabled || !shareConfirmed} loading={busy === "share"} onClick={share}>启用共享</Button></div> : <p className="workspace-account-help">此工作区按成员授权共享，不会公开发布。添加成员前请核对邮箱和角色。</p>}{feedback("share")}
      {current.kind === "shared" && current.is_owner && <div className="workspace-sharing"><h4>恢复个人使用</h4><p>需先撤销其他成员和独立项目授权。资料保留，恢复后仅你能访问。</p><Checkbox checked={privateConfirmed} disabled={disabled} onChange={event => setPrivateConfirmed(event.target.checked)}>我确认恢复为仅本人可见的个人工作区</Checkbox><Button disabled={disabled || !privateConfirmed} loading={busy === "share"} onClick={makePrivate}>恢复为个人工作区</Button></div>}
    </section>}
    {current?.can_manage && current.kind === "shared" && <section className="workspace-account-section" aria-label="工作区成员"><div className="workspace-section-heading"><h3>成员与访问权限</h3><Button size="small" disabled={Boolean(busy) || membersLoading} onClick={() => setMemberRevision(value => value + 1)}>刷新成员</Button></div>{membersLoading && <p role="status"><Spin size="small" /> 正在读取成员…</p>}{membersError && <Alert type="error" role="alert" title="成员读取失败" description={membersError} />}{feedback("members")}
      {members && <ul className="workspace-members">{members.map(member => <li key={member.user_id}><div className="workspace-member-row"><div><strong>{member.display_name || member.email}</strong><span>{member.email}</span><small>{member.is_owner ? "所有者 / 管理员" : roleLabel(member.role)}{member.user_id === session.user.id ? " · 你" : ""}{member.active === false ? " · 账号已停用" : ""}{member.has_scoped_access ? " · 含独立项目授权" : ""}</small></div>{!member.is_owner && member.user_id !== session.user.id && <div className="workspace-member-actions"><Button size="small" disabled={disabled || membersLoading || !member.active} onClick={() => { setEmail(member.email); setRole(member.role === "editor" ? "editor" : "viewer"); setRevoke(undefined); }} aria-label={`修改 ${member.email} 的角色`}>修改角色</Button><Button size="small" danger disabled={disabled || membersLoading} onClick={() => setRevoke(member.user_id)} aria-label={`撤销 ${member.email} 的访问权限`}>撤销访问</Button></div>}</div>{revoke === member.user_id && <div className="workspace-revoke-confirm"><p>确认撤销 {member.email} 对“{current.name}”的访问权限？同时撤销此空间内的独立项目授权，已创建的资料保留。</p><div><Button size="small" disabled={Boolean(busy)} onClick={() => setRevoke(undefined)}>取消撤销</Button><Button size="small" danger disabled={disabled || membersLoading} loading={busy === "members"} onClick={() => remove(member)}>确认撤销访问</Button></div></div>}</li>)}</ul>}
      <form className="workspace-member-form" onSubmit={grant}><h4>添加或更新成员</h4><p className="workspace-account-help">成员须已有可用账户。查看者可读取资料；编辑者可修改资料。此操作不会发送邀请邮件。</p><label htmlFor="workspace-member-email">成员邮箱</label><Input id="workspace-member-email" type="email" autoComplete="off" value={email} disabled={disabled || membersLoading || !members} maxLength={254} onChange={event => setEmail(event.target.value)} placeholder="同事的账户邮箱" /><label htmlFor="workspace-member-role">访问角色</label><Select id="workspace-member-role" aria-label="访问角色" value={role} disabled={disabled || membersLoading || !members} onChange={setRole} options={[{ value: "viewer", label: "查看者 · 只读" }, { value: "editor", label: "编辑者 · 可修改" }]} /><div className="workspace-form-actions"><Button type="primary" htmlType="submit" disabled={disabled || membersLoading || !members} loading={busy === "members"}>保存成员权限</Button></div></form>
    </section>}
    {current?.can_leave && <section className="workspace-account-section" aria-label="退出共享工作区"><h3>退出此共享工作区</h3><p className="workspace-account-help">退出会撤销你在此空间的成员和独立项目授权。已创建的资料留在共享空间；重新加入需要管理员授权。</p>{feedback("leave")}<Checkbox disabled={disabled} checked={leaveConfirmed} onChange={event => setLeaveConfirmed(event.target.checked)}>我确认退出“{current.name}”</Checkbox><div className="workspace-form-actions"><Button danger disabled={disabled || !leaveConfirmed} loading={busy === "leave"} onClick={leave}>确认退出共享工作区</Button></div></section>}
    <footer className="workspace-account-footer"><Button icon={<LogoutOutlined aria-hidden="true" />} disabled={Boolean(busy)} onClick={() => { onSignOut(); onClose(); }}>{session.auth_method === "demo" ? "退出演示身份" : "退出登录"}</Button></footer>
  </div>;
}
