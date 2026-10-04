// Meter.tsx — una cifra de 0 a máximo como barra con su pista: la confianza
// técnica (0-100) se lee de un vistazo en vez de comparar números sueltos.
// El número va siempre al lado (el relleno no es el único canal).
export function Meter({
  value,
  max = 100,
  label,
  strong = false,
  width = "w-16",
}: {
  value: number;
  max?: number;
  /** Texto accesible: qué mide y el valor. */
  label: string;
  /** Resaltado (p. ej., pasa los filtros): relleno más intenso. */
  strong?: boolean;
  width?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <span className={`relative inline-block h-1.5 ${width} rounded-full bg-border-subtle align-middle`} role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-label={label}>
      <span className={`absolute inset-y-0 left-0 rounded-full ${strong ? "bg-accent-600 dark:bg-accent-400" : "bg-accent-300 dark:bg-accent-700"}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

/** La puntuación del plan técnico por partes: un tramo por criterio, ancho
 * proporcional a lo que vale (40/30/20/10), relleno si se cumple. Los tramos
 * cumplidos aparecen en orden (la nota se construye sumando); el color de
 * cada uno es el final desde el primer fotograma. */
export function ScoreSegments({ parts, label }: { parts: { name: string; max: number; value: number }[]; label: string }) {
  const total = parts.reduce((s, p) => s + p.max, 0) || 1;
  return (
    <span className="flex h-2 w-full gap-0.5" role="img" aria-label={label}>
      {parts.map((p, i) => (
        <span
          key={p.name}
          title={`${p.name}: ${p.value}/${p.max}`}
          className={`h-full rounded-sm ${p.value > 0 ? "m-fade bg-accent-600 dark:bg-accent-400" : "bg-border-subtle"}`}
          style={{ width: `${(p.max / total) * 100}%`, animationDelay: `calc(var(--motion-stagger) * ${i})` }}
        />
      ))}
    </span>
  );
}
