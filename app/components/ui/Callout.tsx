"use client";

// Callout.tsx — aviso/nota en línea, con etiqueta de texto en vez de un
// icono o emoji. "Cero emojis" del brief no es solo estética: en una tabla
// financiera un glifo de emoji renderiza distinto según SO/fuente y no dice
// nada por sí mismo — una etiqueta de texto (AVISO, CRÍTICO...) es legible
// en cualquier sistema y explícita sobre qué tan grave es el hallazgo.
const STYLES: Record<string, string> = {
  warning: "bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  positive: "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  critical: "bg-rose-50 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300",
  neutral: "bg-fill text-text-secondary",
};

import { useT } from "@/components/i18n/LocaleProvider";

export function Callout({
  kind,
  children,
  className = "",
}: {
  kind: "warning" | "positive" | "critical" | "neutral";
  children: React.ReactNode;
  className?: string;
}) {
  const t = useT();
  const LABELS: Record<string, string> = {
    warning: t("Aviso", "Warning"),
    positive: t("Resultado", "Result"),
    critical: t("Crítico", "Critical"),
    neutral: t("Nota", "Note"),
  };
  return (
    // Banda con relleno de color y esquinas suaves (Tanda 12c), sin borde.
    <div className={`rounded-control border border-transparent px-3 py-2 text-xs contrast-more:border-border-strong ${STYLES[kind]} ${className}`}>
      <span className="mr-1 font-semibold">{LABELS[kind]}:</span>
      {children}
    </div>
  );
}
