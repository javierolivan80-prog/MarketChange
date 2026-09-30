// DirectionBadge.tsx — única fuente visual para LONG/SHORT/NO_TRADE en toda
// la app. Verde/rojo están reservados a esto y a P&L (ver globals.css) — en
// ningún otro sitio de la interfaz se usa ese color, para que cuando
// aparezca signifique siempre lo mismo: dirección o resultado.
const STYLES: Record<string, string> = {
  LONG: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-400 dark:ring-emerald-400/20",
  SHORT: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-400 dark:ring-rose-400/20",
  NO_TRADE: "bg-surface-sunken text-text-secondary ring-border-strong",
};

const LABELS: Record<string, string> = {
  LONG: "Long",
  SHORT: "Short",
  NO_TRADE: "Sin operar",
};

export function DirectionBadge({ value, className = "" }: { value: string; className?: string }) {
  const style = STYLES[value] ?? STYLES.NO_TRADE;
  const label = LABELS[value] ?? value;
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style} ${className}`}>
      {label}
    </span>
  );
}

/** Texto de P&L o retorno con signo y color — mismo par verde/rojo que
 * DirectionBadge, para que "verde = a favor, rojo = en contra" sea el único
 * significado del color en toda la app. */
export function SignedPct({ value, digits = 2 }: { value: number | null | undefined; digits?: number }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="text-text-tertiary">—</span>;
  }
  const positive = value >= 0;
  return (
    <span className={`num font-medium ${positive ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}`}>
      {positive ? "+" : ""}
      {value.toFixed(digits)}%
    </span>
  );
}
