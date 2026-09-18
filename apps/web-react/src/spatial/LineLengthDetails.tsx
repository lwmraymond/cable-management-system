import { useMemo } from "react";
import type { SpatialPayload } from "./sceneData";
import { describePathLengths } from "./sceneLengths";
import "./lineLengthDetails.css";

export function formatMetres(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toLocaleString("zh-CN", { maximumFractionDigits: 3 })} m`;
}

export function LineLengthDetails({ payload, selection }: { payload: SpatialPayload; selection: { kind: "cable" | "pathway"; id: string } }) {
  const lengths = useMemo(() => describePathLengths(payload, selection), [payload, selection.kind, selection.id]);
  return <section className="line-length-details" aria-label="线路长度明细">
    <h3>长度与路径段</h3>
    {selection.kind === "cable" && <div className="line-length-registered"><span>登记线缆长度</span><strong>{formatMetres(lengths.recordedLengthM)}</strong><p>登记值可能来自估算或人工录入，不等同于实测。</p></div>}
    <dl className="line-length-totals">
      <div><dt>{lengths.segments.some(segment => segment.range) ? "已加载段采用长度合计" : "已加载段登记合计"}</dt><dd>{formatMetres(lengths.segmentRecordedTotalM)}</dd></div>
      <div><dt>已加载段坐标合计</dt><dd>{formatMetres(lengths.segmentGeometryTotalM)}</dd></div>
    </dl>
    <p className="line-length-coverage">{selection.kind === "cable" && payload.cables.find(cable => cable.id === selection.id)?.route_scope === "partial" ? <>当前范围已加载 {lengths.loadedSegmentCount} 个路径段；场景外还有登记路径</> : <>当前范围显示 {lengths.loadedSegmentCount} / {lengths.expectedSegmentCount} 个路径段</>}</p>
    <ol className="line-length-segments">{lengths.segments.map((segment, index) => <li key={`${segment.id}:${index}`}>
      <strong>{segment.name}</strong><small>{segment.pathwayIdentifier}</small>
      {segment.range && <p>使用范围 {formatMetres(segment.range.startM)} → {formatMetres(segment.range.endM)}（沿线槽坐标）；全段登记 {formatMetres(segment.range.fullRecordedM)}</p>}
      <dl><div><dt>{segment.range ? "采用" : "登记"}</dt><dd>{formatMetres(segment.recordedLengthM)}</dd></div><div><dt>坐标</dt><dd>{formatMetres(segment.geometryLengthM)}</dd></div></dl>
      {segment.recordedLengthM != null && segment.geometryLengthM != null && Math.abs(segment.recordedLengthM - segment.geometryLengthM) >= 0.005 && <p>登记与坐标相差 {formatMetres(Math.abs(segment.recordedLengthM - segment.geometryLengthM))}</p>}
    </li>)}</ol>
    {lengths.warnings.map((warning, index) => <p className="line-length-note" key={index}>{warning}</p>)}
  </section>;
}
