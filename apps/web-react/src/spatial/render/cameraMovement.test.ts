import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CameraMovement } from "./cameraMovement";

let canvas: HTMLCanvasElement;
let camera: THREE.PerspectiveCamera;
let target: THREE.Vector3;
let movement: CameraMovement;
let allowed = vi.fn(() => true);
let onMove = vi.fn();
let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;

function frame(time: number) {
  const pending = [...frames.entries()];
  for (const [id, callback] of pending) {
    frames.delete(id);
    callback(time);
  }
}
function key(type: "keydown" | "keyup", code: string, value: string, options: KeyboardEventInit = {}, receiver: EventTarget = canvas) {
  const event = new KeyboardEvent(type, { code, key: value, bubbles: true, cancelable: true, ...options });
  receiver.dispatchEvent(event);
  return event;
}
function press(code = "KeyW", value = "w", options: KeyboardEventInit = {}) {
  return key("keydown", code, value, options);
}
function release(code = "KeyW", value = "w") {
  return key("keyup", code, value, {}, document);
}
function pointer(type: string, button: number, receiver: EventTarget) {
  const event = new MouseEvent(type, { button, bubbles: true });
  Object.assign(event, { pointerId: 1, isPrimary: true, pointerType: "mouse" });
  receiver.dispatchEvent(event);
}
function expectVector(actual: THREE.Vector3, expected: THREE.Vector3 | [number, number, number]) {
  expect(actual.distanceTo(Array.isArray(expected) ? new THREE.Vector3(...expected) : expected)).toBeLessThan(1e-9);
}

beforeEach(() => {
  frames = new Map(); nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => {
    const id = ++nextFrame;
    frames.set(id, callback);
    return id;
  }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => { frames.delete(id); }));
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  canvas = document.createElement("canvas");
  canvas.tabIndex = 0;
  document.body.appendChild(canvas);
  canvas.focus();
  camera = new THREE.PerspectiveCamera();
  camera.position.set(0, 3, 4);
  target = new THREE.Vector3(0, 3, 0);
  camera.lookAt(target);
  allowed = vi.fn(() => true); onMove = vi.fn();
  movement = new CameraMovement(canvas, camera, target, { isAllowed: allowed, onMove });
});
afterEach(() => {
  movement.dispose(); canvas.remove();
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

describe("focused canvas camera movement", () => {
  it("moves camera and target together along the current view without changing height or orientation", () => {
    camera.position.set(4, 3, 0); camera.lookAt(target);
    const initialCamera = camera.position.clone(), initialTarget = target.clone(), orientation = camera.quaternion.clone();
    expect(press().defaultPrevented).toBe(true);
    frame(0);
    expect(onMove).not.toHaveBeenCalled();
    frame(50);
    expectVector(camera.position.clone().sub(initialCamera), [-0.065, 0, 0]);
    expectVector(target.clone().sub(initialTarget), [-0.065, 0, 0]);
    release();
    const beforeStrafe = camera.position.clone();
    press("KeyD", "d"); frame(100); frame(150);
    expectVector(camera.position.clone().sub(beforeStrafe), [0, 0, -0.065]);
    release("KeyD", "d");
    const beforeBackward = camera.position.clone();
    press("KeyS", "s"); frame(200); frame(250);
    expectVector(camera.position.clone().sub(beforeBackward), [0.065, 0, 0]);
    expect(camera.quaternion.angleTo(orientation)).toBeLessThan(1e-7);
    expect(camera.position.y).toBe(3);
    expect(target.y).toBe(3);
  });

  it("normalizes diagonal movement to the same speed as a single direction", () => {
    const start = camera.position.clone();
    press(); press("KeyD", "d"); frame(0); frame(50);
    const delta = camera.position.clone().sub(start);
    expect(delta.length()).toBeCloseTo(0.065, 10);
    expect(delta.x).toBeCloseTo(0.065 / Math.sqrt(2), 10);
    expect(delta.z).toBeCloseTo(-0.065 / Math.sqrt(2), 10);
    release("KeyD", "d");
    const beforeForward = camera.position.clone(); frame(100);
    expectVector(camera.position.clone().sub(beforeForward), [0, 0, -0.065]);
  });

  it("moves vertically on held Space and Shift without changing orientation or accelerating WASD", () => {
    const start = camera.position.clone();
    const orientation = camera.quaternion.clone();
    expect(press("Space", " ").defaultPrevented).toBe(true); frame(0); frame(50);
    expectVector(camera.position.clone().sub(start), [0, 0.065, 0]);
    expectVector(target, [0, 3.065, 0]);
    release("Space", " "); expect(frames.size).toBe(0);
    press("ShiftRight", "Shift", { shiftKey: true }); frame(100); frame(150);
    expectVector(camera.position, start);
    release("ShiftRight", "Shift");
    press(); press("ShiftLeft", "Shift", { shiftKey: true }); frame(200); frame(250);
    expect(camera.position.distanceTo(start)).toBeCloseTo(0.065, 10);
    expect(camera.position.y).toBeLessThan(start.y);
    expect(camera.quaternion.angleTo(orientation)).toBeLessThan(1e-7);
  });

  it("cancels opposite vertical keys and tracks both Shift keys until the last release", () => {
    const start = camera.position.clone();
    press("Space", " "); press("ShiftLeft", "Shift", { shiftKey: true }); frame(0); frame(50);
    expectVector(camera.position, start); expect(frames.size).toBe(0);
    press("ShiftRight", "Shift", { shiftKey: true }); release("ShiftLeft", "Shift");
    frame(100); expectVector(camera.position, start);
    release("Space", " "); frame(150); frame(200);
    expect(camera.position.y).toBeLessThan(start.y);
    release("ShiftRight", "Shift"); expect(frames.size).toBe(0);
  });

  it.each(["input", "textarea", "select", "button", "div"])("leaves Space and Shift to focused %s controls", tag => {
    const input = document.createElement(tag); input.tabIndex = 0;
    if (tag === "div") input.contentEditable = "true";
    document.body.append(input); input.focus();
    const start = camera.position.clone();
    expect(key("keydown", "Space", " ", {}, input).defaultPrevented).toBe(false);
    expect(key("keydown", "ShiftLeft", "Shift", { shiftKey: true }, input).defaultPrevented).toBe(false);
    frame(0); frame(50); expectVector(camera.position, start); expect(frames.size).toBe(0);
    input.remove();
  });

  it("bounds speed at close and distant views and caps movement after a long frame", () => {
    for (const [distance, speed] of [[0.01, 0.25], [4, 1.3], [100, 3]]) {
      movement.stop(); target.set(0, 3, 0); camera.position.set(0, 3, distance); camera.lookAt(target);
      const start = camera.position.clone();
      press(); frame(0); frame(5_000);
      expect(camera.position.distanceTo(start)).toBeCloseTo(speed * 0.05, 10);
    }
  });

  it("uses screen up for a vertical view, including the default-up degenerate case", () => {
    for (const up of [new THREE.Vector3(0, 0, -1), new THREE.Vector3(0, 1, 0)]) {
      movement.stop(); target.set(0, 0, 0); camera.position.set(0, 5, 0); camera.up.copy(up);
      camera.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
      const start = camera.position.clone(), orientation = camera.quaternion.clone();
      press(); frame(0); frame(50);
      const delta = camera.position.clone().sub(start);
      expect(delta.z).toBeLessThan(0);
      expect(delta.x).toBeCloseTo(0, 10);
      expect(delta.y).toBe(0);
      expectVector(target, delta);
      expect(camera.quaternion.angleTo(orientation)).toBeLessThan(1e-7);
    }
  });

  it("prefers physical WASD codes and accepts uppercase key fallback", () => {
    const start = camera.position.clone();
    press("KeyW", "z"); frame(0); frame(50);
    expectVector(camera.position.clone().sub(start), [0, 0, -0.065]);
    movement.stop();
    const beforeFallback = camera.position.clone();
    press("", "A"); frame(100); frame(150);
    expectVector(camera.position.clone().sub(beforeFallback), [-0.065, 0, 0]);
    key("keyup", "", "A", {}, document);
    expect(frames.size).toBe(0);
  });

  it("captures navigation only while the canvas is focused and no editing modifiers or IME are active", () => {
    canvas.blur();
    expect(press().defaultPrevented).toBe(false);
    expect(frames.size).toBe(0);
    canvas.focus();
    for (const options of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { isComposing: true }, { keyCode: 229 }]) {
      expect(press("KeyW", "w", options).defaultPrevented).toBe(false);
      expect(frames.size).toBe(0);
    }
    expect(press().defaultPrevented).toBe(true);
    expect(frames.size).toBe(1);
  });

  it("stops existing motion when a modifier or text composition begins", () => {
    const interrupts = [
      () => press("ControlLeft", "Control", { ctrlKey: true }),
      () => press("MetaLeft", "Meta", { metaKey: true }),
      () => press("AltLeft", "Alt", { altKey: true }),
      () => canvas.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true })),
    ];
    for (const interrupt of interrupts) {
      press(); frame(0); frame(50);
      const stopped = camera.position.clone();
      interrupt();
      expect(frames.size).toBe(0);
      frame(100);
      expectVector(camera.position, stopped);
      canvas.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    }
  });

  it("releases document-level keys and leaves no idle frame after blur, hiding, stop, or disposal", () => {
    const cancel = [
      () => release(),
      () => canvas.blur(),
      () => window.dispatchEvent(new Event("blur")),
      () => {
        vi.spyOn(document, "hidden", "get").mockReturnValue(true);
        document.dispatchEvent(new Event("visibilitychange"));
      },
      () => movement.stop(),
      () => movement.dispose(),
    ];
    for (const stop of cancel) {
      vi.spyOn(document, "hidden", "get").mockReturnValue(false);
      canvas.focus(); press(); frame(0); frame(50);
      const position = camera.position.clone();
      stop();
      expect(frames.size).toBe(0);
      frame(5_000);
      expectVector(camera.position, position);
    }
    expect(press().defaultPrevented).toBe(false);
    expect(frames.size).toBe(0);
  });

  it("suspends keyboard motion during any mouse-button drag until pointer release or cancellation", () => {
    for (const [button, end] of [[0, "pointerup"], [1, "pointercancel"], [2, "lostpointercapture"]] as const) {
      press(); frame(0); frame(50);
      pointer("pointerdown", button, canvas);
      expect(frames.size).toBe(0);
      const stopped = camera.position.clone();
      press(); frame(100);
      expectVector(camera.position, stopped);
      expect(frames.size).toBe(0);
      pointer(end, button, end === "lostpointercapture" ? canvas : document);
      press(); frame(150); frame(200);
      expect(camera.position.distanceTo(stopped)).toBeGreaterThan(0);
      movement.stop();
    }
    pointer("pointerdown", 0, canvas);
    window.dispatchEvent(new Event("blur"));
    canvas.focus();
    const beforeRefocus = camera.position.clone();
    press(); frame(250); frame(300);
    expect(camera.position.distanceTo(beforeRefocus)).toBeGreaterThan(0);
  });

  it("checks movement permission before starting and during motion, without restarting from key repeat", () => {
    allowed.mockReturnValue(false);
    expect(press().defaultPrevented).toBe(false);
    expect(press("Space", " ").defaultPrevented).toBe(true);
    expect(press("ShiftLeft", "Shift", { shiftKey: true }).defaultPrevented).toBe(false);
    expect(frames.size).toBe(0);
    allowed.mockReturnValue(true); press(); frame(0); frame(50);
    const stopped = camera.position.clone();
    allowed.mockReturnValue(false); frame(100);
    expectVector(camera.position, stopped);
    expect(frames.size).toBe(0);
    allowed.mockReturnValue(true); press("KeyW", "w", { repeat: true });
    expect(frames.size).toBe(0);
    press(); frame(150); frame(200);
    expect(camera.position.distanceTo(stopped)).toBeGreaterThan(0);
  });

  it("stops for view shortcuts without consuming them and requires a fresh movement press", () => {
    for (const [code, value] of [["Escape", "Escape"], ["KeyF", "f"], ["Digit1", "1"], ["Digit2", "2"], ["Digit3", "3"]]) {
      press(); frame(0); frame(50);
      expect(press(code, value).defaultPrevented).toBe(false);
      expect(frames.size).toBe(0);
      press("KeyW", "w", { repeat: true });
      expect(frames.size).toBe(0);
      const stopped = camera.position.clone(); frame(100);
      expectVector(camera.position, stopped);
    }
  });
});
