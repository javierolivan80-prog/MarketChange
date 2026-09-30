// Callout.tsx — aviso/nota en línea, con etiqueta de texto en vez de un
// icono o emoji. "Cero emojis" del brief no es solo estética: en una tabla
// financiera un glifo de emoji renderiza distinto según SO/fuente y no dice
// nada por sí mismo — una etiqueta de texto (AVISO, CRÍTICO...) es legible
// en cualquier sistema y explícita sobre qué tan grave es el hallazgo.
const STYLES: Record<string, string> = {
  warning: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800/60 dark:bg-amber-500/10 dark:text-amber-400",
  positive: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800/60 dark:bg-emerald-500/10 dark:text-emerald-400",
  critical: "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800/60 dark:bg-rose-500/10 dark:text-rose-400",
  neutral: "border-border-subtle bg-surface-raised text-text-secondary",
};

const LABELS: Record<string, string> = {
  warning: "Aviso",
  positive: "Resultado",
  critical: "Crítico",
  neutral: "Nota",
};

export function Callout({
  kind,
  children,
  className = "",
}: {
  kind: "warning" | "positive" | "critical" | "neutral";
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded border px-2 py-1.5 text-xs ${STYLES[kind]} ${className}`}>
      <span className="mr-1 font-semibold uppercase tracking-wide">{LABELS[kind]}:</span>
      {children}
    </div>
  );
}
