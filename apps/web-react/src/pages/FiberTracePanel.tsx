import { Alert, Button, Card, Input, InputNumber, Space, Table, Typography } from "antd";
import { useState } from "react";
import { resourceId } from "./fiberUi";
import { traceItemText, type TopologyTrace } from "./fiberTopologyUi";
import type { FiberTopologySession } from "./fiberTopologySession";

export function FiberTracePanel({ session }: { session: FiberTopologySession }) {
  const [cableId, setCableId] = useState("");
  const [strand, setStrand] = useState<number | null>(1);
  const [pair, setPair] = useState<number | null>(null);
  const [maxNodes, setMaxNodes] = useState(500);
  const [trace, setTrace] = useState<TopologyTrace>();
  const rows = (trace?.items ?? []).map((item, index) => ({
    sequence: index + 1,
    kind: String(item.kind ?? "unknown"),
    selected: item.selected === true ? "是" : "",
    detail: traceItemText(item),
  }));

  return <Card title="通用 Cable Trace">
    <Space wrap>
      <Input aria-label="追踪 Cable UUID" placeholder="Cable UUID" value={cableId} onChange={e => setCableId(e.target.value)} style={{ width: 330 }} />
      <InputNumber aria-label="纤芯编号" placeholder="Strand" min={1} max={576} precision={0} value={strand} onChange={setStrand} />
      <InputNumber aria-label="线对编号" placeholder="Pair" min={1} max={600} precision={0} value={pair} onChange={setPair} />
      <InputNumber aria-label="最大节点" min={2} max={1000} precision={0} value={maxNodes} onChange={v => setMaxNodes(v ?? 500)} />
      <Button type="primary" disabled={session.busy || !session.canRead} onClick={() => session.run(async () => {
        if (strand !== null && pair !== null) throw new Error("纤芯编号与线对编号只能选择一个。");
        const query = new URLSearchParams({ max_nodes: String(maxNodes) });
        if (strand !== null) query.set("strand_number", String(strand));
        if (pair !== null) query.set("pair_number", String(pair));
        setTrace(await session.api.request<TopologyTrace>(`/fiber/cables/${resourceId(cableId)}/trace?${query}`));
      })}>追踪</Button>
    </Space>
    <Typography.Paragraph type="secondary">Fiber 默认选第 1 芯；Copper 选择已初始化 pair。两个编号不能同时提交。</Typography.Paragraph>
    {trace && <>
      <Alert type={trace.cycle || trace.truncated ? "warning" : "success"} showIcon title={`${trace.trace_model}；${trace.hop_count} hops；cycle=${trace.cycle}；branching=${trace.branching}；truncated=${trace.truncated}`} />
      <Table rowKey="sequence" size="small" dataSource={rows} pagination={{ pageSize: 20 }} columns={[
        { title: "#", dataIndex: "sequence", width: 70 },
        { title: "类型", dataIndex: "kind", width: 160 },
        { title: "选中", dataIndex: "selected", width: 80 },
        { title: "路径", dataIndex: "detail" },
      ]} />
      <Typography.Text type="secondary">OTDR records: {trace.otdr.length}</Typography.Text>
    </>}
  </Card>;
}
