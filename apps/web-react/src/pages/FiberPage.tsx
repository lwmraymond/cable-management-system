import { Alert, Button, Card, Input, InputNumber, Select, Space, Table, Typography } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import { contextKey, expectedVersion, fiberError, resourceId } from "./fiberUi";
import type { Bundle, Cassette, Side, Slot, Trace } from "./fiberUi";

type Props = { getContext: () => InfrastructureContext };
type CassetteItem = { id: string; name: string; slot_count: number };

// A scope switch unmounts all loaded records and in-flight response handlers.
export function FiberPage({ getContext }: Props) {
  return <FiberWorkbench key={contextKey(getContext())} getContext={getContext} />;
}

function FiberWorkbench({ getContext }: Props) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const alive = useRef(true);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [cableId, setCableId] = useState("");
  const [bundleName, setBundleName] = useState("Fiber bundle");
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [projectId, setProjectId] = useState(getContext().projectId ?? "");
  const [cassetteName, setCassetteName] = useState("Splice cassette");
  const [slotCount, setSlotCount] = useState(24);
  const [cassetteId, setCassetteId] = useState("");
  const [cassettes, setCassettes] = useState<CassetteItem[]>([]);
  const [cassette, setCassette] = useState<Cassette>();
  const [left, setLeft] = useState<string>();
  const [right, setRight] = useState<string>();
  const [leftSide, setLeftSide] = useState<Side>("B");
  const [rightSide, setRightSide] = useState<Side>("A");
  const [loss, setLoss] = useState(0.1);
  const [traceId, setTraceId] = useState<string>();
  const [entrySide, setEntrySide] = useState<Side>("A");
  const [hops, setHops] = useState(64);
  const [trace, setTrace] = useState<Trace>();
  const canRead = permissions.includes("*") || permissions.includes("fiber:read");
  const canWrite = permissions.includes("*") || permissions.includes("fiber:write");

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    api.request<{ principal: { permissions: string[] } }>("/tenants/current")
      .then(result => { if (!cancelled) setPermissions(result.principal.permissions); })
      .catch(error => { if (!cancelled) setNotice(fiberError(error).message); });
    return () => { cancelled = true; alive.current = false; };
  }, [api]);

  async function run(operation: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setNotice("");
    try { await operation(); }
    catch (error) {
      if (alive.current) {
        const result = fiberError(error); setNotice(result.message);
        if (result.conflict) setConflict(true);
      }
    } finally {
      pending.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const post = <T,>(path: string, body: unknown) => api.request<T>(path, {
    method: "POST", body: JSON.stringify(body),
  });
  function rememberBundle(bundle: Bundle) {
    if (alive.current) setBundles(previous => [...previous.filter(b => b.id !== bundle.id), bundle]);
  }
  async function reloadCassette(id = cassetteId) {
    const result = await api.request<Cassette>(`/fiber/cassettes/${resourceId(id)}`);
    if (alive.current) { setCassette(result); setCassetteId(result.id); setConflict(false); }
  }
  async function mutateSlot(slot: Slot, release: boolean) {
    if (!cassette || conflict) return;
    const loadedId = cassette.id;
    const body = release ? expectedVersion(slot) : {
      ...expectedVersion(slot), left_strand_id: resourceId(left ?? ""), left_side: leftSide,
      right_strand_id: resourceId(right ?? ""), right_side: rightSide, loss_db: loss,
    };
    // Keep writes disabled if the server committed but the subsequent reload fails.
    setConflict(true);
    await post(`/fiber/slots/${slot.id}/${release ? "release" : "splice"}`, body);
    if (alive.current) { setTrace(undefined); await reloadCassette(loadedId); }
  }
  const strandOptions = bundles.flatMap(bundle => bundle.strands.map(strand => ({
    value: strand.id, label: `${bundle.name} / #${strand.number} / ${strand.id.slice(0, 8)}`,
  })));
  const sideOptions = [{ value: "A", label: "A" }, { value: "B", label: "B" }];

  return <Space orientation="vertical" size="middle" style={{ display: "flex" }}>
    <Typography.Title level={2}>Fiber / 熔接槽位</Typography.Title>
    <Alert type="info" title="此页管理纤芯束、Cassette 与熔接槽位；端口端接、Pair/Channel、Breakout、OTDR 和通用 Trace 已迁移到 Fiber Topology 页面。" />
    {notice && <Alert type="error" title={notice} showIcon />}
    {conflict && <Alert type="warning" title="写入已锁定。请重新加载目标 Cassette，核对最新槽位版本后再操作。" />}
    <Card title="1. 初始化或读取光缆纤芯">
      <Space wrap>
        <Input aria-label="光缆 UUID" placeholder="Cable UUID" value={cableId} onChange={e => setCableId(e.target.value)} style={{ width: 330 }} />
        <Input aria-label="纤芯束名称" maxLength={180} value={bundleName} onChange={e => setBundleName(e.target.value)} />
        <Button disabled={busy || !canRead} onClick={() => void run(async () => {
          rememberBundle(await api.request<Bundle>(`/fiber/cables/${resourceId(cableId)}/bundle`));
        })}>读取纤芯</Button>
        <Button disabled={busy || !canWrite} onClick={() => void run(async () => {
          rememberBundle(await post<Bundle>("/fiber/bundles", { cable_id: resourceId(cableId), name: bundleName }));
        })}>初始化</Button>
      </Space>
      <Typography.Paragraph type="secondary">纤芯数量取自光缆的 strand_count（1–576）。可依次读取多条光缆；重复初始化返回 409。</Typography.Paragraph>
      <Table rowKey="id" size="small" dataSource={bundles} columns={[
        { title: "名称", dataIndex: "name" }, { title: "光缆", dataIndex: "cable_id" },
        { title: "纤芯数", dataIndex: "strand_count" },
      ]} pagination={{ pageSize: 5 }} />
    </Card>
    <Card title="2. Cassette 与槽位">
      <Space wrap>
        <Input aria-label="设备 UUID" placeholder="Device UUID" value={deviceId} onChange={e => setDeviceId(e.target.value)} style={{ width: 330 }} />
        <Input aria-label="项目 UUID" placeholder="Project UUID" value={projectId} onChange={e => setProjectId(e.target.value)} style={{ width: 330 }} />
        <Input aria-label="Cassette 名称" value={cassetteName} maxLength={180} onChange={e => setCassetteName(e.target.value)} />
        <InputNumber aria-label="槽位数量" min={1} max={288} precision={0} value={slotCount} onChange={v => setSlotCount(v ?? 1)} />
        <Button disabled={busy || !canWrite} onClick={() => void run(async () => {
          const result = await post<Cassette>("/fiber/cassettes", { device_id: resourceId(deviceId),
            project_id: resourceId(projectId), name: cassetteName, slot_count: slotCount });
          if (alive.current) { setCassette(result); setCassetteId(result.id); setConflict(false); }
        })}>创建 Cassette</Button>
        <Button disabled={busy || !canRead} onClick={() => void run(async () => {
          const result = await api.request<{ items: CassetteItem[]; truncated: boolean }>(
            `/fiber/devices/${resourceId(deviceId)}/cassettes?project_id=${resourceId(projectId)}`);
          if (alive.current) { setCassettes(result.items); if (result.truncated) setNotice("列表超过 100 项，未显示全部 Cassette。"); }
        })}>列出 Cassette</Button>
      </Space>
      <Space wrap style={{ marginTop: 12 }}>
        <Select aria-label="选择 Cassette" placeholder="已查询的 Cassette" style={{ width: 260 }}
          value={cassettes.some(c => c.id === cassetteId) ? cassetteId : undefined}
          options={cassettes.map(c => ({ value: c.id, label: c.name }))} onChange={setCassetteId} />
        <Input aria-label="Cassette UUID" placeholder="Cassette UUID" style={{ width: 330 }} value={cassetteId} onChange={e => setCassetteId(e.target.value)} />
        <Button disabled={busy || !canRead} onClick={() => void run(() => reloadCassette())}>重新加载槽位</Button>
      </Space>
      <Typography.Paragraph>已加载：{cassette?.name ?? "无"}。下面的写操作始终针对已加载的槽位，而非未加载的输入 ID。</Typography.Paragraph>
      <Space wrap>
        <Select aria-label="左纤芯" placeholder="左纤芯" style={{ width: 290 }} value={left} options={strandOptions} onChange={setLeft} />
        <Select aria-label="左端" value={leftSide} options={sideOptions} onChange={setLeftSide} />
        <Select aria-label="右纤芯" placeholder="右纤芯" style={{ width: 290 }} value={right} options={strandOptions} onChange={setRight} />
        <Select aria-label="右端" value={rightSide} options={sideOptions} onChange={setRightSide} />
        <InputNumber aria-label="熔接损耗 dB" min={0} max={10} step={0.01} value={loss} onChange={v => setLoss(v ?? 0)} />
        <Typography.Text>dB</Typography.Text>
      </Space>
      <Table<Slot> rowKey="id" size="small" dataSource={cassette?.slots ?? []} pagination={{ pageSize: 12 }} columns={[
        { title: "槽位", dataIndex: "number" }, { title: "版本", dataIndex: "version" },
        { title: "熔接记录", dataIndex: "splice_id", render: value => value ?? "空闲" },
        { title: "损耗 dB", dataIndex: "loss_db" },
        { title: "操作", render: (_value, slot) => <Button danger={Boolean(slot.splice_id)}
          disabled={busy || !canWrite || conflict || (!slot.splice_id && (!left || !right || left === right))}
          onClick={() => void run(() => mutateSlot(slot, Boolean(slot.splice_id)))}>
          {slot.splice_id ? "释放熔接" : "熔接所选端点"}</Button> },
      ]} />
    </Card>
    <Card title="3. 有界单纤芯追踪">
      <Space wrap>
        <Select aria-label="追踪纤芯" placeholder="起始纤芯" style={{ width: 320 }} value={traceId} options={strandOptions} onChange={setTraceId} />
        <Select aria-label="追踪入端" value={entrySide} options={sideOptions} onChange={setEntrySide} />
        <InputNumber aria-label="最大纤芯跳数" value={hops} min={1} max={256} precision={0} onChange={v => setHops(v ?? 64)} />
        <Button disabled={busy || !canRead || !traceId} onClick={() => void run(async () => {
          setTrace(undefined);
          const result = await api.request<Trace>(`/fiber/strands/${resourceId(traceId ?? "")}/trace?entry_side=${entrySide}&max_hops=${hops}`);
          if (alive.current) setTrace(result);
        })}>追踪</Button>
      </Space>
      {trace && <>
        <Alert style={{ marginTop: 12 }} type={trace.cycle || trace.truncated ? "warning" : "success"}
          title={`结束原因：${trace.termination}；已遍历熔接损耗合计：${trace.total_splice_loss_db} dB（不含连接器和光纤衰减）`} />
        <Table rowKey="sequence" size="small" pagination={{ pageSize: 12 }}
          dataSource={trace.steps.map((step, index) => ({ sequence: index + 1, kind: step.kind,
            detail: step.kind === "strand" ? `${step.cable_id} / #${step.number} / ${step.entry_side} → ${step.exit_side}`
              : `${step.cassette_id} / Slot ${step.slot_number} / ${step.loss_db} dB` }))}
          columns={[{ title: "序号", dataIndex: "sequence" }, { title: "类型", dataIndex: "kind" }, { title: "路径", dataIndex: "detail" }]} />
      </>}
    </Card>
  </Space>;
}
