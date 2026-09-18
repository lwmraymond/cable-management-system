import { AimOutlined, ArrowRightOutlined, CloseOutlined, DeleteOutlined } from "@ant-design/icons";
import { Button, Progress, Tag } from "antd";
import type { Selection } from "./render/sceneRenderer";
import type { ReactNode } from "react";
import { rackCapacity, roomEntrances, type SpatialPayload } from "./sceneData";
import { portBlockedReason } from "./sceneCreate";
import { LineLengthDetails } from "./LineLengthDetails";

export function SpatialInspector({ payload, selection, onSelect, onFocus, onClose, routeKind, hasLeadIns, onCreate, onMoveRack, onEditEntrances, onConnectPort, onCableLifecycle, cableLifecycleBlocked, children }: {
  payload: SpatialPayload; selection: Selection | null; onSelect: (selection: Selection) => void;
  onFocus: (id: string) => void; onClose: () => void; routeKind: string; hasLeadIns?: boolean;
  onCreate: (kind: "room" | "rack" | "device" | "pathway" | "cable") => void; onMoveRack: (id: string) => void; onEditEntrances?: (id: string) => void; onConnectPort?: (id: string) => void; onCableLifecycle?: (cable: { id: string; identifier: string }) => void; cableLifecycleBlocked?: string; children?: ReactNode;
}) {
  const room = selection?.kind === "room" ? payload.locations.find(item => item.id === selection.id) : undefined;
  const rack = selection?.kind === "rack" ? payload.racks.find(item => item.id === selection.id) : undefined;
  const selectedPort = selection?.kind === "port" ? payload.ports.find(item => item.id === selection.id) : undefined;
  const device = selection?.kind === "device" || selectedPort ? payload.devices.find(item => item.id === (selectedPort?.device_id ?? selection?.id)) : undefined;
  const cable = selection?.kind === "cable" ? payload.cables.find(item => item.id === selection.id) : undefined;
  const pathway = selection?.kind === "pathway" ? payload.pathways.find(item => item.id === selection.id) : undefined;
  const parentRack = rack ?? payload.racks.find(item => item.id === device?.rack_id);
  const related = payload.cables.filter(item => item.terminations.some(terminal => device ? terminal.device_id === device.id : terminal.rack_id === rack?.id));
  const capacity = parentRack && rackCapacity(parentRack, payload.devices);
  const title = room?.name ?? rack?.name ?? device?.name ?? cable?.identifier ?? pathway?.name ?? "选择对象";
  const identifier = room?.identifier ?? rack?.rack_identifier ?? device?.identifier ?? pathway?.identifier;
  return <aside className={`spatial-inspector ${selection ? "is-selected" : ""}`} aria-label="选中对象详情">
    <div className="spatial-panel-title"><span>对象详情</span><Button type="text" size="small" icon={<CloseOutlined />} aria-label="关闭对象详情" onClick={onClose} /></div>
    <div className="spatial-inspector-body">
      <div className="spatial-eyebrow">{room ? "ROOM" : rack ? "RACK" : device ? "DEVICE" : cable ? "CONNECTION" : pathway ? "PATHWAY" : "INSPECTOR"}</div>
      <h2>{title}</h2>{identifier && <p className="spatial-identifier">{identifier}</p>}
      {!selection && <p className="spatial-muted">选择房间、机柜、设备、线槽或线缆，查看详细信息并继续编辑。</p>}
      {(rack || device) && <Button block icon={<AimOutlined />} onClick={() => onFocus((device ?? rack)!.id)}>定位到{device ? "设备" : "机架"}</Button>}
      {room && <>
        <Button block icon={<AimOutlined />} onClick={() => onFocus(room.id)}>定位到房间</Button>
        <dl className="spatial-properties"><div><dt>类型</dt><dd>{room.location_type === "data_hall" ? "Server Room" : "房间 / 区域"}</dd></div><div><dt>宽 × 深 × 高</dt><dd>{Number(room.dimensions.width_m) || "—"} × {Number(room.dimensions.depth_m) || "—"} × {Number(room.dimensions.height_m) || "—"} m</dd></div><div><dt>机柜</dt><dd>{payload.racks.filter(item => item.location_id === room.id).length}</dd></div><div><dt>线槽 / 桥架</dt><dd>{payload.pathways.filter(item => item.location_id === room.id).length}</dd></div></dl>
        <div className="spatial-detail-actions"><Button onClick={() => onCreate("rack")}>放置机柜</Button><Button onClick={() => onCreate("pathway")}>创建线槽</Button></div>
        <div className="spatial-section-heading">出入口 <span>{roomEntrances(room).length}</span></div>
        {roomEntrances(room).map(door => <p key={door.id} className="spatial-muted">{door.name} · {{ north: "北墙", south: "南墙", east: "东墙", west: "西墙" }[door.wall]} · {door.width_m} × {door.height_m} m</p>)}
        {!roomEntrances(room).length && <p className="spatial-note">此房间尚未配置出入口。</p>}
        <Button block onClick={() => onEditEntrances?.(room.id)}>配置出入口</Button>
      </>}
      {rack && capacity && <>
        <div className="spatial-detail-actions"><Button onClick={() => onMoveRack(rack.id)}>调整位置</Button><Button onClick={() => onCreate("device")}>安装设备</Button></div>
        <div className="spatial-section-heading">U 位容量 <span>{capacity.used} / {rack.height_u} U</span></div>
        <Progress percent={capacity.percent} size="small" strokeColor="#3e6f9c" />
        <dl className="spatial-properties"><div><dt>可用</dt><dd>{capacity.free} U</dd></div><div><dt>预留</dt><dd>{capacity.reserved} U</dd></div><div><dt>宽 × 深</dt><dd>{rack.width_mm} × {rack.depth_mm} mm</dd></div><div><dt>位置</dt><dd>{payload.locations.find(item => item.id === rack.location_id)?.name ?? "—"}</dd></div></dl>
        <div className="spatial-section-heading">已安装设备</div>
        {payload.devices.filter(item => item.rack_id === rack.id).sort((a, b) => b.start_u - a.start_u).map(item => <button key={item.id} className="spatial-detail-link" onClick={() => onSelect({ kind: "device", id: item.id })}><span><b>{item.name}</b><small>U{item.start_u}{item.rack_units > 1 ? `–${item.start_u + item.rack_units - 1}` : ""} · {item.face === "rear" ? "后侧" : "前侧"}</small></span><ArrowRightOutlined /></button>)}
      </>}
      {device && <>
        <dl className="spatial-properties"><div><dt>类型</dt><dd>{device.device_type}</dd></div><div><dt>安装位置</dt><dd>U{device.start_u} · {device.rack_units} U</dd></div><div><dt>安装面</dt><dd>{device.face === "rear" ? "后侧" : "前侧"}</dd></div><div><dt>端口数量</dt><dd>{payload.ports.filter(port => port.device_id === device.id).length}</dd></div><div><dt>状态</dt><dd>{device.status}</dd></div></dl>
        <div className="spatial-section-heading">端口 · 点击开始连线</div>
        {(["front", "rear"] as const).map(face => {
          const ports = payload.ports.filter(port => port.device_id === device.id && port.front_or_rear === face).sort((a, b) => a.position_index - b.position_index);
          return ports.length ? <section key={face}><p className="spatial-muted">{face === "front" ? "前侧" : "后侧"}</p><div className="spatial-port-grid">{ports.map(port => {
            const occupied = port.occupied || port.status !== "available" || payload.cables.some(cable => cable.terminations.some(t => t.port_id === port.id));
            const blockedReason = portBlockedReason(payload, port);
            return <button key={port.id} disabled={occupied || Boolean(blockedReason)} aria-label={`从 ${device.identifier} ${port.identifier} 开始连线`} title={`${port.connector_type} · ${port.media_type ?? ""} · ${blockedReason ?? (occupied ? "已占用" : "可用")}`} className={selectedPort?.id === port.id ? "is-active" : ""} onClick={() => onConnectPort?.(port.id)}>{port.identifier}</button>;
          })}</div></section> : null;
        })}
        {parentRack && <button className="spatial-detail-link" onClick={() => onSelect({ kind: "rack", id: parentRack.id })}>返回 {parentRack.name}<ArrowRightOutlined /></button>}
      </>}
      {(rack || device) && <>
        <div className="spatial-section-heading">关联线缆 <span>{related.length}</span></div>
        {related.length ? related.map(item => <button className="spatial-detail-link" key={item.id} onClick={() => onSelect({ kind: "cable", id: item.id })}><span><b>{item.identifier}</b><small>{item.media_type}</small></span><ArrowRightOutlined /></button>) : <p className="spatial-muted">当前范围没有关联线缆。</p>}
      </>}
      {cable && <>
        <Tag color="blue">{cable.media_type}</Tag><Tag>{cable.installation_status}</Tag>
        {onCableLifecycle && <><Button block danger icon={<DeleteOutlined />} disabled={Boolean(cableLifecycleBlocked) || cable.installation_status === "removed"} onClick={() => onCableLifecycle(cable)}>{cable.installation_status === "removed" ? "已拆除" : cable.installation_status === "planned" ? "删除规划线缆" : "拆除线缆"}</Button>{cableLifecycleBlocked && <p className="spatial-note">{cableLifecycleBlocked}</p>}</>}
        <div className="spatial-section-heading">端到端连接</div>
        {routeKind !== "none" && <Button block icon={<AimOutlined />} onClick={() => onFocus(cable.id)}>定位到连接</Button>}
        <ol className="spatial-endpoints">{["A", "B"].map(side => {
          const terminal = cable.terminations.find(item => item.side === side);
          const endpoint = payload.devices.find(item => item.id === terminal?.device_id);
          const port = payload.ports.find(item => item.id === terminal?.port_id);
          return <li key={side}><span className="spatial-endpoint-side">{side}</span><div><strong>{endpoint?.name ?? "未在当前范围内加载"}</strong><p>{port ? `${port.identifier} · ${port.connector_type} · ${port.front_or_rear === "rear" ? "后侧" : "前侧"}` : "端点详情不可用"}</p>{endpoint && <button onClick={() => onSelect({ kind: "device", id: endpoint.id })}>查看设备 <ArrowRightOutlined /></button>}</div></li>;
        })}</ol>
        <LineLengthDetails payload={payload} selection={{ kind: "cable", id: cable.id }} /><dl className="spatial-properties"><div><dt>测试</dt><dd>{cable.test_status ?? "未记录"}</dd></div></dl>
        <p className="spatial-note">{routeKind === "recorded" ? hasLeadIns ? "线槽段沿已记录坐标显示，端口至线槽的接入段为示意。" : "蓝色路径来自已记录的路由坐标。" : routeKind === "schematic" ? "实线表达两个端口的连接关系；当前没有记录实际走线路径。" : "当前范围缺少完整路由或端点坐标，未生成三维连线。"}</p>
        {(cable.endpoint_scope !== "complete" || cable.route_scope === "partial") && <p className="spatial-note">该线缆延伸至当前场景之外；仅显示本楼层或房间内已加载的端点和路径，不补画其他楼层。</p>}
      </>}
      {pathway && <>
        <Tag>{pathway.type === "ladder" ? "梯式桥架" : pathway.type === "conduit" ? "导管" : "线槽"}</Tag>
        <Button block icon={<AimOutlined />} onClick={() => onFocus(pathway.id)}>定位到线槽</Button>
        <LineLengthDetails payload={payload} selection={{ kind: "pathway", id: pathway.id }} />
        <div className="spatial-section-heading">槽内线缆</div>{payload.cables.filter(item => item.route_segment_ids.some(id => pathway.segments.some(segment => segment.id === id))).map(item => <button className="spatial-detail-link" key={item.id} onClick={() => onSelect({ kind: "cable", id: item.id })}>{item.identifier}<ArrowRightOutlined /></button>)}
        <Button block onClick={() => onCreate("cable")}>创建线缆连接</Button>
      </>}
      {children}
    </div>
  </aside>;
}
