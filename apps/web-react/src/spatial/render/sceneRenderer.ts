import { visibleLabelIds, type LabelBox } from "./labelLayout";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { buildRacks } from "./rackGeometry";
import { buildPaths } from "./pathGeometry";
import { buildRooms } from "./roomGeometry";
import { shouldRefitViewport } from "./viewport";
import { CameraMovement } from "./cameraMovement";
import { buildMeasurement, buildPathDimensions, insideRoom, validMeasurement } from "./sceneOverlays";

export type Selection = { kind: "room" | "rack" | "device" | "port" | "pathway" | "cable"; id: string };
export type ScenePort = { id: string; identifier: string; mediaType: string; face: string; positionIndex: number; occupied: boolean; blocked?: boolean };
export type SceneEntrance = { id: string; name: string; wall: "north" | "south" | "east" | "west"; offset_m: number; width_m: number; height_m: number };
export type SceneMeasurementPoint = { locationId: string; point: [number, number, number] };
export type ScenePlacement = { locationId: string; positionX: number; positionY: number; rackId?: string; startU?: number };
export type SceneDevice = { id: string; identifier: string; name: string; deviceType: string; startU: number; units: number; face: string; portCount: number; ports?: ScenePort[] };
export type SceneRack = { id: string; identifier: string; name: string; position: [number, number, number]; rotation: number; width: number; depth: number; heightU: number; devices: SceneDevice[]; locationId?: string };
export type SceneRoom = { id: string; label: string; center: [number, number]; width: number; depth: number; height?: number; entrances?: SceneEntrance[] };
export type ScenePath = { id: string; identifier: string; kind: "pathway" | "cable"; points: [number, number, number][]; selected?: boolean; dashed?: boolean; pathwayType?: string; width?: number; depth?: number; draft?: boolean; dimension?: { label: string; lengthM: number; basis: "coordinates" | "estimate" } };
export type SceneData = { racks: SceneRack[]; rooms: SceneRoom[]; paths: ScenePath[]; focusRackId?: string };
type Layers = { labels: boolean; shell: boolean; pathways: boolean; dimensions?: boolean };
type View = "iso" | "front" | "rear" | "top";

function disposeObject(root: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  root.traverse(object => {
    if (object instanceof THREE.InstancedMesh) object.dispose();
    if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Sprite) {
      if ("geometry" in object) geometries.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.add(material);
    }
  });
  for (const material of materials) {
    for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
    material.dispose();
  }
  geometries.forEach(geometry => geometry.dispose());
  textures.forEach(texture => texture.dispose());
  root.clear();
}

function visible(object: THREE.Object3D): boolean {
  for (let item: THREE.Object3D | null = object; item; item = item.parent) {
    if (!item.visible || item.userData.ignorePick) return false;
  }
  return true;
}

function objectBounds(root: THREE.Object3D): THREE.Box3 {
  root.updateWorldMatrix(true, true);
  const bounds = new THREE.Box3();
  root.traverse(object => {
    // Screen-sized captions are overlays, never part of the physical framing box.
    if ((object instanceof THREE.Mesh || object instanceof THREE.Line) && !object.userData.draft) bounds.expandByObject(object);
  });
  return bounds;
}

export class InfrastructureScene {
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(38, 1, 0.01, 1000);
  private readonly renderer: THREE.WebGLRenderer;
  private readonly controls: OrbitControls;
  private readonly movement: CameraMovement;
  private readonly resizeObserver: ResizeObserver;
  private readonly light = new THREE.DirectionalLight(0xffffff, 3.2);
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private root = new THREE.Group();
  private readonly outline = new THREE.Box3Helper(new THREE.Box3(), 0x2788c7);
  private readonly bounds = new THREE.Box3();
  private frameBounds = new THREE.Box3();
  private frameRotation = 0;
  private selection: Selection | null = null;
  private layers: Layers = { labels: true, shell: true, pathways: true };
  private view: View = "iso";
  private frame: number | null = null;
  private disposed = false;
  private populated = false;
  private viewportHeight = 1;
  private viewportWidth = 1;
  private fittedViewport: [number, number] = [0, 0];
  private data: SceneData = { rooms: [], racks: [], paths: [] };
  private connectionMode = false;
  private placementMode = false;
  private measurementMode = false;
  private measurementPoints: SceneMeasurementPoint[] = [];
  private measurementOverlay = new THREE.Group();
  private dimensionOverlay = new THREE.Group();
  private panMode = false;
  private placementMarker: THREE.Group | null = null;
  private connection: { portId: string; pointerId: number; origin: THREE.Vector3; plane: THREE.Plane } | null = null;
  private connectionPreview: Line2 | null = null;
  private pointerStart: { x: number; y: number; id: number; dragged: boolean } | null = null;

  constructor(private readonly canvas: HTMLCanvasElement, private readonly onSelect: (selection: Selection) => void, private readonly options: { onPortConnect?: (a: string, b: string) => void; onPlacement?: (placement: ScenePlacement | null) => void; onMeasure?: (point: SceneMeasurementPoint | null) => void; isCameraMovementAllowed?: () => boolean } = {}) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.scene.background = new THREE.Color(0xeef2f6);
    this.scene.add(new THREE.HemisphereLight(0xf6faff, 0xa1aab6, 2.4));
    this.light.position.set(5, 9, 6);
    this.light.castShadow = true;
    this.light.shadow.mapSize.set(2048, 2048);
    this.light.shadow.normalBias = 0.018;
    this.light.shadow.bias = -0.00015;
    this.scene.add(this.light, this.light.target, this.root, this.outline);
    this.outline.visible = false;
    const outlineMaterial = this.outline.material as THREE.LineBasicMaterial;
    outlineMaterial.depthTest = false;
    outlineMaterial.transparent = true;
    outlineMaterial.opacity = 0.86;
    this.outline.renderOrder = 20;
    this.camera.position.set(3, 3, 4);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = false; // Render only for data, resize or interaction changes.
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 0.18;
    this.controls.maxDistance = 100;
    this.controls.minPolarAngle = 0.005;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.015;
    this.controls.addEventListener("change", this.invalidate);
    this.movement = new CameraMovement(canvas, this.camera, this.controls.target, {
      isAllowed: () => !this.disposed && this.controls.enabled && !this.pointerStart && !this.connectionMode && !this.placementMode && !this.measurementMode && (this.options.isCameraMovementAllowed?.() ?? true),
      onMove: () => { this.controls.update(); this.invalidate(); },
    });
    this.raycaster.params.Line = { threshold: 0.035 };
    this.raycaster.params.Line2 = { threshold: 2 };
    canvas.addEventListener("pointerdown", this.pointerDown, true);
    canvas.addEventListener("pointerup", this.pointerUp, true);
    canvas.addEventListener("pointermove", this.pointerMove);
    canvas.addEventListener("pointerleave", this.hidePlacementMarker);
    canvas.addEventListener("pointercancel", this.pointerCancel);
    canvas.addEventListener("lostpointercapture", this.pointerCancel);
    this.resizeObserver = new ResizeObserver(this.resize);
    this.resizeObserver.observe(canvas);
    this.resize();
  }

  private readonly invalidate = (): void => {
    if (this.disposed || this.frame !== null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      if (!this.disposed) { this.updateLabels(); this.renderer.render(this.scene, this.camera); }
    });
  };

  private readonly resize = (): void => {
    if (this.disposed) return;
    const { width, height } = this.canvas.getBoundingClientRect();
    if (width < 1 || height < 1) return;
    const refit = this.populated && shouldRefitViewport(this.fittedViewport, [width, height]);
    this.viewportWidth = width;
    this.viewportHeight = height;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.scene.traverse(object => {
      if (object instanceof Line2) object.material.resolution.set(width, height);
    });
    if (refit) this.fit(this.frameBounds);
    this.invalidate();
  };

  private updateLabels(): void {
    this.camera.updateMatrixWorld();
    this.scene.updateMatrixWorld(true);
    const world = new THREE.Vector3();
    const projected = new THREE.Vector3();
    const boxes: LabelBox[] = [];
    const reserved: LabelBox[] = [];
    const labels = new Map<string, THREE.Sprite>();
    const selectedRack = this.data.racks.find(rack => rack.id === this.selection?.id || rack.devices.some(device => device.id === this.selection?.id || device.ports?.some(port => port.id === this.selection?.id)));
    const factor = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) / this.viewportHeight;
    this.scene.traverse(object => {
      if (!(object instanceof THREE.Sprite) || !object.userData.labelMinimumPixels) return;
      object.getWorldPosition(world);
      projected.copy(world).project(this.camera);
      world.applyMatrix4(this.camera.matrixWorldInverse);
      const metresPerPixel = Math.max(this.camera.near, -world.z) * factor;
      const minimum = object.userData.labelMinimumPixels as number;
      const naturalPixels = (object.userData.labelWorldWidth as number) / metresPerPixel;
      const pixels = THREE.MathUtils.clamp(naturalPixels, minimum, minimum * 1.2);
      const aspect = object.userData.labelAspect as number;
      const width = pixels * metresPerPixel;
      object.scale.set(width, width * aspect, 1);
      const box: LabelBox = { id: object.uuid, x: (projected.x + 1) * this.viewportWidth / 2 - pixels / 2,
        y: (1 - projected.y) * this.viewportHeight / 2 - pixels * aspect / 2,
        width: pixels, height: pixels * aspect, depth: -world.z, priority: 0 };
      if (object.userData.layer !== "labels") {
        // Dimensions retain independent visibility, but object captions avoid their space.
        if (!object.visible || world.z >= -this.camera.near || projected.z < -1 || projected.z > 1) return;
        for (let parent = object.parent; parent; parent = parent.parent) if (!parent.visible) return;
        reserved.push(box);
        return;
      }
      labels.set(object.uuid, object);
      object.visible = false;
      if (!this.layers.labels || world.z >= -this.camera.near || projected.z < -1 || projected.z > 1) return;
      let owner: Selection | undefined;
      for (let parent = object.parent; parent; parent = parent.parent) {
        if (!parent.visible) return;
        if (!owner) owner = parent.userData.selection as Selection | undefined;
      }
      const selected = owner && (owner.id === this.selection?.id || owner.kind === "rack" && owner.id === selectedRack?.id);
      boxes.push({ ...box, priority: selected ? 1000 : owner?.kind === "room" ? 100 : 10 });
    });
    for (const id of visibleLabelIds(boxes, this.viewportWidth, this.viewportHeight, reserved)) labels.get(id)!.visible = true;
  }

  private setRay(clientX: number, clientY: number): boolean {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height || clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) return false;
    this.pointer.set((clientX - rect.left) / rect.width * 2 - 1, -(clientY - rect.top) / rect.height * 2 + 1);
    this.camera.updateMatrixWorld();
    this.raycaster.setFromCamera(this.pointer, this.camera);
    return true;
  }

  private pickAt(clientX: number, clientY: number): { object: THREE.Object3D; selection: Selection; point: THREE.Vector3 } | null {
    if (!this.setRay(clientX, clientY)) return null;
    for (const hit of this.raycaster.intersectObject(this.root, true)) {
      if (!visible(hit.object)) continue;
      for (let object: THREE.Object3D | null = hit.object; object; object = object.parent) {
        const selection = object.userData.selection as Selection | undefined;
        if (selection) return { object, selection, point: hit.point };
      }
    }
    return null;
  }

  private readonly pointerDown = (event: PointerEvent): void => {
    this.pointerStart = event.button === 0 && event.isPrimary ? { x: event.clientX, y: event.clientY, id: event.pointerId, dragged: false } : null;
    if (this.pointerStart) this.canvas.focus({ preventScroll: true });
    if (this.measurementMode || this.placementMode || !this.connectionMode || !this.pointerStart) return;
    const hit = this.pickAt(event.clientX, event.clientY);
    const port = hit?.object.userData.port as ScenePort | undefined;
    if (hit?.selection.kind !== "port" || !port || port.occupied || port.blocked) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const origin = hit.object.getWorldPosition(new THREE.Vector3());
    this.connection = { portId: port.id, pointerId: event.pointerId, origin, plane: new THREE.Plane().setFromNormalAndCoplanarPoint(this.camera.getWorldDirection(new THREE.Vector3()), origin) };
    this.controls.enabled = false;
    this.canvas.setPointerCapture(event.pointerId);
    this.select(hit.selection);
    this.updateConnectionPreview(event.clientX, event.clientY);
  };

  private readonly pointerMove = (event: PointerEvent): void => {
    const start = this.pointerStart;
    if (start && event.pointerId === start.id && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 6) start.dragged = true;
    if (this.placementMode && !this.measurementMode) this.updatePlacementMarker(event.clientX, event.clientY);
    if (this.connection?.pointerId === event.pointerId) {
      event.preventDefault();
      this.updateConnectionPreview(event.clientX, event.clientY);
    }
  };

  private updateConnectionPreview(x: number, y: number): void {
    const drag = this.connection;
    if (!drag) return;
    const hit = this.pickAt(x, y);
    const port = hit?.object.userData.port as ScenePort | undefined;
    let end = hit?.selection.kind === "port" ? hit.object.getWorldPosition(new THREE.Vector3()) : null;
    if (!end && this.setRay(x, y)) end = this.raycaster.ray.intersectPlane(drag.plane, new THREE.Vector3());
    if (!end || drag.origin.distanceToSquared(end) < 0.00000001) return;
    const geometry = new LineGeometry().setFromPoints([drag.origin, end]);
    if (!this.connectionPreview) {
      const material = new LineMaterial({ color: 0xd79737, depthTest: true, depthWrite: false, transparent: true, opacity: 0.98, toneMapped: false });
      material.linewidth = 3.5;
      const rect = this.canvas.getBoundingClientRect();
      material.resolution.set(rect.width, rect.height);
      this.connectionPreview = new Line2(geometry, material);
      this.connectionPreview.userData.ignorePick = true;
      this.connectionPreview.renderOrder = 80;
      this.scene.add(this.connectionPreview);
    } else {
      this.connectionPreview.geometry.dispose();
      this.connectionPreview.geometry = geometry;
    }
    this.connectionPreview.material.color.set(port && !port.occupied && !port.blocked && port.id !== drag.portId ? 0x25899d : 0xd79737);
    this.invalidate();
  }

  private cancelConnection(): void {
    const pointerId = this.connection?.pointerId;
    this.connection = null;
    this.controls.enabled = true;
    if (pointerId !== undefined && this.canvas.hasPointerCapture(pointerId)) this.canvas.releasePointerCapture(pointerId);
    if (this.connectionPreview) {
      this.scene.remove(this.connectionPreview);
      this.connectionPreview.geometry.dispose();
      this.connectionPreview.material.dispose();
      this.connectionPreview = null;
    }
    this.invalidate();
  }

  private readonly pointerCancel = (): void => { this.pointerStart = null; this.cancelConnection(); };

  private readonly pointerUp = (event: PointerEvent): void => {
    const start = this.pointerStart;
    this.pointerStart = null;
    if (this.connection?.pointerId === event.pointerId) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const source = this.connection.portId;
      const hit = this.pickAt(event.clientX, event.clientY);
      const port = hit?.object.userData.port as ScenePort | undefined;
      this.cancelConnection();
      if (hit?.selection.kind === "port" && port && !port.occupied && !port.blocked && port.id !== source) this.options.onPortConnect?.(source, port.id);
      else this.onSelect({ kind: "port", id: source });
      return;
    }
    if (!start || start.dragged || event.pointerId !== start.id || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 6) return;
    if (this.measurementMode) { this.options.onMeasure?.(this.measurementAt(event.clientX, event.clientY)); return; }
    if (this.placementMode) { this.options.onPlacement?.(this.placementAt(event.clientX, event.clientY)); return; }
    const hit = this.pickAt(event.clientX, event.clientY);
    if (hit) { this.select(hit.selection); this.onSelect(hit.selection); }
  };

  setConnectionMode(enabled: boolean): void { this.pointerCancel(); this.connectionMode = enabled; }

  setPlacementMode(enabled: boolean): void {
    this.pointerCancel();
    this.placementMode = enabled;
    if (!enabled && this.placementMarker) {
      this.scene.remove(this.placementMarker);
      disposeObject(this.placementMarker);
      this.placementMarker = null;
    }
    this.updateCursor();
    this.invalidate();
  }

  setPanMode(enabled: boolean): void {
    this.pointerStart = null;
    this.panMode = enabled;
    this.controls.mouseButtons.LEFT = enabled ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    this.controls.touches.ONE = enabled ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
    this.controls.touches.TWO = enabled ? THREE.TOUCH.DOLLY_ROTATE : THREE.TOUCH.DOLLY_PAN;
    this.updateCursor();
  }

  private updateCursor(): void { this.canvas.style.cursor = this.measurementMode || this.placementMode ? "crosshair" : this.panMode ? "grab" : ""; }

  private readonly hidePlacementMarker = (): void => {
    if (this.placementMarker?.visible) { this.placementMarker.visible = false; this.invalidate(); }
  };

  private updatePlacementMarker(clientX: number, clientY: number): void {
    const placement = this.placementAt(clientX, clientY);
    const room = placement && this.data.rooms.find(item => item.id === placement.locationId);
    if (!placement || !room) { this.hidePlacementMarker(); return; }
    if (!this.placementMarker) {
      const marker = new THREE.Group();
      marker.name = "__placement_reticle__";
      marker.userData.ignorePick = true;
      const material = new THREE.MeshBasicMaterial({ color: 0x1685ce, side: THREE.DoubleSide, depthTest: true, depthWrite: false, toneMapped: false });
      marker.add(new THREE.Mesh(new THREE.RingGeometry(0.145, 0.165, 48), material));
      const cross = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-0.24, 0, 0), new THREE.Vector3(0.24, 0, 0),
        new THREE.Vector3(0, -0.24, 0), new THREE.Vector3(0, 0.24, 0),
      ]), new THREE.LineBasicMaterial({ color: 0x1685ce, depthTest: true, depthWrite: false, toneMapped: false }));
      marker.add(cross);
      marker.rotation.x = -Math.PI / 2;
      this.scene.add(marker); // Preview stays outside persistent geometry and framing bounds.
      this.placementMarker = marker;
    }
    this.placementMarker.position.set(room.center[0] - room.width / 2 + placement.positionX, 0.018, room.center[1] - room.depth / 2 + placement.positionY);
    this.placementMarker.visible = true;
    this.invalidate();
  }

  setMeasurementMode(enabled: boolean): void {
    this.pointerCancel();
    this.measurementMode = enabled;
    this.hidePlacementMarker();
    this.updateCursor();
  }

  setMeasurement(points: SceneMeasurementPoint[]): void {
    this.measurementPoints = validMeasurement(points, this.data.rooms);
    this.scene.remove(this.measurementOverlay);
    disposeObject(this.measurementOverlay);
    this.measurementOverlay = buildMeasurement(this.measurementPoints, this.canvas.getBoundingClientRect());
    this.scene.add(this.measurementOverlay);
    this.updateOverlayVisibility();
    this.invalidate();
  }

  private measurementAt(clientX: number, clientY: number): SceneMeasurementPoint | null {
    if (this.measurementPoints.length >= 64) return null;
    const hit = this.pickAt(clientX, clientY);
    if (!this.setRay(clientX, clientY)) return null;
    const point = hit?.point.clone() ?? this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
    if (!point) return null;
    const coordinates = point.toArray() as [number, number, number];
    const room = this.data.rooms.find(room => insideRoom(coordinates, room));
    if (!room || (this.measurementPoints.length && room.id !== this.measurementPoints[0].locationId)) return null;
    coordinates[1] = Math.max(0, coordinates[1]);
    return { locationId: room.id, point: coordinates };
  }

  private updateDimensions(): void {
    this.scene.remove(this.dimensionOverlay);
    disposeObject(this.dimensionOverlay);
    this.dimensionOverlay = buildPathDimensions(this.data.paths, this.selection);
    this.scene.add(this.dimensionOverlay);
    this.updateOverlayVisibility();
  }

  private updateOverlayVisibility(): void {
    this.dimensionOverlay.visible = this.layers.dimensions !== false;
    this.dimensionOverlay.children.forEach(object => { object.visible = object.userData.dimensionPathKind !== "pathway" || this.layers.pathways; });
    this.measurementOverlay.children.forEach(object => { if (object instanceof THREE.Sprite) object.visible = this.layers.dimensions !== false; });
  }

  placementAt(clientX: number, clientY: number): ScenePlacement | null {
    const hit = this.pickAt(clientX, clientY);
    let rackId: string | undefined;
    for (let object: THREE.Object3D | null = hit?.object ?? null; object; object = object.parent) {
      if ((object.userData.selection as Selection | undefined)?.kind === "rack") { rackId = object.userData.selection.id; break; }
    }
    const rack = this.data.racks.find(item => item.id === rackId);
    if (!this.setRay(clientX, clientY)) return null;
    const point = rack && hit ? hit.point : this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
    if (!point) return null;
    const room = this.data.rooms.find(item => rack?.locationId ? item.id === rack.locationId : Math.abs(point.x - item.center[0]) <= item.width / 2 && Math.abs(point.z - item.center[1]) <= item.depth / 2);
    if (!room) return null;
    return { locationId: room.id, positionX: THREE.MathUtils.clamp(point.x - room.center[0] + room.width / 2, 0, room.width), positionY: THREE.MathUtils.clamp(point.z - room.center[1] + room.depth / 2, 0, room.depth),
      ...(rack ? { rackId: rack.id, startU: THREE.MathUtils.clamp(Math.floor((point.y - rack.position[1] - 0.115) / 0.04445) + 1, 1, rack.heightU) } : {}) };
  }

  setData(data: SceneData, options?: { fit?: boolean }): void {
    this.cancelConnection();
    this.hidePlacementMarker();
    this.data = data;
    this.setMeasurement(this.measurementPoints);
    this.scene.remove(this.root);
    disposeObject(this.root);
    this.root = new THREE.Group();
    this.root.add(buildRacks(data.racks));
    this.root.add(buildPaths(data.paths, this.canvas.getBoundingClientRect()));
    this.bounds.copy(objectBounds(this.root));
    if (this.bounds.isEmpty()) this.bounds.set(new THREE.Vector3(-2, 0, -2), new THREE.Vector3(2, 0.1, 2));
    this.root.add(buildRooms(data.rooms, this.bounds));
    // Overview includes room extents; explicit rack/device focus still uses that object.
    this.bounds.copy(objectBounds(this.root));
    this.scene.add(this.root);
    this.root.updateMatrixWorld(true);
    this.setLayers(this.layers);
    this.select(this.selection);
    const center = this.bounds.getCenter(new THREE.Vector3());
    const size = this.bounds.getSize(new THREE.Vector3());
    const radius = Math.max(3, size.length());
    this.light.position.copy(center).add(new THREE.Vector3(radius * 0.7, radius * 1.6, radius));
    this.light.target.position.copy(center);
    Object.assign(this.light.shadow.camera, { left: -radius, right: radius, top: radius, bottom: -radius, near: 0.1, far: radius * 5 });
    this.light.shadow.camera.updateProjectionMatrix();
    this.controls.maxDistance = Math.max(15, size.length() * 10);
    if (options?.fit || !this.populated) this.focus();
    this.populated = true;
    this.invalidate();
  }

  select(selection: Selection | null): void {
    this.selection = selection;
    this.outline.visible = false;
    this.root.traverse(object => {
      const item = object.userData.selection as Selection | undefined;
      const selected = !!item && item.kind === selection?.kind && item.id === selection.id;
      if (object.userData.baseColor !== undefined && (object instanceof THREE.Mesh || object instanceof THREE.Line)) {
        const material = object.material as THREE.MeshStandardMaterial | THREE.LineBasicMaterial | LineMaterial;
        material.color.set(selected ? 0x1685ce : object.userData.baseColor as number);
        if (object instanceof Line2) {
          object.material.linewidth = object.userData.draft ? 3.5 : selected ? 4.5 : 2.4;
          object.renderOrder = object.userData.draft ? 80 : selected ? 3 : 1;
        }
      }
      if (selected && (item?.kind === "room" || item?.kind === "rack" || item?.kind === "device" || item?.kind === "port")) {
        this.outline.box.copy(objectBounds(object)).expandByScalar(0.012);
        this.outline.visible = true;
      }
    });
    this.updateDimensions();
    this.invalidate();
  }

  setLayers(layers: Layers): void {
    this.layers = layers;
    this.root.traverse(object => {
      const layer = object.userData.layer as keyof Layers | undefined;
      if (layer) object.visible = layers[layer] !== false;
    });
    this.updateOverlayVisibility();
    this.invalidate();
  }

  setView(view: View): void { this.view = view; this.fit(this.frameBounds.isEmpty() ? this.bounds : this.frameBounds); }

  focus(id?: string): void {
    const targetId = id ?? this.data.focusRackId;
    const object = targetId ? this.root.getObjectByName(targetId) : undefined;
    const kind = (object?.userData.selection as Selection | undefined)?.kind;
    if (object && (kind === "cable" || kind === "pathway")) {
      this.frameBounds.makeEmpty();
      // A single connection can contain disconnected, independently rendered segments.
      this.root.traverse(segment => {
        if (segment.name === id) this.frameBounds.union(objectBounds(segment));
      });
      this.frameRotation = 0;
    } else {
      this.frameBounds.copy(object ? objectBounds(object) : this.bounds);
      this.frameRotation = object ? new THREE.Euler().setFromQuaternion(object.getWorldQuaternion(new THREE.Quaternion()), "YXZ").y : 0;
    }
    this.fit(this.frameBounds);
  }

  private fit(bounds: THREE.Box3): void {
    if (bounds.isEmpty()) return;
    this.fittedViewport = [this.viewportWidth, this.viewportHeight];
    const center = bounds.getCenter(new THREE.Vector3());
    const verticalTan = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const horizontalTan = verticalTan * this.camera.aspect;
    const directions = { iso: new THREE.Vector3(1.4, 1.05, 1.65), front: new THREE.Vector3(0, 0.015, 1), rear: new THREE.Vector3(0, 0.015, -1), top: new THREE.Vector3(0, 1, 0.006) };
    const direction = directions[this.view].applyAxisAngle(new THREE.Vector3(0, 1, 0), this.frameRotation).normalize();
    const right = new THREE.Vector3(0, 1, 0).cross(direction).normalize();
    const up = direction.clone().cross(right);
    let distance = 0.4;
    // Fit all eight projected corners; a room-wide sphere leaves too much empty space.
    for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
      const point = new THREE.Vector3(x, y, z).sub(center);
      const near = point.dot(direction);
      distance = Math.max(distance, near + Math.abs(point.dot(right)) / horizontalTan, near + Math.abs(point.dot(up)) / verticalTan);
    }
    distance *= 1.12;
    this.controls.target.copy(center);
    this.camera.position.copy(center).add(direction.multiplyScalar(distance));
    this.camera.far = Math.max(100, distance * 20);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.invalidate();
  }

  zoom(factor: number): void {
    if (!Number.isFinite(factor) || factor <= 0) return;
    const offset = this.camera.position.clone().sub(this.controls.target);
    const distance = THREE.MathUtils.clamp(offset.length() / factor, this.controls.minDistance, this.controls.maxDistance);
    this.camera.position.copy(this.controls.target).add(offset.setLength(distance));
    this.controls.update();
    this.invalidate();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.movement.dispose();
    this.cancelConnection();
    this.setPlacementMode(false);
    this.canvas.style.cursor = "";
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener("pointerdown", this.pointerDown, true);
    this.canvas.removeEventListener("pointerup", this.pointerUp, true);
    this.canvas.removeEventListener("pointermove", this.pointerMove);
    this.canvas.removeEventListener("pointerleave", this.hidePlacementMarker);
    this.canvas.removeEventListener("pointercancel", this.pointerCancel);
    this.canvas.removeEventListener("lostpointercapture", this.pointerCancel);
    this.controls.removeEventListener("change", this.invalidate);
    this.controls.dispose();
    disposeObject(this.root);
    disposeObject(this.dimensionOverlay);
    disposeObject(this.measurementOverlay);
    this.outline.geometry.dispose();
    (this.outline.material as THREE.LineBasicMaterial).dispose();
    this.light.shadow.dispose();
    this.scene.clear();
    this.renderer.renderLists.dispose();
    this.renderer.dispose();
  }
}
