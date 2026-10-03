import { test } from "node:test";
import assert from "node:assert/strict";
import { validationText } from "./validationText";

test("criterios de la calificación", () => {
  assert.equal(
    validationText("No cumple todos los criterios de GREENLIGHT, pero tampoco dispara REDLIGHT: calibración=0.41, n=12 (mínimo 30)", "en"),
    "Does not meet every GREENLIGHT criterion, but does not trigger REDLIGHT either: calibration=0.41, n=12 (minimum 30)",
  );
});

test("conclusiones del event study", () => {
  assert.equal(
    validationText("No significativo (p=0.2310 >= 0.05) — MDE=120 bps con esta n, podría ser ruido o un efecto real más pequeño que el MDE", "en"),
    "Not significant (p=0.2310 >= 0.05) — MDE=120 bps with this n, could be noise or a real effect smaller than the MDE",
  );
  assert.equal(validationText("n=4 — muestra insuficiente para cualquier estadístico (mínimo 10)", "en"), "n=4 — sample too small for any statistic (minimum 10)");
  assert.equal(validationText("Significativo (p=0.0100 < 0.05) — el evento SÍ mueve el precio de forma no aleatoria", "es").startsWith("Significativo"), true);
});
