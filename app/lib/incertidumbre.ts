// Intervalos de confianza al 95 % (Tanda 11), los mismos métodos que
// pipeline/backtest/portfolio_metrics.py: Wilson para el acierto y t de
// Student para el resultado medio por operación, este solo desde
// MIN_OPERACIONES (con menos, ningún intervalo es fiable: ver el pipeline).
// Tanda 14: las operaciones se agrupan por mes de entrada, porque las que
// comparten mes no son independientes (ver el pipeline): error agrupado,
// nunca menor que el independiente, y t de G-1 grados de libertad; con un
// solo mes, sin intervalo.

const Z_95 = 1.959963984540054;
export const MIN_OPERACIONES = 20;

/** Intervalo de Wilson de una proporción (fracción, no %). `aciertos` y `n`
 * pueden no ser enteros (n efectivo, Tanda 14). */
export function intervaloWilson(aciertos: number, n: number, z: number = Z_95): [number, number] | null {
  if (n <= 0) return null;
  const p = aciertos / n;
  const z2 = z * z;
  const denominador = 1 + z2 / n;
  const centro = (p + z2 / (2 * n)) / denominador;
  const margen = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denominador;
  // Exactos en los extremos, como en el pipeline.
  const lo = aciertos === 0 ? 0 : Math.max(0, centro - margen);
  const hi = aciertos === n ? 1 : Math.min(1, centro + margen);
  return [lo, hi];
}

// Cuantil 97,5 % de la t con 1 a 30 grados de libertad (scipy.stats.t.ppf):
// con los grupos (meses) los grados de libertad pueden ser muy pocos, donde
// la aproximación de abajo falla.
const T975_TABLA = [
  12.7062047362, 4.3026527297, 3.1824463053, 2.7764451052, 2.5705818356, 2.4469118511, 2.3646242516, 2.3060041352,
  2.2621571628, 2.228138852, 2.2009851601, 2.1788128297, 2.1603686565, 2.1447866879, 2.1314495456, 2.1199052992,
  2.1098155778, 2.1009220402, 2.0930240544, 2.0859634473, 2.0796138447, 2.0738730679, 2.0686576104, 2.0638985616,
  2.0595385528, 2.0555294386, 2.0518305165, 2.0484071418, 2.0452296421, 2.0422724563,
];

/** Cuantil 97,5 % de la t de Student: tabla hasta 30 grados de libertad y,
 * por encima, Cornish-Fisher (error < 2e-5 desde 19). */
export function tStudent975(gradosDeLibertad: number): number {
  if (gradosDeLibertad >= 1 && gradosDeLibertad <= T975_TABLA.length && Number.isInteger(gradosDeLibertad)) {
    return T975_TABLA[gradosDeLibertad - 1];
  }
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

/** Clave de grupo de una operación: el mes de entrada (o de salida), "AAAA-MM";
 * null sin fechas (grupo propio). */
export function mesDeEntrada(entryDate: string | null | undefined, exitDate?: string | null): string | null {
  const d = entryDate ?? exitDate ?? null;
  return d ? d.slice(0, 7) : null;
}

function varianzas(valores: number[], grupos: (string | null)[]): { varI: number; varC: number; g: number } {
  const n = valores.length;
  const media = valores.reduce((s, v) => s + v, 0) / n;
  const sumas = new Map<string, number>();
  valores.forEach((v, i) => {
    const k = grupos[i] ?? `sin-grupo-${i}`;
    sumas.set(k, (sumas.get(k) ?? 0) + (v - media));
  });
  const g = sumas.size;
  const varI = n > 1 ? valores.reduce((s, v) => s + (v - media) ** 2, 0) / (n - 1) / n : 0;
  let suma2 = 0;
  for (const s of sumas.values()) suma2 += s * s;
  const varC = g > 1 ? ((g / (g - 1)) * suma2) / (n * n) : Number.NaN;
  return { varI, varC, g };
}

/** Intervalo al 95 % de la media con operaciones agrupadas por mes (Tanda
 * 14); null con menos de MIN_OPERACIONES o con un solo grupo. */
export function intervaloTMediaAgrupado(valores: number[], grupos: (string | null)[]): [number, number] | null {
  const n = valores.length;
  if (n < MIN_OPERACIONES) return null;
  const { varI, varC, g } = varianzas(valores, grupos);
  if (g < 2) return null;
  const media = valores.reduce((s, v) => s + v, 0) / n;
  const margen = tStudent975(Math.min(n - 1, g - 1)) * Math.sqrt(Math.max(varI, varC));
  return [media - margen, media + margen];
}

/** Wilson con operaciones agrupadas (Tanda 14): n efectivo (n entre el
 * efecto de diseño) y cuantil t de G-1 grados de libertad; null sin
 * operaciones o con un solo grupo. */
export function intervaloWilsonAgrupado(aciertos: boolean[], grupos: (string | null)[]): [number, number] | null {
  const n = aciertos.length;
  if (n === 0) return null;
  const valores: number[] = aciertos.map((a) => (a ? 1 : 0));
  const { varI, varC, g } = varianzas(valores, grupos);
  if (g < 2) return null;
  const efecto = varI > 0 ? Math.max(1, varC / varI) : 1;
  const k = valores.reduce((s, v) => s + v, 0);
  return intervaloWilson(k / efecto, n / efecto, tStudent975(g - 1));
}

/** El intervalo no incluye el 0. */
export function distingueDeCero(ic: [number, number] | null | undefined): boolean | null {
  if (!ic) return null;
  return ic[0] > 0 || ic[1] < 0;
}
