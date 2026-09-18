import { Alert, Button, Form, Input, InputNumber, Modal, Select } from "antd";
import { useMemo, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import { roomEntrances, type RoomEntrance, type SpatialLocation } from "./sceneData";
import { creationError } from "./sceneCreate";
import "./sceneEditor.css";
export function RoomEntrancesEditor({ room, getContext, onClose, onSaved }: { room: SpatialLocation; getContext: () => InfrastructureContext; onClose: () => void; onSaved: () => void }) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const width = Number(room.dimensions.width_m) || 4, height = Number(room.dimensions.height_m) || 3.2;
  const nextEntrance = (): RoomEntrance => ({ id: crypto.randomUUID(), name: "出入口", wall: "south", offset_m: Math.max(0, (width - 1) / 2), width_m: Math.min(width, 1), height_m: Math.min(height, 2.1) });
  const existing = roomEntrances(room);
  const submit = async ({ entrances }: { entrances: RoomEntrance[] }) => {
    if (busy || room.version == null) return;
    setBusy(true); setError("");
    try { await api.request(`/scene/rooms/${room.id}/entrances`, { method: "PATCH", body: JSON.stringify({ expected_version: room.version, entrances }) }); onSaved(); }
    catch (reason) { setError(creationError(reason)); }
    finally { setBusy(false); }
  };
  return <Modal title={`${room.name} · 出入口`} open footer={null} onCancel={onClose} mask={{ closable: !busy }} closable={!busy} keyboard={!busy} width={600}>
    <p className="scene-editor-note">每间房至少保留一个出入口。南 / 北墙沿 X 轴，东 / 西墙沿 Y 轴；边距从该墙坐标最小的一端量至洞口起点，单位为米。</p>
    {error && <Alert type="error" title="出入口未保存" description={error} role="alert" />}
    {room.version == null && <Alert type="warning" title="请刷新场景后编辑出入口" />}
    <Form layout="vertical" disabled={busy} initialValues={{ entrances: existing.length ? existing : [nextEntrance()] }} onFinish={submit}>
      <Form.List name="entrances">{(fields, { add, remove }) => <>
        {fields.map((field, index) => <div className="spatial-door-form" key={field.key}>
          <Form.Item name={[field.name, "id"]} hidden><Input /></Form.Item>
          <Form.Item name={[field.name, "name"]} label={`出入口 ${index + 1} 名称`} rules={[{ required: true, whitespace: true }]}><Input maxLength={80} /></Form.Item>
          <div className="spatial-placement-fields"><Form.Item name={[field.name, "wall"]} label="所在墙面" rules={[{ required: true }]}><Select options={[{ value: "south", label: "南墙 · Y 最大" }, { value: "north", label: "北墙 · Y = 0" }, { value: "east", label: "东墙 · X 最大" }, { value: "west", label: "西墙 · X = 0" }]} /></Form.Item>
          <Form.Item name={[field.name, "offset_m"]} label="洞口起始边距（m）" rules={[{ required: true }, { type: "number", min: 0 }]}><InputNumber min={0} step={0.1} precision={3} /></Form.Item>
          <Form.Item name={[field.name, "width_m"]} label="净宽（m）" rules={[{ required: true }, { type: "number", min: 0.1 }]}><InputNumber min={0.1} step={0.1} precision={3} /></Form.Item>
          <Form.Item name={[field.name, "height_m"]} label="净高（m）" rules={[{ required: true }, { type: "number", min: 0.1 }]}><InputNumber min={0.1} max={height} step={0.1} precision={3} /></Form.Item></div>
          <Button danger disabled={busy || fields.length <= 1} onClick={() => remove(field.name)}>删除出入口 {index + 1}</Button>
        </div>)}
        <Button disabled={busy || fields.length >= 20} onClick={() => add(nextEntrance())}>增加出入口</Button>
      </>}</Form.List>
      <div className="spatial-dialog-actions"><Button disabled={busy} onClick={onClose}>取消</Button><Button type="primary" htmlType="submit" loading={busy} disabled={room.version == null}>保存出入口</Button></div>
    </Form>
  </Modal>;
}
