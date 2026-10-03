import { test } from "node:test";
import assert from "node:assert/strict";
import { localeFromAcceptLanguage, makeT, normalizeLocale } from "./i18n";

test("idioma del navegador: el primero soportado por orden de preferencia", () => {
  assert.equal(localeFromAcceptLanguage("en-US,en;q=0.9,es;q=0.8"), "en");
  assert.equal(localeFromAcceptLanguage("es-ES,es;q=0.9"), "es");
  assert.equal(localeFromAcceptLanguage("fr-FR,fr;q=0.9,en;q=0.5"), "en");
  assert.equal(localeFromAcceptLanguage("fr;q=0.9,es;q=0.95"), "es");
});

test("sin cabecera o sin idioma soportado, español", () => {
  assert.equal(localeFromAcceptLanguage(null), "es");
  assert.equal(localeFromAcceptLanguage("de-DE,de"), "es");
});

test("una cookie con un valor raro no se acepta", () => {
  assert.equal(normalizeLocale("en"), "en");
  assert.equal(normalizeLocale("EN"), null);
  assert.equal(normalizeLocale(undefined), null);
});

test("t elige el texto del idioma", () => {
  assert.equal(makeT("en")("Señales", "Signals"), "Signals");
  assert.equal(makeT("es")("Señales", "Signals"), "Señales");
});
