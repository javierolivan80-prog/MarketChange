import { test } from "node:test";
import assert from "node:assert/strict";
import { distingueDeCero, intervaloBootstrapMedia, intervaloWilson } from "./incertidumbre";

test("Wilson: valores de referencia (iguales que el pipeline)", () => {
  const [lo, hi] = intervaloWilson(50, 100)!;
  assert.ok(Math.abs(lo - 0.4038) < 1e-4 && Math.abs(hi - 0.5962) < 1e-4);
  const [lo0, hi0] = intervaloWilson(0, 10)!;
  assert.equal(lo0, 0);
  assert.ok(Math.abs(hi0 - 0.2775) < 1e-4);
  assert.equal(intervaloWilson(0, 0), null);
});

test("bootstrap: reproducible y contiene la media", () => {
  const valores = [1.0, -0.5, 2.0, 0.3, -1.2, 0.8, 1.5, -0.2, 0.0, 0.9];
  const a = intervaloBootstrapMedia(valores)!;
  assert.deepEqual(a, intervaloBootstrapMedia(valores));
  const media = valores.reduce((s, v) => s + v, 0) / valores.length;
  assert.ok(a[0] < media && media < a[1]);
  assert.equal(intervaloBootstrapMedia([1]), null);
});

test("distinguible de cero solo si el intervalo no lo incluye", () => {
  assert.equal(distingueDeCero(intervaloBootstrapMedia([1, -1, 0.5, -0.5, 2, -2])), false);
  assert.equal(distingueDeCero(intervaloBootstrapMedia(Array.from({ length: 20 }, (_, i) => 1 + 0.01 * i))), true);
  assert.equal(distingueDeCero(null), null);
});
