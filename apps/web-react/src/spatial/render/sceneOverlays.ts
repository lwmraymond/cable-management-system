import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { label } from "./rackGeometry";
import { midpoint } from "./pathGeometry";
import type { SceneMeasurementPoint, ScenePath, SceneRoom, Selection } from "./sceneRenderer";

function overlay(name: string): THREE.Group {
  const group = new THREE.Group();
  group.name = name;
  group.userData.ignorePick = true;
  return group;
}

export function buildPathDimensions(paths: ScenePath[], selection: Selection | null): THREE.Group {
  const group = overlay("__path_dimensions__");
  for (const path of paths) {
    if (!path.draft && (selection?.kind !== path.kind || selection.id !== path.id)) continue;
    const dimension = path.dimension;
    if (!dimension?.label.trim() || !Number.isFinite(dimension.lengthM) || dimension.lengthM < 0 || path.points.length < 2 || path.points.some(point => !point.every(Number.isFinite))) continue;
    const text = `${path.draft ? "预览 · " : ""}${dimension.label}`;
    const title = label(text, 0.9, 152);
    title.userData.layer = "dimensions";
    title.position.copy(midpoint(path.points.map(point => new THREE.Vector3(...point)))).add(new THREE.Vector3(0, 0.14, 0));
    title.userData.dimensionText = text;
    title.userData.dimensionPathKind = path.kind;
    title.userData.dimensionPathId = path.id;
    title.renderOrder = 120; // Keep lengths above ordinary object captions.
    title.material.color.set(path.draft ? 0xffd39a : 0xe6f3ff);
    group.add(title);
  }
  return group;
}

export function insideRoom(point: readonly number[], room: SceneRoom): boolean {
  return point.length === 3 && point.every(Number.isFinite) && point[1] >= -0.005 && point[1] <= (room.height ?? 3.2) + 0.001 && Math.abs(point[0] - room.center[0]) <= room.width / 2 + 0.001 && Math.abs(point[2] - room.center[1]) <= room.depth / 2 + 0.001;
}

/** Invalid points terminate the chain, so no segment bridges an omitted room or point. */
export function validMeasurement(points: SceneMeasurementPoint[], rooms: SceneRoom[]): SceneMeasurementPoint[] {
  const result: SceneMeasurementPoint[] = [];
  for (const item of points.slice(0, 64)) {
    const room = rooms.find(room => room.id === item.locationId);
    if (!room || (result.length && item.locationId !== result[0].locationId) || !insideRoom(item.point, room)) break;
    result.push({ locationId: item.locationId, point: [item.point[0], Math.max(0, item.point[1]), item.point[2]] });
  }
  return result;
}

export function buildMeasurement(points: SceneMeasurementPoint[], viewport: { width: number; height: number }): THREE.Group {
  const group = overlay("__measurement__");
  if (!points.length) return group;
  const vertices = points.map(item => new THREE.Vector3(...item.point));
  const pointGeometry = new THREE.SphereGeometry(0.04, 12, 8);
  const pointMaterial = new THREE.MeshBasicMaterial({ color: 0x167e8a, depthTest: true, toneMapped: false });
  vertices.forEach((position, index) => {
    const marker = new THREE.Mesh(pointGeometry, pointMaterial);
    marker.position.copy(position);
    marker.userData.measurementPoint = index;
    marker.userData.ignorePick = true;
    group.add(marker);
  });
  if (vertices.length < 2) return group;
  let total = 0;
  const caption = (text: string, at: THREE.Vector3) => {
    const title = label(text, 0.85, 148);
    title.userData.layer = "dimensions";
    title.position.copy(at);
    title.userData.measurementLabel = text;
    title.renderOrder = 120;
    title.material.color.set(0xe0f4f2);
    group.add(title);
  };
  for (let index = 1; index < vertices.length; index++) {
    const length = vertices[index - 1].distanceTo(vertices[index]);
    total += length;
    if (length > 0.000001) caption(`图示段 ${index} · ${length.toFixed(2)} m`, vertices[index - 1].clone().lerp(vertices[index], 0.5).add(new THREE.Vector3(0, 0.14, 0)));
  }
  if (total > 0.000001) {
    const material = new LineMaterial({ color: 0x168895, linewidth: 3, transparent: true, opacity: 0.98, depthTest: true, depthWrite: false, toneMapped: false });
    material.resolution.set(Math.max(1, viewport.width), Math.max(1, viewport.height));
    const line = new Line2(new LineGeometry().setFromPoints(vertices.filter((point, index) => !index || point.distanceToSquared(vertices[index - 1]) > 0.000000000001)), material);
    line.userData.ignorePick = true;
    line.renderOrder = 30;
    group.add(line);
  }
  caption(`图示总长 · ${total.toFixed(2)} m`, vertices[vertices.length - 1].clone().add(new THREE.Vector3(0, 0.32, 0)));
  return group;
}
