// SampleBadge.tsx — de qué periodo salen las cifras: el que se usó para
// ajustar el sistema (in-sample) o uno que nunca vio (out-of-sample). La
// distinción se mantiene porque es justo lo que separa un resultado fiable de
// uno optimista, pero contada al usuario: el texto que trae el pipeline
// (oos_warning, "no usar para ajustar parámetros") es una instrucción para
// quien opera el sistema y no se muestra.
import type { SampleSplit } from "@/lib/queries";

const COPY: Record<"oos" | "in_sample" | "full", { label: string; text: string; tone: string }> = {
  oos: {
    label: "Periodo no visto",
    text: "Resultados en un periodo que no se usó para ajustar el sistema: la prueba más exigente.",
    tone: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800/60 dark:bg-emerald-500/10 dark:text-emerald-400",
  },
  in_sample: {
    label: "Periodo de ajuste",
    text: "Resultados en el mismo periodo con el que se ajustó el sistema; suelen ser más optimistas que los futuros.",
    tone: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800/60 dark:bg-amber-500/10 dark:text-amber-400",
  },
  full: {
    label: "Histórico completo",
    text: "Incluye el periodo con el que se ajustó el sistema, así que puede ser algo optimista.",
    tone: "border-border-subtle bg-surface-raised text-text-secondary",
  },
};

// `warning` se acepta por compatibilidad con los llamadores, pero no se pinta (ver arriba).
export function SampleBadge({ sample }: { sample: SampleSplit | undefined; warning?: string }) {
  const c = COPY[sample === "oos" ? "oos" : sample === "in_sample" ? "in_sample" : "full"];
  return (
    <div className={`border px-3 py-2 text-xs ${c.tone}`}>
      <span className="mr-1 font-semibold">{c.label}.</span>
      {c.text}
    </div>
  );
}
