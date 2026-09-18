import { AimOutlined, BookOutlined, AppstoreOutlined, ArrowLeftOutlined, ArrowUpOutlined, SwapOutlined, MinusOutlined, PlusOutlined, ReloadOutlined, SettingOutlined } from "@ant-design/icons";
import { Alert, App, Button, Checkbox, Dropdown, Popover, Segmented, Spin } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import type { InfrastructureScene, Selection, ScenePlacement, SceneMeasurementPoint } from "./render/sceneRenderer";
import { buildScene, type SpatialPayload } from "./sceneData";
import { SpatialCanvas } from "./SpatialCanvas";
import { SpatialInspector } from "./SpatialInspector";
import { SceneInventory } from "./SceneInventory";
import { SceneCreateDrawer } from "./SceneCreateDrawer";
import { RackPlacementDialog } from "./RackPlacementDialog";
import { InstallPalette, installationPreset, installationItems, type InstallKind, type InstallPreset, type WorkspaceTool } from "./InstallPalette";
import { ConnectionWorkbench } from "./ConnectionWorkbench";
import { CableLifecycleDialog, type CableLifecycleResult } from "../cables/CableLifecycleDialog";
import { HardwareProperties } from "./HardwareProperties";
import { RoomEntrancesEditor } from "./RoomEntrancesEditor";
import { ConnectionPortPicker } from "./ConnectionPortPicker";
import { MeasurementWorkbench } from "./MeasurementWorkbench";
import { WorkspaceAssistant } from "./WorkspaceAssistant";
import { WorkspaceGuide, type GuideAction } from "./WorkspaceGuide";
import { checkWorkspace, type WorkspaceCheckResult } from "./workspaceChecks";
import { shouldIgnoreSpatialShortcut } from "./keyboardShortcuts";
import { availablePorts, mediaFamily, portBlockedReason } from "./sceneCreate";
import { selectionView, type SceneMode } from "./selectionView";
import "./spatial.css";
import { loadScene } from "./loadScene";
import { useSpatialLayers } from "./useSpatialLayers";
import type { RoutePortion } from "./routePortions";

type Props = { getContext: () => InfrastructureContext; onContextChange?: (context: InfrastructureContext) => void; onBrowse?: (locationId?: string) => void; scopePath?: string };
type CreateKind = "room" | "rack" | "device" | "pathway" | "cable";
type Created = { kind: string; id?: string; locationId?: string };
export function SpatialWorkbench({ getContext, onBrowse, scopePath }: Props) {
  const { message, modal } = App.useApp();
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [payload, setPayload] = useState<SpatialPayload>();
  const [loading, setLoading] = useState(true);
  const [portProgress, setPortProgress] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [locationId, setLocationId] = useState("");
  const [mode, setMode] = useState<SceneMode>("overview");
  const [createKind, setCreateKind] = useState<CreateKind | null>(null);
  const [installPreset, setInstallPreset] = useState<InstallPreset>();
  const [activeTool, setActiveTool] = useState<WorkspaceTool>("select");
  const [portPicker, setPortPicker] = useState<Selection>();
  const [measurementPoints, setMeasurementPoints] = useState<SceneMeasurementPoint[]>([]);
  const connectionPorts = useRef({ portA: "", portB: "", media: "Cat6A copper" });
  const [leftTab, setLeftTab] = useState<"install" | "inventory" | "assistant">("install");
  const [guideOpen, setGuideOpen] = useState(false);
  const [loadedAt, setLoadedAt] = useState<number>();
  const [focusRequest, setFocusRequest] = useState<{ id: string }>();
  const lastRackFocus = useRef<string>(undefined);
  const [editingRoom, setEditingRoom] = useState<string>();
  const [connection, setConnection] = useState<{ startPortId?: string; endPortId?: string; initialMedia?: string }>();
  const [connectionSaving, setConnectionSaving] = useState(false);
  const [draft, setDraft] = useState<{ segmentIds: string[]; portA: string; portB: string; portions?: RoutePortion[] }>();
  const [movingRackId, setMovingRackId] = useState<string | null>(null);
  const [lifecycleCable, setLifecycleCable] = useState<{ id: string; identifier: string }>();
  const [ready, setReady] = useState(false);
  const [layers, setLayers] = useSpatialLayers(getContext());
  const engine = useRef<InfrastructureScene | null>(null);
  const pendingCreated = useRef<Created | null>(null);
  const appliedLink = useRef<string | null>(null);
  const pickPort = useCallback((id: string) => {
    if (!payload || !connection || connectionSaving) return;
    const current = connectionPorts.current;
    const addingB = Boolean(current.portA && !current.portB);
    const port = payload.ports.find(item => item.id === id);
    if (!port || !availablePorts(payload, port.device_id, current.media, addingB ? current.portA : undefined).some(item => item.id === id)) {
      void message.warning("该接口已占用或不支持当前线缆介质，请重新选择。"); return;
    }
    const next = { startPortId: addingB ? current.portA : id, endPortId: addingB ? id : undefined, initialMedia: current.media };
    connectionPorts.current = { portA: next.startPortId, portB: next.endPortId ?? "", media: current.media };
    setConnection(next); setSelection({ kind: "port", id }); setPortPicker(undefined);
  }, [payload, connection, connectionSaving, message]);
  const select = useCallback((value: Selection) => {
    if (!payload || connectionSaving) return;
    const next = selectionView(payload, value, { locationId, mode });
    if (!next) return;
    setSelection(next.selection); setLocationId(next.locationId); setMode(next.mode);
    if (connection) {
      if (value.kind === "port") pickPort(value.id);
      else if (value.kind === "device" || value.kind === "rack") setPortPicker(value);
    }
  }, [payload, locationId, mode, connection, connectionSaving, pickPort]);
  const requestedRack = searchParams.get("rack");
  const requestedCable = searchParams.get("cable");
  const requestedRoom = searchParams.get("room");
  useEffect(() => {
    if (!getContext().tenantId) return;
    const controller = new AbortController();
    setLoading(true); setError(""); setPortProgress("");
    loadScene(api, controller.signal, (loaded, total) => { if (!controller.signal.aborted) setPortProgress(`正在加载端口 ${loaded.toLocaleString()} / ${total.toLocaleString()}…`); }).then(data => {
      if (controller.signal.aborted) return;
      setPayload(data); setLoadedAt(Date.now());
      const created = pendingCreated.current;
      const key = [requestedRack, requestedCable, requestedRoom].join("|");
      if (created) {
        pendingCreated.current = null; appliedLink.current = key;
        const next = created.id && ["room", "rack", "device", "pathway", "cable"].includes(created.kind)
          ? selectionView(data, { kind: created.kind as Selection["kind"], id: created.id }, { locationId: created.locationId ?? locationId, mode: "overview" }) : null;
        if (next) { setLocationId(next.locationId); setSelection(next.selection); setMode(next.mode); }
        else { if (created.locationId) setLocationId(created.locationId); setSelection(null); setMode("overview"); }
      } else if (appliedLink.current !== key) {
        appliedLink.current = key;
        if (requestedRack && data.racks.some(rack => rack.id === requestedRack)) { setSelection({ kind: "rack", id: requestedRack }); setMode("rack"); }
        else if (requestedCable && data.cables.some(cable => cable.id === requestedCable)) { setSelection({ kind: "cable", id: requestedCable }); setMode("overview"); setLocationId(""); }
        else if (requestedRoom && data.locations.some(room => room.id === requestedRoom)) { setLocationId(requestedRoom === data.scope.location_id ? "" : requestedRoom); setSelection(null); setMode("overview"); }
        else if (requestedRack || requestedCable || requestedRoom) setError("指定对象不在当前项目或位置范围内，请返回工作台调整上下文。");
      }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法加载场景"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, refresh, getContext, requestedRack, requestedCable, requestedRoom]);
  const visibleRacks = useMemo(() => payload?.racks.filter(rack => !locationId || rack.location_id === locationId) ?? [], [payload, locationId]);
  const activeRack = visibleRacks.find(rack => selection?.kind === "rack" ? rack.id === selection.id : selection?.kind === "device" || selection?.kind === "port" ? payload?.devices.some(device => device.id === (selection.kind === "port" ? payload.ports.find(port => port.id === selection.id)?.device_id : selection.id) && device.rack_id === rack.id) : false) ?? visibleRacks[0];
  const focusedRackId = mode === "rack" ? activeRack?.id : undefined;
  // Connection mode retains both ends as pickable surfaces for cross-rack dragging.
  const showPortDetails = Boolean(focusedRackId || connection);
  const checks = useMemo(() => payload ? checkWorkspace(payload, locationId || undefined) : undefined, [payload, locationId]);
  // Selecting a cable changes its material only; keep all geometry mounted.
  const scene = useMemo(() => payload ? buildScene(payload, { locationId: locationId || undefined, rackId: focusedRackId, showPathways: true, portDetailRackIds: showPortDetails ? undefined : [] }) : undefined, [payload, locationId, focusedRackId, showPortDetails]);
  useEffect(() => { setMeasurementPoints([]); }, [scene]);
  const canvasData = useMemo(() => {
    if (!scene || !payload || !draft) return scene?.data;
    const ends = [draft.portA, draft.portB].map((id, index) => {
      const port = payload.ports.find(item => item.id === id);
      const device = payload.devices.find(item => item.id === port?.device_id);
      return port && device ? { side: index === 0 ? "A" : "B", port_id: port.id, device_id: device.id, rack_id: device.rack_id, location_id: device.location_id } : null;
    });
    if (!ends.every((end): end is NonNullable<typeof end> => end !== null)) return scene.data;
    const temporary = { id: "__connection_preview__", identifier: "待确认路由", media_type: "preview", construction: "patch_cord", installation_status: "draft", length_m: null, test_status: null, terminations: ends, route_segment_ids: draft.segmentIds, route_portions: draft.portions, endpoint_scope: "complete" as const };
    const preview = buildScene({ ...payload, cables: [temporary] }, { locationId: locationId || undefined, rackId: focusedRackId, showPathways: false });
    return { ...scene.data, paths: [...scene.data.paths, ...preview.data.paths.map(path => ({ ...path, draft: true }))] };
  }, [scene, payload, draft, locationId, focusedRackId]);
  useEffect(() => {
    if (!ready) return;
    if (focusedRackId || lastRackFocus.current) engine.current?.focus(focusedRackId);
    lastRackFocus.current = focusedRackId;
  }, [ready, focusedRackId]);
  useEffect(() => {
    if (!ready || !focusRequest) return;
    engine.current?.focus(focusRequest.id); setFocusRequest(undefined);
  }, [ready, focusRequest, canvasData]);
  const onPreview = useCallback((segmentIds: string[], endpoints?: { portA: string; portB: string }, portions?: RoutePortion[]) => {
    setDraft(endpoints ? { segmentIds, ...endpoints, portions } : undefined);
    if (endpoints && payload) {
      const rooms = [endpoints.portA, endpoints.portB].map(id => payload.devices.find(device => device.id === payload.ports.find(port => port.id === id)?.device_id)?.location_id);
      setMode("overview");
      if (rooms.every(Boolean)) setLocationId(rooms[0] === rooms[1] ? rooms[0]! : "");
    }
  }, [payload]);
  useEffect(() => { if (draft) { engine.current?.focus(); engine.current?.setView("iso"); } }, [draft]);
  const routeKind = selection?.kind === "cable" ? scene?.routeKinds[selection.id] ?? "none" : "none";
  const scopeName = payload?.locations.find(location => location.id === (locationId || payload.scope.location_id))?.name ?? "当前可见空间";
  const scopeRoot = payload?.locations.find(room => room.id === payload.scope.location_id);
  const movingRack = payload?.racks.find(rack => rack.id === movingRackId);
  const browse = (target?: string) => {
    if (connectionSaving || !onBrowse) return;
    if (connection || createKind || editingRoom || movingRackId || measurementPoints.length) {
      modal.confirm({ title: "切换楼层或房间？", content: "当前未保存的接线、表单和临时测距将关闭。已保存资料不受影响。", okText: "放弃草稿并切换", cancelText: "继续编辑", onOk: () => onBrowse(target) });
    } else onBrowse(target);
  };
  const cableLifecycleBlocked = loading || error ? "请先成功刷新场景后操作。" : connection || connectionSaving || createKind || editingRoom || movingRackId || measurementPoints.length || !["select", "pan"].includes(activeTool) ? "请先完成或取消当前编辑与临时测距，再删除或拆除线缆。" : undefined;
  useEffect(() => { if (selection?.kind !== "cable" || selection.id !== lifecycleCable?.id || cableLifecycleBlocked) setLifecycleCable(undefined); }, [selection, lifecycleCable?.id, cableLifecycleBlocked]);
  const completedCableLifecycle = (result: CableLifecycleResult) => {
    setLifecycleCable(undefined); setSelection(null);
    const query = new URLSearchParams(searchParams);
    if (!query.has("location") && payload?.scope.location_id) query.set("location", payload.scope.location_id);
    if (query.get("cable") === result.id) query.delete("cable");
    setSearchParams(query, { replace: true });
    if (query.get("cable") === requestedCable) setRefresh(value => value + 1);
    void message.success(result.action === "delete" ? "规划线缆已删除，正在更新场景" : "线缆已登记拆除，正在更新场景");
  };
  const focus = (id?: string) => engine.current?.focus(id);
  const created = (result: Created) => {
    pendingCreated.current = result; setMeasurementPoints([]); setActiveTool("select"); setPortPicker(undefined); setCreateKind(null); setMovingRackId(null); setConnection(undefined); setDraft(undefined);
    const query = new URLSearchParams(searchParams);
    // Preserve the verified entry even when arriving through an old ?room= link.
    if (!query.has("location") && payload?.scope.location_id) query.set("location", payload.scope.location_id);
    ["rack", "cable", "room"].forEach(key => query.delete(key));
    if (result.kind === "cable" && result.id) query.set("cable", result.id);
    else if (result.locationId) query.set("room", result.locationId);
    const linkChanged = query.get("rack") !== requestedRack || query.get("cable") !== requestedCable || query.get("room") !== requestedRoom;
    setSearchParams(query, { replace: true });
    if (!linkChanged) setRefresh(value => value + 1);
    void message.success("已保存，正在更新三维场景");
  };
  const startConnection = (startPortId?: string, endPortId?: string, initialMedia?: string) => {
    if (connectionSaving) return;
    const port = payload?.ports.find(item => item.id === startPortId);
    const media = initialMedia ?? (mediaFamily(port?.media_type ?? port?.connector_type ?? "copper") === "fiber" ? "OS2 fiber" : "Cat6A copper");
    for (const id of [startPortId, endPortId]) {
      if (!id) continue;
      const endpoint = payload?.ports.find(item => item.id === id);
      if (!payload || !endpoint) return;
      const blocked = portBlockedReason(payload, endpoint);
      if (blocked) { void message.warning(blocked); return; }
      if (!availablePorts(payload, endpoint.device_id, media).some(item => item.id === id)) { void message.warning("该接口已占用或不支持当前线缆介质，请重新选择。"); return; }
    }
    if (startPortId && startPortId === endPortId) return;
    setMeasurementPoints([]); setActiveTool(mediaFamily(media) === "fiber" ? "fiber" : "copper"); setPortPicker(undefined);
    connectionPorts.current = { portA: startPortId ?? "", portB: endPortId ?? "", media };
    setCreateKind(null); setConnection({ startPortId, endPortId, initialMedia: media }); setDraft(undefined);
  };
  const cancelTool = () => {
    if (connectionSaving) return;
    setActiveTool("select"); setMeasurementPoints([]); setConnection(undefined); setPortPicker(undefined); setDraft(undefined);
  };
  const beginCreate = (kind: CreateKind) => {
    if (connectionSaving) return;
    if (kind === "cable") { startConnection(); return; }
    cancelTool(); setInstallPreset(undefined);
    const targetLocation = selection?.kind === "room" ? selection.id : selection?.kind === "rack" ? payload?.racks.find(rack => rack.id === selection.id)?.location_id : undefined;
    if (targetLocation) setLocationId(targetLocation);
    setCreateKind(kind);
  };
  const install = (kind: string, placement?: ScenePlacement) => {
    if (connectionSaving || !payload || !installationItems.some(item => item.id === kind)) return;
    if (kind === "copper" || kind === "fiber") {
      if (placement?.locationId) setLocationId(placement.locationId);
      startConnection(undefined, undefined, kind === "fiber" ? "OS2 fiber" : "Cat6A copper");
      if (placement?.rackId) { setSelection({ kind: "rack", id: placement.rackId }); setMode("rack"); }
      return;
    }
    const item = installationPreset(kind as InstallKind, placement, payload);
    if (placement?.locationId && kind !== "room") setLocationId(placement.locationId);
    setInstallPreset(item.preset); setCreateKind(item.kind);
  };
  const chooseTool = (tool: WorkspaceTool) => {
    if (connectionSaving || loading) return;
    setPortPicker(undefined); setMeasurementPoints([]);
    if (tool === "copper" || tool === "fiber") { install(tool); return; }
    setConnection(undefined); setDraft(undefined); setActiveTool(tool);
    if (tool === "measure") { setSelection(null); setMode("overview"); }
    if (tool === "room") { setInstallPreset(undefined); setCreateKind("room"); }
  };
  const placementMode = !["select", "pan", "measure", "copper", "fiber", "room"].includes(activeTool) && !createKind;
  const placeTool = (placement: ScenePlacement | null) => {
    if (!placementMode || connectionSaving) return;
    if (!placement) { void message.info("请在房间地面或机柜上点击放置。"); return; }
    if (["patch_panel", "fiber_panel", "switch", "server"].includes(activeTool) && !placement.rackId) { void message.info("请点击目标机柜安装设备，或使用“填写参数”选择机柜。"); return; }
    install(activeTool, placement);
  };
  const onEndpointsChange = useCallback((value: { portA: string; portB: string; media: string }) => {
    connectionPorts.current = value;
    setPortPicker(undefined);
    setActiveTool(mediaFamily(value.media) === "fiber" ? "fiber" : "copper");
  }, []);
  const measure = (point: SceneMeasurementPoint | null) => {
    if (activeTool !== "measure" || createKind || connectionSaving) return;
    if (!point || !point.point.every(Number.isFinite) || !scene?.data.rooms.some(room => room.id === point.locationId)) { void message.info("请在房间地面或物件表面点击测距。"); return; }
    if (measurementPoints.length >= 64) { void message.info("最多保留 64 个测距点，请完成或清空后重新测量。"); return; }
    if (measurementPoints.length && measurementPoints[0].locationId !== point.locationId) { void message.warning("不同房间采用示意布局，不能直接跨房间测距。请先清空当前测距。"); return; }
    const previous = measurementPoints.at(-1);
    if (previous && Math.hypot(...point.point.map((value, index) => value - previous.point[index])) < 0.001) return;
    setMeasurementPoints([...measurementPoints, { ...point, point: [...point.point] }]);
  };
  const measurementRoom = scene?.data.rooms.find(room => room.id === measurementPoints[0]?.locationId);
  const createMeasuredPathway = () => {
    if (!measurementRoom || measurementPoints.length < 2 || measurementPoints.some(point => point.locationId !== measurementRoom.id)) return;
    const points = measurementPoints.map(({ point: [x, y, z] }) => ({
      x: Math.round((x - measurementRoom.center[0] + measurementRoom.width / 2) * 1000) / 1000,
      y: Math.round((z - measurementRoom.center[1] + measurementRoom.depth / 2) * 1000) / 1000,
      z: Math.round(y * 1000) / 1000,
    }));
    setInstallPreset({ values: { location_id: measurementRoom.id, points }, warning: "已带入测距折线。请确认每个点的实际 X/Y 与线槽高度，再保存线槽。" });
    setMeasurementPoints([]); setLocationId(measurementRoom.id); setActiveTool("select"); setCreateKind("pathway");
  };
  const guideAction = (action: GuideAction) => {
    if (!payload || loading || connectionSaving || error) return;
    setGuideOpen(false);
    if (action === "checks") { setLeftTab("assistant"); return; }
    if (action === "inventory") { setLeftTab("inventory"); return; }
    cancelTool(); setLeftTab("install");
    if (action !== "room" && !scene?.data.rooms.length) { void message.info("先创建房间，再继续安装和布线。"); beginCreate("room"); return; }
    if (action === "device" && !visibleRacks.length) { void message.info("当前范围还没有机柜，请先放置机柜。"); beginCreate("rack"); return; }
    if (action === "copper" || action === "fiber" || action === "measure") chooseTool(action);
    else beginCreate(action);
  };
  const inspectIssue = (issue: WorkspaceCheckResult["issues"][number]) => {
    if (!payload || loading || connectionSaving || error) return;
    if (connection) { void message.info("请先保存或退出接线，再定位处理检查项。"); return; }
    const target = selectionView(payload, issue.selection, { locationId, mode: "overview" });
    if (!target) return;
    cancelTool(); setSelection(target.selection); setLocationId(target.locationId); setMode(target.mode);
    setFocusRequest({ id: issue.selection.id });
    if (issue.action === "entrances") setEditingRoom(issue.selection.id);
    else if (issue.action === "rack-position") setMovingRackId(issue.selection.id);
  };
  const showMeasurement = !createKind && (activeTool === "measure" || measurementPoints.length > 0);
  const showMeasurementPanel = showMeasurement && leftTab !== "assistant";
  const toolLabel = activeTool === "select" ? "选择" : activeTool === "pan" ? "平移" : activeTool === "measure" ? "测距" : installationItems.find(item => item.id === activeTool)?.name;
  const toolHint = connection ? "点击设备选择接口，或从起点端口拖到对端" : activeTool === "measure" ? "连续点击同一房间取点，计算折线长度" : activeTool === "pan" ? "按住左键平移视图" : activeTool === "room" ? "填写房间尺寸和入口" : activeTool === "select" ? "点击查看对象 · 拖动旋转" : ["rack", "tray"].includes(activeTool) ? "在房间地面单击放置" : "在目标机柜上单击安装";
  return <main className="spatial-workspace" onKeyDown={event => { if (event.key === "Escape" && !shouldIgnoreSpatialShortcut(event) && !guideOpen && !lifecycleCable && !createKind && !editingRoom && !movingRackId && !(event.target instanceof HTMLElement && event.target.closest("input, textarea, select, [role=combobox]"))) { cancelTool(); event.preventDefault(); } }}>
    <header className="spatial-header">
      <div className="spatial-brand"><Link to="/" aria-label="返回工作台"><ArrowLeftOutlined /></Link><span className="spatial-brand-mark"><AppstoreOutlined /></span><div><h1>3D 空间工作区</h1><p className="spatial-scope-path" title={scopePath}>{scopePath ?? "ROOMS · RACKS · CONNECTIVITY"}</p></div></div>
      <div className="spatial-header-context"><span>{scopeName}</span>{locationId && scopeRoot?.location_type === "floor" && <Button disabled={connectionSaving} type="text" icon={<ArrowUpOutlined />} onClick={() => { setLocationId(""); setSelection(null); setMode("overview"); }}>返回本层总览</Button>}{onBrowse && <Button disabled={connectionSaving} icon={<SwapOutlined aria-hidden="true" />} onClick={() => browse(scopeRoot?.parent_id ?? scopeRoot?.id)}>切换楼层 / 房间</Button>}<Dropdown trigger={["click"]} menu={{ items: [{ key: "room", label: "房间 / Server Room" }, { key: "rack", label: "批量放置机柜" }, { key: "device", label: "安装设备" }, { key: "pathway", label: "线槽 / 桥架" }, { key: "cable", label: "线缆连接" }], onClick: ({ key }) => beginCreate(key as CreateKind) }}><Button type="primary" icon={<PlusOutlined />} disabled={!payload || loading || connectionSaving}>新建</Button></Dropdown><Button disabled={connectionSaving} icon={<ReloadOutlined />} loading={loading} onClick={() => setRefresh(value => value + 1)}>刷新</Button></div>
    </header>
    {error && <div className="spatial-error"><Alert type="error" showIcon title="场景加载失败" description={error} action={<Button onClick={() => setRefresh(value => value + 1)}>重试</Button>} /></div>}
    <div className={`spatial-body ${connection ? "has-connection" : ""}`}>
      <aside className={`spatial-left-panel ${showMeasurementPanel ? "has-measurement" : ""}`}><div className="spatial-left-tabs"><button aria-pressed={leftTab === "install"} onClick={() => setLeftTab("install")}>工具箱</button><button aria-pressed={leftTab === "inventory"} onClick={() => setLeftTab("inventory")}>对象列表</button><button aria-pressed={leftTab === "assistant"} onClick={() => setLeftTab("assistant")}>工作助手</button></div>
      <button className="spatial-help-entry" onClick={() => setGuideOpen(true)}><BookOutlined />使用指南<span>快速上手</span></button>
      {leftTab === "assistant" && <WorkspaceAssistant result={checks} scopeName={scopeName} loadedAt={loadedAt} loading={loading} stale={Boolean(error)} inspectionBlocked={Boolean(connection)} onAction={guideAction} onIssue={inspectIssue} onRefresh={() => setRefresh(value => value + 1)} onGuide={() => setGuideOpen(true)} />}
      {(leftTab === "install" || showMeasurementPanel) && <InstallPalette disabled={!payload || loading || connectionSaving} activeTool={activeTool} onInstall={chooseTool} onToolChange={chooseTool} onConfigure={() => install(activeTool)} />}
      {showMeasurementPanel && <div className="spatial-measure-panel"><MeasurementWorkbench points={measurementPoints} room={measurementRoom} active={activeTool === "measure"} onUndo={() => setMeasurementPoints(points => points.slice(0, -1))} onClear={() => setMeasurementPoints([])} onFinish={() => setActiveTool("select")} onResume={() => setActiveTool("measure")} onCreatePathway={createMeasuredPathway} /></div>}
      {leftTab === "inventory" && <SceneInventory payload={payload} locationId={locationId} selection={selection} loading={loading} onLocation={value => { setLocationId(value); setSelection(null); setMode("overview"); }} onSelect={select} onInspectRack={id => { select({ kind: "rack", id }); setMode("rack"); }} />}
      </aside>
      <section className="spatial-stage" aria-label="三维视图">
        <div className="spatial-toolbar">
          <Segmented aria-label="场景模式" value={mode} options={[{ value: "overview", label: "空间总览" }, { value: "rack", label: "机柜视图", disabled: !activeRack }]} onChange={value => setMode(value === "rack" ? "rack" : "overview")} />
          <div className="spatial-view-buttons"><Button disabled={connectionSaving} type={connection ? "primary" : "default"} onClick={() => connection ? cancelTool() : startConnection()}>{connection ? "结束接线" : "端口接线"}</Button><Button disabled={!ready} onClick={() => engine.current?.setView("iso")}>等轴</Button><Button disabled={!ready} onClick={() => engine.current?.setView("front")}>正面</Button><Button disabled={!ready} onClick={() => engine.current?.setView("rear")}>背面</Button><Button disabled={!ready} onClick={() => engine.current?.setView("top")}>俯视</Button></div>
          <Popover trigger="click" title="可见图层" content={<div className="spatial-layer-options"><Checkbox checked={layers.shell} onChange={event => setLayers(value => ({ ...value, shell: event.target.checked }))}>机柜外壳</Checkbox><Checkbox checked={layers.labels} onChange={event => setLayers(value => ({ ...value, labels: event.target.checked }))}>对象标签</Checkbox><Checkbox checked={layers.pathways} onChange={event => setLayers(value => ({ ...value, pathways: event.target.checked }))}>线槽 / 桥架</Checkbox><Checkbox checked={layers.dimensions} onChange={event => setLayers(value => ({ ...value, dimensions: event.target.checked }))}>线路 / 测距长度</Checkbox></div>}><Button icon={<SettingOutlined />} aria-label="设置三维图层" /></Popover>
        </div>
        <div className={`spatial-tool-status ${activeTool !== "select" ? "is-active" : ""}`} role="status"><strong>{toolLabel}工具</strong><span>{toolHint}</span>{activeTool !== "select" && <Button size="small" disabled={connectionSaving} onClick={cancelTool}>取消 · Esc</Button>}</div>
        <div className="spatial-viewport">
          {canvasData && payload && <SpatialCanvas data={canvasData} selection={selection} layers={layers} onSelect={select} onReady={setReady} engine={engine} connectionMode={Boolean(connection) && !connectionSaving} onPortConnect={(a, b) => startConnection(a, b, connectionPorts.current.media)} onInstallDrop={install} placementMode={placementMode} panMode={activeTool === "pan"} onPlace={placeTool} onCancelTool={cancelTool} measurementMode={activeTool === "measure" && !createKind} measurementPoints={measurementPoints} onMeasure={measure} />}
          {loading && !payload && <div className="spatial-canvas-message"><Spin size="large" /><p>{portProgress || "正在构建场景…"}</p></div>}
          {!loading && payload && !error && !scene?.data.rooms.length && <div className="spatial-canvas-message"><strong>从一个房间开始</strong><p>建立 Server Room，再放置机柜、线槽和线缆。</p><Button type="primary" onClick={() => beginCreate("room")}>创建房间</Button></div>}
          {portPicker && payload && connection && <ConnectionPortPicker key={`${portPicker.kind}:${portPicker.id}`} target={portPicker} payload={payload} media={connectionPorts.current.media} side={connectionPorts.current.portA && !connectionPorts.current.portB ? "B" : "A"} otherPortId={connectionPorts.current.portA && !connectionPorts.current.portB ? connectionPorts.current.portA : undefined} onPick={pickPort} onClose={() => setPortPicker(undefined)} />}
          <div className="spatial-scene-caption"><span className="spatial-eyebrow">{mode === "rack" ? "RACK INSPECTION" : "SPATIAL OVERVIEW"}</span><h2>{mode === "rack" ? activeRack?.name : scopeName}</h2><p>{scene?.data.racks.length ?? 0} 机柜 / {scene?.data.racks.reduce((sum, rack) => sum + rack.devices.length, 0) ?? 0} 设备</p></div>
          <div className="spatial-camera-controls"><Button icon={<PlusOutlined />} aria-label="放大三维视图" disabled={!ready} onClick={() => engine.current?.zoom(1.25)} /><Button icon={<MinusOutlined />} aria-label="缩小三维视图" disabled={!ready} onClick={() => engine.current?.zoom(0.8)} /><Button icon={<AimOutlined />} aria-label="适配全部对象" disabled={!ready} onClick={() => focus()} /></div>
          <div className="spatial-route-badge"><span />{measurementPoints.length ? "蓝绿色为图示测距 · 不代表实测" : draft ? "待确认 · 橙色为预览路线" : selection?.kind === "cable" ? routeKind === "recorded" ? "选中线缆 · 已记录路由" : routeKind === "schematic" ? "选中线缆 · 端口连接示意" : "当前范围缺少完整连接坐标" : "实线连接 · 点击线缆高亮"}</div>
        </div>
        <footer className="spatial-statusbar"><span>{connection ? "点击设备选接口 · 空白处拖动旋转" : activeTool === "pan" ? "左键拖动平移 · 滚轮缩放" : activeTool === "measure" ? "单击添加测距点 · 拖动旋转 · Esc 清除" : placementMode ? "单击放置 · 拖动旋转 · 右键平移" : "拖动旋转 · 右键平移 · 滚轮缩放"}</span><span id="spatial-camera-help">点击画布 · WASD 移动 · Shift 加速 · F 适配 · 1 / 2 / 3 视角</span></footer>
        {(scene?.warnings.length || payload?.truncated.length) ? <div className="spatial-data-notes">{scene?.warnings.map(warning => <span key={warning}>{warning}</span>)}{Boolean(payload?.truncated.length) && <span>数据较多，部分结果被截断：{payload?.truncated.join("、")}。请缩小空间范围。</span>}</div> : null}
      </section>
      {payload && connection ? <ConnectionWorkbench payload={payload} getContext={getContext} {...connection} onClose={cancelTool} onCreated={created} onPreview={onPreview} onBusyChange={setConnectionSaving} onEndpointsChange={onEndpointsChange} /> : payload && <SpatialInspector payload={payload} selection={selection} onSelect={select} onFocus={focus} onClose={() => setSelection(null)} routeKind={routeKind} hasLeadIns={Boolean(selection && scene?.leadInCableIds.includes(selection.id))} onCreate={beginCreate} onMoveRack={id => setMovingRackId(id)} onEditEntrances={setEditingRoom} onConnectPort={id => startConnection(id)} onCableLifecycle={setLifecycleCable} cableLifecycleBlocked={cableLifecycleBlocked}>
        {selection && <HardwareProperties key={`${selection.kind}:${selection.id}`} selection={selection} payload={payload} getContext={getContext} onSaved={() => setRefresh(value => value + 1)} />}
      </SpatialInspector>}
    </div>
    {lifecycleCable && selection?.kind === "cable" && selection.id === lifecycleCable.id && !cableLifecycleBlocked && <CableLifecycleDialog cable={lifecycleCable} getContext={getContext} onClose={() => setLifecycleCable(undefined)} onCompleted={completedCableLifecycle} />}
    <WorkspaceGuide open={guideOpen} onClose={() => setGuideOpen(false)} onAction={guideAction} disabled={!payload || loading || connectionSaving || Boolean(error)} storageKey={`sim.spatial-guide.v1:${getContext().tenantId}:${getContext().actorId ?? "anonymous"}`} />
    {payload && <SceneCreateDrawer kind={createKind} payload={payload} locationId={locationId} rackId={activeRack?.id} preset={installPreset} getContext={getContext} onClose={() => { setCreateKind(null); cancelTool(); }} onCreated={created} />}
    {payload && editingRoom && payload.locations.find(room => room.id === editingRoom) && <RoomEntrancesEditor key={editingRoom} room={payload.locations.find(room => room.id === editingRoom)!} getContext={getContext} onClose={() => setEditingRoom(undefined)} onSaved={() => { setEditingRoom(undefined); setRefresh(value => value + 1); }} />}
    {movingRack && <RackPlacementDialog key={`${movingRack.id}:${movingRack.version}`} rack={movingRack} room={payload?.locations.find(room => room.id === movingRack.location_id)} getContext={getContext} onClose={() => setMovingRackId(null)} onSaved={() => created({ kind: "rack", id: movingRack.id, locationId: movingRack.location_id })} />}
  </main>;
}
