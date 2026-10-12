import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDate, formatDrawdown, formatFracAsPct, formatIntervalo, formatNum, formatPct, formatShare } from "./format";

test("un dato ausente se muestra como —, nunca como 0", () => {
  for (const fn of [formatPct, formatFracAsPct, formatNum, formatDrawdown]) {
    assert.equal(fn(null), "—");
    assert.equal(fn(undefined), "—");
    assert.equal(fn(Number.NaN), "—");
  }
});

test("formatPct pone signo a los positivos", () => {
  assert.equal(formatPct(2.345, 1), "+2.3%");
  assert.equal(formatPct(-2.345, 1), "-2.3%");
});

test("formatFracAsPct multiplica por 100", () => {
  assert.equal(formatFracAsPct(0.0215), "+2.15%");
});

test("la caída máxima nunca lleva signo de ganancia", () => {
  // El pipeline la guarda en positivo (abs(max_dd)).
  assert.equal(formatDrawdown(0.123), "−12.3%");
  assert.equal(formatDrawdown(-0.123), "−12.3%");
  assert.equal(formatDrawdown(0), "0.0%");
});

test("formatDate no depende de la zona horaria", () => {
  assert.equal(formatDate("2026-09-01"), "01/09/2026");
  assert.equal(formatDate(null), "—");
  assert.equal(formatDate("2026-09-01", "en"), "Sep 1, 2026");
});

test("formatShare no pone signo a una proporción", () => {
  assert.equal(formatShare(4), "4.0%");
  assert.equal(formatShare(null), "—");
});

test("formatIntervalo: «entre X y Y» en español y «between X and Y» en inglés (Tanda 11)", () => {
  assert.equal(formatIntervalo([0.4, 0.6], "es", { escala: 100, digits: 0 }), "40% y 60%");
  assert.equal(formatIntervalo([0.4, 0.6], "en", { escala: 100, digits: 0 }), "40% and 60%");
  assert.equal(formatIntervalo([-1, 3], "es", { digits: 2, conSigno: true }), "-1.00% y +3.00%");
  assert.equal(formatIntervalo(null, "es"), "—");
});
