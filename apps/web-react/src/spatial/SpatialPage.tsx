import { Alert, Button, Spin } from "antd";
import { lazy, Suspense, useEffect, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import { useApiResource } from "../components/useApiResource";
import { contextKey } from "../pages/fiberUi";
import type { LocationRecord, PageResult } from "../types";
import { SpatialLocationPicker } from "./SpatialLocationPicker";
import { SpatialDirectoryRoomCreate } from "./SpatialDirectoryRoomCreate";

const Workbench = lazy(() => import("./SpatialWorkbench").then(module => ({ default: module.SpatialWorkbench })));
const sceneTypes = new Set(["floor", "room", "tr", "er", "mdf", "mmr", "data_hall", "entrance_facility"]);
type Props = { getContext: () => InfrastructureContext; onContextChange?: (context: InfrastructureContext) => void };

function pathFor(locations: LocationRecord[], target: LocationRecord): string {
  const byId = new Map(locations.map(item => [item.id, item]));
  const visited = new Set<string>();
  const names: string[] = [];
  let item: LocationRecord | undefined = target;
  while (item && !visited.has(item.id)) {
    visited.add(item.id); names.unshift(item.name || item.identifier);
    item = item.parent_id ? byId.get(item.parent_id) : undefined;
  }
  return names.join(" / ");
}

export default function SpatialPage(props: Props) {
  return <SpatialEntry key={contextKey(props.getContext())} {...props} />;
}
function SpatialEntry({ getContext, onContextChange }: Props) {
  const [params, setParams] = useSearchParams();
  const context = getContext();
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  // Read a lightweight, authorized directory before loading any scene assets/WebGL.
  const catalog = useApiResource(async signal => {
    const result = await api.request<LocationRecord[] | PageResult<LocationRecord>>("/locations", { signal });
    return Array.isArray(result) ? result : result.items;
  }, [getContext]);
  const locations = catalog.data ?? [];
  const requested = params.get("location") || params.get("room");
  const target = locations.find(item => item.id === requested);
  const canEnter = target && sceneTypes.has(target.location_type);
  const needsContext = Boolean(canEnter && context.locationId !== target.id);
  useEffect(() => {
    if (canEnter && needsContext && !catalog.loading && !catalog.error && onContextChange) {
      onContextChange({ ...getContext(), locationId: target.id });
    }
  }, [canEnter, needsContext, target?.id, catalog.loading, catalog.error, getContext, onContextChange]);

  const browse = (id?: string, preserveObject = false) => {
    const next = new URLSearchParams(params);
    ["location", "room", "browse"].forEach(key => next.delete(key));
    if (!preserveObject) ["rack", "cable"].forEach(key => next.delete(key));
    if (id) next.set("browse", id);
    setParams(next);
  };
  const enter = (location: LocationRecord) => {
    if (catalog.loading || catalog.error || !sceneTypes.has(location.location_type) || !locations.some(item => item.id === location.id)) return;
    const next = new URLSearchParams(params);
    next.delete("browse"); next.delete("room"); next.set("location", location.id);
    setParams(next);
  };
  const failure = catalog.error?.message ?? (!catalog.loading && requested && !target ? "指定位置不存在、未加载或无权访问。请选择列表中可访问的楼层或房间。" : undefined);
  if (canEnter && !catalog.error && !catalog.loading) {
    if (needsContext && !onContextChange) return <main className="login-card"><Alert type="error" title="无法切换空间" description="当前页面未启用空间范围切换，请返回位置目录选择。" /><Link to="/locations">返回位置目录</Link></main>;
    if (needsContext) return <main className="login-card" role="status"><Spin /> 正在切换到所选空间…</main>;
    return <Suspense fallback={<main className="login-card" role="status"><Spin /> 正在加载所选空间…</main>}><Workbench key={target.id} getContext={getContext} onBrowse={browse} scopePath={pathFor(locations, target)} /></Suspense>;
  }
  const selectedId = params.get("browse") ?? target?.id ?? context.locationId;
  return <main className="spatial-location-entry">
    {(params.has("cable") || params.has("rack")) && <Alert type="info" showIcon title="先选择对象所在的楼层或房间" description="进入后会定位指定对象；不会合并其他楼层的设备与线路。" />}
    <SpatialLocationPicker locations={locations} selectedId={selectedId} onSelect={id => browse(id, true)} onEnter={enter} loading={catalog.loading} error={failure} onRetry={() => void catalog.reload()} enterDisabled={!onContextChange && context.locationId !== (params.get("browse") ?? target?.id ?? context.locationId)} />
    <section className="workspace-page spatial-directory-create-section"><div className="workspace-panel spatial-directory-create"><div><h2>还没有要进入的房间？</h2><p>可先创建房间，再选择进入。保存前请核对它所属的建筑或楼层。</p></div><SpatialDirectoryRoomCreate key={selectedId ?? "root"} locations={locations} parentId={selectedId} getContext={getContext} disabled={catalog.loading || Boolean(failure)} onCreated={id => { browse(id); void catalog.reload(); }} /></div></section>
    {failure && requested && <div className="login-card"><Button onClick={() => browse()}>返回空间选择</Button></div>}
  </main>;
}
