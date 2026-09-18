import type { SceneMeasurementPoint, SceneRoom } from "./render/sceneRenderer";
import "./measurementWorkbench.css";

export type MeasurementWorkbenchProps = {
  points: SceneMeasurementPoint[];
  room?: SceneRoom;
  active?: boolean;
  onUndo: () => void;
  onClear: () => void;
  onFinish: () => void;
  onResume?: () => void;
  onCreatePathway: () => void;
};

export function MeasurementWorkbench({ points, room, active = true, onUndo, onClear, onFinish, onResume, onCreatePathway }: MeasurementWorkbenchProps) {
  const invalidPoints = points.some(value => !value.locationId || !Array.isArray(value.point) || value.point.length !== 3 || !value.point.every(Number.isFinite));
  const crossRoom = points.some(value => value.locationId !== points[0]?.locationId);
  const tooManyPoints = points.length > 64;
  let total = 0, horizontal = 0, elevation = 0;
  const validPoints = !invalidPoints && !crossRoom && !tooManyPoints;
  if (validPoints) for (let index = 1; index < points.length; index++) {
    const before = points[index - 1].point, after = points[index].point;
    const dx = after[0] - before[0], dy = after[1] - before[1], dz = after[2] - before[2];
    total += Math.hypot(dx, dy, dz);
    horizontal += Math.hypot(dx, dz);
    elevation += Math.abs(dy);
  }
  const finiteTotals = [total, horizontal, elevation].every(Number.isFinite);
  const complete = validPoints && points.length >= 2 && finiteTotals;
  const validRoom = room && room.id === points[0]?.locationId && room.center.every(Number.isFinite) && Number.isFinite(room.width) && Number.isFinite(room.depth) && room.width > 0 && room.depth > 0;
  const origin = validRoom ? [room.center[0] - room.width / 2, room.center[1] - room.depth / 2] : undefined;
  const error = invalidPoints || !finiteTotals ? "测点坐标无效，请撤回或清空后重新取点。" : crossRoom ? "测点来自不同房间，不能计算跨房间距离。请撤回或清空。" : tooManyPoints ? "一次测距最多 64 个点，请撤回多余测点。" : "";
  const coordinate = (value: number) => Number.isFinite(value) ? value.toFixed(3) : "—";
  return <aside className="measurement-workbench" aria-label="折线测距">
    <header><div><h2>折线测距</h2><p>{room?.label ?? "在场景中选取测点"}</p></div><span>{points.length} / 64 点</span></header>
    <div className="measurement-summary" aria-live="polite">
      <p className="measurement-state">{!active ? "测距已完成" : points.length === 0 ? "点击场景取第一个点。" : points.length === 1 ? "继续点击第二个点开始测量。" : points.length === 64 ? "已达 64 点，可撤回或完成测距。" : "继续点击取点，可撤回最后一点。"}</p>
      <dl>{[{ name: "图示总长", value: total }, { name: "水平投影距离", value: horizontal }, { name: "累计高差", value: elevation }].map(metric => <div key={metric.name}><dt>{metric.name}</dt><dd><output aria-label={metric.name}>{complete ? `${metric.value.toFixed(3)} m` : "—"}</output></dd></div>)}</dl>
    </div>
    {error && <p className="measurement-error" role="alert">{error}</p>}
    {points.length > 0 && <div className="measurement-points">
      <table><caption>房间内坐标（m）</caption><thead><tr><th scope="col">点</th><th scope="col">X</th><th scope="col">Y</th><th scope="col">Z（高）</th></tr></thead><tbody>{points.slice(0, 64).map((value, index) => {
        const local = origin && value.locationId === room?.id && Array.isArray(value.point) && value.point.length === 3 && value.point.every(Number.isFinite) ? [value.point[0] - origin[0], value.point[2] - origin[1], value.point[1]] : undefined;
        return <tr key={index}><th scope="row">{index + 1}</th>{[0, 1, 2].map(axis => <td key={axis}>{local ? coordinate(local[axis]) : "—"}</td>)}</tr>;
      })}</tbody></table>
      <p>{origin ? "X / Y 为房间平面坐标，Z 为高度。" : "等待加载测点所属房间后显示本地坐标。"}</p>
    </div>}
    <div className="measurement-actions">
      <div><button type="button" disabled={!points.length} onClick={onUndo}>撤回一点</button><button type="button" disabled={!points.length} onClick={onClear}>清空</button></div>
      {active ? <button type="button" disabled={!complete} onClick={onFinish}>完成测距</button> : <button type="button" disabled={!onResume || !validPoints || points.length >= 64} onClick={onResume}>继续取点</button>}
      <button className="measurement-create" type="button" disabled={!complete || !origin} onClick={onCreatePathway}>用于创建线槽</button>
    </div>
    <p className="measurement-note">按当前模型坐标计算；临时测距不保存，跨房间不可直接测量。线槽测点可在后续参数表单中修改。</p>
  </aside>;
}
