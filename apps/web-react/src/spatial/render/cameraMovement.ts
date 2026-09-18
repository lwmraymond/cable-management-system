import { MathUtils, Quaternion, Vector3, type PerspectiveCamera } from "three";

type MovementOptions = { isAllowed: () => boolean; onMove: () => void };
const movementKeys = new Set(["KeyW", "KeyS", "KeyA", "KeyD"]);

/** Focus-scoped horizontal camera translation; only held movement keys schedule frames. */
export class CameraMovement {
  private readonly document: Document;
  private readonly window: Window | null;
  private readonly keys = new Set<string>();
  private readonly pointers = new Set<number>();
  private readonly forward = new Vector3();
  private readonly right = new Vector3();
  private readonly delta = new Vector3();
  private readonly worldUp = new Vector3(0, 1, 0);
  private readonly rotation = new Quaternion();
  private frame: number | null = null;
  private lastTime: number | null = null;
  private accelerated = false;
  private disposed = false;

  constructor(private readonly canvas: HTMLCanvasElement, private readonly camera: PerspectiveCamera, private readonly target: Vector3, private readonly options: MovementOptions) {
    this.document = canvas.ownerDocument;
    this.window = this.document.defaultView;
    canvas.addEventListener("keydown", this.keyDown);
    canvas.addEventListener("blur", this.blur);
    canvas.addEventListener("compositionstart", this.stop);
    canvas.addEventListener("pointerdown", this.pointerDown, true);
    canvas.addEventListener("lostpointercapture", this.pointerUp);
    this.document.addEventListener("keyup", this.keyUp, true);
    this.document.addEventListener("pointerup", this.pointerUp, true);
    this.document.addEventListener("pointercancel", this.pointerUp, true);
    this.document.addEventListener("visibilitychange", this.visibilityChange);
    this.window?.addEventListener("blur", this.blur);
  }

  private allowed(): boolean {
    return !this.disposed && this.document.activeElement === this.canvas && !this.document.hidden && this.pointers.size === 0 && this.options.isAllowed();
  }

  private code(event: KeyboardEvent): string | null {
    if (event.code) return movementKeys.has(event.code) ? event.code : null;
    const code = `Key${event.key.toUpperCase()}`;
    return movementKeys.has(code) ? code : null;
  }

  private readonly keyDown = (event: KeyboardEvent): void => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.keyCode === 229) { this.stop(); return; }
    if (["escape", "f", "1", "2", "3"].includes(event.key.toLowerCase())) { this.stop(); return; }
    if (!this.allowed()) { this.stop(); return; }
    this.accelerated = event.shiftKey;
    const code = this.code(event);
    // A cancelled gesture requires a fresh press, not the OS's repeating keydown.
    if (!code || (event.repeat && !this.keys.has(code))) return;
    event.preventDefault();
    this.keys.add(code);
    this.schedule();
  };

  private readonly keyUp = (event: KeyboardEvent): void => {
    const code = this.code(event);
    if (code) this.keys.delete(code);
    this.accelerated = event.shiftKey;
    if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing || !this.allowed()) this.stop();
    else this.schedule();
  };

  private readonly pointerDown = (event: PointerEvent): void => { this.pointers.add(event.pointerId); this.stop(); };
  private readonly pointerUp = (event: PointerEvent): void => { this.pointers.delete(event.pointerId); };
  private readonly blur = (): void => { this.pointers.clear(); this.stop(); };
  private readonly visibilityChange = (): void => { if (this.document.hidden) this.blur(); };

  private axes(): [number, number] {
    return [Number(this.keys.has("KeyD")) - Number(this.keys.has("KeyA")), Number(this.keys.has("KeyW")) - Number(this.keys.has("KeyS"))];
  }

  private schedule(): void {
    const [sideways, forward] = this.axes();
    if (!sideways && !forward) { this.pause(); return; }
    if (this.frame === null && this.allowed()) this.frame = requestAnimationFrame(this.tick);
  }

  private readonly tick = (time: number): void => {
    this.frame = null;
    if (!this.allowed()) { this.stop(); return; }
    const seconds = this.lastTime === null ? 0 : MathUtils.clamp((time - this.lastTime) / 1000, 0, 0.05);
    this.lastTime = time;
    const [sideways, forwards] = this.axes();
    if (seconds > 0 && (sideways || forwards)) {
      this.camera.getWorldDirection(this.forward).setY(0);
      if (this.forward.lengthSq() < 0.000001) {
        this.forward.copy(this.camera.up).setY(0);
        if (this.forward.lengthSq() < 0.000001) this.forward.set(0, 1, 0).applyQuaternion(this.camera.getWorldQuaternion(this.rotation)).setY(0);
      }
      if (this.forward.lengthSq() < 0.000001) this.forward.set(0, 0, -1);
      this.forward.normalize();
      this.right.crossVectors(this.forward, this.worldUp).normalize();
      const speed = MathUtils.clamp(Math.sqrt(this.camera.position.distanceTo(this.target)) * 0.65, 0.25, 3) * (this.accelerated ? 2.25 : 1);
      this.delta.copy(this.forward).multiplyScalar(forwards).addScaledVector(this.right, sideways).normalize().multiplyScalar(speed * seconds);
      this.camera.position.add(this.delta);
      this.target.add(this.delta);
      this.options.onMove();
    }
    this.schedule();
  };

  private pause(): void {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.lastTime = null;
  }

  readonly stop = (): void => { this.keys.clear(); this.accelerated = false; this.pause(); };

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stop();
    this.pointers.clear();
    this.canvas.removeEventListener("keydown", this.keyDown);
    this.canvas.removeEventListener("blur", this.blur);
    this.canvas.removeEventListener("compositionstart", this.stop);
    this.canvas.removeEventListener("pointerdown", this.pointerDown, true);
    this.canvas.removeEventListener("lostpointercapture", this.pointerUp);
    this.document.removeEventListener("keyup", this.keyUp, true);
    this.document.removeEventListener("pointerup", this.pointerUp, true);
    this.document.removeEventListener("pointercancel", this.pointerUp, true);
    this.document.removeEventListener("visibilitychange", this.visibilityChange);
    this.window?.removeEventListener("blur", this.blur);
  }
}
