import { Button, Card, Form, Input, Select, Space, Typography } from "antd";
import { useState } from "react";
import { resourceId } from "./fiberUi";
import { parseBreakoutLegs, parseChannelMembers, type BreakoutMode, type ChannelMedium, type ChannelTopology } from "./fiberTopologyUi";
import type { FiberTopologySession } from "./fiberTopologySession";

type Props = { session: FiberTopologySession; initialProjectId: string };
type Result = { id: string; version: number; released?: boolean };
type ChannelForm = { projectId: string; identifier: string; name: string; medium: ChannelMedium; topology: ChannelTopology; members: string };
type BreakoutForm = { projectId: string; deviceId: string; identifier: string; name: string; mode: BreakoutMode; legs: string };
const required = [{ required: true, whitespace: true, message: "请填写此字段" }];

export function FiberChannelBreakoutPanel({ session, initialProjectId }: Props) {
  const [channelId, setChannelId] = useState("");
  const [breakoutId, setBreakoutId] = useState("");
  const [channel, setChannel] = useState<Result>();
  const [breakout, setBreakout] = useState<Result>();
  return <Space orientation="vertical" size="middle" style={{ display: "flex" }}>
    <Card title="Channel">
      <Form<ChannelForm> name="channel" layout="vertical" initialValues={{ projectId: initialProjectId, medium: "fiber", topology: "simplex" }} disabled={session.busy || !session.canWrite} onFinish={values => session.run(async () => {
        const result = await session.post<Result>("/fiber/channels", {
          project_id: resourceId(values.projectId), identifier: values.identifier.trim(), name: values.name.trim(),
          medium: values.medium, topology: values.topology, members: parseChannelMembers(values.members),
        });
        setChannel(result); setChannelId(result.id);
      })}>
        <Form.Item name="projectId" label="Channel 项目 UUID" rules={required}><Input /></Form.Item>
        <Space wrap>
          <Form.Item name="identifier" label="Channel 标识" rules={required}><Input maxLength={180} /></Form.Item>
          <Form.Item name="name" label="Channel 名称" rules={required}><Input maxLength={180} /></Form.Item>
          <Form.Item name="medium" label="介质"><Select style={{ width: 120 }} options={[{ value: "fiber" }, { value: "copper" }]} /></Form.Item>
          <Form.Item name="topology" label="拓扑"><Select style={{ width: 140 }} options={["simplex", "duplex", "quad", "bundle", "ethernet"].map(value => ({ value }))} /></Form.Item>
        </Space>
        <Form.Item name="members" label="成员（每行：fiber_strand 或 copper_pair,资源 UUID,角色）" rules={required}><Input.TextArea rows={3} /></Form.Item>
        <Button type="primary" htmlType="submit">创建 Channel</Button>
      </Form>
      <Space wrap style={{ marginTop: 16 }}>
        <Input aria-label="查询 Channel UUID" placeholder="Channel UUID" value={channelId} onChange={e => { setChannelId(e.target.value); setChannel(undefined); }} style={{ width: 330 }} />
        <Button disabled={session.busy || !session.canRead} onClick={() => session.run(async () => setChannel(await session.api.request<Result>(`/fiber/channels/${resourceId(channelId)}`)))}>加载 Channel</Button>
        <Button danger disabled={session.busy || !session.canWrite || !channel || channel.released} onClick={() => session.run(async () => {
          if (!channel) return;
          setChannel(await session.post<Result>(`/fiber/channels/${resourceId(channel.id)}/release`, { expected_version: channel.version }));
        })}>释放 Channel</Button>
      </Space>
      {channel && <Typography.Paragraph style={{ marginTop: 12 }}><pre>{JSON.stringify(channel, null, 2)}</pre></Typography.Paragraph>}
    </Card>
    <Card title="Breakout">
      <Form<BreakoutForm> name="breakout" layout="vertical" initialValues={{ projectId: initialProjectId, mode: "fanout" }} disabled={session.busy || !session.canWrite} onFinish={values => session.run(async () => {
        const result = await session.post<Result>("/fiber/breakouts", {
          project_id: resourceId(values.projectId), device_id: resourceId(values.deviceId),
          identifier: values.identifier.trim(), name: values.name.trim(), mode: values.mode, legs: parseBreakoutLegs(values.legs),
        });
        setBreakout(result); setBreakoutId(result.id);
      })}>
        <Form.Item name="projectId" label="Breakout 项目 UUID" rules={required}><Input /></Form.Item>
        <Form.Item name="deviceId" label="Breakout 设备 UUID" rules={required}><Input /></Form.Item>
        <Space wrap>
          <Form.Item name="identifier" label="Breakout 标识" rules={required}><Input maxLength={180} /></Form.Item>
          <Form.Item name="name" label="Breakout 名称" rules={required}><Input maxLength={180} /></Form.Item>
          <Form.Item name="mode" label="模式"><Select style={{ width: 140 }} options={["fanout", "fanin", "passive"].map(value => ({ value }))} /></Form.Item>
        </Space>
        <Form.Item name="legs" label="分支（每行：父纤芯 UUID,A/B,子纤芯 UUID,A/B,标签,损耗 dB）" rules={required}><Input.TextArea rows={3} /></Form.Item>
        <Button type="primary" htmlType="submit">创建 Breakout</Button>
      </Form>
      <Space wrap style={{ marginTop: 16 }}>
        <Input aria-label="查询 Breakout UUID" placeholder="Breakout UUID" value={breakoutId} onChange={e => { setBreakoutId(e.target.value); setBreakout(undefined); }} style={{ width: 330 }} />
        <Button disabled={session.busy || !session.canRead} onClick={() => session.run(async () => setBreakout(await session.api.request<Result>(`/fiber/breakouts/${resourceId(breakoutId)}`)))}>加载 Breakout</Button>
        <Button danger disabled={session.busy || !session.canWrite || !breakout || breakout.released} onClick={() => session.run(async () => {
          if (!breakout) return;
          setBreakout(await session.post<Result>(`/fiber/breakouts/${resourceId(breakout.id)}/release`, { expected_version: breakout.version }));
        })}>释放 Breakout</Button>
      </Space>
      {breakout && <Typography.Paragraph style={{ marginTop: 12 }}><pre>{JSON.stringify(breakout, null, 2)}</pre></Typography.Paragraph>}
    </Card>
  </Space>;
}
