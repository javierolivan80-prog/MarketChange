// format.ts — un único sitio para dar forma a cifras, fechas y monedas.
//
// Requisito de credibilidad del producto: "cifras tabulares, alineación
// decimal, unidades y divisas siempre explícitas". Si cada componente
// formatea a mano con toFixed(), un 2% en un sitio y un 2.00% en otro
// (o un 0.05 sin multiplicar por 100) es un accidente de tiempo, no una
// cuestión de si va a pasar.

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

/** Para valores ya en fracción (0.05 = 5%), como ev_balanced o net_conviction. */
export function formatFracAsPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return formatPct(value * 100, digits);
}

export function formatUsd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

/** Fecha corta legible (es-ES), sin depender de la zona horaria del navegador
 * para fechas puras (YYYY-MM-DD de Postgres 'date', sin componente horario). */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const [y, m, d] = value.slice(0, 10).split("-");
  if (!y || !m || !d) return value;
  return `${d}/${m}/${y}`;
}

/** Fecha+hora con zona explícita — para timestamps reales (analyzed_at,
 * filed_at), donde ocultar la hora perdería precisión de trazabilidad. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("es-ES", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(d);
}

/** Porcentaje que es una magnitud, no un resultado (proporción de datos con
 * huecos, sesgo de supervivencia…): sin signo. formatPct le ponía "+", que en
 * "+4.0% posible sesgo" se lee como algo positivo. Valor ya en puntos (4.0 = 4%). */
export function formatShare(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

/** Drawdown: el pipeline lo guarda como magnitud positiva (portfolio_metrics.py,
 * `abs(max_dd)`), y pasarlo por formatPct lo pintaba como "+12.0%" bajo la
 * etiqueta "Peor caída" — una pérdida con signo de ganancia. Se muestra
 * siempre con signo negativo. */
export function formatDrawdown(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value === 0) return `${(0).toFixed(digits)}%`;
  return `−${(Math.abs(value) * 100).toFixed(digits)}%`;
}
