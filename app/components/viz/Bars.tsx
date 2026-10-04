// Bars.tsx — barras horizontales simples, de una sola serie y un solo color,
// con el valor escrito en la punta (nunca solo el largo de la barra).
export function HBars({
  rows,
  max,
  format,
  labelWidth = "minmax(0,14rem)",
  animate = false,
}: {
  rows: { label: string; value: number; key?: string }[];
  max?: number;
  format: (v: number) => string;
  /** Ancho de la columna de etiquetas (grid-template-columns). */
  labelWidth?: string;
  /** Rellenar las barras al aparecer (a la vez: se comparan entre sí). Solo
   * fuera de tablas; la cifra no se anima nunca. */
  animate?: boolean;
}) {
  const top = max ?? Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="space-y-1.5 text-sm">
      {rows.map((r) => (
        <li key={r.key ?? r.label} className="grid items-center gap-3" style={{ gridTemplateColumns: `${labelWidth} 1fr` }}>
          <span className="truncate text-text-secondary" title={r.label}>
            {r.label}
          </span>
          <span className="flex max-w-md items-center gap-2">
            <span className={`h-2 rounded-r-sm bg-accent-500 dark:bg-accent-400 ${animate ? "m-fill-right" : ""}`} style={{ width: `${Math.max(2, (r.value / top) * 100)}%`, maxWidth: "calc(100% - 3.5rem)" }} />
            <span className="num text-xs text-text-secondary">{format(r.value)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Resultado con signo como barra a un lado u otro de un cero central:
 * a la derecha y en color de ganancia si es positivo, a la izquierda y en
 * color de pérdida si es negativo. Escala común para toda la columna. */
export function PnlBar({ value, maxAbs }: { value: number | null; maxAbs: number }) {
  if (value === null || Number.isNaN(value) || maxAbs <= 0) return <span className="inline-block w-20" />;
  const half = Math.min(50, (Math.abs(value) / maxAbs) * 50);
  return (
    <span className="relative inline-block h-2.5 w-20 align-middle" aria-hidden="true">
      <span className="absolute inset-y-0 left-1/2 w-px bg-border-strong" />
      <span
        className={`absolute top-1/2 h-1.5 -translate-y-1/2 ${value >= 0 ? "rounded-r-sm bg-gain" : "rounded-l-sm bg-loss"}`}
        style={value >= 0 ? { left: "50%", width: `${half}%` } : { right: "50%", width: `${half}%` }}
      />
    </span>
  );
}
