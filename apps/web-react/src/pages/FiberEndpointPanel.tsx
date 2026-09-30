import { Alert, Button, Card, Input, InputNumber, Select, Space, Typography } from "antd";
import { useState } from "react";
import { resourceId } from "./fiberUi";
import type { Side } from "./fiberTopologyUi";
import type { FiberTopologySession } from "./fiberTopologySession";

type Versioned = { id: string; version: number; released?: boolean };
type PairResult = { cable_id: string; pair_count: number; pairs: { id: string; number: number }[] };

function versionBody(value?: Versioned) {
  if (!value || !Number.isSafeInteger(value.version) || value.version < 1) throw new Error("请先加载最新资源版本。");
  return { expected_version: value.version };
}

export function FiberEndpointPanel({ session }: { session: FiberTopologySession }) {
  const [strandId, setStrandId] = useState("");
  const [side, setSide] = useState<Side>("A");
  const [portId, setPortId] = useState("");
  const [loss, setLoss] = useState(0.1);
  const [termination, setTermination] = useState<Versioned>();
  const [pairCableId, setPairCableId] = useState("");
  const [pairs, setPairs] = useState<PairResult>();

  return <Space orientation="vertical" size="middle" style={{ display: "flex" }}>
    <Card title="纤芯端接与共享端口占用">
      <Space wrap>
        <Input aria-label="端接 Strand UUID" placeholder="Strand UUID" value={strandId} onChange={e => setStrandId(e.target.value)} style={{ width: 330 }} />
        <Select aria-label="端接侧" value={side} options={[{ value: "A" }, { value: "B" }]} onChange={setSide} />
        <Input aria-label="端口 UUID" placeholder="Port UUID" value={portId} onChange={e => setPortId(e.target.value)} style={{ width: 330 }} />
        <InputNumber aria-label="端接损耗" min={0} max={10} value={loss} onChange={v => setLoss(v ?? 0)} />
        <Button type="primary" disabled={session.busy || !session.canWrite} onClick={() => session.run(async () => {
          setTermination(await session.post<Versioned>("/fiber/terminations", {
            strand_id: resourceId(strandId), side, port_id: resourceId(portId),
            connection_type: "connector", loss_db: loss,
          }));
        })}>端接</Button>
        <Button danger disabled={session.busy || !session.canWrite || !termination || termination.released} onClick={() => session.run(async () => {
          setTermination(await session.post<Versioned>(`/fiber/terminations/${resourceId(termination?.id ?? "")}/release`, versionBody(termination)));
        })}>释放当前端接</Button>
      </Space>
      {termination && <Alert style={{ marginTop: 12 }} type="info" title={`Termination ${termination.id} / version ${termination.version}${termination.released ? " / released" : ""}`} />}
    </Card>
    <Card title="Copper Pair 初始化">
      <Space wrap>
        <Input aria-label="Pair Cable UUID" placeholder="Copper Cable UUID" value={pairCableId} onChange={e => setPairCableId(e.target.value)} style={{ width: 330 }} />
        <Button type="primary" disabled={session.busy || !session.canWrite} onClick={() => session.run(async () => {
          setPairs(await session.post<PairResult>(`/fiber/cables/${resourceId(pairCableId)}/pairs/provision`));
        })}>初始化 Pair</Button>
        <Button disabled={session.busy || !session.canRead} onClick={() => session.run(async () => {
          setPairs(await session.api.request<PairResult>(`/fiber/cables/${resourceId(pairCableId)}/pairs`));
        })}>加载 Pair</Button>
      </Space>
      {pairs && <Typography.Paragraph style={{ marginTop: 12 }}>{pairs.pair_count} pairs；首个 Pair UUID：{pairs.pairs[0]?.id ?? "-"}</Typography.Paragraph>}
    </Card>
  </Space>;
}
