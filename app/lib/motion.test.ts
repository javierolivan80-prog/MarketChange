import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { motion } from "./motion";

const css = readFileSync(join(process.cwd(), "app", "globals.css"), "utf8");

function cssMs(name: string): number {
  const m = css.match(new RegExp(`--${name}:\\s*(\\d+)ms`));
  assert.ok(m, `falta --${name} en globals.css`);
  return Number(m[1]);
}

test("las duraciones de motion.ts coinciden con las de globals.css", () => {
  for (const [key, ms] of Object.entries(motion.duration)) {
    assert.equal(cssMs(`motion-duration-${key}`), ms, `motion-duration-${key}`);
  }
  assert.equal(cssMs("motion-stagger"), motion.stagger);
});

test("ninguna duración supera el tope", () => {
  for (const ms of Object.values(motion.duration)) assert.ok(ms <= motion.duration.max);
});

test("sin curvas con rebote: ningún punto de control fuera de [0, 1]", () => {
  const curves = [...css.matchAll(/--motion-ease-[\w-]+:\s*cubic-bezier\(([^)]+)\)/g)];
  assert.ok(curves.length >= 3);
  for (const [, args] of curves) {
    for (const v of args.split(",").map(Number)) assert.ok(v >= 0 && v <= 1, `cubic-bezier(${args})`);
  }
});
