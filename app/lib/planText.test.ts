import { test } from "node:test";
import assert from "node:assert/strict";
import { planReason, planText } from "./planText";

test("en español no se toca nada", () => {
  assert.equal(planText("Medias móviles", "es"), "Medias móviles");
  assert.equal(planReason("Catalizador no confirmado.", "es"), "Catalizador no confirmado.");
});

test("frases exactas y con cifras", () => {
  assert.equal(planText("Medias móviles", "en"), "Moving averages");
  assert.equal(planText("Máximo 52 sesiones", "en"), "52-session high");
  assert.equal(planText("VIX en 31: mercado en tensión, tamaño a la mitad.", "en"), "VIX at 31: market under stress, position size halved.");
  assert.equal(planText("Con la otra mitad, stop dinámico a 2.5 ATR del extremo alcanzado.", "en"), "With the other half, a trailing stop 2.5 ATR from the extreme reached.");
});

test("una frase desconocida se enseña tal cual", () => {
  assert.equal(planText("EMA 50", "en"), "EMA 50");
  assert.equal(planText("Algo nuevo del pipeline", "en"), "Algo nuevo del pipeline");
});

test("el motivo de rechazo se traduce pieza a pieza", () => {
  assert.equal(
    planReason("Catalizador no confirmado; solo 1 indicador alineado; riesgo/beneficio 1:1.4 < 1:2.", "en"),
    "Catalyst not confirmed; only 1 aligned indicator; risk/reward 1:1.4 < 1:2.",
  );
  assert.equal(planReason("Histórico insuficiente (40 sesiones; hacen falta 60).", "en"), "Not enough history (40 sessions; 60 needed).");
});

test("línea de impacto de los casos parecidos", () => {
  assert.equal(planText("±4.25% según histórico de 37 eventos análogos", "en"), "±4.25% based on 37 similar past events");
});
