import { ArrowRightOutlined, ApartmentOutlined, BorderOutlined, DeploymentUnitOutlined, FileSearchOutlined, InfoCircleOutlined, NodeIndexOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Skeleton } from "antd";
import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import type { InfrastructureContext } from "../api/context";
import { createApiClient } from "../api/client";
import type { DashboardData } from "../types";
import { useApiResource } from "../components/useApiResource";
import "./dashboard.css";

const numberFormat = new Intl.NumberFormat("zh-CN");
const workOrderLabels: Record<string, string> = {
  draft: "草稿", ready: "待执行", in_progress: "进行中", awaiting_test: "待测试",
  awaiting_approval: "待审批", completed: "已完成", cancelled: "已取消",
};

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}
function countValue(counts: Record<string, unknown>, key: string): number | undefined {
  const value = counts[key];
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}
function textValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}
function Count({ value, unit = "" }: { value: number | undefined; unit?: string }) {
  return <span className="dashboard-count">{value === undefined ? <><span aria-hidden="true">—</span><small>未返回</small></> : <>{numberFormat.format(value)}{unit && <small>{unit}</small>}</>}</span>;
}
function Metric({ title, value, unit, detail }: { title: string; value: number | undefined; unit: string; detail: ReactNode }) {
  return <article className="workspace-panel dashboard-metric" aria-label={title}>
    <h2>{title}</h2><Count value={value} unit={unit} /><p>{detail}</p>
  </article>;
}
function WorkflowLink({ to, title, description, icon }: { to: string; title: string; description: string; icon: ReactNode }) {
  return <Link className="dashboard-workflow-link" to={to}>
    <span className="dashboard-workflow-icon" aria-hidden="true">{icon}</span>
    <span className="dashboard-workflow-copy"><strong>{title}</strong><span>{description}</span></span>
    <ArrowRightOutlined aria-hidden="true" />
  </Link>;
}

export function DashboardPage({ getContext }: { getContext: () => InfrastructureContext }) {
  const api = createApiClient({ getContext });
  const state = useApiResource((signal?: AbortSignal) => api.request<DashboardData>("/dashboard", { signal }), [getContext]);
  const counts = record(state.data?.counts) ?? {};
  const count = (key: string) => countValue(counts, key);
  const recent = Array.isArray(state.data?.recent_work_orders) ? state.data.recent_work_orders.slice(0, 5) : undefined;
  const unknownCounts = ["buildings", "telecom_rooms", "racks", "devices", "active_cables", "open_work_orders", "failed_tests", "expiring_access"].some(key => count(key) === undefined);
  const showData = !state.loading && !state.error;

  return <div className="workspace-page dashboard-page">
    <header className="workspace-page-heading">
      <div><span className="workspace-eyebrow">基础设施管理</span><h1>工作台</h1><p>整理空间、规划连接，跟进线缆与光纤运维。</p></div>
      <div className="dashboard-heading-actions">
        <Button icon={<ReloadOutlined aria-hidden="true" />} onClick={() => void state.reload()} disabled={state.loading}>刷新数据</Button>
        <Link className="dashboard-primary-action" to="/3d">进入三维工作区<ArrowRightOutlined aria-hidden="true" /></Link>
      </div>
    </header>

    <div className="dashboard-scope-note"><InfoCircleOutlined aria-hidden="true" /><p><strong>当前账号可见资料</strong><span>空间成员可查看全空间统计；限定范围协作方只统计获授权资料。三维工作区按当前选择的范围展示。</span></p></div>

    {state.loading && <section className="workspace-panel dashboard-loading" aria-label="正在加载工作台数据" aria-busy="true"><p role="status">正在加载工作台数据…</p><Skeleton active={false} title={false} paragraph={{ rows: 3 }} /></section>}
    {state.error && <section className="workspace-panel dashboard-error" role="alert"><div><h2>工作台数据加载失败</h2><p>{state.error.message}</p><span>请检查网络或工作范围后重试，也可以继续使用下方工作入口。</span></div><Button onClick={() => void state.reload()}>重试加载</Button></section>}

    {showData && <section aria-label="资产总览">
      {unknownCounts && <p className="dashboard-missing-note" role="status">部分统计未返回，暂以“未返回”标示。</p>}
      <div className="dashboard-metrics">
        <Metric title="建筑" value={count("buildings")} unit="栋" detail={<>通信间 {count("telecom_rooms") === undefined ? "未返回" : numberFormat.format(count("telecom_rooms")!)} · TR / MDF / ER / MMR</>} />
        <Metric title="机柜" value={count("racks")} unit="台" detail="已登记机柜，包括未摆位记录" />
        <Metric title="设备" value={count("devices")} unit="台" detail="已登记设备，包括未入柜记录" />
        <Metric title="未拆除线缆" value={count("active_cables")} unit="条" detail="包括规划、安装与在用等状态" />
      </div>
    </section>}

    <div className="dashboard-workspace-grid">
      <section className="workspace-panel dashboard-workflows" aria-labelledby="dashboard-workflows-title">
        <div className="dashboard-section-heading"><h2 id="dashboard-workflows-title">常用工作流程</h2><p>选择工作入口，继续规划或核对现有连接。</p></div>
        <div className="dashboard-workflow-columns">
          <div><h3>空间与安装规划</h3>
            <WorkflowLink to="/locations" title="空间台账" description="查看建筑、楼层与房间层级" icon={<ApartmentOutlined />} />
            <WorkflowLink to="/floor-plans" title="平面图规划" description="编辑布局、查看与发布版本" icon={<BorderOutlined />} />
            <WorkflowLink to="/racks" title="机柜与设备" description="核对机柜清单与设备安装信息" icon={<DeploymentUnitOutlined />} />
          </div>
          <div><h3>线缆与光纤运维</h3>
            <WorkflowLink to="/cables" title="线缆查询" description="查找线缆、追踪连接并导出台账" icon={<FileSearchOutlined />} />
            <WorkflowLink to="/fiber" title="光纤熔接" description="管理纤芯束、熔接盘与端接" icon={<NodeIndexOutlined />} />
            <WorkflowLink to="/fiber-topology" title="光纤拓扑" description="核对通道、分支与 OTDR 记录" icon={<DeploymentUnitOutlined />} />
          </div>
        </div>
        <div className="dashboard-workflow-tip"><InfoCircleOutlined aria-hidden="true" /><span>首次规划可从三维工作区开始，内置操作教程与数据检查助手。</span></div>
      </section>

      <section className="workspace-panel dashboard-operations" aria-labelledby="dashboard-operations-title">
        <div className="dashboard-section-heading"><h2 id="dashboard-operations-title">工作与测试</h2><p>依据已登记工单、测试与授权记录。</p></div>
        {state.loading ? <p className="dashboard-state-copy">正在加载记录…</p> : state.error ? <p className="dashboard-state-copy">记录暂不可用，请重试加载。</p> : <>
          <dl className="dashboard-operation-counts">
            <div><dt>未结工单<small>不含已完成与已取消</small></dt><dd><Count value={count("open_work_orders")} /></dd></div>
            <div><dt>失败测试记录<small>累计失败记录，不代表待处理数量</small></dt><dd><Count value={count("failed_tests")} /></dd></div>
            <div><dt>有效授权<small>状态为有效，未按到期时间筛选</small></dt><dd><Count value={count("expiring_access")} /></dd></div>
          </dl>
          <div className="dashboard-recent-heading"><h3>近期工单</h3><span>按创建时间 · 最多 5 条</span></div>
          {recent === undefined ? <p className="dashboard-state-copy">近期工单数据未返回。</p> : recent.length === 0 ? <p className="dashboard-state-copy">暂无工单记录。</p> : <ul className="dashboard-work-orders">{recent.map((item, index) => {
            const order = record(item) ?? {};
            const status = typeof order.status === "string" && Object.hasOwn(workOrderLabels, order.status) ? workOrderLabels[order.status] : undefined;
            return <li key={textValue(order.id, String(index))}><div><span className="dashboard-order-number">{textValue(order.number, "编号未返回")}</span><span className="dashboard-order-status">{status ?? "状态未知"}</span></div><p>{textValue(order.title, "工单标题未返回")}</p></li>;
          })}</ul>}
        </>}
      </section>
    </div>
  </div>;
}
