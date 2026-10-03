import { test } from "node:test";
import assert from "node:assert/strict";
import { qualityText } from "./qualityText";

test("criterios, explicaciones con cifras y veredictos", () => {
  assert.equal(qualityText("Solidez financiera", "en"), "Financial strength");
  assert.equal(qualityText("Excelente: gana 24€ al año por cada 100€ de capital propio.", "en"), "Excellent: earns $24 a year for every $100 of equity.");
  assert.equal(qualityText("Se encoge: los ingresos bajan un 3.2% al año de media.", "en"), "Shrinking: revenue falls 3.2% a year on average.");
  assert.equal(qualityText("Flojo en casi todos los criterios.", "en"), "Weak on almost every criterion.");
});

test("en español, o si no se reconoce, tal cual", () => {
  assert.equal(qualityText("Rentabilidad", "es"), "Rentabilidad");
  assert.equal(qualityText("Texto nuevo", "en"), "Texto nuevo");
});
