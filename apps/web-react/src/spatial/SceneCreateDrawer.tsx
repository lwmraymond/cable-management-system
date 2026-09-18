import { MinusOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Drawer, Form, Input, InputNumber, Select, Spin } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import { sceneLocations, type SpatialPayload } from "./sceneData";
import { availablePorts, createSceneRequest, creationError, pathLength, type DeviceTemplate, type SceneCreateKind, type SceneCreateValues } from "./sceneCreate";
import "./sceneEditor.css";
import { hardwareTemplates } from "./hardwareTemplates";
import type { InstallPreset, HardwareKind } from "./InstallPalette";
import { suggestCreationIdentity, suggestRackSlot } from "./creationSuggestions";

export type SceneCreated = { kind: SceneCreateKind; id?: string; locationId?: string };
type Props = {
  kind: SceneCreateKind | null; payload: SpatialPayload; locationId: string; rackId?: string; preset?: InstallPreset;
  getContext: () => InfrastructureContext; onClose: () => void; onCreated: (result: SceneCreated) => void;
};
const titles: Record<SceneCreateKind, string> = { room: "新建房间", rack: "添加机柜", device: "安装设备", pathway: "绘制线槽", cable: "连接线缆" };
const required = [{ required: true, message: "请选择或填写此项" }];
const textRequired = [{ required: true, whitespace: true, message: "请填写此项" }];

export function SceneCreateDrawer(props: Props) {
  const [busy, setBusy] = useState(false);
  return <Drawer rootClassName="scene-editor" title={props.kind ? titles[props.kind] : "新建对象"} open={props.kind !== null} size="min(620px, 100vw)" destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onClose={() => { if (!busy) props.onClose(); }}>
    {props.kind && <CreationForm key={props.kind} {...props} kind={props.kind} onBusyChange={setBusy} />}
  </Drawer>;
}

function NumberField({ name, label, min, max, step = 1, integer = false }: { name: keyof SceneCreateValues; label: string; min?: number; max?: number; step?: number; integer?: boolean }) {
  return <Form.Item name={name} label={label} rules={[...required, { type: "number", min, max, ...(integer ? { validator: (_rule, value) => Number.isInteger(value) ? Promise.resolve() : Promise.reject(new Error("请输入整数")) } : {}) }]}><InputNumber min={min} max={max} step={step} precision={integer ? 0 : 3} /></Form.Item>;
}

function CreationForm({ kind, payload, locationId, rackId: preferredRackId, preset, getContext, onClose, onCreated, onBusyChange }: Props & { kind: SceneCreateKind; onBusyChange: (busy: boolean) => void }) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [form] = Form.useForm<SceneCreateValues>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [templates, setTemplates] = useState<DeviceTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(kind === "device");
  const [templatesError, setTemplatesError] = useState("");
  const [templateRefresh, setTemplateRefresh] = useState(0);
  const [slotNotice, setSlotNotice] = useState<{ type: "success" | "warning"; text: string }>();
  const pending = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (kind !== "device") return;
    const controller = new AbortController();
    setTemplatesLoading(true); setTemplatesError("");
    api.request<DeviceTemplate[]>("/device-templates", { signal: controller.signal }).then(items => { if (!controller.signal.aborted) setTemplates(items); }).catch(reason => { if (!controller.signal.aborted) setTemplatesError(creationError(reason)); }).finally(() => { if (!controller.signal.aborted) setTemplatesLoading(false); });
    return () => controller.abort();
  }, [api, kind, templateRefresh]);
  const rooms = sceneLocations(payload);
  const selectedRoom = rooms.find(room => room.id === locationId)?.id ?? rooms.find(room => room.id === payload.scope.location_id)?.id ?? rooms[0]?.id;
  const roomRacks = payload.racks.filter(rack => !selectedRoom || rack.location_id === selectedRoom);
  const preferredRack = payload.racks.find(rack => rack.id === preferredRackId && (!locationId || rack.location_id === locationId));
  const initialValues: Partial<SceneCreateValues> = {
    parent_id: locationId || payload.scope.location_id || undefined, kind: "server_room", width_m: 10, depth_m: 8, height_m: 3.6,
    location_id: selectedRoom, identifier_prefix: "RACK", name_prefix: "机柜", count: 1, columns: 1, position_x: 1, position_y: 1, rotation: 0, gap_m: 0.8,
    width_mm: 600, depth_mm: 1000, height_u: 42, rack_id: preferredRack?.id ?? roomRacks[0]?.id, start_u: 1, face: "front",
    pathway_type: "basket_tray", points: [{ x: 1, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }, { x: 4, y: 3, z: 3 }],
    media_type: "Cat6A copper", construction: "patch_cord", route_segment_ids: [],
    ...(preset?.hardware ? { template_id: `builtin:${preset.hardware}` } : {}), ...preset?.values,
  };
  const rackId = Form.useWatch("rack_id", form);
  const templateId = Form.useWatch("template_id", form);
  const points = Form.useWatch("points", form);
  const mediaType = Form.useWatch("media_type", form) ?? initialValues.media_type!;
  const deviceA = Form.useWatch("device_a_id", form), deviceB = Form.useWatch("device_b_id", form);
  const portA = Form.useWatch("port_a_id", form), portB = Form.useWatch("port_b_id", form);
  const selectedRack = payload.racks.find(rack => rack.id === rackId);
  const builtIn = templateId?.startsWith("builtin:") ? hardwareTemplates[templateId.slice(8) as HardwareKind] : undefined;
  const selectedTemplate = builtIn ?? templates.find(template => template.id === templateId);
  let lengthLabel = "请填写完整坐标";
  try { lengthLabel = `${pathLength(points ?? initialValues.points!)} m`; } catch { /* Per-field validation explains incomplete points. */ }
  const locationOptions = rooms.map(room => ({ value: room.id, label: `${room.name} · ${room.identifier}` }));
  const fillIdentity = () => {
    if (kind !== "room" && kind !== "device" && kind !== "pathway") return;
    const suggestion = suggestCreationIdentity(kind, payload);
    form.setFieldsValue({ identifier: suggestion.identifier, ...(!form.getFieldValue("name")?.trim() ? { name: suggestion.name } : {}) });
  };
  const fillRackSlot = () => {
    const suggestion = suggestRackSlot(selectedRack, payload.devices, selectedTemplate?.rack_units, form.getFieldValue("face"), !payload.truncated.includes("devices"));
    if ("reason" in suggestion) { setSlotNotice({ type: "warning", text: suggestion.reason }); return; }
    form.setFieldValue("start_u", suggestion.startU);
    setSlotNotice({ type: "success", text: `已填入 U${suggestion.startU}${suggestion.endU > suggestion.startU ? `–U${suggestion.endU}` : ""}；保存时会再次校验占用及预留。` });
  };
  const names = <>{kind !== "cable" && <div className="scene-editor-suggestion"><Button htmlType="button" disabled={busy} onClick={fillIdentity}>填写建议编号</Button><p>根据当前可见同类对象建议编号；仅补充空白名称。保存时再次校验，不保证全局唯一。</p></div>}<Form.Item name="identifier" label={kind === "cable" ? "线缆编号" : "对象编号"} rules={[...textRequired, { min: 3, message: "编号至少 3 个字符" }]}><Input maxLength={180} placeholder={kind === "room" ? "如 ROOM-01" : kind === "pathway" ? "如 TRAY-01" : kind === "device" ? "如 SW-01" : "如 CABLE-01"} /></Form.Item>{kind !== "cable" && <Form.Item name="name" label="名称" rules={textRequired}><Input maxLength={180} /></Form.Item>}</>;
  const roomPicker = <Form.Item name="location_id" label="所属房间" rules={required}><Select showSearch optionFilterProp="label" options={locationOptions} placeholder="选择房间" notFoundContent="当前范围没有房间，请先新建房间" /></Form.Item>;
  const submit = async (values: SceneCreateValues) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError("");
    let result: { id?: string; location_id?: string; racks?: { id: string; location_id: string }[] } | undefined;
    try {
      if (kind === "cable" && (!availablePorts(payload, values.device_a_id, values.media_type, values.port_b_id).some(port => port.id === values.port_a_id) || !availablePorts(payload, values.device_b_id, values.media_type, values.port_a_id).some(port => port.id === values.port_b_id))) throw new Error("端口不可用或与介质不兼容，请重新选择 A/B 端口。");
      if (kind === "device" && values.template_id?.startsWith("builtin:")) {
        const chosen = hardwareTemplates[values.template_id.slice(8) as HardwareKind];
        if (!chosen) throw new Error("设备模板无效，请重新选择。");
        let template = templates.find(item => item.manufacturer === chosen.manufacturer && item.model === chosen.model);
        if (!template) {
          const { label: _label, ...body } = chosen;
          template = await api.request<DeviceTemplate>("/device-templates", { method: "POST", body: JSON.stringify(body) });
          setTemplates(items => [...items, template!]);
        }
        values = { ...values, template_id: template.id };
      }
      const request = createSceneRequest(kind, values, getContext());
      result = await api.request(request.path, { method: "POST", body: JSON.stringify(request.body) });
    } catch (reason) { if (alive.current) setError(creationError(reason)); }
    finally { pending.current = false; if (alive.current) { setBusy(false); onBusyChange(false); } }
    if (result && alive.current) {
      onCreated({ kind, id: result.racks?.[0]?.id ?? result.id, locationId: kind === "room" ? result.id : result.racks?.[0]?.location_id ?? result.location_id ?? values.location_id ?? selectedRack?.location_id });
      onClose();
    }
  };
  return <Form<SceneCreateValues> form={form} name={`scene-create-${kind}`} layout="vertical" className="scene-editor-form" initialValues={initialValues} disabled={busy} onFinish={submit} onFinishFailed={({ errorFields }) => { if (errorFields.length) form.scrollToField(errorFields[0].name, { block: "center", focus: true }); }} onValuesChange={changed => {
    if (["rack_id", "template_id", "face", "start_u"].some(field => field in changed)) setSlotNotice(undefined);
    if ("media_type" in changed) form.setFieldsValue({ device_a_id: undefined, device_b_id: undefined, port_a_id: undefined, port_b_id: undefined });
    else { if ("device_a_id" in changed) form.setFieldValue("port_a_id", undefined); if ("device_b_id" in changed) form.setFieldValue("port_b_id", undefined); }
  }}>
    <p className="scene-editor-note">{kind === "room" ? "录入房间尺寸后，可继续布置机柜和线槽。默认在南墙居中创建入口，保存后可在房间属性中调整或添加出入口。" : kind === "rack" ? "机柜按网格一次创建，编号和名称自动添加 -01、-02 等序号。" : kind === "device" ? "选择设备模板后安装到机柜，端口会随模板一起生成。" : kind === "pathway" ? "按顺序设置折线点。X / Y 是房间地面坐标，高度从地面起算，单位均为米。" : "从现有设备选择空闲端口。提交时会再次校验端口占用和介质兼容性。"}</p>
    {preset?.warning && <Alert type="warning" title="请调整安装位置" description={preset.warning} />}
    {preset?.dropped && <Alert type="info" showIcon title="已从三维落点填写位置" description="确认编号、坐标或机柜 U 位后保存。线槽起点靠近现有端点时会自动吸附。" />}
    {error && <Alert type="error" showIcon title="创建未完成" description={error} role="alert" />}
    {kind === "room" && <>
      {names}
      <Form.Item name="parent_id" label="上级位置（可选）"><Select allowClear showSearch optionFilterProp="label" options={payload.locations.map(location => ({ value: location.id, label: `${location.name} · ${location.identifier}` }))} placeholder="不指定上级位置" /></Form.Item>
      <Form.Item name="kind" label="房间类型" rules={required}><Select options={[{ value: "server_room", label: "服务器机房" }, { value: "room", label: "普通房间" }]} /></Form.Item>
      <div className="scene-editor-grid"><NumberField name="width_m" label="房间宽度（m）" min={1} max={1000} step={0.5} /><NumberField name="depth_m" label="房间进深（m）" min={1} max={1000} step={0.5} /><NumberField name="height_m" label="房间净高（m）" min={1} max={100} step={0.1} /></div>
    </>}
    {kind === "rack" && <>
      {roomPicker}
      <div className="scene-editor-grid"><Form.Item name="identifier_prefix" label="机柜编号前缀" rules={textRequired}><Input maxLength={170} /></Form.Item><Form.Item name="name_prefix" label="机柜名称前缀" rules={textRequired}><Input maxLength={170} /></Form.Item><NumberField name="count" label="机柜数量" min={1} max={12} integer /><NumberField name="columns" label="每行机柜数量" min={1} max={6} integer /></div>
      <h3 className="scene-editor-heading">网格摆位</h3>
      <div className="scene-editor-grid"><NumberField name="position_x" label="起始中心 X（m）" min={0} step={0.1} /><NumberField name="position_y" label="起始中心 Y（m）" min={0} step={0.1} /><NumberField name="gap_m" label="机柜净间距（m）" min={0} max={20} step={0.1} /><NumberField name="rotation" label="旋转角度（°）" min={-360} max={360} step={15} /></div>
      <h3 className="scene-editor-heading">机柜规格</h3>
      <div className="scene-editor-grid"><NumberField name="width_mm" label="机柜宽度（mm）" min={500} max={3000} step={100} integer /><NumberField name="depth_mm" label="机柜深度（mm）" min={450} max={3000} step={100} integer /><NumberField name="height_u" label="机柜容量（U）" min={1} max={60} integer /></div>
      <p className="scene-editor-note">坐标表示首个机柜中心；后续机柜按宽深、旋转角和净间距排列。批量创建整体成功后才会保存。</p>
    </>}
    {kind === "device" && <>
      {names}
      <Form.Item name="rack_id" label="安装机柜" rules={required}><Select showSearch optionFilterProp="label" options={payload.racks.map(rack => ({ value: rack.id, label: `${rack.name} · ${rack.rack_identifier} · ${rack.height_u}U` }))} placeholder="选择机柜" notFoundContent="请先添加机柜" /></Form.Item>
      {templatesError && <Alert type="error" title="设备模板加载失败" description={templatesError} action={<Button onClick={() => setTemplateRefresh(value => value + 1)}>重试</Button>} />}
      <Form.Item name="template_id" label="设备模板" rules={required} extra={selectedTemplate ? `${selectedTemplate.device_type} · 占用 ${selectedTemplate.rack_units}U` : "模板决定设备尺寸和端口数量"}><Select loading={templatesLoading} showSearch optionFilterProp="label" options={[...Object.entries(hardwareTemplates).map(([key, template]) => ({ value: `builtin:${key}`, label: template.label })), ...templates.map(template => ({ value: template.id, label: `${template.manufacturer} ${template.model} · ${template.rack_units}U` }))]} placeholder="选择设备模板" notFoundContent={templatesLoading ? <Spin size="small" /> : "当前没有可用设备模板"} /></Form.Item>
      <div className="scene-editor-grid"><NumberField name="start_u" label="起始 U 位" min={1} max={selectedRack ? Math.max(1, selectedRack.height_u - (selectedTemplate?.rack_units ?? 1) + 1) : 60} integer /><Form.Item name="face" label="安装面" rules={required}><Select options={[{ value: "front", label: "前侧" }, { value: "rear", label: "后侧" }]} /></Form.Item></div>
      <div className="scene-editor-suggestion"><Button htmlType="button" disabled={busy || templatesLoading} onClick={fillRackSlot}>采用空闲 U 位</Button><p>按所选机柜、模板高度及安装面查找最小连续空位，跳过已占用与预留 U 位。</p></div>
      {slotNotice && <Alert type={slotNotice.type} showIcon title={slotNotice.text} role="status" />}
      {selectedRack && <p className="scene-editor-note">{selectedRack.name} 已有 {payload.devices.filter(device => device.rack_id === selectedRack.id).length} 台设备；起始 U 位及占用冲突会在保存时校验。</p>}
    </>}
    {kind === "pathway" && <>
      {names}{roomPicker}
      <div className="scene-editor-grid"><Form.Item name="pathway_type" label="线槽类型" rules={required}><Select options={[{ value: "basket_tray", label: "网格式桥架" }, { value: "ladder_tray", label: "梯式桥架" }, { value: "conduit", label: "导管" }, { value: "duct_bank", label: "管道组" }]} /></Form.Item><Form.Item name="capacity_area_mm2" label="可用截面积（mm²，可选）"><InputNumber min={1} precision={0} /></Form.Item></div>
      <h3 className="scene-editor-heading">折线坐标 · 长度 {lengthLabel}</h3>
      <div className="scene-editor-points"><Form.List name="points">{(fields, { add, remove }) => <>
        {fields.map((field, index) => <div className="scene-editor-point" key={field.key}><span>{index + 1}</span>{(["x", "y", "z"] as const).map(axis => <Form.Item key={axis} name={[field.name, axis]} label={axis === "z" ? "高度（m）" : `${axis.toUpperCase()}（m）`} rules={[...required, { type: "number", min: 0, message: "坐标必须为非负数" }]}><InputNumber aria-label={`点 ${index + 1} ${axis === "z" ? "高度" : axis.toUpperCase()}`} min={0} precision={3} step={0.1} /></Form.Item>)}<Button aria-label={`删除点 ${index + 1}`} icon={<MinusOutlined />} disabled={fields.length <= 2 || busy} onClick={() => remove(field.name)} /></div>)}
        <Button icon={<PlusOutlined />} disabled={fields.length >= 100 || busy} onClick={() => { const last = form.getFieldValue("points")?.at(-1); add({ x: (last?.x ?? 0) + 1, y: last?.y ?? 0, z: last?.z ?? 3 }); }}>增加坐标点</Button>
      </>}</Form.List></div>
    </>}
    {kind === "cable" && <>
      {names}
      <div className="scene-editor-grid"><Form.Item name="media_type" label="线缆介质" rules={required}><Select options={[{ value: "Cat6A copper", label: "Cat6A 铜缆" }, { value: "Cat6 copper", label: "Cat6 铜缆" }, { value: "OS2 fiber", label: "OS2 单模光纤" }, { value: "OM4 fiber", label: "OM4 多模光纤" }]} /></Form.Item><Form.Item name="construction" label="线缆结构" rules={required}><Select options={[{ value: "patch_cord", label: "跳线" }, { value: "horizontal_cable", label: "水平布线" }, { value: "fiber_trunk", label: "光纤主干" }]} /></Form.Item></div>
      {(["A", "B"] as const).map(side => {
        const selected = side === "A" ? deviceA : deviceB, other = side === "A" ? portB : portA;
        const ports = availablePorts(payload, selected, mediaType, other);
        return <section className="scene-editor-endpoint" key={side}><h3>{side} 端</h3><Form.Item name={side === "A" ? "device_a_id" : "device_b_id"} label={`${side} 端设备`} rules={required}><Select showSearch optionFilterProp="label" placeholder="选择有空闲端口的设备" options={payload.devices.filter(device => availablePorts(payload, device.id, mediaType, other).length).map(device => ({ value: device.id, label: `${device.name} · ${device.identifier}` }))} notFoundContent="没有匹配介质的空闲端口，请先安装设备" /></Form.Item><Form.Item name={side === "A" ? "port_a_id" : "port_b_id"} label={`${side} 端空闲端口`} rules={required}><Select disabled={!selected || busy} showSearch optionFilterProp="label" placeholder="选择端口" options={ports.map(port => ({ value: port.id, label: `${port.identifier} · ${port.connector_type} · ${port.front_or_rear === "rear" ? "后侧" : "前侧"}` }))} notFoundContent="该设备没有可用端口" /></Form.Item></section>;
      })}
      <Form.Item name="route_segment_ids" label="经过的线槽段（可选）" extra="按实际经过顺序逐项选择；需要调整顺序时，移除后重新选择。"><Select mode="multiple" showSearch optionFilterProp="label" placeholder="未选择时显示端口连接示意" options={payload.pathways.flatMap(pathway => pathway.segments.map(segment => ({ value: segment.id, label: `${pathway.name} / ${segment.name} · ${segment.length_m}m` })))} /></Form.Item>
      <div className="scene-editor-grid"><Form.Item name="length_m" label="记录长度（m，可选）"><InputNumber min={0.001} precision={3} /></Form.Item><Form.Item name="color" label="线缆颜色（可选）"><Input placeholder="如蓝色" maxLength={80} /></Form.Item></div>
    </>}
    <div className="scene-editor-actions"><Button disabled={busy} onClick={onClose}>取消</Button><Button type="primary" htmlType="submit" loading={busy} disabled={kind === "device" && templatesLoading}>保存{kind === "room" ? "房间" : kind === "rack" ? "机柜" : kind === "device" ? "设备" : kind === "pathway" ? "线槽" : "线缆"}</Button></div>
  </Form>;
}
