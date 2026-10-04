import { test } from "node:test";
import assert from "node:assert/strict";
import { eventClassLabel, exitReasonLabel, sourceLabel, versionLabel } from "./labels";

test("una sola palabra por versión", () => {
  assert.equal(versionLabel("BALANCED"), "Equilibrado");
  assert.equal(versionLabel("CONSERVATIVE"), "Conservador");
  assert.equal(versionLabel("AGGRESSIVE"), "Agresivo");
});

test("tipos de evento legibles, con caída a código si no está mapeado", () => {
  assert.equal(eventClassLabel("8K_2.02_EARNINGS"), "Resultados");
  assert.equal(eventClassLabel("8K_9.99_NUEVO"), "9.99_NUEVO");
  assert.equal(eventClassLabel(null), "—");
});

test("motivos de salida y fuentes legibles", () => {
  assert.equal(exitReasonLabel("TAKE_PROFIT"), "Objetivo alcanzado");
  assert.equal(sourceLabel("FDA_OPENFDA"), "FDA");
  assert.equal(sourceLabel("EDGAR"), "SEC");
});

test("las etiquetas existen también en inglés", async () => {
  const { reliability } = await import("./labels");
  assert.equal(versionLabel("BALANCED", "en"), "Balanced");
  assert.equal(eventClassLabel("8K_2.02_EARNINGS", "en"), "Earnings");
  assert.equal(exitReasonLabel("TAKE_PROFIT", "en"), "Target reached");
  assert.equal(reliability("en").A.title, "High reliability");
});
