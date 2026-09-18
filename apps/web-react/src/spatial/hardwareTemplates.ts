import type { HardwareKind } from "./InstallPalette";
const ports = (count: number, media: "copper" | "fiber", face = "front", mapping = false) => ({ count, prefix: `${media === "fiber" ? "LC" : "P"}${face === "rear" ? "R" : ""}`, face, connector_type: media === "fiber" ? "LC" : "RJ45", media_type: media, ...(mapping ? { mapping_key: "panel" } : {}) });
export const hardwareTemplates = {
  patch_panel: { label: "标准铜配线架 · 24口 · 1U", manufacturer: "Generic", model: "CMS Copper Panel 24", device_type: "patch_panel", rack_units: 1, width_mm: 482, depth_mm: 300, port_blueprint: [ports(24, "copper", "front", true), ports(24, "copper", "rear", true)] },
  fiber_panel: { label: "标准光纤配线架 · 24 LC · 1U", manufacturer: "Generic", model: "CMS Fiber Panel 24 LC", device_type: "fiber_patch_panel", rack_units: 1, width_mm: 482, depth_mm: 300, port_blueprint: [ports(24, "fiber", "front", true), ports(24, "fiber", "rear", true)] },
  switch: { label: "标准交换机 · 24口 · 1U", manufacturer: "Generic", model: "CMS Switch 24", device_type: "switch", rack_units: 1, width_mm: 482, depth_mm: 350, port_blueprint: [ports(24, "copper")] },
  server: { label: "标准服务器 · 4口 · 2U", manufacturer: "Generic", model: "CMS Server 2U", device_type: "server", rack_units: 2, width_mm: 482, depth_mm: 600, port_blueprint: [ports(4, "copper")] },
} satisfies Record<HardwareKind, unknown>;
