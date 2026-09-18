import { ArrowDownOutlined, ArrowUpOutlined, CloseOutlined, DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Checkbox, Form, Input, InputNumber, Radio, Select, Spin } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import type { SceneCreated } from "./SceneCreateDrawer";
import { availablePorts, creationError } from "./sceneCreate";
import type { SpatialPayload } from "./sceneData";
import "./connectionWorkbench.css";
import { formatMetres } from "./LineLengthDetails";
import { ProjectPicker } from "./ProjectPicker";
import type { RoutePortion } from "./routePortions";

export type RouteCandidate = {
  id: string; label: string; segment_ids: string[];
  segments: { id: string; pathway_id: string; pathway_identifier: string; name: string; length_m: number; full_length_m?: number; start_offset_m?: number; end_offset_m?: number }[];
  route_portions?: RoutePortion[];
  length_m: number; warnings: string[];
};
type RoutePreview = { candidates: RouteCandidate[]; warnings: string[] };
export type ConnectionWorkbenchProps = {
  payload: SpatialPayload; getContext: () => InfrastructureContext;
  startPortId?: string; endPortId?: string; initialMedia?: string;
  onEndpointsChange?: (value: { portA: string; portB: string; media: string }) => void;
  onBusyChange?: (busy: boolean) => void; onClose: () => void; onCreated: (result: SceneCreated) => void; onPreview: (segmentIds: string[], endpoints?: { portA: string; portB: string }, portions?: RoutePortion[]) => void;
};
const mediaOptions = [
  { value: "Cat6A copper", label: "Cat6A 铜缆" }, { value: "Cat6 copper", label: "Cat6 铜缆" },
  { value: "OS2 fiber", label: "OS2 单模光纤" }, { value: "OM4 fiber", label: "OM4 多模光纤" },
];
function preferredMedia(value?: string): string {
  if (mediaOptions.some(option => option.value === value)) return value!;
  return /fiber|os[12]|om[1-5]|lc|sc|mpo|mtp/i.test(value ?? "") ? "OS2 fiber" : "Cat6A copper";
}

export function ConnectionWorkbench({ payload, getContext, startPortId, endPortId, initialMedia, onClose, onCreated, onPreview, onBusyChange, onEndpointsChange }: ConnectionWorkbenchProps) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [form] = Form.useForm<{ identifier: string; construction: string; color?: string; reserve_length_m?: number }>();
  const [projectId, setProjectId] = useState(getContext().projectId ?? "");
  const reserveLength = Form.useWatch("reserve_length_m", form) ?? 0;
  const [portA, setPortA] = useState(startPortId ?? "");
  const [portB, setPortB] = useState(endPortId ?? "");
  const [media, setMedia] = useState(() => preferredMedia(initialMedia ?? payload.ports.find(port => port.id === startPortId)?.media_type));
  const [excluded, setExcluded] = useState<string[]>([]);
  const [manual, setManual] = useState(false);
  const [customSegments, setCustomSegments] = useState<string[]>([]);
  const [manualRequestKey, setManualRequestKey] = useState("");
  const [revision, setRevision] = useState(0);
  const [preview, setPreview] = useState<(RoutePreview & { key: string })>();
  const [candidateId, setCandidateId] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveNeedsPreview, setSaveNeedsPreview] = useState(false);
  const savingRef = useRef(false);
  const generation = useRef(0);
  const alive = useRef(true);
  const callbacks = useRef({ onPreview, onCreated, onClose, onBusyChange, onEndpointsChange });
  useEffect(() => { callbacks.current = { onPreview, onCreated, onClose, onBusyChange, onEndpointsChange }; }, [onPreview, onCreated, onClose, onBusyChange, onEndpointsChange]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; callbacks.current.onPreview([]); callbacks.current.onBusyChange?.(false); }; }, []);
  useEffect(() => {
    if (savingRef.current) return;
    setPortA(startPortId ?? ""); setPortB(endPortId ?? "");
  }, [startPortId, endPortId]);
  useEffect(() => { if (!savingRef.current && initialMedia) setMedia(preferredMedia(initialMedia)); }, [initialMedia]);
  useEffect(() => {
    if (savingRef.current || initialMedia) return;
    const start = payload.ports.find(port => port.id === startPortId);
    setMedia(preferredMedia(start?.media_type ?? start?.connector_type));
  }, [startPortId, initialMedia]);
  useEffect(() => { callbacks.current.onEndpointsChange?.({ portA, portB, media }); }, [portA, portB, media]);
  const family = media.includes("fiber") ? "fiber" : "copper";
  const allAvailable = useMemo(() => payload.devices.filter(device => device.cable_policy?.allows_cables !== false && (!device.cable_policy || device.cable_policy.allowed_media.includes(family))).flatMap(device => availablePorts(payload, device.id, media)), [payload, media, family]);
  const availableIds = useMemo(() => new Set(allAvailable.map(port => port.id)), [allAvailable]);
  const endpointsValid = !!portA && !!portB && portA !== portB && availableIds.has(portA) && availableIds.has(portB);
  const context = getContext();
  const requestKey = JSON.stringify([portA, portB, media, excluded, manual, manual ? customSegments : [], context.tenantId, context.actorId, context.projectId, projectId, context.locationId]);
  const requestBody = { port_a_id: portA, port_b_id: portB, media_type: media, excluded_pathway_ids: excluded, ...(manual ? { route_segment_ids: customSegments } : {}) };
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    setPreview(undefined); setCandidateId(""); setPreviewError(""); setSaveError("");
    if (!endpointsValid || manual && manualRequestKey !== requestKey) { setPreviewing(false); return () => controller.abort(); }
    setPreviewing(true);
    api.request<RoutePreview>("/scene/routes/preview", { method: "POST", body: JSON.stringify(requestBody), signal: controller.signal }).then(result => {
      if (controller.signal.aborted || current !== generation.current) return;
      setPreview({ ...result, key: requestKey }); setCandidateId(result.candidates[0]?.id ?? ""); setSaveNeedsPreview(false);
    }).catch(reason => { if (!controller.signal.aborted && current === generation.current) setPreviewError(creationError(reason)); })
      .finally(() => { if (!controller.signal.aborted && current === generation.current) setPreviewing(false); });
    return () => controller.abort();
  }, [api, payload, requestKey, endpointsValid, manualRequestKey, revision]);
  const candidate = endpointsValid && preview?.key === requestKey ? preview.candidates.find(item => item.id === candidateId) : undefined;
  const routeLength = candidate && candidate.segments.every(segment => Number.isFinite(segment.length_m) && segment.length_m >= 0) ? candidate.segments.reduce((total, segment) => total + segment.length_m, 0) : null;
  const accessLength = candidate && routeLength != null && candidate.length_m >= routeLength - 0.001 ? Math.max(0, candidate.length_m - routeLength) : null;
  const registeredLength = candidate && Number.isFinite(reserveLength) && reserveLength >= 0 ? Math.round((candidate.length_m + reserveLength) * 1000) / 1000 : null;
  const previewIds = candidate?.segment_ids.join("|") ?? "";
  useEffect(() => { if (candidate?.route_portions) callbacks.current.onPreview([...candidate.segment_ids], { portA, portB }, candidate.route_portions); else if (candidate) callbacks.current.onPreview([...candidate.segment_ids], { portA, portB }); else callbacks.current.onPreview([]); }, [previewIds, requestKey, candidateId]);
  const portOptions = useMemo(() => {
    const devices = new Map(payload.devices.map(device => [device.id, device]));
    const racks = new Map(payload.racks.map(rack => [rack.id, rack]));
    return allAvailable.map(port => {
    const device = devices.get(port.device_id);
    const rack = device?.rack_id ? racks.get(device.rack_id) : undefined;
    return { value: port.id, label: `${rack?.name ?? "未入柜"} / ${device?.name ?? "设备"} / ${port.identifier} · ${port.connector_type} · ${port.front_or_rear === "rear" ? "后侧" : "前侧"}` };
  });
  }, [allAvailable, payload]);
  const segmentOptions = payload.pathways.flatMap(pathway => pathway.segments.map(segment => ({ value: segment.id, label: `${pathway.identifier} / ${segment.name} · ${segment.length_m} m`, pathwayId: pathway.id })));
  const moveSegment = (index: number, direction: -1 | 1) => setCustomSegments(previous => { const next = [...previous]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; return next; });
  const confirm = async (values: { identifier: string; construction: string; color?: string; reserve_length_m?: number }) => {
    if (!projectId || !candidate || previewing || saveNeedsPreview || savingRef.current) return;
    const reserve = values.reserve_length_m ?? 0;
    if (!Number.isFinite(reserve) || reserve < 0) { setSaveError("预留长度必须是非负的有限数值。"); return; }
    const totalLength = Math.round((candidate.length_m + reserve) * 1000) / 1000;
    savingRef.current = true; setSaving(true); callbacks.current.onBusyChange?.(true); setSaveError("");
    let result: { id: string } | undefined;
    try {
      result = await api.request<{ id: string }>("/scene/cables", { method: "POST", body: JSON.stringify({
        identifier: values.identifier.trim(), media_type: media, construction: values.construction,
        port_a_id: portA, port_b_id: portB, project_id: projectId,
        ...(candidate.route_portions ? { route_portions: candidate.route_portions.map(({ segment_id, start_offset_m, end_offset_m, geometry_hash }) => ({ segment_id, start_offset_m, end_offset_m, geometry_hash })) } : {}),
        ...(values.color?.trim() ? { color: values.color.trim() } : {}),
        ...(Number.isFinite(totalLength) && totalLength > 0 ? { length_m: totalLength } : {}), route_segment_ids: [...candidate.segment_ids],
      }) });
    } catch (reason) { if (alive.current) { setSaveError(creationError(reason)); setSaveNeedsPreview(true); } }
    finally { savingRef.current = false; if (alive.current) { setSaving(false); callbacks.current.onBusyChange?.(false); } }
    if (result && alive.current) { callbacks.current.onCreated({ kind: "cable", id: result.id }); callbacks.current.onClose(); }
  };
  return <aside className="connection-workbench" aria-label="端口接线工作区">
    <header><div><h2>端口接线</h2><p>选择端口 → 检查走线 → 确认保存</p></div><Button type="text" icon={<CloseOutlined />} aria-label="关闭接线工作区" disabled={saving} onClick={onClose} /></header>
    <Form form={form} layout="vertical" initialValues={{ construction: "patch_cord", reserve_length_m: 0 }} disabled={saving} onFinish={confirm}>
      <p className="connection-help">点击三维设备逐端选择接口，也可直接拖接端口或在下面搜索。检查路线后确认保存。</p>
      {!context.projectId && <ProjectPicker getContext={getContext} value={projectId} onChange={setProjectId} disabled={saving} />}
      <Form.Item label="线缆介质" required><Select aria-label="线缆介质" value={media} options={mediaOptions} onChange={value => { setMedia(value); setPortA(""); setPortB(""); }} /></Form.Item>
      <Form.Item label="A 端端口" required><Select aria-label="A 端端口" showSearch optionFilterProp="label" value={portA || undefined} allowClear placeholder="搜索机柜 / 设备 / 端口" options={portOptions.filter(option => option.value !== portB)} onChange={value => setPortA(value ?? "")} notFoundContent="没有匹配介质的空闲端口" /></Form.Item>
      <Form.Item label="B 端端口" required><Select aria-label="B 端端口" showSearch optionFilterProp="label" value={portB || undefined} allowClear placeholder="搜索机柜 / 设备 / 端口" options={portOptions.filter(option => option.value !== portA)} onChange={value => setPortB(value ?? "")} notFoundContent="没有匹配介质的空闲端口" /></Form.Item>
      {(portA && !availableIds.has(portA) || portB && !availableIds.has(portB) || portA && portA === portB) && <Alert type="warning" showIcon title="端口组合不可用" description="请选择两个不同、介质匹配且未被占用的端口。" />}
      <Form.Item label="避开的线槽 / 桥架"><Select aria-label="避开的线槽" mode="multiple" showSearch optionFilterProp="label" value={excluded} options={payload.pathways.map(path => ({ value: path.id, label: `${path.identifier} · ${path.name}` }))} onChange={setExcluded} placeholder="可排除暂不可用的线槽" /></Form.Item>
      <div className="connection-route-heading"><h3>走线方案</h3><Button size="small" icon={<ReloadOutlined />} disabled={!endpointsValid || saving || previewing} onClick={() => { if (manual) setManualRequestKey(requestKey); setRevision(value => value + 1); }}>{manual ? "校验当前走线" : "重新计算"}</Button></div>
      <Checkbox checked={manual} disabled={saving} onChange={event => { setManual(event.target.checked); if (event.target.checked) setCustomSegments(candidate?.segment_ids ?? []); }}>手动调整线槽段顺序</Checkbox>
      {manual && <div className="connection-custom-route"><ol>{customSegments.map((id, index) => <li key={id}><span>{segmentOptions.find(option => option.value === id)?.label ?? "未加载的路径段"}</span><div><Button aria-label={`上移路径段 ${index + 1}`} icon={<ArrowUpOutlined />} size="small" disabled={index === 0 || saving} onClick={() => moveSegment(index, -1)} /><Button aria-label={`下移路径段 ${index + 1}`} icon={<ArrowDownOutlined />} size="small" disabled={index === customSegments.length - 1 || saving} onClick={() => moveSegment(index, 1)} /><Button aria-label={`移除路径段 ${index + 1}`} icon={<DeleteOutlined />} size="small" disabled={saving} onClick={() => setCustomSegments(ids => ids.filter(value => value !== id))} /></div></li>)}</ol><Select aria-label="添加线槽段" showSearch optionFilterProp="label" value={undefined} placeholder="按经过顺序添加路径段" disabled={saving || customSegments.length >= 64} options={segmentOptions.filter(option => !customSegments.includes(option.value) && !excluded.includes(option.pathwayId))} onChange={id => { if (id) setCustomSegments(ids => [...ids, id]); }} /><p className="connection-help">调整后点击“校验当前走线”。清空路径段表示不经过已记录线槽的直连，仍需校验。</p></div>}
      {previewing && <div className="connection-loading" role="status"><Spin size="small" />正在检查端口与可用走线…</div>}
      {previewError && <Alert type="error" showIcon title="走线预览失败" description={previewError} />}
      {!endpointsValid && <p className="connection-help">选择两个可用端口后自动推荐走线。</p>}
      {preview?.key === requestKey && <>
        {!preview.candidates.length && <Alert type="warning" title="没有可用的连接方案" description="请调整端口或避开的线槽后重试。" />}
        <Radio.Group className="connection-candidates" value={candidateId} onChange={event => setCandidateId(event.target.value)} disabled={saving}>{preview.candidates.map(item => <div className={`connection-candidate ${candidateId === item.id ? "is-selected" : ""}`} key={item.id}><Radio value={item.id}>{item.label} · 预计 {item.length_m.toFixed(2)} m</Radio>{item.segments.length ? <ol>{item.segments.map((segment, index) => <li key={`${segment.id}:${index}`}>{segment.pathway_identifier} / {segment.name}<small>{segment.full_length_m !== undefined ? `使用 ${segment.length_m.toFixed(2)} / 全段 ${segment.full_length_m.toFixed(2)} m` : `${segment.length_m.toFixed(2)} m`}</small>{segment.start_offset_m !== undefined && <small>接入 {formatMetres(segment.start_offset_m)} → 离开 {formatMetres(segment.end_offset_m)}（沿线槽坐标）</small>}</li>)}</ol> : <p className="connection-help">未经过已记录的线槽段。</p>}{item.warnings.map((warning, index) => <p className="connection-warning" key={index}>{warning}</p>)}</div>)}</Radio.Group>
        {preview.warnings.map((warning, index) => <p className="connection-warning" key={index}>{warning}</p>)}
      </>}
      {candidate && <section className="connection-length-summary" aria-label="当前路线长度估算">
        <h3>长度配置</h3>
        <dl><div><dt>线槽段采用长度</dt><dd>{formatMetres(routeLength)}</dd></div><div><dt>接入 / 直连估算</dt><dd>{formatMetres(accessLength)}</dd></div><div><dt>路线估算合计</dt><dd>{formatMetres(candidate.length_m)}</dd></div></dl>
        <Form.Item name="reserve_length_m" label="预留长度（m）" rules={[{ type: "number", min: 0, max: 10000, message: "请输入 0–10000 m 的预留长度" }]}><InputNumber aria-label="预留长度（m）" min={0} max={10000} step={0.5} precision={3} /></Form.Item>
        <dl className="length-final"><div><dt>将保存的登记长度</dt><dd>{formatMetres(registeredLength)}</dd></div></dl>
      </section>}
      <p className="connection-help">路线估算采用实际经过的线槽部分，加上端口到接入位置的距离估算；局部长度按几何占比折算登记值，登记值无效时采用坐标长度。保存的登记长度包含预留，尚未经现场测量。</p>
      <Form.Item name="identifier" label="线缆编号" rules={[{ required: true, whitespace: true, min: 3, message: "请输入至少 3 个字符的线缆编号" }]}><Input maxLength={180} placeholder="如 CABLE-01" /></Form.Item>
      <div className="connection-two-fields"><Form.Item name="construction" label="线缆结构" rules={[{ required: true }]}><Select options={[{ value: "patch_cord", label: "跳线" }, { value: "horizontal_cable", label: "水平布线" }, { value: "fiber_trunk", label: "光纤主干" }]} /></Form.Item><Form.Item name="color" label="颜色（可选）"><Input maxLength={80} placeholder="如蓝色" /></Form.Item></div>
      {saveError && <Alert type="error" showIcon title="连接未保存" description={`${saveError} 请重新计算或校验走线后再确认。`} />}
      <footer><Button disabled={saving} onClick={onClose}>取消</Button><Button type="primary" htmlType="submit" loading={saving} disabled={!projectId || !candidate || previewing || saveNeedsPreview || saving}>确认连接并保存</Button></footer>
    </Form>
  </aside>;
}
