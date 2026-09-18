import { AppstoreOutlined, BuildOutlined, DatabaseOutlined, DragOutlined, PartitionOutlined, SelectOutlined, SettingOutlined } from "@ant-design/icons";
import type { ScenePlacement } from "./render/sceneRenderer";
import type { SpatialPayload } from "./sceneData";
import type { SceneCreateKind, SceneCreateValues } from "./sceneCreate";
import "./toolPalette.css";

function CableIcon({ fiber = false }: { fiber?: boolean }) {
  return <svg width="30" height="20" viewBox="0 0 30 20" fill="none" aria-hidden="true" focusable="false">
    <path d="M4 10H26" stroke={fiber ? "#b97512" : "#2479b9"} strokeWidth="3" strokeLinecap="round" />
    {fiber ? <><circle cx="4" cy="10" r="3" fill="#b97512" /><circle cx="26" cy="10" r="3" fill="#b97512" /></> : <><rect x="1" y="6" width="6" height="8" rx="1" fill="#2479b9" /><rect x="23" y="6" width="6" height="8" rx="1" fill="#2479b9" /></>}
  </svg>;
}

export const installationItems = [
  { id: "copper", name: "铜缆", detail: "Cat6A · RJ45", icon: <CableIcon /> },
  { id: "fiber", name: "光纤", detail: "OS2 · LC / SC", icon: <CableIcon fiber /> },
  { id: "tray", name: "线槽 / Tray", detail: "布线路径", icon: <PartitionOutlined /> },
  { id: "rack", name: "机柜", detail: "42U · 可批量", icon: <DatabaseOutlined /> },
  { id: "patch_panel", name: "铜配线架", detail: "24 × RJ45", icon: <BuildOutlined /> },
  { id: "fiber_panel", name: "光纤配线架", detail: "24 × LC", icon: <BuildOutlined /> },
  { id: "switch", name: "交换机", detail: "24 × RJ45", icon: <AppstoreOutlined /> },
  { id: "server", name: "服务器", detail: "2U · 4 × RJ45", icon: <DatabaseOutlined /> },
  { id: "room", name: "房间", detail: "Server Room + 入口", icon: <AppstoreOutlined /> },
] as const;
export type InstallKind = typeof installationItems[number]["id"];
export type WorkspaceTool = "select" | "pan" | "measure" | InstallKind;
export type HardwareKind = "patch_panel" | "fiber_panel" | "switch" | "server";
export type InstallPreset = { values?: Partial<SceneCreateValues>; hardware?: HardwareKind; dropped?: boolean; warning?: string };
const toolGroups: { name: string; items: readonly InstallKind[] }[] = [
  { name: "连线", items: ["copper", "fiber"] },
  { name: "设备", items: ["patch_panel", "fiber_panel", "switch", "server"] },
  { name: "空间", items: ["room", "rack", "tray"] },
];
export type InstallPaletteProps = {
  onInstall: (kind: InstallKind) => void;
  disabled: boolean;
  activeTool?: WorkspaceTool;
  onToolChange?: (tool: WorkspaceTool) => void;
  onConfigure?: () => void;
};
export function InstallPalette({ onInstall, disabled, activeTool = "select", onToolChange, onConfigure }: InstallPaletteProps) {
  const activeItem = installationItems.find(item => item.id === activeTool);
  const connecting = activeTool === "copper" || activeTool === "fiber";
  const configurable = Boolean(activeItem) && !connecting;
  const toolName = activeTool === "select" ? "选择" : activeTool === "pan" ? "平移" : activeTool === "measure" ? "测距" : activeItem?.name;
  return <section className="tool-palette" aria-label="工具箱">
    <div className="tool-palette-heading"><h2>工具箱</h2><span>点击或拖放</span></div>
    <div className="tool-palette-navigation" role="group" aria-label="视图工具">
      <button type="button" disabled={disabled} aria-label="选择工具" aria-pressed={activeTool === "select"} onClick={() => onToolChange?.("select")}><SelectOutlined aria-hidden="true" />选择</button>
      <button type="button" disabled={disabled} aria-label="平移工具" aria-pressed={activeTool === "pan"} onClick={() => onToolChange?.("pan")}><DragOutlined aria-hidden="true" />平移</button>
      <button type="button" disabled={disabled} draggable={false} aria-label="测距工具" aria-pressed={activeTool === "measure"} style={{ gridColumn: "1 / -1" }} onClick={() => onToolChange?.("measure")}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false"><rect x="1" y="4" width="14" height="8" rx="1" stroke="currentColor" strokeWidth="1.4" /><path d="M4 4V8M7 4V7M10 4V8M13 4V7" stroke="currentColor" strokeWidth="1.4" /></svg>测距</button>
    </div>
    <div className="tool-palette-groups">{toolGroups.map(group => <section className="tool-palette-group" key={group.name} aria-label={group.name}>
      <h3>{group.name}</h3>
      <div className="tool-palette-grid">{group.items.map(id => {
        const item = installationItems.find(candidate => candidate.id === id)!;
        return <button key={item.id} type="button" disabled={disabled} draggable={!disabled} aria-pressed={activeTool === item.id} onDragStart={event => {
          if (disabled) { event.preventDefault(); return; }
          event.dataTransfer.setData("application/x-cable-install", item.id);
          event.dataTransfer.effectAllowed = "copy";
        }} onClick={() => onInstall(item.id)} aria-label={`安装${item.name}`}>
          <span className={`tool-palette-icon ${item.id}`} aria-hidden="true">{item.icon}</span>
          <strong>{item.name}</strong><small>{item.detail}</small>
        </button>;
      })}</div>
    </section>)}</div>
    <div className="tool-palette-status" role="status" aria-live="polite">
      <span>当前工具 <b>{toolName}</b></span>
      <p>{activeTool === "measure" ? "点击场景连续取点，可撤回最后一点；Esc 退出测距。" : connecting ? "点击起点、终点或拖线；调整走线后确认保存。" : activeTool === "pan" ? "在场景中按住左键拖动平移；Esc 退出。" : activeTool === "select" ? "点击物件查看属性。单击工具，再在场景中点击放置；Esc 退出。" : "在场景中点击放置，也可拖到房间或机柜；Esc 退出。"}</p>
      {connecting && <small>绿色端口可用 · 灰色端口不可用</small>}
    </div>
    {configurable && <button className="tool-palette-configure" type="button" disabled={disabled || !onConfigure} onClick={() => onConfigure?.()} aria-label={`填写${toolName}参数`}><SettingOutlined aria-hidden="true" />填写参数</button>}
  </section>;
}
export function installationPreset(kind: InstallKind, placement: ScenePlacement | undefined, payload: SpatialPayload): { kind: SceneCreateKind; preset: InstallPreset } {
  const values: Partial<SceneCreateValues> = {};
  let warning: string | undefined;
  if (placement) {
    values.location_id = placement.locationId;
    values.position_x = Math.round(placement.positionX * 100) / 100;
    values.position_y = Math.round(placement.positionY * 100) / 100;
    if (placement.rackId) {
      values.rack_id = placement.rackId;
      const rack = payload.racks.find(item => item.id === placement.rackId);
      const units = kind === "server" ? 2 : 1;
      const occupied = new Set(rack?.reserved_units ?? []);
      payload.devices.filter(device => device.rack_id === placement.rackId && device.face === "front").forEach(device => { for (let u = device.start_u; u < device.start_u + device.rack_units; u++) occupied.add(u); });
      const available = Array.from({ length: Math.max(0, (rack?.height_u ?? 42) - units + 1) }, (_, i) => i + 1).filter(u => Array.from({ length: units }, (_, i) => u + i).every(slot => !occupied.has(slot)));
      values.start_u = available.sort((a, b) => Math.abs(a - (placement.startU ?? 35)) - Math.abs(b - (placement.startU ?? 35)))[0];
      if (!available.length) warning = "该机柜没有足够的连续空闲 U 位，请选择其他机柜或调整安装面。";
    }
    if (kind === "tray") {
      const room = payload.locations.find(item => item.id === placement.locationId);
      const width = Number(room?.dimensions.width_m) || 4, depth = Number(room?.dimensions.depth_m) || 4;
      let start = { x: Math.min(width, Math.max(0, values.position_x)), y: Math.min(depth, Math.max(0, values.position_y)), z: Math.max(0.1, Math.min(3, (Number(room?.dimensions.height_m) || 3.2) - 0.2)) };
      const nearby = payload.pathways.filter(pathway => pathway.location_id === placement.locationId).flatMap(pathway => pathway.segments.flatMap(segment => [segment.coordinates[0], segment.coordinates.at(-1)]).filter(point => Boolean(point))).filter(point => Math.hypot(point!.x - start.x, point!.y - start.y) <= 0.4).sort((a, b) => Math.hypot(a!.x - start.x, a!.y - start.y) - Math.hypot(b!.x - start.x, b!.y - start.y))[0];
      if (nearby) start = { x: nearby.x, y: nearby.y, z: nearby.z ?? 0 };
      values.points = [start, { ...start, x: start.x + (start.x + 1 <= width ? 1 : -Math.min(1, start.x)) }];
    }
  }
  const hardware = ["patch_panel", "fiber_panel", "switch", "server"].includes(kind) ? kind as HardwareKind : undefined;
  return { kind: hardware ? "device" : kind === "tray" ? "pathway" : kind === "fiber" || kind === "copper" ? "cable" : kind as SceneCreateKind, preset: { values, hardware, dropped: Boolean(placement), warning } };
}
