import { Alert, Button, Card, Drawer, Space, Table, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, NodeIndexOutlined, ReloadOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import type { CableRecord, PageResult, TraceResult } from "../types";
import { useApiResource } from "../components/useApiResource";
import { CableLifecycleDialog } from "../cables/CableLifecycleDialog";
import { contextKey } from "./fiberUi";
import { AsyncState } from "./AsyncState";

export function CablesPage(props: { getContext: () => InfrastructureContext }) {
  return <CableInventory key={contextKey(props.getContext())} {...props} />;
}
function CableInventory({ getContext }: { getContext: () => InfrastructureContext }) {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const [traceCable, setTraceCable] = useState<CableRecord>();
  const [lifecycleCable, setLifecycleCable] = useState<CableRecord>();
  const [exportError, setExportError] = useState(""), [exporting, setExporting] = useState(false);
  const mounted = useRef(true), exportPending = useRef(false);
  const scope = contextKey(getContext());
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const state = useApiResource(async signal => {
    const result = await api.request<CableRecord[] | PageResult<CableRecord>>("/cables", { signal });
    return Array.isArray(result) ? result : result.items;
  }, [scope]);
  const trace = useApiResource(signal => traceCable ? api.request<TraceResult>(`/cables/${traceCable.id}/trace`, { signal }) : Promise.resolve({ complete: false }), [scope, traceCable?.id]);
  const exportSchedule = async () => {
    if (exportPending.current) return;
    exportPending.current = true; setExporting(true); setExportError("");
    try {
      const blob = await api.download("/reports/cable-schedule.csv");
      if (!mounted.current || contextKey(getContext()) !== scope) return;
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "cable-schedule.csv"; anchor.click(); URL.revokeObjectURL(url);
    } catch (caught) { if (mounted.current && contextKey(getContext()) === scope) setExportError(caught instanceof Error ? caught.message : "导出未完成，请重试。"); }
    finally { exportPending.current = false; if (mounted.current) setExporting(false); }
  };
  const stale = state.loading || Boolean(state.error);
  return <>
    <Space className="page-title" wrap><Typography.Title level={2}>Cables</Typography.Title><Button icon={<ReloadOutlined />} loading={state.loading} onClick={() => void state.reload()}>刷新线缆</Button><Button icon={<DownloadOutlined />} loading={exporting} onClick={() => void exportSchedule()}>Cable Schedule CSV</Button></Space>
    {exportError && <Alert type="error" title="导出失败" description={exportError} role="alert" />}
    <Card><AsyncState loading={state.loading} error={state.error} empty={!state.data?.length}><Table rowKey="id" size="small" dataSource={state.data} columns={[
      { title: "Identifier", dataIndex: "identifier" }, { title: "Media", dataIndex: "media_type" }, { title: "Construction", dataIndex: "construction" },
      { title: "Status", dataIndex: "installation_status", render: value => <Tag>{value ?? "unknown"}</Tag> },
      { title: "操作", render: (_, row: CableRecord) => <Space wrap><Button icon={<NodeIndexOutlined />} disabled={stale} onClick={() => setTraceCable(row)}>Trace</Button><Link to={`/3d?cable=${encodeURIComponent(row.id)}`}><Button>3D 查看</Button></Link><Button danger icon={<DeleteOutlined />} disabled={stale || row.installation_status === "removed"} aria-label={`${row.installation_status === "planned" ? "删除" : "拆除"}线缆 ${row.identifier}`} onClick={() => setLifecycleCable(row)}>{row.installation_status === "removed" ? "已拆除" : row.installation_status === "planned" ? "删除" : "拆除"}</Button></Space> },
    ]} /></AsyncState>{state.error && <Button onClick={() => void state.reload()}>重试加载线缆</Button>}</Card>
    <Drawer width={720} title={`Cable Trace — ${traceCable?.identifier ?? ""}`} open={Boolean(traceCable)} onClose={() => setTraceCable(undefined)}><AsyncState loading={trace.loading} error={trace.error} empty={!traceCable}><pre className="trace-json">{JSON.stringify(trace.data, null, 2)}</pre></AsyncState></Drawer>
    {lifecycleCable && <CableLifecycleDialog cable={lifecycleCable} getContext={getContext} onClose={() => setLifecycleCable(undefined)} onCompleted={result => { setLifecycleCable(undefined); if (traceCable?.id === result.id) setTraceCable(undefined); void state.reload(); }} />}
  </>;
}
