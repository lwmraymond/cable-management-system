import { Alert, Button, Form, InputNumber, Modal } from "antd";
import { useMemo, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import type { SpatialLocation, SpatialRack } from "./sceneData";

export function RackPlacementDialog({ rack, room, getContext, onClose, onSaved }: {
  rack: SpatialRack; room?: SpatialLocation; getContext: () => InfrastructureContext; onClose: () => void; onSaved: () => void;
}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  return <Modal open title={`调整机柜位置 · ${rack.name}`} onCancel={() => { if (!saving) onClose(); }} footer={null} destroyOnHidden maskClosable={!saving} closable={!saving}>
    <p>在 {room?.name ?? "当前房间"} 内移动机柜。坐标以机柜中心为准，单位为米；保存时会检查房间边界和其他机柜。</p>
    {error && <Alert type="error" showIcon title="位置未保存" description={error} style={{ marginBottom: 16 }} />}
    <Form form={form} layout="vertical" initialValues={{ position_x: rack.position_x, position_y: rack.position_y, position_z: rack.position_z, rotation: rack.rotation }} onFinish={async values => {
      if (saving) return;
      if (rack.version == null) { setError("缺少最新版本，请刷新场景后重试。"); return; }
      setSaving(true); setError("");
      try {
        await api.request(`/scene/racks/${rack.id}`, { method: "PATCH", body: JSON.stringify({ ...values, expected_version: rack.version }) });
        onSaved();
      } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败，请重试"); }
      finally { setSaving(false); }
    }}>
      <div className="spatial-placement-fields">{[["position_x", "X 坐标（米）"], ["position_y", "Y 坐标（米）"], ["position_z", "底部高度（米）"], ["rotation", "朝向（度）"]].map(([name, label]) => <Form.Item key={name} name={name} label={label} rules={[{ required: true, message: "请输入数值" }]}><InputNumber style={{ width: "100%" }} min={name === "rotation" ? -360 : 0} max={name === "rotation" ? 360 : 500} step={name === "rotation" ? 90 : 0.1} /></Form.Item>)}</div>
      <div className="spatial-dialog-actions"><Button disabled={saving} onClick={onClose}>取消</Button><Button type="primary" htmlType="submit" loading={saving}>保存位置</Button></div>
    </Form>
  </Modal>;
}
