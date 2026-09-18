import * as THREE from "three";
import type { SceneRack, Selection } from "./sceneRenderer";

const U = 0.04445;

export function label(text: string, width = 0.72, minimumPixels = 132): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 768;
  canvas.height = 144;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas 2D is unavailable for equipment labels.");
  context.fillStyle = "#f8fafc";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#ced6df";
  context.lineWidth = 5;
  context.strokeRect(2, 2, canvas.width - 4, canvas.height - 4);
  context.fillStyle = "#26364a";
  context.font = "600 58px system-ui, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text.slice(0, 45), 384, 72, 716);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthWrite: false, depthTest: false, toneMapped: false }));
  sprite.scale.set(width, width * canvas.height / canvas.width, 1);
  sprite.userData.layer = "labels";
  sprite.userData.ignorePick = true;
  sprite.userData.labelWorldWidth = width;
  sprite.userData.labelMinimumPixels = minimumPixels;
  sprite.userData.labelAspect = canvas.height / canvas.width;
  sprite.renderOrder = 100;
  return sprite;
}

function deviceTexture(identifier: string, deviceType: string, ports: number, interactive = false): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 192;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas 2D is unavailable for equipment panels.");
  context.fillStyle = "#65717d";
  context.fillRect(0, 0, 1024, 192);
  context.fillStyle = "#56616d";
  context.fillRect(14, 12, 996, 168);
  context.fillStyle = "#dee5eb";
  context.font = "600 26px system-ui, sans-serif";
  context.fillText(identifier.slice(0, 35), 32, 49, 920);
  context.font = "20px system-ui, sans-serif";
  context.fillStyle = "#bec9d1";
  context.fillText(`${deviceType}${ports ? ` · ${ports} ports` : ""}`, 32, 78, 920);
  const visiblePorts = interactive ? 0 : Math.max(0, Math.min(48, Math.floor(ports)));
  const columns = Math.min(24, visiblePorts);
  for (let index = 0; index < visiblePorts; index++) {
    const x = 35 + index % columns * 38;
    const y = 96 + Math.floor(index / columns) * 36;
    context.fillStyle = "#9aabb8";
    context.fillRect(x, y, 29, 27);
    context.fillStyle = "#19232c";
    context.fillRect(x + 3, y + 3, 23, 21);
  }
  // Vent slots are physical panel detail; no invented port or link status is shown.
  if (!visiblePorts && !interactive) {
    context.fillStyle = "#303b45";
    for (let index = 0; index < 29; index++) context.fillRect(35 + index * 31, 108, 17, 35);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

export function buildRacks(racks: SceneRack[]): THREE.Group {
  const root = new THREE.Group();
  const metal = new THREE.MeshStandardMaterial({ color: 0x35414d, roughness: 0.61, metalness: 0.5 });
  const rail = new THREE.MeshStandardMaterial({ color: 0x87939f, roughness: 0.6, metalness: 0.55 });
  const caseMaterial = new THREE.MeshStandardMaterial({ color: 0x333e49, roughness: 0.8, metalness: 0.2 });
  const shellMaterial = new THREE.MeshStandardMaterial({ color: 0x445565, roughness: 0.5, metalness: 0.2, transparent: true, opacity: 0.14, depthWrite: false });
  const geometries = new Map<string, THREE.BoxGeometry>();
  const box = (group: THREE.Group, size: number[], position: number[], material: THREE.Material | THREE.Material[]) => {
    const key = size.join(",");
    let geometry = geometries.get(key);
    if (!geometry) { geometry = new THREE.BoxGeometry(...size as [number, number, number]); geometries.set(key, geometry); }
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(...position as [number, number, number]);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
    return mesh;
  };
  for (const rack of racks) {
    const group = new THREE.Group();
    group.userData.selection = { kind: "rack", id: rack.id } satisfies Selection;
    group.name = rack.id;
    group.position.set(...rack.position);
    group.rotation.y = rack.rotation;
    const width = Math.max(0.5, rack.width), depth = Math.max(0.45, rack.depth);
    const height = Math.max(1, rack.heightU) * U + 0.16;
    box(group, [width, 0.08, depth], [0, 0.06, 0], metal);
    box(group, [width, 0.065, depth], [0, height, 0], metal);
    for (const x of [-1, 1]) for (const z of [-1, 1]) {
      box(group, [0.038, height - 0.08, 0.038], [x * (width / 2 - 0.023), height / 2 + 0.035, z * (depth / 2 - 0.023)], metal);
      box(group, [0.02, height - 0.19, 0.021], [x * Math.min(0.238, width / 2 - 0.06), height / 2 + 0.02, z * (depth / 2 - 0.065)], rail);
    }
    // Short rail marks express actual rack-unit spacing without thousands of meshes.
    const tickPoints: THREE.Vector3[] = [];
    for (let unit = 0; unit <= rack.heightU; unit++) for (const side of [-1, 1]) {
      const x = side * Math.min(0.24, width / 2 - 0.055), y = 0.115 + unit * U;
      tickPoints.push(new THREE.Vector3(x, y, depth / 2 - 0.05), new THREE.Vector3(x + side * 0.012, y, depth / 2 - 0.05));
    }
    group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(tickPoints), new THREE.LineBasicMaterial({ color: 0xbed0dc })));
    const shell = new THREE.Group();
    shell.userData.layer = "shell";
    shell.userData.ignorePick = true;
    box(shell, [0.012, height - 0.13, depth - 0.075], [-width / 2 + 0.01, height / 2 + 0.04, 0], shellMaterial);
    box(shell, [0.012, height - 0.13, depth - 0.075], [width / 2 - 0.01, height / 2 + 0.04, 0], shellMaterial);
    group.add(shell);
    const rackLabel = label(rack.identifier || rack.name);
    rackLabel.position.set(0, height + 0.18, 0);
    group.add(rackLabel);
    for (const device of rack.devices) {
      const item = new THREE.Group();
      item.name = device.id;
      item.userData.selection = { kind: "device", id: device.id } satisfies Selection;
      const panelHeight = Math.max(0.7, device.units) * U - 0.003;
      const panelWidth = Math.min(0.475, width - 0.08);
      const texture = deviceTexture(device.identifier || device.name, device.deviceType, device.portCount, !!device.ports?.length);
      const panelMaterial = new THREE.MeshStandardMaterial({ map: texture, roughness: 0.7, metalness: 0.15 });
      item.position.y = 0.115 + (Math.max(1, device.startU) - 1) * U + panelHeight / 2;
      if (device.face === "rear" || device.face === "back") item.rotation.y = Math.PI;
      box(item, [panelWidth - 0.035, panelHeight * 0.91, depth * 0.63], [0, 0, depth / 2 - 0.065 - depth * 0.63 / 2], caseMaterial);
      box(item, [panelWidth, panelHeight, 0.018], [0, 0, depth / 2 - 0.056], [metal, metal, metal, metal, panelMaterial, metal]);
      for (const x of [-1, 1]) box(item, [0.009, Math.min(0.025, panelHeight * 0.5), 0.007], [x * (panelWidth / 2 - 0.012), 0, depth / 2 - 0.043], rail);
      group.add(item);
      for (const face of ["front", "rear"]) {
        const ports = (device.ports ?? []).filter(port => port.face === face).sort((a, b) => a.positionIndex - b.positionIndex);
        for (const [index, port] of ports.entries()) {
          const x = ((index + 0.5) / ports.length - 0.5) * width * 0.7;
          const z = (face === "rear" ? -1 : 1) * (depth / 2 - 0.047);
          const portWidth = Math.min(0.032, width * 0.7 / ports.length * 0.8);
          const portHeight = Math.min(0.018, panelHeight * 0.45);
          const color = port.blocked ? 0x697581 : port.occupied ? 0x9aa4ad : 0x38a36b;
          box(group, [portWidth + 0.003, portHeight + 0.003, 0.003], [x, item.position.y, z], rail);
          const socket = box(group, [portWidth, portHeight, 0.006], [x, item.position.y, z + (face === "rear" ? -0.003 : 0.003)], new THREE.MeshBasicMaterial({ color, toneMapped: false }));
          socket.name = port.id;
          socket.userData.selection = { kind: "port", id: port.id } satisfies Selection;
          socket.userData.port = port;
          socket.userData.baseColor = color;
        }
      }
    }
    root.add(group);
  }
  return root;
}
