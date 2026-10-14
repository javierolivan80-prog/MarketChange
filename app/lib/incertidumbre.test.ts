import { test } from "node:test";
import assert from "node:assert/strict";
import { distingueDeCero, intervaloTMedia, intervaloTMediaAgrupado, intervaloWilson, intervaloWilsonAgrupado, mesDeEntrada, tStudent975 } from "./incertidumbre";

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

// Tanda 14: la misma muestra agrupada que pipeline/tests/test_portfolio_metrics.py
// (seis meses de cinco operaciones casi iguales), con los valores del pipeline.
const MESES = [
  [2.0, 2.1, 1.9, 2.2, 1.8], [1.5, 1.6, 1.4, 1.7, 1.3], [1.8, 1.9, 1.7, 2.0, 1.6],
  [-0.4, -0.3, -0.5, -0.2, -0.6], [-0.2, -0.1, -0.3, 0.0, -0.4], [-0.6, -0.5, -0.7, -0.4, -0.8],
];
const VALORES = MESES.flat();
const GRUPOS = MESES.flatMap((m, i) => m.map(() => `2022-${String(i + 1).padStart(2, "0")}`));

test("agrupado por mes: los mismos valores que el pipeline y más ancho que el independiente", () => {
  const [lo, hi] = intervaloTMediaAgrupado(VALORES, GRUPOS)!;
  assert.ok(Math.abs(lo - -0.5802092580570604) < 1e-9 && Math.abs(hi - 1.946875924723727) < 1e-9);
  const [loW, hiW] = intervaloWilsonAgrupado(VALORES.map((v) => v > 0), GRUPOS)!;
  assert.ok(Math.abs(loW - 0.1255247014972204) < 1e-9 && Math.abs(hiW - 0.8744752985027796) < 1e-9);
  const [loI, hiI] = intervaloTMedia(VALORES)!;
  assert.ok(loI > 0 && hi - lo > 2 * (hiI - loI));
  assert.equal(distingueDeCero([lo, hi]), false);
});

test("agrupado: un mes por operación es el de siempre; todas en un mes, sin intervalo", () => {
  const unoPorMes = MUESTRA_REF.map((_, i) => `${2024 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`);
  const [lo, hi] = intervaloTMediaAgrupado(MUESTRA_REF, unoPorMes)!;
  assert.ok(Math.abs(lo - -0.0019938518) < 1e-5 && Math.abs(hi - 0.9619938518) < 1e-5);
  assert.equal(intervaloTMediaAgrupado(MUESTRA_REF, MUESTRA_REF.map(() => "2024-01")), null);
  assert.equal(intervaloWilsonAgrupado(MUESTRA_REF.map((v) => v > 0), MUESTRA_REF.map(() => "2024-01")), null);
  assert.equal(mesDeEntrada("2024-03-15", "2024-04-02"), "2024-03");
  assert.equal(mesDeEntrada(null, "2024-04-02"), "2024-04");
  assert.equal(mesDeEntrada(null, null), null);
});

test("t de Student con pocos grados de libertad (tabla)", () => {
  assert.ok(Math.abs(tStudent975(1) - 12.7062047362) < 1e-9);
  assert.ok(Math.abs(tStudent975(5) - 2.5705818356) < 1e-9);
  assert.ok(Math.abs(tStudent975(30) - 2.0422724563) < 1e-9);
});
