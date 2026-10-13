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

test("el muelle es críticamente amortiguado: empieza en 0, acaba en 1, nunca retrocede ni se pasa", () => {
  const m = css.match(/--motion-ease-spring:\s*linear\(([^)]+)\)/);
  assert.ok(m, "falta --motion-ease-spring en globals.css");
  const puntos = m[1].split(",").map(Number);
  assert.ok(puntos.length >= 10);
  assert.equal(puntos[0], 0);
  assert.equal(puntos[puntos.length - 1], 1);
  for (let i = 1; i < puntos.length; i++) {
    assert.ok(puntos[i] >= puntos[i - 1], `retrocede en el punto ${i}`);
    assert.ok(puntos[i] <= 1, `se pasa de 1 en el punto ${i} (rebote)`);
  }
  // Misma curva que x(u) = 1 − (1 + 9,23u)·e^(−9,23u) (amortiguación 1).
  const n = puntos.length - 1;
  for (let i = 1; i < n; i++) {
    const u = i / n;
    const esperado = 1 - (1 + 9.23 * u) * Math.exp(-9.23 * u);
    assert.ok(Math.abs(puntos[i] - esperado) < 0.001, `punto ${i}: ${puntos[i]} frente a ${esperado.toFixed(4)}`);
  }
});

test("las duraciones del muelle corresponden a una respuesta de 0,16 s y 0,12 s", () => {
  const ms = (r: number) => Math.round((9.23 / ((2 * Math.PI) / r)) * 1000);
  assert.equal(cssMs("motion-duration-spring"), ms(0.16));
  assert.equal(cssMs("motion-duration-spring-fast"), ms(0.12));
});

test("la interfaz se mueve por debajo de 300 ms y la salida es más rápida que la entrada (Tanda 12d)", () => {
  for (const nombre of ["micro", "fast", "base", "spring", "spring-fast"]) {
    assert.ok(cssMs(`motion-duration-${nombre}`) < 300, `--motion-duration-${nombre}`);
  }
  assert.ok(cssMs("motion-duration-spring-fast") < cssMs("motion-duration-spring"));
});

test("sin ease-in: las curvas de entrada/salida arrancan rápido (Tanda 12d)", () => {
  assert.ok(!/--motion-ease-in:/.test(css), "--motion-ease-in no debe existir");
  const curvas = [...css.matchAll(/--motion-ease-([\w-]+):\s*cubic-bezier\(([^)]+)\)/g)];
  for (const [, nombre, args] of curvas) {
    if (nombre.includes("in-out")) continue; // movimiento de un sitio a otro: ease-in-out a propósito
    const [x1, y1] = args.split(",").map(Number);
    assert.ok(y1 >= x1, `--motion-ease-${nombre}: cubic-bezier(${args}) arranca lento`);
  }
});
