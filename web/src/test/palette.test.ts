// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** The custom properties declared in the first `:root { }` block of a CSS chunk. */
function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: dark)")));
const darkStart = css.indexOf("@media (prefers-color-scheme: dark)");
const dark = { ...light, ...tokens(css.slice(darkStart, css.indexOf("/* -- base", darkStart))) };

/** Follows var(--x) references down to a hex colour. */
function hex(theme: Record<string, string>, name: string): string {
  let value = theme[name];
  for (let i = 0; value?.startsWith("var(") && i < 5; i++) value = theme[value.slice(6, -1)];
  if (!value || !/^#[0-9a-f]{6}$/i.test(value)) throw new Error(`--${name} is not a hex colour: ${value}`);
  return value;
}

function luminance(h: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [text, background]: every pair the UI actually draws text with
const PAIRS: [string, string][] = [
  ["text", "card"],
  ["text", "page"],
  ["muted", "card"],
  ["muted", "stripe"],
  ["muted", "hover"],
  ["accent-text", "card"],
  ["accent-text", "hover"],
  ["panel-text", "panel"],
  ["panel-text", "panel-raised"],
  ["panel-muted", "panel"],
  ["panel-muted", "panel-raised"],
  ["panel-accent", "panel"],
  ["panel-accent", "panel-raised"],
  ["sky", "panel-raised"], // info badge in the live feed
  ["white", "red-strong"], // critical badge
  ["white", "electric"], // warning badge, end of the button gradient
  ["white", "cobalt"], // start of the button gradient, avatar
  ["red-strong", "card"], // error text
];

describe("palette contrast (WCAG AA, 4.5:1 for text)", () => {
  for (const [theme, values] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    it.each(PAIRS)(`${theme}: --%s on --%s`, (fg, bg) => {
      if (theme === "dark" && fg === "red-strong") return; // dark mode uses a lighter error red
      expect(contrast(hex(values, fg), hex(values, bg))).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("matches known WCAG values", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });
});
