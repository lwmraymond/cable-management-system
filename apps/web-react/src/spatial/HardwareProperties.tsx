import { Alert, Button, Checkbox, Form } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import type { Selection } from "./render/sceneRenderer";
import type { CablePolicy, SpatialPayload } from "./sceneData";
import { creationError } from "./sceneCreate";
import "./hardwareProperties.css";

type Props = { selection: Selection; payload: SpatialPayload; getContext: () => InfrastructureContext; onSaved: () => void };
export function HardwareProperties({ selection, payload, getContext, onSaved }: Props) {
  const entity = selection.kind === "device" ? payload.devices.find(item => item.id === selection.id) : selection.kind === "pathway" ? payload.pathways.find(item => item.id === selection.id) : undefined;
  if (!entity || selection.kind !== "device" && selection.kind !== "pathway") return null;
  return <PolicyEditor key={`${selection.kind}:${entity.id}:${entity.version}`} kind={selection.kind} entity={entity} getContext={getContext} onSaved={onSaved} />;
}
function PolicyEditor({ kind, entity, getContext, onSaved }: { kind: "device" | "pathway"; entity: { id: string; name: string; version?: number; cable_policy?: CablePolicy }; getContext: () => InfrastructureContext; onSaved: () => void }) {
  const [form] = Form.useForm<CablePolicy>();
  const allows = Form.useWatch("allows_cables", form) ?? entity.cable_policy?.allows_cables ?? true;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false), alive = useRef(true);
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const versionAvailable = entity.version != null && entity.version >= 1;
  return <section className="hardware-properties" aria-label="硬件布线属性"><h3>布线属性</h3><p>{kind === "device" ? "设置设备是否允许端口接线及支持的介质。" : "设置线槽是否允许布线；自动推荐会遵守这些限制。"}</p>
    {!versionAvailable && <Alert type="warning" title="缺少最新版本" description="请刷新场景后再保存属性。" />}
    {error && <Alert type="error" title="属性未保存" description={error} />}
    <Form<CablePolicy> form={form} layout="vertical" disabled={saving || !versionAvailable} initialValues={entity.cable_policy ?? { allows_cables: true, allowed_media: ["copper", "fiber"] }} onFinish={async values => {
      if (!versionAvailable || pending.current) return;
      pending.current = true; setSaving(true); setError("");
      let saved = false;
      try {
        await api.request(`/scene/${kind === "device" ? "devices" : "pathways"}/${entity.id}/cable-policy`, { method: "PATCH", body: JSON.stringify({ expected_version: entity.version, allows_cables: values.allows_cables, allowed_media: values.allows_cables ? values.allowed_media : [] }) });
        saved = true;
      } catch (reason) { if (alive.current) setError(creationError(reason)); }
      finally { pending.current = false; if (alive.current) setSaving(false); }
      if (saved) onSaved();
    }}>
      <Form.Item name="allows_cables" valuePropName="checked"><Checkbox>允许布放线缆</Checkbox></Form.Item>
      <Form.Item name="allowed_media" label="允许的介质" dependencies={["allows_cables"]} rules={[{ validator: (_rule, values) => !form.getFieldValue("allows_cables") || Array.isArray(values) && values.length > 0 ? Promise.resolve() : Promise.reject(new Error("允许布线时至少选择一种介质")) }]}><Checkbox.Group disabled={!allows || saving || !versionAvailable} options={[{ label: "铜缆", value: "copper" }, { label: "光纤", value: "fiber" }]} /></Form.Item>
      <Button htmlType="submit" loading={saving} disabled={!versionAvailable || saving}>保存布线属性</Button>
    </Form>
  </section>;
}
