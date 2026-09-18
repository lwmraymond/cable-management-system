import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InfrastructureScene, type SceneData, type SceneMeasurementPoint, type ScenePath, type Selection } from "./sceneRenderer";
import { validMeasurement } from "./sceneOverlays";

vi.mock("three", async importOriginal => {
  const actual = await importOriginal<typeof import("three")>();
  return { ...actual, WebGLRenderer: class {
    shadowMap = { enabled: false, type: 0 }; renderLists = { dispose: vi.fn() };
    setPixelRatio() {} setSize() {} render() {} dispose() {}
  } };
});

let canvas: HTMLCanvasElement, engine: InfrastructureScene;
let measure = vi.fn<(point: SceneMeasurementPoint | null) => void>();
let select = vi.fn<(selection: Selection) => void>();
let place = vi.fn(), connect = vi.fn();
const rooms: SceneData["rooms"] = [{ id: "room", label: "", center: [0, 0], width: 6, depth: 4, height: 3.2 }, { id: "other", label: "", center: [10, 0], width: 6, depth: 4 }];
type Internals = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; updateLabels: () => void; pickAt: (x: number, y: number) => { object: THREE.Object3D; selection: Selection; point: THREE.Vector3 } | null };
const state = () => engine as unknown as Internals;
const overlay = (name: string) => state().scene.getObjectByName(name) as THREE.Group;
const point = (x: number, y = 0, z = 0, locationId = "room"): SceneMeasurementPoint => ({ locationId, point: [x, y, z] });
function pointer(type: string, x = 400, y = 300) {
  const event = new MouseEvent(type, { clientX: x, clientY: y, button: 0, buttons: type === "pointerup" ? 0 : 1, bubbles: true, cancelable: true });
  Object.assign(event, { pointerId: 1, isPrimary: true, pointerType: "mouse" });
  canvas.dispatchEvent(event);
}
const click = () => { pointer("pointerdown"); pointer("pointerup"); };
const cable = (id: string, x: number, label: string): ScenePath => ({ id, identifier: id, kind: "cable", points: [[x, 1, 0], [x + 1, 1, 0]], dimension: { label, lengthM: 1, basis: "coordinates" } });

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1)); vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect: vi.fn(), strokeRect: vi.fn(), fillText: vi.fn() } as unknown as CanvasRenderingContext2D);
  canvas = document.createElement("canvas"); canvas.tabIndex = 0; document.body.appendChild(canvas);
  canvas.setPointerCapture = vi.fn(); canvas.releasePointerCapture = vi.fn(); canvas.hasPointerCapture = vi.fn(() => false);
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ width: 800, height: 600, left: 0, top: 0, right: 800, bottom: 600, x: 0, y: 0, toJSON() {} });
  Object.defineProperties(canvas, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  measure = vi.fn(); select = vi.fn(); place = vi.fn(); connect = vi.fn();
  engine = new InfrastructureScene(canvas, select, { onMeasure: measure, onPlacement: place, onPortConnect: connect });
  engine.setData({ rooms: [rooms[0]], racks: [], paths: [] });
});
afterEach(() => { engine.dispose(); canvas.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("path dimensions and temporary measurements", () => {
  it("shows every selected path part, keeps drafts distinct, and disposes previous captions", () => {
    const draft = { ...cable("draft", 0, "图示 1.00 m"), draft: true };
    const paths = [cable("a", -2, "A段 · 坐标 1.00 m"), cable("a", 0, "B段 · 坐标 1.00 m"), cable("b", 1, "另一条"), draft];
    const original = structuredClone(paths);
    engine.setData({ rooms: [rooms[0]], racks: [], paths });
    engine.select({ kind: "cable", id: "a" });
    const titles = overlay("__path_dimensions__").children as THREE.Sprite[];
    expect(titles.map(title => title.userData.dimensionText)).toEqual(["A段 · 坐标 1.00 m", "B段 · 坐标 1.00 m", "预览 · 图示 1.00 m"]);
    expect(titles[2].material.color.getHex()).toBe(0xffd39a);
    expect(titles.every(title => title.userData.ignorePick && !title.material.depthTest && title.renderOrder === 120)).toBe(true);
    expect(titles[0].material.color.getHex()).toBe(0xe6f3ff);
    const disposed = vi.fn(); titles[0].material.map!.addEventListener("dispose", disposed);
    engine.select(null);
    expect(disposed).toHaveBeenCalledOnce();
    expect(overlay("__path_dimensions__").children).toHaveLength(1);
    expect(paths).toEqual(original);
  });

  it("treats dimensions independently from object labels and follows the pathway layer", () => {
    const path = { ...cable("tray", 0, "桥架 · 坐标 1.00 m"), kind: "pathway" as const };
    engine.setData({ rooms: [rooms[0]], racks: [], paths: [path] });
    engine.select({ kind: "pathway", id: "tray" });
    engine.setLayers({ labels: false, shell: true, pathways: true });
    state().updateLabels();
    expect(overlay("__path_dimensions__").visible).toBe(true);
    expect(overlay("__path_dimensions__").children[0].visible).toBe(true);
    engine.setLayers({ labels: false, shell: true, pathways: false });
    expect(overlay("__path_dimensions__").children[0].visible).toBe(false);
    engine.setLayers({ labels: true, shell: true, pathways: true, dimensions: false });
    expect(overlay("__path_dimensions__").visible).toBe(false);
  });

  it("measures a model surface with priority over placement, port connection and selection", () => {
    const object = new THREE.Mesh(); object.userData.port = { id: "port", occupied: false };
    vi.spyOn(state(), "pickAt").mockReturnValue({ object, selection: { kind: "port", id: "port" }, point: new THREE.Vector3(1, 1.5, 0) });
    engine.setConnectionMode(true); engine.setPlacementMode(true); engine.setMeasurementMode(true);
    click();
    expect(measure).toHaveBeenCalledExactlyOnceWith(point(1, 1.5));
    expect(select).not.toHaveBeenCalled(); expect(place).not.toHaveBeenCalled(); expect(connect).not.toHaveBeenCalled();
    const initial = state().camera.position.clone();
    pointer("pointerdown"); pointer("pointermove", 445, 320);
    expect(state().camera.position.distanceTo(initial)).toBeGreaterThan(0.1);
    pointer("pointerup");
    expect(measure).toHaveBeenCalledTimes(1);
  });

  it("projects empty space onto the floor and rejects outside or cross-room clicks", () => {
    engine.setMeasurementMode(true); engine.setView("top");
    const hit = vi.spyOn(state(), "pickAt").mockReturnValue(null);
    click();
    expect(measure.mock.calls[0][0]?.locationId).toBe("room");
    expect(measure.mock.calls[0][0]?.point[1]).toBe(0);
    engine.setData({ rooms, racks: [], paths: [] }); engine.setMeasurement([point(0)]);
    hit.mockReturnValue({ object: new THREE.Group(), selection: { kind: "room", id: "other" }, point: new THREE.Vector3(10, 0, 0) });
    click(); expect(measure).toHaveBeenLastCalledWith(null);
    hit.mockReturnValue({ object: new THREE.Group(), selection: { kind: "room", id: "other" }, point: new THREE.Vector3(50, 1, 0) });
    click(); expect(measure).toHaveBeenLastCalledWith(null);
  });

  it("draws diagram segment and total lengths without affecting physical framing or picking", () => {
    engine.focus(); const camera = state().camera.position.clone();
    engine.setMeasurement([point(-1, 2.8), point(0, 2.8), point(0, 2.8, 1)]);
    const group = overlay("__measurement__");
    expect(group.userData.ignorePick).toBe(true);
    expect(group.children.filter(item => item instanceof THREE.Sprite).every(item => item.renderOrder === 120 && item.material.color.getHex() === 0xe0f4f2)).toBe(true);
    expect(group.children.filter(item => item instanceof THREE.Mesh).every(item => (item.material as THREE.Material).depthTest)).toBe(true);
    expect(group.children.filter(item => item instanceof THREE.Sprite).map(item => item.userData.measurementLabel)).toEqual(["图示段 1 · 1.00 m", "图示段 2 · 1.00 m", "图示总长 · 2.00 m"]);
    engine.focus(); expect(state().camera.position.distanceTo(camera)).toBeLessThan(0.000001);
    engine.setLayers({ labels: false, shell: true, pathways: true });
    state().updateLabels();
    expect(group.children.filter(item => item instanceof THREE.Sprite).every(item => item.visible)).toBe(true);
    engine.setLayers({ labels: true, shell: true, pathways: true, dimensions: false });
    expect(group.children.filter(item => item instanceof THREE.Sprite).every(item => !item.visible)).toBe(true);
    expect(group.children.filter(item => item instanceof THREE.Mesh).every(item => item.visible)).toBe(true);
    const caption = group.children.find(item => item instanceof THREE.Sprite) as THREE.Sprite;
    const disposed = vi.fn(); caption.material.map!.addEventListener("dispose", disposed);
    engine.dispose(); expect(disposed).toHaveBeenCalledOnce();
  });

  it("bounds input to a same-room valid prefix and 64 points without mutating source data", () => {
    const input = [point(0), point(1), point(10, 0, 0, "other"), point(2)];
    const before = structuredClone(input);
    expect(validMeasurement(input, rooms)).toEqual(input.slice(0, 2));
    expect(input).toEqual(before);
    expect(validMeasurement([point(0), point(Number.NaN), point(1)], rooms)).toEqual([point(0)]);
    expect(validMeasurement([point(0, -0.1)], rooms)).toEqual([]);
    expect(validMeasurement(Array.from({ length: 80 }, (_, index) => point(index / 100)), rooms)).toHaveLength(64);
    engine.setMeasurement(input);
    expect(overlay("__measurement__").children.filter(item => item.userData.measurementPoint !== undefined)).toHaveLength(2);
    engine.setData({ rooms: [rooms[1]], racks: [], paths: [] });
    expect(overlay("__measurement__").children).toHaveLength(0);
  });
});
