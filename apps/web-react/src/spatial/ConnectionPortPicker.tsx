import { CloseOutlined } from "@ant-design/icons";
import { Button, Input } from "antd";
import { useState } from "react";
import type { Selection } from "./render/sceneRenderer";
import type { SpatialPayload } from "./sceneData";
import { availablePorts } from "./sceneCreate";

export function ConnectionPortPicker({ payload, target, media, side, otherPortId, onPick, onClose }: {
  payload: SpatialPayload; target: Selection; media: string; side: "A" | "B"; otherPortId?: string;
  onPick: (id: string) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const devices = payload.devices.filter(device => target.kind === "rack" ? device.rack_id === target.id : device.id === target.id);
  const groups = devices.map(device => ({ device, ports: availablePorts(payload, device.id, media, otherPortId).filter(port => `${device.name} ${device.identifier} ${port.identifier} ${port.connector_type} ${port.front_or_rear}`.toLowerCase().includes(query.toLowerCase())) })).filter(group => group.ports.length);
  return <aside className="spatial-port-picker" aria-label={`选择 ${side} 端接口`}>
    <header><div><strong>选择 {side} 端接口</strong><small>{media} · 仅显示可用接口</small></div><Button type="text" size="small" icon={<CloseOutlined />} aria-label="关闭接口选择" onClick={onClose} /></header>
    <Input aria-label="筛选设备接口" placeholder="搜索设备或端口" value={query} onChange={event => setQuery(event.target.value)} allowClear />
    <div className="spatial-port-picker-list">{groups.map(({ device, ports }) => <section key={device.id}><h3>{device.name}<small>{device.identifier}</small></h3><div>{ports.map(port => <button key={port.id} aria-label={`选择接口 ${device.identifier} ${port.identifier}`} onClick={() => onPick(port.id)}><b>{port.identifier}</b><small>{port.front_or_rear === "rear" ? "后侧" : "前侧"} · {port.connector_type}</small></button>)}</div></section>)}{!groups.length && <p>此对象没有匹配当前介质的空闲接口。请选择其他设备，或切换线缆工具。</p>}</div>
  </aside>;
}
