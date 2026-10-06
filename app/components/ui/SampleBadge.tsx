"use client";

// SampleBadge.tsx — de qué periodo salen las cifras: el que se usó para
// ajustar el sistema (in-sample) o uno que nunca vio (out-of-sample). La
// distinción se mantiene porque es justo lo que separa un resultado fiable de
// uno optimista, pero contada al usuario: el texto que trae el pipeline
// (oos_warning, "no usar para ajustar parámetros") es una instrucción para
// quien opera el sistema y no se muestra.
import type { SampleSplit } from "@/lib/queries";
import type { T } from "@/lib/i18n";
import { useT } from "@/components/i18n/LocaleProvider";

const copy = (t: T): Record<"oos" | "in_sample" | "full", { label: string; text: string; tone: string }> => ({
  oos: {
    label: t("Periodo no visto", "Unseen period"),
    text: t(
      "Resultados en un periodo que no se usó para ajustar el sistema: la prueba más exigente.",
      "Results over a period that was not used to tune the system: the toughest test.",
    ),
    tone: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800/60 dark:bg-emerald-500/10 dark:text-emerald-400",
  },
  in_sample: {
    label: t("Periodo de ajuste", "Tuning period"),
    text: t(
      "Resultados en el mismo periodo con el que se ajustó el sistema; suelen ser más optimistas que los futuros.",
      "Results over the same period used to tune the system; they tend to be more optimistic than future ones.",
    ),
    tone: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800/60 dark:bg-amber-500/10 dark:text-amber-400",
  },
  full: {
    label: t("Histórico completo", "Full history"),
    text: t(
      "Incluye el periodo con el que se ajustó el sistema, así que puede ser algo optimista.",
      "It includes the period used to tune the system, so it may be somewhat optimistic.",
    ),
    tone: "border-border-subtle bg-surface-raised text-text-secondary",
  },
});

// Todos los backtests deciden con la regla sin IA (BUGS_REPORT.md H-06): la
// IA no se puede validar con eventos anteriores a la fecha de corte de sus
// modelos, que pudieron haber leído qué pasó después.
const reglaSinIa = (t: T) =>
  t(
    "Decide la regla sin IA (la dirección de los casos parecidos del pasado): la IA no se puede comprobar con eventos anteriores a la fecha de corte de sus modelos.",
    "Decisions come from the rule without AI (the direction of similar past cases): the AI cannot be tested on events before its models' cutoff date.",
  );

// `warning` se acepta por compatibilidad con los llamadores, pero no se pinta (ver arriba).
// inset: dentro de otro bloque con borde (Inicio), sin borde propio para no
// dibujar un recuadro dentro del recuadro.
export function SampleBadge({ sample, inset = false }: { sample: SampleSplit | undefined; warning?: string; inset?: boolean }) {
  const t = useT();
  const c = copy(t)[sample === "oos" ? "oos" : sample === "in_sample" ? "in_sample" : "full"];
  return (
    <div className={`px-3 py-2 text-xs ${c.tone} ${inset ? "border-0" : "border"}`}>
      <span className="mr-1 font-semibold">{c.label}.</span>
      {c.text}
      <span className="mt-1 block">
        <span className="mr-1 font-semibold">{t("Regla sin IA.", "Rule without AI.")}</span>
        {reglaSinIa(t)}
      </span>
    </div>
  );
}
