import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InfrastructureScene, type ScenePlacement, type Selection } from "./sceneRenderer";

vi.mock("three", async importOriginal => {
  const actual = await importOriginal<typeof import("three")>();
  return { ...actual, WebGLRenderer: class {
    shadowMap = { enabled: false, type: 0 };
    renderLists = { dispose: vi.fn() };
    setPixelRatio() {} setSize() {} render() {} dispose() {}
  } };
});

let canvas: HTMLCanvasElement;
let engine: InfrastructureScene;
let onPlace = vi.fn<(value: ScenePlacement | null) => void>();
let onSelect = vi.fn<(value: Selection) => void>();
let onConnect = vi.fn<(a: string, b: string) => void>();
const placement: ScenePlacement = { locationId: "room", positionX: 1, positionY: 2 };
type SceneInternals = { camera: THREE.PerspectiveCamera; controls: { target: THREE.Vector3; enabled: boolean }; scene: THREE.Scene; pickAt: (x: number, y: number) => { object: THREE.Object3D; selection: Selection; point: THREE.Vector3 } | null };
function internals() { return engine as unknown as SceneInternals; }
function pointer(type: string, x: number, y: number, button = 0) {
  const event = new MouseEvent(type, { clientX: x, clientY: y, button, buttons: type === "pointerup" ? 0 : 1 << button, bubbles: true, cancelable: true });
  Object.assign(event, { pointerId: 1, isPrimary: true, pointerType: "mouse" });
  canvas.dispatchEvent(event);
}
function port(id: string, blocked = false) {
  const object = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  object.position.set(id === "source" ? 0 : 1, 1, 0);
  object.userData.port = { id, occupied: false, blocked };
  return { object, selection: { kind: "port" as const, id }, point: object.position.clone() };
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  canvas = document.createElement("canvas");
  canvas.tabIndex = 0;
  document.body.appendChild(canvas);
  canvas.setPointerCapture = vi.fn(); canvas.releasePointerCapture = vi.fn(); canvas.hasPointerCapture = vi.fn(() => false);
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ width: 800, height: 600, left: 0, top: 0, right: 800, bottom: 600, x: 0, y: 0, toJSON() {} });
  Object.defineProperties(canvas, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  onPlace = vi.fn(); onSelect = vi.fn(); onConnect = vi.fn();
  engine = new InfrastructureScene(canvas, onSelect, { onPlacement: onPlace, onPortConnect: onConnect });
  engine.setData({ rooms: [{ id: "room", label: "", center: [0, 0], width: 6, depth: 4 }], racks: [], paths: [] });
  vi.spyOn(engine, "placementAt").mockReturnValue(placement);
});
afterEach(() => { engine.dispose(); canvas.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("scene tool gestures", () => {
  it("translates the actual renderer camera and Orbit target together, then releases all movement frames", () => {
    vi.mocked(requestAnimationFrame).mock.calls[0]?.[0](0); // Flush the constructor's pending draw.
    const frames = new Map<number, FrameRequestCallback>();
    let nextFrame = 10;
    vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => { const id = nextFrame++; frames.set(id, callback); return id; }));
    vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => frames.delete(id)));
    const advance = (time: number) => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(time)); };
    const key = (type: string) => canvas.dispatchEvent(new KeyboardEvent(type, { code: "KeyW", key: "w", bubbles: true, cancelable: true }));
    const { camera, controls } = internals();
    const position = camera.position.clone(), target = controls.target.clone(), orientation = camera.quaternion.clone();
    canvas.focus(); key("keydown"); advance(100); advance(150);
    const translation = camera.position.clone().sub(position);
    expect(translation.length()).toBeGreaterThan(0.01);
    expect(translation.y).toBeCloseTo(0, 10);
    expect(controls.target.clone().sub(target).distanceTo(translation)).toBeLessThan(0.000001);
    expect(camera.quaternion.angleTo(orientation)).toBeLessThan(0.000001);
    key("keyup"); advance(200);
    expect(frames.size).toBe(0);
    controls.enabled = false; key("keydown");
    expect(frames.size).toBe(0);
    controls.enabled = true; key("keydown"); advance(250); advance(300);
    expect(frames.size).toBeGreaterThan(0);
    engine.dispose();
    expect(frames.size).toBe(0);
    key("keydown");
    expect(frames.size).toBe(0);
  });

  it("places on a stationary left click without selecting an existing object", () => {
    engine.setPlacementMode(true);
    const hit = port("source");
    vi.spyOn(internals(), "pickAt").mockReturnValue(hit);
    pointer("pointerdown", 100, 100); pointer("pointerup", 103, 102);
    expect(onPlace).toHaveBeenCalledExactlyOnceWith(placement);
    expect(onSelect).not.toHaveBeenCalled();
    expect(canvas.style.cursor).toBe("crosshair");
    pointer("pointerdown", 100, 100, 2); pointer("pointerup", 100, 100, 2);
    expect(onPlace).toHaveBeenCalledTimes(1);
  });

  it("keeps an out-and-back orbit drag from placing and still moves the camera", () => {
    engine.setPlacementMode(true);
    const camera = internals().camera;
    const initial = camera.position.clone();
    pointer("pointerdown", 100, 100); pointer("pointermove", 145, 120);
    expect(camera.position.distanceTo(initial)).toBeGreaterThan(0.1);
    pointer("pointermove", 100, 100); pointer("pointerup", 100, 100);
    expect(onPlace).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("passes a null placement for empty space and cancels a pending click when the tool is dismissed", () => {
    engine.setPlacementMode(true);
    vi.mocked(engine.placementAt).mockReturnValue(null);
    pointer("pointerdown", 100, 100); pointer("pointerup", 100, 100);
    expect(onPlace).toHaveBeenCalledExactlyOnceWith(null);
    pointer("pointerdown", 100, 100); engine.setPlacementMode(false); pointer("pointerup", 100, 100);
    expect(onPlace).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("pans with the left button while retaining camera direction, then restores orbit", () => {
    const camera = internals().camera;
    const direction = camera.getWorldDirection(new THREE.Vector3());
    const position = camera.position.clone();
    engine.setPanMode(true);
    pointer("pointerdown", 100, 100); pointer("pointermove", 145, 120); pointer("pointerup", 145, 120);
    expect(camera.position.distanceTo(position)).toBeGreaterThan(0.1);
    expect(camera.getWorldDirection(new THREE.Vector3()).distanceTo(direction)).toBeLessThan(0.000001);
    engine.setPanMode(false);
    pointer("pointerdown", 100, 100); pointer("pointermove", 145, 120); pointer("pointerup", 145, 120);
    expect(camera.getWorldDirection(new THREE.Vector3()).distanceTo(direction)).toBeGreaterThan(0.01);
  });

  it("pans with one touch, retains pinch zoom, and restores default touch rotation", () => {
    const touch = (type: string, x: number, id = 1) => {
      const event = new MouseEvent(type, { clientX: x, clientY: 300, button: 0, bubbles: true, cancelable: true });
      Object.assign(event, { pointerId: id, isPrimary: id === 1, pointerType: "touch" });
      canvas.dispatchEvent(event);
    };
    const { camera, controls } = internals();
    const orientation = camera.quaternion.clone(), position = camera.position.clone();
    engine.setPanMode(true);
    touch("pointerdown", 300); touch("pointermove", 350); touch("pointerup", 350);
    expect(camera.position.distanceTo(position)).toBeGreaterThan(0.1);
    expect(camera.quaternion.angleTo(orientation)).toBeLessThan(0.000001);
    const distance = camera.position.distanceTo(controls.target);
    touch("pointerdown", 300); touch("pointerdown", 500, 2); touch("pointermove", 580, 2);
    expect(camera.position.distanceTo(controls.target)).toBeLessThan(distance);
    touch("pointerup", 580, 2); touch("pointerup", 300);
    engine.setPanMode(false);
    const beforeRotation = camera.quaternion.clone();
    touch("pointerdown", 300); touch("pointermove", 350); touch("pointerup", 350);
    expect(camera.quaternion.angleTo(beforeRotation)).toBeGreaterThan(0.01);
    const beforeTwoFingerPan = camera.quaternion.clone(), beforePanPosition = camera.position.clone();
    touch("pointerdown", 300); touch("pointerdown", 500, 2);
    touch("pointermove", 350); touch("pointermove", 550, 2);
    expect(camera.quaternion.angleTo(beforeTwoFingerPan)).toBeLessThan(0.000001);
    expect(camera.position.distanceTo(beforePanPosition)).toBeGreaterThan(0.1);
    touch("pointerup", 550, 2); touch("pointerup", 350);
  });

  it("removes and disposes the floor reticle when placement mode ends", () => {
    engine.setPlacementMode(true);
    pointer("pointermove", 100, 100);
    const marker = internals().scene.getObjectByName("__placement_reticle__")!;
    expect(marker.position.toArray()).toEqual([-2, 0.018, 0]);
    expect(marker.userData.ignorePick).toBe(true);
    const disposed = vi.fn();
    marker.traverse(object => {
      if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
        object.geometry.addEventListener("dispose", disposed);
        (object.material as THREE.Material).addEventListener("dispose", disposed);
      }
    });
    engine.setPlacementMode(false);
    expect(disposed).toHaveBeenCalledTimes(4);
    expect(internals().scene.getObjectByName("__placement_reticle__")).toBeUndefined();
  });

  it("does not start a connection from a policy-blocked port", () => {
    engine.setConnectionMode(true);
    vi.spyOn(internals(), "pickAt").mockReturnValue(port("source", true));
    pointer("pointerdown", 100, 100); pointer("pointerup", 100, 100);
    expect(onConnect).not.toHaveBeenCalled();
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ kind: "port", id: "source" });
    const initial = internals().camera.position.clone();
    pointer("pointerdown", 100, 100); pointer("pointermove", 145, 120); pointer("pointerup", 145, 120);
    expect(internals().camera.position.distanceTo(initial)).toBeGreaterThan(0.1);
    expect(onConnect).not.toHaveBeenCalled();
  });

  it("rejects a blocked target but permits a free target during port dragging", () => {
    engine.setConnectionMode(true);
    const source = port("source"), blocked = port("target", true), free = port("target");
    const hit = vi.spyOn(internals(), "pickAt").mockReturnValue(source);
    pointer("pointerdown", 100, 100);
    hit.mockReturnValue(blocked); pointer("pointermove", 200, 100); pointer("pointerup", 200, 100);
    expect(onConnect).not.toHaveBeenCalled();
    hit.mockReturnValue(source); pointer("pointerdown", 100, 100);
    hit.mockReturnValue(free); pointer("pointermove", 200, 100); pointer("pointerup", 200, 100);
    expect(onConnect).toHaveBeenCalledExactlyOnceWith("source", "target");
  });
});


it("frames the selected rack when fitting rack mode, and returns to the full room in overview", () => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect() {}, strokeRect() {}, fillText() {} } as unknown as CanvasRenderingContext2D);
  const data = { rooms: [{ id: "room", label: "Room", center: [12.5, 10] as [number, number], width: 25, depth: 20 }],
    racks: [{ id: "rack", identifier: "R01", name: "Rack", locationId: "room", position: [3, 0, 4] as [number, number, number], rotation: 0, width: 0.8, depth: 1.2, heightU: 44, devices: [] }], paths: [] };
  engine.setData({ ...data, focusRackId: "rack" }, { fit: true });
  const { camera, controls } = internals();
  expect(controls.target.x).toBeCloseTo(3);
  expect(controls.target.z).toBeCloseTo(4);
  const distance = camera.position.distanceTo(controls.target);
  expect(distance).toBeLessThan(8);
  engine.zoom(0.25); engine.focus();
  expect(camera.position.distanceTo(controls.target)).toBeCloseTo(distance);
  engine.setData(data, { fit: true });
  expect(camera.position.distanceTo(controls.target)).toBeGreaterThan(distance * 4);
});
