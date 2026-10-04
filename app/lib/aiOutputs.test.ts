import { test } from "node:test";
import assert from "node:assert/strict";
import { splitAiOutputs } from "./aiOutputs";

const skipped = { skipped_no_llm_needed: true, reason: "ADV < 1M$ — NO_TRADE garantizado, no se invoca el debate de IA" };

test("un evento descartado antes de la IA no llega como veredicto", () => {
  const r = splitAiOutputs(skipped, skipped, skipped);
  assert.equal(r.bull_output, null);
  assert.equal(r.bear_output, null);
  assert.equal(r.judge_output, null);
  assert.equal(r.ai_skipped_reason, skipped.reason);
});

test("un análisis real pasa tal cual y sin motivo de descarte", () => {
  const judge = { net_conviction: 0.4, confidence_in_conviction: 70, key_uncertainty: "x", overriding_concern: "" };
  const r = splitAiOutputs({ thesis: "t" }, { counter_thesis: "c" }, judge);
  assert.deepEqual(r.judge_output, judge);
  assert.equal(r.ai_skipped_reason, null);
});

test("sin datos sigue siendo null, no un descarte", () => {
  const r = splitAiOutputs(null, undefined, null);
  assert.equal(r.judge_output, null);
  assert.equal(r.ai_skipped_reason, null);
});

test("marcador sin motivo: descarte con motivo vacío", () => {
  assert.equal(splitAiOutputs({ skipped_no_llm_needed: true }, null, null).ai_skipped_reason, "");
});
