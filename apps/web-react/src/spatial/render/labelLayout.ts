export type LabelBox = { id: string; x: number; y: number; width: number; height: number; priority: number; depth: number };

/** Screen-space decluttering keeps selected labels first and never clips text at edges. */
export function visibleLabelIds(labels: LabelBox[], width: number, height: number, reserved: LabelBox[] = []): Set<string> {
  const accepted: LabelBox[] = [];
  const margin = 6, gap = 4;
  const limit = Math.max(1, Math.min(60, Math.floor(width * height / 5500)));
  for (const label of [...labels].sort((a, b) => b.priority - a.priority || a.depth - b.depth || a.id.localeCompare(b.id))) {
    if (![label.x, label.y, label.width, label.height, label.depth].every(Number.isFinite) || label.depth <= 0 || label.width <= 0 || label.height <= 0) continue;
    if (label.x < margin || label.y < margin || label.x + label.width > width - margin || label.y + label.height > height - margin) continue;
    if ([...reserved, ...accepted].some(other => label.x < other.x + other.width + gap && label.x + label.width + gap > other.x && label.y < other.y + other.height + gap && label.y + label.height + gap > other.y)) continue;
    accepted.push(label);
    if (accepted.length >= limit) break;
  }
  return new Set(accepted.map(label => label.id));
}
