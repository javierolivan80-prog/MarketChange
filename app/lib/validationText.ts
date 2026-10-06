// validationText.ts — traduce al inglés los textos del motor de validación.
//
// pipeline/validation/decision.py (criterios de cada calificación) y
// event_study.py (conclusión por tipo de evento) los escriben en español con
// las cifras dentro. Son frases técnicas fijas: se traducen sustituyendo sus
// piezas en orden, sin tocar las cifras. Lo que no se reconoce se queda igual.
import type { Locale } from "./i18n";

const REPLACEMENTS: [RegExp, string][] = [
  [/ violación\(es\) anti-look-ahead detectadas — bloqueante para cualquier decisión positiva/g, " look-ahead violation(s) detected — blocks any positive decision"],
  [/No cumple todos los criterios de GREENLIGHT, pero tampoco dispara REDLIGHT: /g, "Does not meet every GREENLIGHT criterion, but does not trigger REDLIGHT either: "],
  [/walk-forward pasó, sin violaciones anti-look-ahead/g, "walk-forward passed, no look-ahead violations"],
  [/^solo (\d+) operaciones \(mínimo (\d+)\): sin muestra suficiente para juzgar/g, "only $1 trades (minimum $2): not enough sample to judge"],
  [/calibración=/g, "calibration="],
  [/ — muestra insuficiente para cualquier estadístico \(mínimo (\d+)\)/g, " — sample too small for any statistic (minimum $1)"],
  [/ — varianza cero en la muestra \(todos los CAR idénticos\), t-test no aplicable/g, " — zero variance in the sample (all CARs identical), t-test not applicable"],
  [/ — todos los eventos en el mismo mes, sin contraste agrupado posible/g, " — all events in the same month, no clustered test possible"],
  [/ — sin dispersión entre meses tras winsorizar, contraste agrupado no aplicable/g, " — no dispersion across months after winsorizing, clustered test not applicable"],
  [/^Significativo \(/g, "Significant ("],
  [/ — el evento SÍ mueve el precio de forma no aleatoria/g, " — the event DOES move the price in a non-random way"],
  [/^No significativo \(/g, "Not significant ("],
  [/MDE=(\d+) bps con esta n/g, "MDE=$1 bps with this n"],
  [/, podría ser ruido o un efecto real más pequeño que el MDE/g, ", could be noise or a real effect smaller than the MDE"],
  [/ — ADVERTENCIA: no sobrevive a la corrección por contrastes múltiples /g, " — WARNING: does not survive the multiple-comparisons correction "],
  [/ clases testeadas a la vez\)/g, " classes tested at once)"],
  // Genérica, al final: las frases de arriba que llevan "(mínimo N)" ya se han traducido enteras.
  [/\(mínimo (\d+)\)/g, "(minimum $1)"],
];

export function validationText(text: string, locale: Locale): string {
  if (locale !== "en") return text;
  return REPLACEMENTS.reduce((s, [re, rep]) => s.replace(re, rep), text);
}
