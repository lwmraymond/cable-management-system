import { ApartmentOutlined, ArrowLeftOutlined, ArrowRightOutlined, EnvironmentOutlined, HomeOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Input, Spin } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { LocationRecord } from "../types";
import "./spatialLocationPicker.css";

const typeLabels: Record<string, string> = {
  region: "区域", campus: "园区", site: "站点", building: "建筑", floor: "楼层", zone: "分区",
  room: "房间", tr: "电信间（TR）", er: "设备间（ER）", mdf: "主配线间（MDF）",
  mmr: "运营商互联间（MMR）", data_hall: "数据机房", entrance_facility: "进线设施", row: "机柜列", other: "其他位置",
};
const roomTypes = new Set(["room", "tr", "er", "mdf", "mmr", "data_hall", "entrance_facility"]);
const typeLabel = (location: LocationRecord) => typeLabels[location.location_type] ?? "其他位置";
const nameOf = (location: LocationRecord) => location.name || location.identifier || "名称未提供";

function ancestry(location: LocationRecord, byId: Map<string, LocationRecord>) {
  const path: LocationRecord[] = [], seen = new Set<string>();
  let current: LocationRecord | undefined = location;
  let note = "";
  while (current) {
    if (seen.has(current.id)) { note = "上级关系存在循环，路径待核对"; break; }
    seen.add(current.id);
    path.unshift(current);
    if (current.parent_id === null) break;
    if (!current.parent_id) { note = "上级关系未提供"; break; }
    const parent = byId.get(current.parent_id);
    if (!parent) { note = "上级位置未加载"; break; }
    current = parent;
  }
  return { path, note };
}

export interface SpatialLocationPickerProps {
  locations: LocationRecord[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onEnter: (location: LocationRecord) => void;
  loading: boolean;
  error?: string;
  onRetry: () => void;
  enterDisabled?: boolean;
  contextLabel?: string;
}

export function SpatialLocationPicker({ locations, selectedId, onSelect, onEnter, loading, error, onRetry, enterDisabled = false, contextLabel }: SpatialLocationPickerProps) {
  const [query, setQuery] = useState("");
  // Directory navigation does not commit a scene entry or discard the chosen location.
  const [browseId, setBrowseId] = useState<string | null | undefined>(undefined);
  useEffect(() => { setBrowseId(undefined); }, [selectedId]);
  const byId = useMemo(() => new Map(locations.map(location => [location.id, location])), [locations]);
  const selected = selectedId ? byId.get(selectedId) : undefined;
  const directory = browseId === undefined ? selected : browseId ? byId.get(browseId) : undefined;
  const directoryPath = directory ? ancestry(directory, byId) : { path: [], note: "" };
  const selectedPath = selected ? ancestry(selected, byId) : undefined;
  const parent = directory?.parent_id ? byId.get(directory.parent_id) : undefined;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const isSearch = Boolean(normalizedQuery);
  const blocked = loading || Boolean(error);
  const roots = locations.filter(location => !location.parent_id || !byId.has(location.parent_id));
  const hierarchyUnavailable = !roots.length && locations.length > 0;
  const visible = isSearch
    ? locations.filter(location => `${location.name} ${location.identifier}`.toLocaleLowerCase().includes(normalizedQuery))
    : directory ? locations.filter(location => location.parent_id === directory.id && location.id !== directory.id) : hierarchyUnavailable ? locations : roots;
  const canEnter = Boolean(selected && (selected.location_type === "floor" || roomTypes.has(selected.location_type)));
  const choose = (location: LocationRecord) => {
    if (blocked || !byId.has(location.id)) return;
    setQuery("");
    setBrowseId(location.id);
    onSelect(location.id);
  };
  const showRoots = () => { setQuery(""); setBrowseId(null); };
  const groups = isSearch ? [{ title: "搜索结果", items: visible }] : [
    { title: directory ? "楼层" : "楼层入口", items: visible.filter(location => location.location_type === "floor") },
    { title: directory?.location_type === "building" ? "直属房间" : "房间", items: visible.filter(location => roomTypes.has(location.location_type)) },
    { title: directory ? "其他下级位置" : "位置目录", items: visible.filter(location => location.location_type !== "floor" && !roomTypes.has(location.location_type)) },
  ];

  return <section className="spatial-location-picker workspace-page" aria-labelledby="spatial-location-picker-title">
    <header className="workspace-page-heading spatial-location-picker-heading">
      <div><span className="workspace-eyebrow">3D 基础设施工作台</span><h1 id="spatial-location-picker-title">先选择要进入的位置</h1><p>浏览建筑与楼层，确认范围后进入 3D。建筑和园区仅作为位置目录。</p></div>
      <Link className="spatial-location-back" to="/"><ArrowLeftOutlined aria-hidden="true" />返回工作台</Link>
    </header>
    {contextLabel && <p className="spatial-location-context">当前可见范围：{contextLabel}</p>}
    {error && <Alert type="error" showIcon role="alert" title="位置读取失败" description={<>{error}{locations.length > 0 && <p>当前为上次加载的目录。请重试，确认可见范围后再进入。</p>}</>} action={<Button onClick={() => onRetry()} loading={loading}>重试</Button>} />}
    <section className="workspace-panel spatial-location-browser" aria-label="选择位置" aria-busy={loading}>
      <div className="spatial-location-search-row">
        <div className="spatial-location-search"><label htmlFor="spatial-location-search">搜索名称或编号</label><Input id="spatial-location-search" prefix={<SearchOutlined aria-hidden="true" />} placeholder="搜索当前可见范围内的位置" allowClear value={query} disabled={blocked || !locations.length} onChange={event => setQuery(event.target.value)} /></div>
        <span className="spatial-location-count">已加载 <strong>{locations.length}</strong> 个位置</span>
      </div>
      <div className="spatial-location-navigation">
        <nav aria-label="位置层级路径"><ol>
          <li><button type="button" disabled={blocked} onClick={showRoots}>全部可见位置</button></li>
          {directoryPath.note && <li className="spatial-location-path-note">{directoryPath.note}</li>}
          {directoryPath.path.map(location => <li key={location.id}><button type="button" disabled={blocked} aria-current={location.id === directory?.id ? "location" : undefined} onClick={() => choose(location)}>{nameOf(location)}</button></li>)}
        </ol></nav>
        <Button size="small" icon={<ArrowLeftOutlined aria-hidden="true" />} disabled={blocked || !directory} onClick={() => parent ? choose(parent) : showRoots()}>{parent ? "上一级" : "返回目录"}</Button>
      </div>
      {!isSearch && <p className="spatial-location-directory-note">{directory ? `${nameOf(directory)} · 仅显示已登记的直属下级位置` : "仅显示当前可见的层级入口；未加载的上级不会补造。"}</p>}
      {loading ? <div className="spatial-location-state" role="status"><Spin size="small" /><span>正在加载位置目录…</span></div>
        : error && !locations.length ? <div className="spatial-location-state" role="status">位置尚未读取成功，请重试。</div>
        : !locations.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前可见范围暂无位置" />
        : <>
          {!isSearch && !directory && hierarchyUnavailable && <Alert type="warning" showIcon title="位置层级关系待核对" description="未找到有效的层级入口，以下列出已加载的位置。" />}
          {isSearch && <p className="spatial-location-search-summary" role="status">找到 {visible.length} 个位置 · 点击结果可定位其层级</p>}
          {!visible.length && (directory && !isSearch && (directory.location_type === "floor" || roomTypes.has(directory.location_type)) ? <p className="spatial-location-directory-note">{directory.location_type === "floor" ? "本层暂无已加载房间，可进入本层继续规划。" : "已选定独立房间，点击下方按钮进入 3D。"}</p> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={isSearch ? "没有匹配的位置，请尝试其他名称或编号" : "此位置没有已加载的下级位置"}>{isSearch && <Button onClick={() => setQuery("")}>清除搜索</Button>}</Empty>)}
          {groups.filter(group => group.items.length > 0).map(group => <section key={group.title} className="spatial-location-group" aria-label={group.title}>
            <h2>{group.title}<span>{group.items.length}</span></h2>
            <div className="spatial-location-grid">{group.items.map(location => {
              const path = ancestry(location, byId);
              return <button type="button" key={location.id} className="spatial-location-card" disabled={blocked} aria-pressed={location.id === selectedId} aria-label={`选择${typeLabel(location)}：${nameOf(location)}`} onClick={() => choose(location)}>
                <span className="spatial-location-card-icon" aria-hidden="true">{location.location_type === "floor" ? <ApartmentOutlined /> : roomTypes.has(location.location_type) ? <EnvironmentOutlined /> : <HomeOutlined />}</span>
                <span className="spatial-location-card-content"><span className="spatial-location-card-title">{nameOf(location)}</span><span className="spatial-location-identifier">{location.identifier || "编号未提供"}</span><span className="spatial-location-card-type">{typeLabel(location)}</span>{isSearch && <span className="spatial-location-result-path">{[path.note, ...path.path.map(nameOf)].filter(Boolean).join(" / ")}</span>}{!isSearch && !directory && path.note && <span className="spatial-location-result-path">{path.note}</span>}</span>
                <ArrowRightOutlined className="spatial-location-card-arrow" aria-hidden="true" />
              </button>;
            })}</div>
          </section>)}
        </>}
    </section>
    <section className="workspace-panel spatial-location-selection" aria-label="确认进入 3D">
      <div className="spatial-location-selection-description">
        <span className="spatial-location-selection-label">已选择的位置</span>
        <h2>{selected ? nameOf(selected) : "尚未选择"}</h2>
        {selected && <p>{typeLabel(selected)} · {selected.identifier || "编号未提供"}</p>}
        {selectedPath && <p className="spatial-location-selected-path">{[selectedPath.note, ...selectedPath.path.map(nameOf)].filter(Boolean).join(" / ")}</p>}
        <p role="status">{selectedId && !selected ? "原选择已不在当前可见目录，请重新选择。" : !selected ? "选择楼层或房间后，再确认进入。" : selected.location_type === "floor" ? "可进入整个本层，也可在上方继续选择房间。" : canEnter ? "仅在确认后打开此房间的 3D 场景。" : "此位置是目录，请继续选择楼层或房间。"}</p>
      </div>
      <Button type="primary" size="large" icon={<ArrowRightOutlined aria-hidden="true" />} disabled={blocked || enterDisabled || !canEnter} onClick={() => { if (!blocked && !enterDisabled && selected && canEnter && byId.has(selected.id)) onEnter(selected); }}>{selected?.location_type === "floor" ? "进入楼层3D" : selected && roomTypes.has(selected.location_type) ? "进入房间3D" : "进入3D"}</Button>
    </section>
  </section>;
}
