import { DownOutlined } from "@ant-design/icons";
import { Alert, Button, Collapse, Drawer, Form, Input, Select, Space } from "antd";
import { useState } from "react";
import { normalizeContext, type InfrastructureContext } from "../api/context";
import type { LocationRecord } from "../types";

type Props = { allowIdentityEdit?: boolean; value: InfrastructureContext; onChange: (value: InfrastructureContext) => void; locations?: LocationRecord[]; loading?: boolean; error?: Error; onReload?: () => void; triggerLabel?: string };
export function TenantContextEditor({ allowIdentityEdit = true, value, onChange, locations = [], loading, error, onReload, triggerLabel = "选择工作范围" }: Props) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<InfrastructureContext>();
  const [advancedKeys, setAdvancedKeys] = useState<string[]>([]);
  const selectedLocationId = Form.useWatch("locationId", form);
  const options = locations.map(item => ({ value: item.id, label: `${item.name} · ${item.identifier}` }));
  if (value.locationId && !locations.some(item => item.id === value.locationId)) options.unshift({ value: value.locationId, label: `未载入的位置 · ${value.locationId}` });
  const uuidRule = { pattern: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, message: "请输入完整的 UUID" };
  return <>
    <Button className="application-scope-button" aria-label={`工作范围：${triggerLabel}`} title={triggerLabel} onClick={() => { form.setFieldsValue({ tenantId: value.tenantId, actorId: value.actorId, projectId: value.projectId, locationId: value.locationId }); setOpen(true); }}><span>{triggerLabel}</span><DownOutlined /></Button>
    <Drawer rootClassName="context-editor" title="工作范围" open={open} onClose={() => setOpen(false)} size="min(460px, 100vw)" closable={{ "aria-label": "关闭工作范围" }}>
      <p className="context-editor-intro">选择接下来查看和操作的位置。切换范围不会移动或修改设施记录。</p>
      <Form form={form} layout="vertical" initialValues={value} onFinish={next => { onChange(normalizeContext(allowIdentityEdit ? next : { ...next, tenantId: value.tenantId, actorId: value.actorId })); setOpen(false); }} onFinishFailed={({ errorFields }) => { if (!errorFields.length) return; if (errorFields[0].name[0] !== "locationId") setAdvancedKeys(["advanced"]); requestAnimationFrame(() => form.scrollToField(errorFields[0].name, { focus: true })); }}>
        {error && <Alert type="warning" showIcon title="位置列表未能加载" description="可重试读取，或在高级设置中填写位置 ID。" action={onReload && <Button onClick={onReload}>重试</Button>} />}
        <Form.Item name="locationId" label="工作位置" rules={[uuidRule]} extra="清空后使用当前账户和项目允许的可见范围。"><Select aria-label="工作位置" allowClear showSearch optionFilterProp="label" options={options} loading={loading} placeholder="搜索位置名称或编号" notFoundContent={loading ? "正在读取位置…" : "没有可选位置"} /></Form.Item>
        <Collapse ghost className="context-editor-advanced" activeKey={advancedKeys} onChange={keys => setAdvancedKeys(typeof keys === "string" ? [keys] : keys)} items={[{ key: "advanced", label: allowIdentityEdit ? "高级设置 · 租户与项目" : "高级设置 · 项目与位置", forceRender: true, children: <>
          <p>{allowIdentityEdit ? "用于配置工作身份与项目。" : "工作空间与账号由登录身份决定，可在右上角切换空间。"}此处只选择请求范围，实际访问权限由服务器校验。</p>
          <Form.Item hidden={!allowIdentityEdit} name="tenantId" label="租户 ID" rules={[{ required: true, message: "请填写租户 ID" }, uuidRule]}><Input autoComplete="off" /></Form.Item>
          <Form.Item name="projectId" label="项目 ID" rules={[uuidRule]}><Input autoComplete="off" placeholder="可选" /></Form.Item>
          <Form.Item hidden={!allowIdentityEdit} name="actorId" label="演示用户 ID" rules={[uuidRule]} extra="仅演示身份使用；正式登录由账户认证决定。"><Input autoComplete="off" placeholder="可选" /></Form.Item>
          <Form.Item label="位置 ID" htmlFor="context-location-id"><Input id="context-location-id" value={selectedLocationId ?? ""} onChange={event => form.setFieldValue("locationId", event.target.value.trim() || undefined)} autoComplete="off" placeholder="可选，可手动填写未载入的位置" /></Form.Item>
        </> }]} />
        <div className="context-editor-actions"><Space><Button type="primary" htmlType="submit">应用范围</Button><Button onClick={() => setOpen(false)}>取消</Button></Space></div>
      </Form>
    </Drawer>
  </>;
}
