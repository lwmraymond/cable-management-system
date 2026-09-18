export type SpatialShortcutEvent = {
  defaultPrevented?: boolean;
  ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean;
  isComposing?: boolean; keyCode?: number;
  nativeEvent?: { defaultPrevented?: boolean; isComposing?: boolean; keyCode?: number };
  target?: EventTarget | null;
};

/** Shared by canvas and page handlers, including React events bubbling from child controls. */
export function shouldIgnoreSpatialShortcut(event: SpatialShortcutEvent): boolean {
  if (event.defaultPrevented || event.nativeEvent?.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229 || event.nativeEvent?.keyCode === 229) return true;
  const target = event.target;
  if (typeof Element === "undefined" || !(target instanceof Element)) return false;
  if (target.closest("input, textarea, select, [role=combobox]")) return true;
  const editable = target.closest("[contenteditable]");
  return Boolean(editable && editable.getAttribute("contenteditable")?.toLowerCase() !== "false");
}
