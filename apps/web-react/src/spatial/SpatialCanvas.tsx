import { useEffect, useRef, useState, type RefObject } from "react";
import { shouldIgnoreSpatialShortcut } from "./keyboardShortcuts";
import { InfrastructureScene, type SceneData, type Selection, type ScenePlacement, type SceneMeasurementPoint } from "./render/sceneRenderer";

type Props = {
  data: SceneData; selection: Selection | null; layers: { labels: boolean; shell: boolean; pathways: boolean; dimensions?: boolean };
  onSelect: (selection: Selection) => void; onReady: (ready: boolean) => void;
  engine: RefObject<InfrastructureScene | null>;
  connectionMode?: boolean; onPortConnect?: (a: string, b: string) => void;
  onInstallDrop?: (kind: string, placement: ScenePlacement) => void;
  placementMode?: boolean; panMode?: boolean; keyboardMovementEnabled?: boolean;
  onPlace?: (placement: ScenePlacement | null) => void; onCancelTool?: () => void;
  measurementMode?: boolean; measurementPoints?: SceneMeasurementPoint[]; onMeasure?: (point: SceneMeasurementPoint | null) => void;
};
export function SpatialCanvas({ data, selection, layers, onSelect, onReady, engine, connectionMode = false, onPortConnect, onInstallDrop, placementMode = false, panMode = false, keyboardMovementEnabled = true, onPlace, onCancelTool, measurementMode = false, measurementPoints, onMeasure }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  const lastLayout = useRef("");
  const movementAllowed = useRef(keyboardMovementEnabled);
  movementAllowed.current = keyboardMovementEnabled;
  const callbacks = useRef({ onSelect, onReady, onPortConnect, onPlace, onCancelTool, onMeasure });
  useEffect(() => { callbacks.current = { onSelect, onReady, onPortConnect, onPlace, onCancelTool, onMeasure }; }, [onSelect, onReady, onPortConnect, onPlace, onCancelTool, onMeasure]);
  useEffect(() => {
    if (!canvas.current) return;
    try {
      engine.current = new InfrastructureScene(canvas.current, value => callbacks.current.onSelect(value), { isCameraMovementAllowed: () => movementAllowed.current, onPortConnect: (a, b) => callbacks.current.onPortConnect?.(a, b), onPlacement: placement => callbacks.current.onPlace?.(placement), onMeasure: point => callbacks.current.onMeasure?.(point) });
      callbacks.current.onReady(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "WebGL 初始化失败"); }
    return () => { engine.current?.dispose(); engine.current = null; callbacks.current.onReady(false); };
  }, [engine]);
  useEffect(() => {
    const layout = [data.focusRackId ?? "", ...data.rooms.map(room => room.id), ...data.racks.map(rack => `${rack.id}:${rack.position.join(":")}:${rack.rotation}`)].join("|");
    engine.current?.setData(data, { fit: layout !== lastLayout.current });
    lastLayout.current = layout;
    engine.current?.select(selection);
    engine.current?.setLayers(layers);
  }, [data, engine]); // Selection/layers have independent lightweight updates below.
  useEffect(() => { engine.current?.select(selection); }, [engine, selection]);
  useEffect(() => { engine.current?.setLayers(layers); }, [engine, layers]);
  useEffect(() => { engine.current?.setConnectionMode(connectionMode); }, [engine, connectionMode]);
  useEffect(() => { engine.current?.setPlacementMode(placementMode); }, [engine, placementMode]);
  useEffect(() => { engine.current?.setPanMode(panMode); }, [engine, panMode]);
  useEffect(() => { engine.current?.setMeasurementMode(measurementMode); }, [engine, measurementMode]);
  useEffect(() => { engine.current?.setMeasurement(measurementPoints ?? []); }, [engine, measurementPoints]);
  return <>
    <canvas ref={canvas} className="spatial-canvas" tabIndex={0} aria-describedby="spatial-camera-help" aria-keyshortcuts="W A S D Space Shift F 1 2 3" aria-label={measurementMode ? "三维图示测距。左键依次点击同一房间内的地板或对象表面，拖动旋转；Escape退出。长度依据当前模型坐标。" : placementMode ? "三维放置模式。左键单击地板或机柜选择安装位置，拖动旋转，右键平移；Escape取消工具。" : panMode ? "三维平移模式。左键或单指拖动平移；滚轮或双指缩放，双指拖动旋转；Escape取消工具。" : connectionMode ? "三维端口连接模式。点击机柜或设备选择接口，或从空闲端口拖到另一端口；Escape取消接线工具。" : "三维空间。鼠标拖动旋转，右键拖动平移，滚轮缩放；也可使用视角按钮和左侧对象列表选择。"}
      onDragOver={event => {
        if (onInstallDrop && event.dataTransfer.types.includes("application/x-cable-install")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }
      }}
      onDrop={event => {
        const raw = event.dataTransfer.getData("application/x-cable-install");
        if (!onInstallDrop || !raw || raw.length > 512) return;
        event.preventDefault();
        let kind = raw;
        try { const parsed = JSON.parse(raw) as unknown; if (typeof parsed === "string") kind = parsed; else if (parsed && typeof parsed === "object" && "kind" in parsed && typeof parsed.kind === "string") kind = parsed.kind; else return; } catch { /* Plain kind strings are supported. */ }
        const placement = engine.current?.placementAt(event.clientX, event.clientY);
        if (placement && kind.trim().length > 0 && kind.length <= 80) onInstallDrop(kind.trim(), placement);
      }}
      onKeyDown={event => {
      if (shouldIgnoreSpatialShortcut(event)) return;
      if (event.key === "Escape") { engine.current?.setConnectionMode(connectionMode); engine.current?.setPlacementMode(false); engine.current?.setPanMode(false); engine.current?.setMeasurementMode(false); callbacks.current.onCancelTool?.(); event.preventDefault(); }
      if (event.key === "+" || event.key === "=") { engine.current?.zoom(1.2); event.preventDefault(); }
      if (event.key === "-") { engine.current?.zoom(1 / 1.2); event.preventDefault(); }
      if (event.key.toLowerCase() === "f") { engine.current?.focus(); event.preventDefault(); }
      if (event.key === "1") engine.current?.setView("front");
      if (event.key === "2") engine.current?.setView("rear");
      if (event.key === "3") engine.current?.setView("top");
    }} />
    {error && <div className="spatial-canvas-message" role="alert"><strong>无法创建三维视图</strong><p>{error}</p><p>对象列表和详细数据仍可查看。请确认浏览器已启用硬件加速后刷新。</p></div>}
  </>;
}
