import { test } from "node:test";
import assert from "node:assert/strict";
import { distingueDeCero, intervaloTMedia, intervaloWilson, tStudent975 } from "./incertidumbre";

// La misma muestra de referencia que pipeline/tests/test_portfolio_metrics.py
// (valores con scipy.stats.t): intervalo [−0,00199385, 0,96199385].
const MUESTRA_REF = [1.2, -0.4, 0.8, 2.1, -1.5, 0.3, 0.9, -0.2, 1.7, 0.0, 0.6, -0.9, 1.1, 0.4, -0.3, 2.4, 0.7, -1.1, 0.5, 1.3];

test("Wilson: valores de referencia (statsmodels) y extremos exactos", () => {
  const [lo, hi] = intervaloWilson(50, 100)!;
  assert.ok(Math.abs(lo - 0.4038) < 1e-4 && Math.abs(hi - 0.5962) < 1e-4);
  const [lo3, hi3] = intervaloWilson(3, 10)!;
  assert.ok(Math.abs(lo3 - 0.10779) < 1e-5 && Math.abs(hi3 - 0.60322) < 1e-5);
  assert.equal(intervaloWilson(0, 3)![0], 0);
  assert.equal(intervaloWilson(10, 10)![1], 1);
  assert.equal(intervaloWilson(0, 0), null);
});

test("t de Student: cuantil y la muestra de referencia del pipeline", () => {
  assert.ok(Math.abs(tStudent975(19) - 2.0930240544) < 2e-5);
  assert.ok(Math.abs(tStudent975(99) - 1.9842169516) < 1e-7);
  const [lo, hi] = intervaloTMedia(MUESTRA_REF)!;
  assert.ok(Math.abs(lo - -0.0019938518) < 1e-5 && Math.abs(hi - 0.9619938518) < 1e-5);
  assert.equal(intervaloTMedia(MUESTRA_REF.slice(0, 19)), null);
});

test("distinguible de cero: la referencia lo roza; desplazada +0,01, ya no", () => {
  assert.equal(distingueDeCero(intervaloTMedia(MUESTRA_REF)), false);
  assert.equal(distingueDeCero(intervaloTMedia(MUESTRA_REF.map((v) => v + 0.01))), true);
  assert.equal(distingueDeCero(null), null);
});
