import { Alert, Button, Checkbox, Drawer, Pagination, Select, Space, Tag } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";

type Diff = { id: string; identifier: string; kind: string; status: string; reason: string; base: unknown; incoming: unknown; current: unknown };
type Preview = { id: string; filename: string; format: string; diffs: Diff[]; can_apply: boolean; preview_token?: string; applied: boolean; error?: string; references: number };
type Capabilities = Record<"dxf" | "ifc" | "dwg", { available: boolean; reason?: string }>;
type Props = { getContext: () => InfrastructureContext; rooms: { id: string; name: string }[]; initialRoom: string; onClose: () => void; onApplied: () => void };
const statusNames: Record<string, string> = { update: "可更新", unchanged: "未改动", server_changed: "保留系统新版本", already_current: "已一致", reference: "只作参考", conflict: "冲突", blocked: "不支持应用" };

export function CadExchange({ getContext, rooms, initialRoom, onClose, onApplied }: Props) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [room, setRoom] = useState(rooms.some(r => r.id === initialRoom) ? initialRoom : rooms.length === 1 ? rooms[0].id : "");
  const [capabilities, setCapabilities] = useState<Capabilities>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview>();
  const [exported, setExported] = useState<{ id: string; format: string; filename: string }>();
  const [showReferences, setShowReferences] = useState(false);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [page, setPage] = useState(1);
  const visibleDiffs = (preview?.diffs ?? []).filter(d => ["update", "blocked", "conflict"].includes(d.status) || (d.status === "reference" ? showReferences : showUnchanged))
    .sort((a, b) => a.identifier.localeCompare(b.identifier));
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    api.request<Capabilities>("/scene/cad/capabilities", { signal: controller.signal }).then(setCapabilities)
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => { active.current = false; controller.abort(); };
  }, [api]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); }
    catch (e) { if (active.current) setError(e instanceof Error ? e.message : "CAD 操作失败"); }
    finally { if (active.current) setBusy(false); }
  }
  async function download(path: string, name: string) {
    const blob = await api.download(path);
    if (!active.current) return;
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function exportFile(format: "dxf" | "ifc") {
    await run(async () => {
      const item = await api.request<{ id: string; format: string; filename: string }>("/scene/cad/exports", { method: "POST", body: JSON.stringify({ location_id: room, format }) });
      if (active.current) setExported(item);
      await download(`/scene/cad/exports/${item.id}/file`, item.filename);
    });
  }
  async function stage(file: File) {
    if (file.size > 8 * 1024 * 1024) { setError("文件不能超过 8 MiB。"); return; }
    const body = new FormData(); body.append("file", file);
    await run(async () => {
      const next = await api.request<Preview>(`/scene/cad/imports?location_id=${encodeURIComponent(room)}`, { method: "POST", body });
      if (active.current) { setPreview(next); setPage(1); }
    });
  }
  return <Drawer open title="CAD 文件同步" width={720} onClose={() => { if (!busy) onClose(); }} closable={!busy} maskClosable={!busy} keyboard={!busy}>
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon title="导出 → CAD 编辑 → 导入差异 → 确认应用" description="房间局部坐标，米，Z 向上。支持机柜位置/朝向、未被线缆引用的线槽几何。设备与端口为示意锚点；不自动改接线、登记长度、对象身份或删除对象。" />
      <label>交换房间<Select aria-label="交换房间" style={{ width: "100%" }} value={room || undefined} disabled={busy} placeholder="选择单个房间" options={rooms.map(r => ({ value: r.id, label: r.name }))} onChange={value => { setRoom(value); setPreview(undefined); setExported(undefined); }} /></label>
      <Space wrap>
        <Button disabled={!room || busy || !capabilities?.dxf.available} onClick={() => void exportFile("dxf")}>导出 DXF</Button>
        <Button disabled={!room || busy || !capabilities?.ifc.available} onClick={() => void exportFile("ifc")}>导出 IFC</Button>
        <Button disabled title={capabilities?.dwg.reason}>DWG 未启用</Button>
      </Space>
      <p>DXF R2013 / IFC4：三维几何与稳定对象 ID。单房间交换不表示跨房间实测坐标。保留原文件；这是手动审阅式同步。</p>
      {exported && <Space wrap><Button disabled={busy} onClick={() => void run(() => download(`/scene/cad/exports/${exported.id}/file`, exported.filename))}>重新下载导出文件</Button><Button disabled={busy} onClick={() => void run(() => download(`/scene/cad/exports/${exported.id}/manifest`, `${exported.filename}.manifest.json`))}>下载映射说明</Button></Space>}
      <label>导入 CAD 文件（DXF / IFC，最多 8 MiB）<input aria-label="导入 CAD 文件" type="file" accept=".dxf,.ifc" disabled={!room || busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void stage(file); }} /></label>
      {busy && <p role="status">正在处理文件，请稍候…</p>}
      {error && <Alert type="error" showIcon title="操作未完成" description={error} />}
      {preview && <section aria-label="CAD 差异预览">
        <h3>{preview.filename}</h3>
        {preview.error && <Alert type="warning" title="仅保存修订，不能应用" description={preview.error} />}
        {preview.applied && <Alert type="success" title="此修订已应用；重复导入不会新增对象" />}
        {!!preview.references && <p>{preview.references} 项未映射几何只作参考，不自动创建业务对象。</p>}
        <p>{preview.diffs.filter(d => d.status === "update").length} 项可更新 · {preview.diffs.filter(d => ["blocked", "conflict"].includes(d.status)).length} 项需处理</p>
        <Space wrap><Button disabled={busy || preview.applied} onClick={() => void run(async () => { const next = await api.request<Preview>(`/scene/cad/imports/${preview.id}`); if (active.current) setPreview(next); })}>重新核对差异</Button>
          <Button type="primary" disabled={busy || !preview.can_apply || preview.applied || !preview.preview_token} onClick={() => void run(async () => {
            await api.request(`/scene/cad/imports/${preview.id}/apply`, { method: "POST", body: JSON.stringify({ preview_token: preview.preview_token }) });
            if (active.current) { setPreview({ ...preview, applied: true, can_apply: false }); onApplied(); }
          })}>确认应用</Button>
          <Button disabled={busy} onClick={() => void run(() => download(`/scene/cad/imports/${preview.id}/file`, `original-${preview.id}.${preview.format}`))}>下载导入原件</Button></Space>
        <p><Checkbox checked={showReferences} onChange={e => { setShowReferences(e.target.checked); setPage(1); }}>显示派生参考几何（{preview.diffs.filter(d => d.status === "reference").length}）</Checkbox><Checkbox checked={showUnchanged} onChange={e => { setShowUnchanged(e.target.checked); setPage(1); }}>显示未改动 / 已一致对象</Checkbox></p>
        {visibleDiffs.slice((page - 1) * 30, page * 30).map(d => <details key={d.id} data-cad-status={d.status} style={{ marginBottom: 8 }}><summary><Tag color={d.status === "update" ? "blue" : ["blocked", "conflict"].includes(d.status) ? "red" : undefined}>{statusNames[d.status] ?? d.status}</Tag>{d.identifier}</summary><p>{d.reason}</p><small>{d.kind} · {d.id}</small><pre style={{ overflowX: "auto" }}>{JSON.stringify({ "导出基线": d.base, "当前系统": d.current, "CAD 文件": d.incoming }, null, 2)}</pre></details>)}
        {visibleDiffs.length > 30 && <Pagination current={page} total={visibleDiffs.length} pageSize={30} showSizeChanger={false} onChange={setPage} />}
      </section>}
      <Button disabled={busy} onClick={onClose}>关闭（未确认的差异不写入系统）</Button>
    </Space>
  </Drawer>;
}
