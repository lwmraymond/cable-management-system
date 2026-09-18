import { Alert, Button, Input, Modal, Select, Space } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";

type Project = { id: string; project_number: string; name: string; status: string };
type Catalog = { projects: Project[]; can_create: boolean; truncated: boolean };

export function ProjectPicker({ getContext, value, onChange, disabled }: { getContext: () => InfrastructureContext; value: string; onChange: (id: string) => void; disabled: boolean }) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [catalog, setCatalog] = useState<Catalog>();
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [number, setNumber] = useState("");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const alive = useRef(true);
  const busy = useRef(false);
  const currentValue = useRef(value); currentValue.current = value;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    api.request<Catalog>("/projects", { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return;
      setCatalog(result);
      const active = result.projects.filter(project => project.status === "active");
      if (!active.some(project => project.id === currentValue.current)) onChange(active.length === 1 ? active[0].id : "");
    }).catch(reason => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "项目加载失败"); onChange(""); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, revision, onChange]);
  const create = async () => {
    if (busy.current || !number.trim() || !name.trim()) return;
    busy.current = true; setSaving(true); setSaveError("");
    try {
      const project = await api.request<Project>("/projects", { method: "POST", body: JSON.stringify({ project_number: number.trim(), name: name.trim() }) });
      if (!alive.current) return;
      setCatalog(previous => ({ projects: [...(previous?.projects ?? []), project], can_create: true, truncated: previous?.truncated ?? false }));
      onChange(project.id); setOpen(false); setNumber(""); setName("");
    } catch (reason) {
      if (alive.current) setSaveError(reason instanceof ApiError && reason.status === 409 ? "项目编号已存在，请关闭弹窗并刷新项目列表后选择。" : reason instanceof Error ? reason.message : "项目未创建，请重试。");
    } finally { busy.current = false; if (alive.current) setSaving(false); }
  };
  return <section aria-label="布线项目" className="connection-project">
    <label htmlFor="connection-project">保存到项目</label>
    <Select id="connection-project" aria-label="保存到项目" style={{ width: "100%" }} showSearch optionFilterProp="label" value={value || undefined} loading={loading} disabled={disabled || loading || saving || !!error} placeholder="选择布线项目" options={(catalog?.projects ?? []).filter(project => project.status === "active").map(project => ({ value: project.id, label: `${project.name} · ${project.project_number}` }))} onChange={onChange} />
    <Space><Button size="small" disabled={disabled || loading || saving} onClick={() => setRevision(v => v + 1)}>刷新项目</Button>{catalog?.can_create && <Button size="small" disabled={disabled || saving} onClick={() => setOpen(true)}>新建项目</Button>}</Space>
    {!value && !loading && !error && <p className="connection-help">保存线缆前需选择项目。{catalog?.can_create ? "可以在这里创建项目，再继续当前接线。" : "请联系工作区管理员创建项目。"}</p>}
    {error && <Alert type="error" title="项目加载失败" description={error} />}
    {catalog?.truncated && <Alert type="warning" title="项目超过列表上限，请通过工作范围选择目标项目。" />}
    <Modal title="新建布线项目" open={open} onCancel={() => { if (!saving) setOpen(false); }} onOk={create} okText="创建并选择" cancelText="取消" confirmLoading={saving} okButtonProps={{ disabled: number.trim().length < 3 || !name.trim() }} cancelButtonProps={{ disabled: saving }} closable={!saving} mask={{ closable: !saving }} keyboard={!saving}>
      <p>项目属于当前工作区，用于管理本次布线记录。</p>
      <label htmlFor="new-project-number">项目编号</label><Input id="new-project-number" value={number} onChange={event => setNumber(event.target.value)} maxLength={80} disabled={saving} placeholder="例如 HPC-NET-01" />
      <label htmlFor="new-project-name">项目名称</label><Input id="new-project-name" value={name} onChange={event => setName(event.target.value)} maxLength={180} disabled={saving} placeholder="例如 超算网络建设" />
      {saveError && <Alert type="error" title="项目未创建" description={saveError} />}
    </Modal>
  </section>;
}
