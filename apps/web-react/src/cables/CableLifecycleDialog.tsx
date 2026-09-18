import { Alert, Button, Descriptions, Input, Modal, Space, Spin, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import { contextKey } from "../pages/fiberUi";

export interface CableLifecyclePreview {
  id: string; identifier: string; version: number; action: "delete" | "remove"; status: string; allowed: boolean;
  blockers: { code: string; message: string }[];
  endpoints: { side: string; device_name: string | null; port_label: string | null }[];
  route_segment_count: number;
}
export interface CableLifecycleResult { id: string; action: "delete" | "remove"; version: number }
const statusLabels: Record<string, string> = { planned: "规划中", installed: "已安装", tested: "已测试", approved: "已审批", in_service: "在用", removed: "已拆除" };
type Props = {
  cable: { id: string; identifier: string }; getContext: () => InfrastructureContext;
  onClose: () => void; onCompleted: (result: CableLifecycleResult) => void;
};
export function CableLifecycleDialog(props: Props) {
  return <CableLifecycleConfirmation key={`${contextKey(props.getContext())}:${props.cable.id}`} {...props} />;
}
function CableLifecycleConfirmation({ cable, getContext, onClose, onCompleted }: Props) {
  const [context] = useState(() => ({ ...getContext() }));
  const scope = contextKey(context);
  const api = useMemo(() => createApiClient({ getContext: () => context }), [context]);
  const [preview, setPreview] = useState<CableLifecyclePreview>();
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState(""), [reason, setReason] = useState("");
  const [requiresPreview, setRequiresPreview] = useState(false);
  const alive = useRef(false), pending = useRef(false), controller = useRef<AbortController | null>(null);
  const currentContext = useRef(getContext);
  currentContext.current = getContext;
  const isCurrent = useCallback(() => alive.current && contextKey(currentContext.current()) === scope, [scope]);
  const loadPreview = useCallback(async () => {
    if (pending.current || !isCurrent()) return;
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    setLoading(true); setRequiresPreview(true); setError("");
    try {
      const result = await api.request<CableLifecyclePreview>(`/cables/${encodeURIComponent(cable.id)}/deletion-preview`, { signal: requestController.signal });
      if (!isCurrent() || requestController.signal.aborted) return;
      if (result.id !== cable.id || !Number.isSafeInteger(result.version) || result.version < 1 || !["delete", "remove"].includes(result.action)) throw new Error("线缆预览无效，请重新读取后核对。");
      setPreview(result); setRequiresPreview(false);
    } catch (caught) {
      if (isCurrent() && !requestController.signal.aborted) setError(caught instanceof Error ? caught.message : "无法读取线缆影响范围。");
    } finally { if (isCurrent() && !requestController.signal.aborted) setLoading(false); }
  }, [api, cable.id, isCurrent]);
  useEffect(() => {
    alive.current = true; void loadPreview();
    return () => { alive.current = false; controller.current?.abort(); };
  }, [loadPreview]);
  const allowed = Boolean(preview?.allowed && !preview.blockers.length && !loading && !requiresPreview);
  const remove = preview?.action === "remove";
  const submit = async () => {
    if (!preview || !allowed || pending.current || !isCurrent() || remove && !reason.trim()) return;
    pending.current = true; setSaving(true); setError("");
    let result: CableLifecycleResult;
    try {
      result = await api.request<CableLifecycleResult>(remove ? `/cables/${encodeURIComponent(cable.id)}/remove` : `/cables/${encodeURIComponent(cable.id)}?expected_version=${preview.version}`, {
        method: remove ? "POST" : "DELETE",
        ...(remove ? { body: JSON.stringify({ expected_version: preview.version, reason: reason.trim() }) } : {}),
      });
      if (!isCurrent()) return;
      if (result.id !== cable.id || result.action !== preview.action) throw new Error("操作结果无法核对，请刷新预览确认线缆状态，勿重复提交。");
    } catch (caught) {
      if (isCurrent()) { setError(caught instanceof Error ? caught.message : "操作未完成，请核对当前状态。"); setRequiresPreview(true); }
      return;
    } finally { pending.current = false; if (isCurrent()) setSaving(false); }
    if (isCurrent()) onCompleted(result);
  };
  return <Modal open title="线缆删除 / 拆除" width={600} onCancel={() => { if (!pending.current) onClose(); }} closable={!saving} keyboard={!saving} mask={{ closable: !saving }} footer={<Space wrap>
    <Button disabled={saving} onClick={() => { if (!pending.current) onClose(); }}>取消</Button>
    <Button disabled={saving} loading={loading} onClick={() => void loadPreview()}>刷新影响预览</Button>
    <Button danger type="primary" loading={saving} disabled={!allowed || saving || Boolean(remove && !reason.trim())} onClick={() => void submit()}>{remove ? "确认拆除" : "确认删除"}</Button>
  </Space>}>
    <Typography.Paragraph strong>{cable.identifier}</Typography.Paragraph>
    {loading && <div role="status"><Spin size="small" /> 正在核对线缆状态、权限与依赖…</div>}
    {error && <Alert type="error" showIcon title="操作尚未确认" description={error} role="alert" />}
    {requiresPreview && !loading && <Typography.Paragraph>请先刷新影响预览，核对当前状态后再次确认。</Typography.Paragraph>}
    {preview && <>
      <Descriptions column={1} size="small" items={[
        { key: "identifier", label: "线缆编号", children: preview.identifier },
        { key: "status", label: "当前状态", children: statusLabels[preview.status] ?? preview.status },
        { key: "action", label: "本次操作", children: remove ? "登记拆除" : "删除规划线缆" },
        { key: "endpoints", label: "端接影响", children: preview.endpoints.length ? <div>{preview.endpoints.map((endpoint, index) => <div key={`${endpoint.side}:${index}`}>{endpoint.side} 端：{endpoint.device_name ?? "设备信息不可见"} / {endpoint.port_label ?? "端口信息不可见"}</div>)}</div> : "没有已登记端接" },
        { key: "route", label: "路径影响", children: `${preview.route_segment_count} 个登记路径段` },
      ]} />
      <Alert type="warning" showIcon title={remove ? "确认实际拆除后再登记" : "删除前请核对影响"} description="操作会解除当前端口占用和路径关联。历史及审计记录保留，但不提供一键恢复。" />
      {!preview.allowed || preview.blockers.length > 0 ? <Alert type="error" showIcon title="当前不可执行" description={preview.blockers.length ? <ul>{preview.blockers.map((blocker, index) => <li key={`${blocker.code}:${index}`}>{blocker.message}</li>)}</ul> : "当前账号或线缆状态不允许此操作。"} /> : null}
      {remove && <div style={{ marginTop: 16 }}><label htmlFor="cable-removal-reason">拆除原因（必填）</label><Input.TextArea id="cable-removal-reason" aria-required="true" value={reason} onChange={event => setReason(event.target.value)} disabled={saving} maxLength={1000} showCount rows={3} placeholder="例如：设备迁移完成，现场已拆除旧连接" /></div>}
    </>}
  </Modal>;
}
