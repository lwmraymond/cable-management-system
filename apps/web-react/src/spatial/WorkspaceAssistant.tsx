import { BookOutlined, CheckCircleOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Input, Segmented, Spin } from "antd";
import { useState } from "react";
import type { GuideAction } from "./WorkspaceGuide";
import type { WorkspaceCheckResult } from "./workspaceChecks";
import "./workspaceAssistant.css";

type Issue = WorkspaceCheckResult["issues"][number];
export function WorkspaceAssistant({ result, scopeName, loadedAt, loading, stale, inspectionBlocked, onAction, onIssue, onRefresh, onGuide }: {
  result?: WorkspaceCheckResult; scopeName: string; loadedAt?: number; loading: boolean; stale?: boolean; inspectionBlocked?: boolean;
  onAction: (action: GuideAction) => void; onIssue: (issue: Issue) => void; onRefresh: () => void; onGuide: () => void;
}) {
  const [workflow, setWorkflow] = useState<"plan" | "operate">("plan");
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("all");
  const [limit, setLimit] = useState(12);
  const count = result?.checked;
  const issues = result?.issues ?? [];
  const filtered = issues.filter(issue => (severity === "all" || issue.severity === severity) && `${issue.objectLabel} ${issue.title} ${issue.detail}`.toLowerCase().includes(query.trim().toLowerCase()));
  const planning: { title: string; detail: string; action: GuideAction; value?: number }[] = [
    { title: "创建房间", detail: "尺寸、类型与出入口", action: "room", value: count?.rooms },
    { title: "放置机柜", detail: "按行批量安排机柜", action: "rack", value: count?.racks },
    { title: "安装设备", detail: "设备模板、U 位和端口", action: "device", value: count?.devices },
    { title: "建立线槽", detail: "折线坐标与布线准入", action: "pathway", value: count?.pathways },
    { title: "连接端口", detail: "推荐路线、预留与确认", action: "copper", value: count?.cables },
  ];
  return <section className="workspace-assistant" aria-label="工作助手">
    <header><h2>工作助手</h2><p>{scopeName}</p><Button icon={<BookOutlined />} onClick={onGuide} block>打开教学指南</Button></header>
    <Segmented block aria-label="工作类型" value={workflow} options={[{ value: "plan", label: "规划安装" }, { value: "operate", label: "日常运维" }]} onChange={value => setWorkflow(value as "plan" | "operate")} />
    {workflow === "plan" ? <ol className="assistant-workflow">{planning.map((step, index) => <li key={step.action}><button disabled={!result || loading || stale} onClick={() => onAction(step.action)}><span className="assistant-step-number">{index + 1}</span><span><strong>{step.title}</strong><small>{step.detail}</small><small>{step.value == null ? "等待读取资料" : `当前范围已记录 ${step.value} 项`}</small></span></button></li>)}</ol> : <div className="assistant-operations">
      <Button disabled={!result || loading || stale} onClick={() => onAction("inventory")}>查找设备与线缆</Button>
      <Button disabled={!result || loading || stale} onClick={() => onAction("fiber")}>新建光纤连接</Button>
      <Button disabled={!result || loading || stale} onClick={() => onAction("copper")}>新建铜缆连接</Button>
      <Button disabled={!result || loading || stale} onClick={() => onAction("measure")}>测距与线槽规划</Button>
      <p>先核对当前范围与现场记录，再处理下面的资料检查结果。</p>
    </div>}
    <section className="assistant-checks" aria-label="自动检查结果">
      <div className="assistant-checks-heading"><h3>自动检查</h3><Button size="small" aria-label="刷新并重新检查" disabled={loading} icon={<ReloadOutlined />} onClick={onRefresh} /></div>
      <p className="assistant-scope-note">数据更新后自动检查当前范围；不会修改记录。</p>
      {loadedAt && <p className="assistant-check-time">最近读取 {new Date(loadedAt).toLocaleTimeString("zh-CN", { hour12: false })}</p>}
      {loading && <p role="status"><Spin size="small" /> 正在读取与检查…</p>}
      {stale && <p className="assistant-warning" role="alert">刷新未成功，以下为上次读取的资料，请重试后再处理。</p>}
      {inspectionBlocked && <p className="assistant-scope-note" role="status">接线草稿已保留。请先保存或退出接线，再定位处理检查项。</p>}
      {result?.partial && <p className="assistant-warning">部分资料未完整加载，请缩小范围后核对。结果不代表全系统状态。</p>}
      {!result && !loading && <p>资料尚未读取，点击刷新重试。</p>}
      {result && <>
        <div className="assistant-check-counts" aria-label="已列出的检查项"><span>{issues.filter(issue => issue.severity === "warning").length} 项待核对</span><span>{issues.filter(issue => issue.severity === "info").length} 项资料提示</span></div>
        {issues.length > 0 && <>
          <Input aria-label="搜索检查结果" placeholder="搜索检查结果" prefix={<SearchOutlined />} value={query} onChange={event => { setQuery(event.target.value); setLimit(12); }} allowClear />
          <Segmented block aria-label="检查结果类型" value={severity} options={[{ value: "all", label: "全部" }, { value: "warning", label: "待核对" }, { value: "info", label: "提示" }]} onChange={value => { setSeverity(String(value)); setLimit(12); }} />
        </>}
        {!issues.length && <p className="assistant-no-issues"><CheckCircleOutlined /> 已加载资料未发现上述检查项的问题；仍需现场验收。</p>}
        {issues.length > 0 && !filtered.length && <p>没有匹配的检查结果。</p>}
        <ul className="assistant-issues">{filtered.slice(0, limit).map(issue => <li key={issue.id} className={`is-${issue.severity}`}><span>{issue.severity === "warning" ? "待核对" : "资料提示"}</span><h4>{issue.title}</h4><strong className="assistant-issue-object">{issue.objectLabel}</strong><p>{issue.detail}</p><Button size="small" disabled={loading || stale || inspectionBlocked} onClick={() => onIssue(issue)}>{issue.actionLabel}</Button></li>)}</ul>
        {filtered.length > limit && <Button block onClick={() => setLimit(value => value + 12)}>再显示 {Math.min(12, filtered.length - limit)} 项</Button>}
        {Boolean(result.omittedCount) && <p>还有 {result.omittedCount} 项未列出，请缩小空间范围查看。</p>}
      </>}
    </section>
    <p className="assistant-connector-note"><strong>后续设备对接</strong>Observium 等系统的设备发现与运行状态将和已确认的物理布线分别管理。当前尚未连接外部发现源。</p>
  </section>;
}
