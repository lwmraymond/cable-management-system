import { describe, expect, it } from "vitest";
import { shouldIgnoreSpatialShortcut } from "./keyboardShortcuts";

describe("shared spatial shortcut guard", () => {
  it.each([{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { isComposing: true }, { keyCode: 229 }])("ignores editing modifiers and IME: %j", options => {
    expect(shouldIgnoreSpatialShortcut(new KeyboardEvent("keydown", { key: "Escape", ...options }))).toBe(true);
  });
  it("accepts React native IME state and already-consumed events", () => {
    expect(shouldIgnoreSpatialShortcut({ nativeEvent: { isComposing: true } })).toBe(true);
    expect(shouldIgnoreSpatialShortcut({ nativeEvent: { keyCode: 229 } })).toBe(true);
    const event = new KeyboardEvent("keydown", { key: "Escape", cancelable: true });
    event.preventDefault();
    expect(shouldIgnoreSpatialShortcut(event)).toBe(true);
    expect(shouldIgnoreSpatialShortcut({ nativeEvent: event })).toBe(true);
    expect(shouldIgnoreSpatialShortcut({ defaultPrevented: true })).toBe(true);
  });
  it.each(["input", "textarea", "select", "div[role=combobox]"])("leaves Escape to focused %s controls", selector => {
    const target = document.createElement(selector.startsWith("div") ? "div" : selector);
    if (selector.startsWith("div")) target.setAttribute("role", "combobox");
    const child = document.createElement("span"); target.appendChild(child);
    expect(shouldIgnoreSpatialShortcut({ target: child })).toBe(true);
  });
  it("respects editable ancestry and an explicit noneditable descendant", () => {
    const editor = document.createElement("div"), child = document.createElement("span");
    editor.setAttribute("contenteditable", "plaintext-only"); editor.appendChild(child);
    expect(shouldIgnoreSpatialShortcut({ target: child })).toBe(true);
    child.setAttribute("contenteditable", "false");
    expect(shouldIgnoreSpatialShortcut({ target: child })).toBe(false);
  });
  it("permits unmodified canvas and toolbar shortcuts, including Shift", () => {
    expect(shouldIgnoreSpatialShortcut(new KeyboardEvent("keydown", { key: "Escape", shiftKey: true }))).toBe(false);
    expect(shouldIgnoreSpatialShortcut({ target: document.createElement("canvas") })).toBe(false);
    expect(shouldIgnoreSpatialShortcut({ target: document.createElement("button") })).toBe(false);
  });
});
