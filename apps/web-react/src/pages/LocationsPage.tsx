import { AimOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Input, Select, Table } from "antd";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import type { LocationRecord, PageResult } from "../types";
import { useApiResource } from "../components/useApiResource";
import "./locations.css";

const locationTypes: Record<string, string> = {
  region: "区域", campus: "园区", site: "站点", building: "楼宇", floor: "楼层", zone: "分区",
  room: "房间", tr: "电信间（TR）", er: "设备间（ER）", mdf: "主配线间（MDF）",
  mmr: "运营商互联间（MMR）", data_hall: "数据机房", entrance_facility: "进线设施", row: "机柜列", other: "其他",
};
// Buildings and campuses open the directory; only a floor or room opens a scene.
const sceneTypes = new Set(["floor", "room", "tr", "er", "mdf", "mmr", "data_hall", "entrance_facility"]);
const typeLabel = (type: string) => locationTypes[type] ?? (type ? `其他类型 · ${type}` : "类型未提供");

export function LocationsPage({ getContext, onContextChange }: {
  getContext: () => InfrastructureContext;
  onContextChange?: (context: InfrastructureContext) => void;
}) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);
  const state = useApiResource(async (signal?: AbortSignal) => {
    const result = await api.request<LocationRecord[] | PageResult<LocationRecord>>("/locations", { signal });
    return Array.isArray(result) ? result : result.items;
  }, [getContext]);
  const locations = state.data ?? [];
  const byId = new Map(locations.map(location => [location.id, location]));
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = locations.filter(location => (!type || location.location_type === type) && `${location.name} ${location.identifier}`.toLocaleLowerCase().includes(normalizedQuery));
  const typeOptions = [...new Set(locations.map(location => location.location_type))].map(value => ({ value, label: typeLabel(value) }));
  const filtersActive = Boolean(query || type);
  const resetFilters = () => { setQuery(""); setType(""); setPage(1); };
  const openScene = (location: LocationRecord) => {
    if (state.loading || state.error) return;
    const context = getContext();
    if (sceneTypes.has(location.location_type)) {
      if (onContextChange) onContextChange({ ...context, locationId: location.id });
      else if (context.locationId !== location.id) return;
      navigate(`/3d?location=${encodeURIComponent(location.id)}`);
    } else navigate(`/3d?browse=${encodeURIComponent(location.id)}`);
  };
  const empty = state.loading ? <span className="locations-muted">正在读取位置…</span> : state.error ? <span className="locations-muted">读取成功后显示位置列表</span> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={locations.length ? "没有符合条件的位置" : "当前可见范围暂无位置记录"}>{locations.length > 0 && <Button onClick={resetFilters}>清除筛选</Button>}</Empty>;

  return <section className="workspace-page locations-page" aria-labelledby="locations-title">
    <header className="workspace-page-heading">
      <div><span className="workspace-eyebrow">基础设施目录</span><h1 id="locations-title">位置管理</h1><p>查找园区、楼宇与房间，核对上级关系，进入对应的 3D 空间。</p></div>
      <Button icon={<ReloadOutlined aria-hidden="true" />} loading={state.loading} onClick={() => void state.reload()}>刷新位置</Button>
    </header>
    {state.error && <Alert type="error" showIcon role="alert" title="位置读取失败" description={<>{state.error.message}{state.data && <p>当前列表为上次读取的资料，请重试后再打开位置。</p>}</>} action={<Button loading={state.loading} onClick={() => void state.reload()}>重试</Button>} />}
    <section className="workspace-panel locations-directory" aria-label="位置目录" aria-busy={state.loading}>
      <div className="locations-directory-heading"><div><h2>位置列表</h2><p>上级关系以本次已加载资料为准。</p></div><div className="locations-counts" aria-live="polite"><span>已加载位置 <strong>{state.data ? locations.length : "—"}</strong></span><span>筛选结果 <strong>{state.data ? filtered.length : "—"}</strong></span></div></div>
      <div className="locations-filters">
        <div className="locations-search"><label htmlFor="locations-search">搜索位置</label><Input id="locations-search" prefix={<SearchOutlined aria-hidden="true" />} placeholder="输入名称或编号" value={query} allowClear disabled={!state.data} onChange={event => { setQuery(event.target.value); setPage(1); }} /></div>
        <div className="locations-type-filter"><label htmlFor="locations-type">位置类型</label><Select id="locations-type" aria-label="位置类型" value={type} options={[{ value: "", label: "全部类型" }, ...typeOptions]} disabled={!state.data} onChange={value => { setType(value); setPage(1); }} /></div>
        <Button disabled={!filtersActive} onClick={resetFilters}>重置筛选</Button>
      </div>
      <p className="locations-scroll-hint">左右滑动查看类型和上级位置，3D 操作固定在右侧。</p>
      <Table<LocationRecord> className="locations-table" rowKey="id" size="middle" tableLayout="fixed" loading={state.loading} dataSource={filtered} locale={{ emptyText: empty }} scroll={{ x: 700 }} pagination={{ current: Math.min(page, Math.max(1, Math.ceil(filtered.length / 20))), pageSize: 20, showSizeChanger: false, hideOnSinglePage: true, showTotal: total => `共 ${total} 项`, onChange: setPage }} columns={[
        { title: "位置 / 编号", key: "location", width: 240, render: (_, location) => <div className="locations-name"><strong>{location.name || "名称未提供"}</strong><span>{location.identifier || "编号未提供"}</span></div> },
        { title: "位置类型", dataIndex: "location_type", width: 140, render: value => <span className="locations-type-tag">{typeLabel(value)}</span> },
        { title: "上级位置", key: "parent", width: 200, render: (_, location) => {
          if (location.parent_id === null) return <span className="locations-muted">顶层位置</span>;
          if (!location.parent_id) return <span className="locations-muted">上级关系未提供</span>;
          const parent = byId.get(location.parent_id);
          return parent ? <div className="locations-parent"><span>{parent.name || "名称未提供"}</span><small>{parent.identifier || "编号未提供"}</small></div> : <div className="locations-parent"><span className="locations-muted">上级位置未加载</span><small>当前资料无法确认上级名称</small></div>;
        } },
        { title: "操作", key: "actions", fixed: "right", width: 120, render: (_, location) => <Button className="locations-scene-action" type="link" icon={<AimOutlined aria-hidden="true" />} aria-label={sceneTypes.has(location.location_type) ? `在 3D 中查看 ${location.name || location.identifier}` : `选择 ${location.name || location.identifier} 下的楼层或房间`} disabled={state.loading || Boolean(state.error) || (sceneTypes.has(location.location_type) && !onContextChange && getContext().locationId !== location.id)} title={sceneTypes.has(location.location_type) && !onContextChange && getContext().locationId !== location.id ? "当前页面未启用空间范围切换" : undefined} onClick={() => openScene(location)}>{sceneTypes.has(location.location_type) ? "进入 3D" : "选择空间"}</Button> },
      ]} />
      <p className="locations-scope-note">楼层和房间可直接进入；建筑、园区先选择具体楼层或房间。不同楼层独立显示，当前项目保持不变。</p>
    </section>
  </section>;
}
