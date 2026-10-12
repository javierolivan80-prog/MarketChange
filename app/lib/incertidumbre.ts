// Intervalos de confianza al 95 % (Tanda 11), los mismos métodos que
// pipeline/backtest/portfolio_metrics.py: Wilson para el acierto y t de
// Student para el resultado medio por operación, este solo desde
// MIN_OPERACIONES (con menos, ningún intervalo es fiable: ver el pipeline).
// Tratan las operaciones como independientes.

const Z_95 = 1.959963984540054;
export const MIN_OPERACIONES = 20;

/** Intervalo de Wilson de una proporción (fracción, no %). */
export function intervaloWilson(aciertos: number, n: number): [number, number] | null {
  if (n <= 0) return null;
  const p = aciertos / n;
  const z2 = Z_95 * Z_95;
  const denominador = 1 + z2 / n;
  const centro = (p + z2 / (2 * n)) / denominador;
  const margen = (Z_95 * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denominador;
  // Exactos en los extremos, como en el pipeline.
  const lo = aciertos === 0 ? 0 : Math.max(0, centro - margen);
  const hi = aciertos === n ? 1 : Math.min(1, centro + margen);
  return [lo, hi];
}

/** Cuantil 97,5 % de la t de Student (Cornish-Fisher): error < 2e-5 con 19 o
 * más grados de libertad, los únicos que se usan (MIN_OPERACIONES). */
export function tStudent975(gradosDeLibertad: number): number {
  const z = Z_95;
  const v = gradosDeLibertad;
  return (
    z +
    (z ** 3 + z) / (4 * v) +
    (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * v ** 2) +
    (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * v ** 3)
  );
}

/** Intervalo al 95 % de la media (t de Student); null con menos de MIN_OPERACIONES valores. */
export function intervaloTMedia(valores: number[]): [number, number] | null {
  const n = valores.length;
  if (n < MIN_OPERACIONES) return null;
  const media = valores.reduce((s, v) => s + v, 0) / n;
  const varianza = valores.reduce((s, v) => s + (v - media) ** 2, 0) / (n - 1);
  const margen = (tStudent975(n - 1) * Math.sqrt(varianza)) / Math.sqrt(n);
  return [media - margen, media + margen];
}

/** El intervalo no incluye el 0. */
export function distingueDeCero(ic: [number, number] | null | undefined): boolean | null {
  if (!ic) return null;
  return ic[0] > 0 || ic[1] < 0;
}
