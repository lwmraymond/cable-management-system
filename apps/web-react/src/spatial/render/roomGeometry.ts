import * as THREE from "three";
import { label } from "./rackGeometry";
import type { SceneRoom, Selection } from "./sceneRenderer";

export function buildRooms(rooms: SceneRoom[], fallbackBounds: THREE.Box3): THREE.Group {
  const root = new THREE.Group();
  const floorMaterial = new THREE.MeshStandardMaterial({ color: 0xf7f9fb, roughness: 0.96 });
  const wallMaterial = new THREE.MeshStandardMaterial({ color: 0xd7dfe7, roughness: 0.88 });
  const capMaterial = new THREE.MeshStandardMaterial({ color: 0xfdfefe, roughness: 0.82 });
  const borderMaterial = new THREE.LineBasicMaterial({ color: 0xabb9c8 });
  const gridMaterial = new THREE.LineBasicMaterial({ color: 0xd6dee7, transparent: true, opacity: 0.65 });
  const floor = (group: THREE.Group, x: number, z: number, width: number, depth: number) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, 0.055, depth), floorMaterial);
    mesh.position.set(x, -0.03, z);
    mesh.receiveShadow = true;
    group.add(mesh);
    const left = x - width / 2, right = x + width / 2, back = z - depth / 2, front = z + depth / 2;
    const spacing = Math.max(0.5, Math.ceil(Math.max(width, depth) / 80));
    const vertices: THREE.Vector3[] = [];
    for (let px = Math.ceil(left / spacing) * spacing; px < right; px += spacing) vertices.push(new THREE.Vector3(px, 0.001, back), new THREE.Vector3(px, 0.001, front));
    for (let pz = Math.ceil(back / spacing) * spacing; pz < front; pz += spacing) vertices.push(new THREE.Vector3(left, 0.001, pz), new THREE.Vector3(right, 0.001, pz));
    group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(vertices), gridMaterial));
    const corners = [[left, back], [right, back], [right, front], [left, front], [left, back]];
    group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(corners.map(([px, pz]) => new THREE.Vector3(px, 0.006, pz))), borderMaterial));
  };
  if (!rooms.length) {
    const size = fallbackBounds.getSize(new THREE.Vector3()), center = fallbackBounds.getCenter(new THREE.Vector3());
    floor(root, center.x, center.z, Math.max(2, size.x + 0.6), Math.max(2, size.z + 0.6));
    return root;
  }
  for (const room of rooms) {
    const [x, z] = room.center;
    if (![x, z, room.width, room.depth].every(Number.isFinite) || room.width <= 0 || room.depth <= 0) continue;
    const group = new THREE.Group();
    group.name = room.id;
    group.userData.selection = { kind: "room", id: room.id } satisfies Selection;
    root.add(group);
    floor(group, x, z, room.width, room.depth);
    const wallHeight = Math.min(0.38, Math.max(0.18, (Number.isFinite(room.height) && room.height! > 0 ? room.height! : 3.2) * 0.11));
    const wallThickness = Math.min(0.085, room.width / 20, room.depth / 20);
    const wall = (width: number, depth: number, px: number, pz: number, height: number) => {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), wallMaterial);
      mesh.position.set(px, height / 2, pz);
      mesh.receiveShadow = true;
      mesh.castShadow = true;
      group.add(mesh);
      const cap = new THREE.Mesh(new THREE.BoxGeometry(width, 0.012, depth), capMaterial);
      cap.position.set(px, height + 0.006, pz);
      group.add(cap);
    };
    // Door openings use source offsets measured from each wall's minimum X/Z.
    for (const side of ["north", "south", "west", "east"] as const) {
      const horizontal = side === "north" || side === "south";
      const length = horizontal ? room.width : room.depth;
      const fixed = side === "north" ? z - room.depth / 2 : side === "south" ? z + room.depth / 2 : side === "west" ? x - room.width / 2 : x + room.width / 2;
      const base = horizontal ? x - room.width / 2 : z - room.depth / 2;
      const lowHeight = wallHeight * (side === "south" || side === "east" ? 0.34 : 1);
      const entrances = (room.entrances ?? []).filter(entry => entry.wall === side && [entry.offset_m, entry.width_m, entry.height_m].every(Number.isFinite) && entry.offset_m >= 0 && entry.width_m > 0 && entry.offset_m + entry.width_m <= length && entry.height_m > 0).sort((a, b) => a.offset_m - b.offset_m);
      const section = (start: number, end: number) => {
        if (end <= start) return;
        if (horizontal) wall(end - start, wallThickness, base + (start + end) / 2, fixed, lowHeight);
        else wall(wallThickness, end - start, fixed, base + (start + end) / 2, lowHeight);
      };
      let cursor = 0;
      for (const entry of entrances) {
        section(cursor, entry.offset_m);
        cursor = Math.max(cursor, entry.offset_m + entry.width_m);
        const doorway = new THREE.Group();
        doorway.name = entry.id;
        const frameMaterial = new THREE.MeshStandardMaterial({ color: 0x7791a3, metalness: 0.3, roughness: 0.65 });
        const origin = new THREE.Vector3(horizontal ? base + entry.offset_m : fixed, 0, horizontal ? fixed : base + entry.offset_m);
        const tangent = new THREE.Vector3(horizontal ? 1 : 0, 0, horizontal ? 0 : 1);
        const inward = new THREE.Vector3(side === "west" ? 1 : side === "east" ? -1 : 0, 0, side === "north" ? 1 : side === "south" ? -1 : 0);
        for (const distance of [0, entry.width_m]) {
          const post = new THREE.Mesh(new THREE.BoxGeometry(0.045, entry.height_m, 0.045), frameMaterial);
          post.position.copy(origin).addScaledVector(tangent, distance); post.position.y = entry.height_m / 2;
          doorway.add(post);
        }
        const lintel = new THREE.Mesh(new THREE.BoxGeometry(horizontal ? entry.width_m + 0.045 : 0.045, 0.045, horizontal ? 0.045 : entry.width_m + 0.045), frameMaterial);
        lintel.position.copy(origin).addScaledVector(tangent, entry.width_m / 2); lintel.position.y = entry.height_m;
        doorway.add(lintel);
        const arc = Array.from({ length: 21 }, (_, index) => origin.clone().addScaledVector(tangent, Math.cos(index / 20 * Math.PI / 3) * entry.width_m).addScaledVector(inward, Math.sin(index / 20 * Math.PI / 3) * entry.width_m).setY(0.018));
        doorway.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(arc), new THREE.LineBasicMaterial({ color: 0x5a8a9f })));
        const tip = arc[arc.length - 1];
        const leaf = new THREE.Mesh(new THREE.BoxGeometry(entry.width_m, 0.06, 0.035), frameMaterial);
        leaf.position.copy(origin).lerp(tip, 0.5); leaf.position.y = 0.04;
        leaf.rotation.y = -Math.atan2(tip.z - origin.z, tip.x - origin.x);
        doorway.add(leaf);
        const name = label(entry.name || "入口", Math.max(0.6, Math.min(1.2, entry.width_m)));
        name.position.copy(lintel.position); name.position.y += 0.16;
        doorway.add(name);
        group.add(doorway);
      }
      section(cursor, length);
    }
    if (room.label.trim()) {
      const titleWidth = Math.min(1.5, room.width * 0.6);
      const title = label(room.label, titleWidth, 160);
      title.position.set(x, wallHeight + 0.15, z - room.depth / 2);
      group.add(title);
    }
  }
  return root;
}
