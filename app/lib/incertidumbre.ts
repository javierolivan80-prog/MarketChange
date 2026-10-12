// Intervalos de confianza al 95 % (Tanda 11), los mismos métodos que
// pipeline/backtest/portfolio_metrics.py: Wilson para el acierto y bootstrap
// de percentiles, con semilla fija, para el resultado medio por operación.
// Tratan las operaciones como independientes (ver el pipeline).

const Z_95 = 1.959963984540054;
const REMUESTREOS = 2000;
const SEMILLA = 20261012;

/** Intervalo de Wilson de una proporción (fracción, no %). */
export function intervaloWilson(aciertos: number, n: number): [number, number] | null {
  if (n <= 0) return null;
  const p = aciertos / n;
  const z2 = Z_95 * Z_95;
  const denominador = 1 + z2 / n;
  const centro = (p + z2 / (2 * n)) / denominador;
  const margen = (Z_95 * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denominador;
  return [Math.max(0, centro - margen), Math.min(1, centro + margen)];
}

// Generador pseudoaleatorio con semilla (mulberry32): el mismo historial da
// siempre el mismo intervalo.
function generador(semilla: number): () => number {
  let a = semilla >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Intervalo al 95 % de la media por bootstrap de percentiles; null con menos de 2 valores. */
export function intervaloBootstrapMedia(valores: number[], remuestreos = REMUESTREOS): [number, number] | null {
  const n = valores.length;
  if (n < 2) return null;
  const azar = generador(SEMILLA);
  const medias = new Float64Array(remuestreos);
  for (let b = 0; b < remuestreos; b++) {
    let suma = 0;
    for (let i = 0; i < n; i++) suma += valores[Math.floor(azar() * n)];
    medias[b] = suma / n;
  }
  medias.sort();
  const percentil = (q: number) => {
    const pos = (remuestreos - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return medias[lo] + (medias[hi] - medias[lo]) * (pos - lo);
  };
  return [percentil(0.025), percentil(0.975)];
}

/** El intervalo no incluye el 0. */
export function distingueDeCero(ic: [number, number] | null | undefined): boolean | null {
  if (!ic) return null;
  return ic[0] > 0 || ic[1] < 0;
}
