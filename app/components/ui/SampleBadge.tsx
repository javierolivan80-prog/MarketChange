// SampleBadge.tsx — "Transparencia de rendimiento: IN_SAMPLE y OOS
// separados y etiquetados" (requisito de credibilidad del brief). El campo
// viene de portfolio_report.py / validation report.py (sample_split.py):
// null = sin partición de muestra (todo el rango histórico disponible),
// 'in_sample' = acotado a la parte usada para calibrar, 'oos' = fuera de
// muestra — un resultado OOS nunca debe leerse como si fuera in-sample, así
// que se marca de forma explícita y, si trae oos_warning, con más énfasis.
import type { SampleSplit } from "@/lib/queries";

export function SampleBadge({ sample, warning }: { sample: SampleSplit | undefined; warning?: string }) {
  if (sample === "oos") {
    return (
      <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800/60 dark:bg-amber-500/10 dark:text-amber-400">
        <span className="mr-1 font-semibold uppercase tracking-wide">Out-of-sample</span>
        {warning ?? "Datos fuera de la muestra usada para calibrar el sistema — no usar para ajustar parámetros."}
      </div>
    );
  }
  if (sample === "in_sample") {
    return (
      <div className="border border-border-subtle bg-surface-raised px-3 py-2 text-xs text-text-secondary">
        <span className="mr-1 font-semibold uppercase tracking-wide text-foreground">In-sample</span>
        Corrida sobre la parte del histórico usada para calibrar el sistema.
      </div>
    );
  }
  return (
    <div className="border border-border-subtle bg-surface-raised px-3 py-2 text-xs text-text-secondary">
      <span className="mr-1 font-semibold uppercase tracking-wide text-foreground">Sin partición de muestra</span>
      Corrida sobre todo el histórico disponible, sin separar in-sample de out-of-sample.
    </div>
  );
}
