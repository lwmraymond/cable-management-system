import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { label } from "./rackGeometry";
import type { ScenePath, Selection } from "./sceneRenderer";

const CABLE_COLOR = 0x507d95;
const TRAY_COLOR = 0x9ba9b3;

export function midpoint(points: THREE.Vector3[]): THREE.Vector3 {
  if (!points.length) return new THREE.Vector3();
  const lengths = points.slice(1).map((point, index) => point.distanceTo(points[index]));
  let remaining = lengths.reduce((total, length) => total + length, 0) / 2;
  for (let index = 0; index < lengths.length; index++) {
    if (lengths[index] > 0 && remaining <= lengths[index]) return points[index].clone().lerp(points[index + 1], remaining / lengths[index]);
    remaining -= lengths[index];
  }
  return points[0].clone();
}

export function buildPaths(paths: ScenePath[], viewport: { width: number; height: number }): THREE.Group {
  const root = new THREE.Group();
  const labelled = new Set<string>();
  for (const path of paths) {
    if (path.points.some(point => !point.every(Number.isFinite))) continue;
    const points = path.points.map(point => new THREE.Vector3(...point)).filter((point, index, all) => !index || point.distanceToSquared(all[index - 1]) > 0.000001);
    if (points.length < 2) continue;
    const selection: Selection = { kind: path.kind, id: path.id };
    if (path.kind === "cable") {
      // Stroke style is a display choice, independent of whether coordinates were surveyed.
      const material = new LineMaterial({ color: path.draft ? 0xd79737 : CABLE_COLOR, dashed: false, transparent: true, opacity: 0.98, depthTest: true, depthWrite: false, toneMapped: false });
      material.linewidth = path.draft ? 3.5 : 2.4;
      material.resolution.set(Math.max(1, viewport.width), Math.max(1, viewport.height));
      const line = new Line2(new LineGeometry().setFromPoints(points), material);
      line.name = path.id;
      line.userData.selection = path.draft ? undefined : selection;
      line.userData.baseColor = path.draft ? 0xd79737 : CABLE_COLOR;
      line.userData.draft = path.draft;
      line.userData.ignorePick = !!path.draft;
      line.renderOrder = path.draft ? 80 : 1;
      root.add(line);
      continue;
    }
    const group = new THREE.Group();
    group.name = path.id;
    group.userData.selection = selection;
    group.userData.layer = "pathways";
    const width = Number.isFinite(path.width) && path.width! > 0 ? path.width! : 0.2;
    const depth = Number.isFinite(path.depth) && path.depth! > 0 ? path.depth! : 0.06;
    const thickness = Math.min(0.006, width / 10, depth / 5);
    const material = new THREE.MeshStandardMaterial({ color: TRAY_COLOR, metalness: 0.42, roughness: 0.64 });
    const ladder = /ladder|梯/i.test(path.pathwayType ?? "");
    const box = (segment: THREE.Group, size: [number, number, number], position: [number, number, number]) => {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
      mesh.position.set(...position);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.selection = selection;
      mesh.userData.baseColor = TRAY_COLOR;
      segment.add(mesh);
    };
    for (let index = 1; index < points.length; index++) {
      const forward = points[index].clone().sub(points[index - 1]);
      const length = forward.length();
      forward.normalize();
      const right = new THREE.Vector3(0, 1, 0).cross(forward);
      if (right.lengthSq() < 0.000001) right.set(1, 0, 0);
      right.normalize();
      const up = forward.clone().cross(right).normalize();
      const segment = new THREE.Group();
      segment.position.copy(points[index - 1]).lerp(points[index], 0.5);
      segment.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, forward));
      // Keep the opening on the given centerline; cable coordinates remain unchanged.
      if (!ladder) box(segment, [width, thickness, length], [0, -depth + thickness / 2, 0]);
      for (const side of [-1, 1]) {
        box(segment, [thickness, depth, length], [side * (width - thickness) / 2, -depth / 2, 0]);
        box(segment, [thickness * 2, thickness, length], [side * (width - thickness * 2) / 2, -thickness / 2, 0]);
      }
      const count = Math.min(180, Math.max(1, Math.ceil(length / 0.3)));
      const rungs = new THREE.InstancedMesh(new THREE.BoxGeometry(width - thickness * 2, thickness * 1.4, Math.min(0.025, length / 2)), material, count);
      const matrix = new THREE.Matrix4();
      for (let rung = 0; rung < count; rung++) {
        matrix.makeTranslation(0, -depth + thickness * 1.4, -length / 2 + length * (rung + 0.5) / count);
        rungs.setMatrixAt(rung, matrix);
      }
      rungs.computeBoundingBox();
      rungs.computeBoundingSphere();
      rungs.castShadow = true;
      rungs.receiveShadow = true;
      rungs.userData.selection = selection;
      rungs.userData.baseColor = TRAY_COLOR;
      segment.add(rungs);
      group.add(segment);
    }
    if (!labelled.has(path.id)) {
      const title = label(path.identifier || path.id.slice(0, 8), 0.85);
      title.position.copy(midpoint(points)).add(new THREE.Vector3(0, 0.15, 0));
      group.add(title);
      labelled.add(path.id);
    }
    root.add(group);
  }
  return root;
}
