import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildRacks, label } from "./rackGeometry";
import { buildPaths } from "./pathGeometry";
import { buildRooms } from "./roomGeometry";
import type { SceneRack, SceneRoom } from "./sceneRenderer";

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect: vi.fn(), strokeRect: vi.fn(), fillText: vi.fn() } as unknown as CanvasRenderingContext2D);
});
afterEach(() => vi.restoreAllMocks());

const room: SceneRoom = { id: "room", label: "Server room", center: [3, 2], width: 6, depth: 4, height: 3.2 };

describe("interactive infrastructure geometry", () => {
  it("keeps labels visible above objects without capturing pointer selection", () => {
    const title = label("Rack 01");
    expect(title.material.depthTest).toBe(false);
    expect(title.material.depthWrite).toBe(false);
    expect(title.renderOrder).toBe(100);
    expect(title.userData.labelMinimumPixels).toBe(132);
    expect(title.userData).toMatchObject({ layer: "labels", ignorePick: true });
  });

  it("creates distinct occupied and free port hit surfaces in the rotated rack coordinates", () => {
    const rack: SceneRack = { id: "rack", identifier: "R01", name: "Rack", position: [2, 0.5, 3], rotation: Math.PI / 2, width: 0.6, depth: 1, heightU: 42, locationId: "room", devices: [{ id: "device", identifier: "D01", name: "Switch", deviceType: "switch", startU: 8, units: 1, face: "front", portCount: 1, ports: [
      { id: "front", identifier: "F01", mediaType: "copper", face: "front", positionIndex: 1, occupied: false },
      { id: "rear", identifier: "R01", mediaType: "copper", face: "rear", positionIndex: 1, occupied: true },
    ] }] };
    const scene = buildRacks([rack]);
    scene.updateMatrixWorld(true);
    const front = scene.getObjectByName("front") as THREE.Mesh<THREE.BoxGeometry, THREE.MeshBasicMaterial>;
    const rear = scene.getObjectByName("rear") as THREE.Mesh<THREE.BoxGeometry, THREE.MeshBasicMaterial>;
    expect(front.userData.selection).toEqual({ kind: "port", id: "front" });
    expect(rear.userData.port.occupied).toBe(true);
    expect(front.material.color.getHex()).toBe(0x38a36b);
    expect(rear.material.color.getHex()).toBe(0x9aa4ad);
    const a = front.getWorldPosition(new THREE.Vector3()), b = rear.getWorldPosition(new THREE.Vector3());
    expect(a.x).toBeCloseTo(2.456, 6);
    expect(b.x).toBeCloseTo(1.544, 6);
    expect(a.y).toBeCloseTo(0.5 + 0.115 + 7 * 0.04445 + (0.04445 - 0.003) / 2, 6);
    expect([a.z, b.z]).toEqual([3, 3]);
  });

  it("cuts a real north-wall opening while leaving adjacent wall geometry intact", () => {
    const scene = buildRooms([{ ...room, entrances: [{ id: "door", name: "Entry", wall: "north", offset_m: 2, width_m: 1.2, height_m: 2.1 }] }], new THREE.Box3());
    scene.updateMatrixWorld(true);
    const meshes: THREE.Object3D[] = [];
    scene.traverse(object => { if (object instanceof THREE.Mesh) meshes.push(object); });
    const openingRay = new THREE.Raycaster(new THREE.Vector3(2.6, 0.1, -1), new THREE.Vector3(0, 0, 1));
    expect(openingRay.intersectObjects(meshes, false)[0].distance).toBeGreaterThan(4.9);
    const wallRay = new THREE.Raycaster(new THREE.Vector3(1, 0.1, -1), new THREE.Vector3(0, 0, 1));
    expect(wallRay.intersectObjects(meshes, false)[0].distance).toBeLessThan(1);
    expect(scene.getObjectByName("door")).toBeDefined();
  });

  it("measures east-wall entrance offsets from minimum Z", () => {
    const scene = buildRooms([{ ...room, entrances: [{ id: "east", name: "East", wall: "east", offset_m: 1, width_m: 0.8, height_m: 2.1 }] }], new THREE.Box3());
    const doorway = scene.getObjectByName("east")!;
    expect(doorway.children.slice(0, 2).map(post => [post.position.x, post.position.z])).toEqual([[6, 1], [6, 1.8]]);
  });

  it("renders drafts as non-pickable solid previews without changing source coordinates", () => {
    const input = { id: "draft", identifier: "Draft", kind: "cable" as const, points: [[1, 2, 3], [4, 2, 3]] as [number, number, number][], draft: true, dashed: true };
    const before = structuredClone(input);
    const line = buildPaths([input], { width: 900, height: 600 }).children[0] as Line2;
    expect(line.material.dashed).toBe(false);
    expect(line.material.linewidth).toBe(3.5);
    expect(line.material.depthTest).toBe(true);
    expect(line.userData.ignorePick).toBe(true);
    expect(line.userData.selection).toBeUndefined();
    expect(input).toEqual(before);
  });
});
