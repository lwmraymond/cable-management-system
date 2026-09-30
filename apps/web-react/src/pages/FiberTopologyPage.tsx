import { Alert, Space, Tabs, Typography } from "antd";
import type { InfrastructureContext } from "../api/context";
import { contextKey } from "./fiberUi";
import { FiberChannelBreakoutPanel } from "./FiberChannelBreakoutPanel";
import { FiberEndpointPanel } from "./FiberEndpointPanel";
import { FiberOtdrPanel } from "./FiberOtdrPanel";
import { FiberTracePanel } from "./FiberTracePanel";
import { useFiberTopologySession } from "./fiberTopologySession";

type Props = { getContext: () => InfrastructureContext };

export function FiberTopologyPage({ getContext }: Props) {
  return <FiberTopologyWorkbench key={contextKey(getContext())} getContext={getContext} />;
}

function FiberTopologyWorkbench({ getContext }: Props) {
  const session = useFiberTopologySession(getContext);
  const projectId = getContext().projectId ?? "";
  return <Space orientation="vertical" size="middle" style={{ display: "flex" }}>
    <Typography.Title level={2}>Fiber Advanced Topology</Typography.Title>
    <Alert type="info" showIcon title="统一端口/纤芯端点占用、Pair/Channel、Breakout、OTDR 与铜缆/光纤通用 Trace。所有写入使用服务端实际项目/位置权限和乐观版本。" />
    {session.notice && <Alert type="error" showIcon title={session.notice} />}
    <Tabs items={[
      { key: "trace", label: "Generic Trace", children: <FiberTracePanel session={session} /> },
      { key: "endpoints", label: "Termination / Pair", children: <FiberEndpointPanel session={session} /> },
      { key: "channel", label: "Channel / Breakout", children: <FiberChannelBreakoutPanel session={session} initialProjectId={projectId} /> },
      { key: "otdr", label: "OTDR", children: <FiberOtdrPanel session={session} initialProjectId={projectId} /> },
    ]} />
  </Space>;
}
