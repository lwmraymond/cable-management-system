import { ArrowLeftOutlined, BorderOutlined, DeploymentUnitOutlined, HomeOutlined, SearchOutlined } from "@ant-design/icons";
import { Input, Select, Spin } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Selection } from "./render/sceneRenderer";
import { rackCapacity, sceneLocations, type SpatialPayload } from "./sceneData";

type InventoryTab = "rooms" | "racks" | "pathways" | "cables";
export function SceneInventory({ payload, locationId, selection, loading, onLocation, onSelect, onInspectRack }: {
  payload?: SpatialPayload; locationId: string; selection: Selection | null; loading: boolean;
  onLocation: (id: string) => void; onSelect: (selection: Selection) => void; onInspectRack: (id: string) => void;
}) {
  const [list, setList] = useState<InventoryTab>("racks");
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const rooms = payload ? sceneLocations(payload) : [];
  const racks = payload?.racks.filter(rack => !locationId || rack.location_id === locationId) ?? [];
  const pathways = payload?.pathways.filter(path => !locationId || path.location_id === locationId) ?? [];
  const routeSegments = new Set(pathways.flatMap(pathway => pathway.segments.map(segment => segment.id)));
  const cables = payload?.cables.filter(cable => !locationId || cable.terminations.some(terminal => terminal.location_id === locationId) || cable.route_segment_ids.some(id => routeSegments.has(id))) ?? [];
  const matches = (...values: string[]) => values.join(" ").toLowerCase().includes(needle);
  const items = list === "rooms" ? rooms.filter(room => matches(room.name, room.identifier)) : list === "racks" ? racks.filter(rack => matches(rack.name, rack.rack_identifier)) : list === "pathways" ? pathways.filter(path => matches(path.name, path.identifier)) : cables.filter(cable => matches(cable.identifier, cable.media_type));
  const active = (kind: Selection["kind"], id: string) => `spatial-object ${selection?.kind === kind && selection.id === id ? "is-active" : ""}`;
  return <aside className="spatial-inventory" aria-label="场景对象列表">
    <div className="spatial-panel-title"><span>场景浏览器</span><DeploymentUnitOutlined /></div>
    <div className="spatial-inventory-filter">
      <label htmlFor="spatial-location">空间范围</label>
      <Select id="spatial-location" aria-label="空间范围" value={locationId} options={[{ value: "", label: "当前范围 · 全部房间" }, ...rooms.map(room => ({ value: room.id, label: room.name }))]} onChange={onLocation} />
      <Input aria-label="搜索场景对象" placeholder="搜索名称或编号" prefix={<SearchOutlined />} value={query} onChange={event => setQuery(event.target.value)} allowClear />
      <div className="spatial-list-tabs">{([["rooms", "房间", rooms.length], ["racks", "机柜", racks.length], ["pathways", "线槽", pathways.length], ["cables", "线缆", cables.length]] as const).map(([key, label, count]) => <button key={key} aria-pressed={list === key} onClick={() => setList(key)}>{label}<b>{count}</b></button>)}</div>
    </div>
    <div className="spatial-object-list">
      {loading && !payload && <div className="spatial-list-empty"><Spin /><p>读取空间数据…</p></div>}
      {list === "rooms" && rooms.filter(room => matches(room.name, room.identifier)).map(room => <button key={room.id} className={active("room", room.id)} onClick={() => onSelect({ kind: "room", id: room.id })}>
        <div className="spatial-object-heading"><HomeOutlined /><span>{room.name}</span></div><p>{room.identifier}</p><div className="spatial-object-foot"><span>{Number(room.dimensions.width_m) || "—"} × {Number(room.dimensions.depth_m) || "—"} m</span><span>{payload?.racks.filter(rack => rack.location_id === room.id).length} 机柜</span></div>
      </button>)}
      {list === "racks" && racks.filter(rack => matches(rack.name, rack.rack_identifier)).map(rack => {
        const capacity = rackCapacity(rack, payload!.devices);
        return <button key={rack.id} className={active("rack", rack.id)} onClick={() => onSelect({ kind: "rack", id: rack.id })} onDoubleClick={() => onInspectRack(rack.id)}>
          <div className="spatial-object-heading"><span className="spatial-rack-glyph"><BorderOutlined /></span><span>{rack.name}</span><small>{rack.height_u}U</small></div><p>{rack.rack_identifier}</p><div className="spatial-capacity-bar"><span style={{ width: `${capacity.percent}%` }} /></div><div className="spatial-object-foot"><span>{payload?.devices.filter(device => device.rack_id === rack.id).length} 设备</span><span>{capacity.free}U 可用</span></div>
        </button>;
      })}
      {list === "pathways" && pathways.filter(path => matches(path.name, path.identifier)).map(path => <button key={path.id} className={active("pathway", path.id)} onClick={() => onSelect({ kind: "pathway", id: path.id })}>
        <div className="spatial-object-heading"><BorderOutlined /><span>{path.identifier}</span></div><p>{path.name}</p><div className="spatial-object-foot"><span>{path.type === "ladder" ? "梯式桥架" : path.type === "conduit" ? "导管" : "线槽"}</span><span>{path.segments.reduce((total, segment) => total + segment.length_m, 0).toFixed(1)} m</span></div>
      </button>)}
      {list === "cables" && cables.filter(cable => matches(cable.identifier, cable.media_type)).map(cable => <button key={cable.id} className={active("cable", cable.id)} onClick={() => onSelect({ kind: "cable", id: cable.id })}>
        <div className="spatial-object-heading"><DeploymentUnitOutlined /><span>{cable.identifier}</span></div><p>{cable.media_type}</p><div className="spatial-object-foot"><span>{cable.installation_status}</span><span>{cable.endpoint_scope === "complete" ? "双端可见" : "部分端点"}</span></div>
      </button>)}
      {!loading && !items.length && <div className="spatial-list-empty">当前范围没有匹配的对象。使用“新建”添加房间、机柜或连接。</div>}
    </div>
    <div className="spatial-inventory-note"><strong>实线为连接 · 选中高亮</strong><p>路由坐标已记录的线缆沿线槽展示，其余仅表达端口连接关系。</p><Link to="/">调整项目 / 位置范围 <ArrowLeftOutlined /></Link></div>
  </aside>;
}
