import { Button, Card, Form, Input, InputNumber, Select, Space, Typography } from "antd";
import { useState } from "react";
import { resourceId } from "./fiberUi";
import { isoTimestamp, parseOtdrEvents, type Side } from "./fiberTopologyUi";
import type { FiberTopologySession } from "./fiberTopologySession";

type RecordResult = { id: string; events: { id: string; version: number }[] };
type RecordForm = { projectId: string; cableId: string; strandId?: string; direction: Side; wavelength: number; acquiredAt: string; sourceName: string; events?: string };
type LinkForm = { eventId: string; linkedKind: "splice" | "fiber_termination" | "breakout_leg"; linkedId: string };
const required = [{ required: true, whitespace: true, message: "请填写此字段" }];

export function FiberOtdrPanel({ session, initialProjectId }: { session: FiberTopologySession; initialProjectId: string }) {
  const [recordId, setRecordId] = useState("");
  const [record, setRecord] = useState<RecordResult>();
  const [linkForm] = Form.useForm<LinkForm>();
  const showRecord = (result: RecordResult) => { setRecord(result); setRecordId(result.id); linkForm.resetFields(); };
  return <Space orientation="vertical" size="middle" style={{ display: "flex" }}>
    <Card title="OTDR 结构化测量记录">
      <Form<RecordForm> name="otdr" layout="vertical" disabled={session.busy || !session.canWrite} initialValues={{ projectId: initialProjectId, direction: "A", wavelength: 1550, acquiredAt: new Date().toISOString() }} onFinish={values => session.run(async () => {
        showRecord(await session.post<RecordResult>("/fiber/otdr-records", {
          project_id: resourceId(values.projectId), cable_id: resourceId(values.cableId),
          ...(values.strandId?.trim() ? { strand_id: resourceId(values.strandId) } : {}),
          direction: values.direction, wavelength_nm: values.wavelength, acquired_at: isoTimestamp(values.acquiredAt),
          source_name: values.sourceName.trim(), events: parseOtdrEvents(values.events ?? ""),
        }));
      })}>
        <Form.Item name="projectId" label="OTDR 项目 UUID" rules={required}><Input /></Form.Item>
        <Form.Item name="cableId" label="OTDR 线缆 UUID" rules={required}><Input /></Form.Item>
        <Form.Item name="strandId" label="OTDR 纤芯 UUID（可选）"><Input /></Form.Item>
        <Space wrap>
          <Form.Item name="direction" label="测试方向"><Select style={{ width: 100 }} options={[{ value: "A" }, { value: "B" }]} /></Form.Item>
          <Form.Item name="wavelength" label="波长 nm" rules={[{ required: true }]}><InputNumber min={600} max={1700} precision={0} /></Form.Item>
          <Form.Item name="acquiredAt" label="采集时间（含时区）" rules={required}><Input style={{ width: 290 }} /></Form.Item>
        </Space>
        <Form.Item name="sourceName" label="测量来源名称" rules={required}><Input maxLength={500} /></Form.Item>
        <Form.Item name="events" label="事件（每行：距离 m,类型,损耗 dB,反射 dB,置信度,备注）" extra="类型：launch / connector / splice / bend / reflective / end / unknown；按距离排序。"><Input.TextArea rows={4} /></Form.Item>
        <Button type="primary" htmlType="submit">保存 OTDR 记录</Button>
      </Form>
    </Card>
    <Card title="查询与关联事件">
      <Space wrap>
        <Input aria-label="查询 OTDR 记录 UUID" placeholder="OTDR Record UUID" value={recordId} onChange={e => { setRecordId(e.target.value); setRecord(undefined); linkForm.resetFields(); }} style={{ width: 330 }} />
        <Button disabled={session.busy || !session.canRead} onClick={() => session.run(async () => showRecord(await session.api.request<RecordResult>(`/fiber/otdr-records/${resourceId(recordId)}`)))}>加载 OTDR</Button>
      </Space>
      {record && <>
        <Typography.Paragraph style={{ marginTop: 12 }}><pre>{JSON.stringify(record, null, 2)}</pre></Typography.Paragraph>
        <Form<LinkForm> name="otdr-link" form={linkForm} layout="vertical" initialValues={{ linkedKind: "splice" }} disabled={session.busy || !session.canWrite} onFinish={values => session.run(async () => {
          const event = record.events.find(item => item.id === values.eventId);
          if (!event) throw new Error("请从当前记录中选择事件。");
          await session.post(`/fiber/otdr-events/${resourceId(event.id)}/link`, {
            expected_version: event.version, linked_kind: values.linkedKind, linked_id: resourceId(values.linkedId),
          });
          showRecord(await session.api.request<RecordResult>(`/fiber/otdr-records/${resourceId(record.id)}`));
        })}>
          <Form.Item name="eventId" label="关联事件" rules={required}><Select options={record.events.map(event => ({ value: event.id, label: `${event.id} / v${event.version}` }))} /></Form.Item>
          <Form.Item name="linkedKind" label="关联对象类型"><Select options={["splice", "fiber_termination", "breakout_leg"].map(value => ({ value }))} /></Form.Item>
          <Form.Item name="linkedId" label="关联对象 UUID" rules={required}><Input /></Form.Item>
          <Button htmlType="submit" type="primary">关联事件</Button>
        </Form>
      </>}
    </Card>
  </Space>;
}
