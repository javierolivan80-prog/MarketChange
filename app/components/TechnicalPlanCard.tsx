// TechnicalPlanCard.tsx — el plan técnico de una señal
// (pipeline/analyze/technical_analysis.py): entrada, stop, objetivos,
// riesgo/beneficio, tamaño y horizonte, con el porqué de cada número
// (niveles en que se apoyan, indicadores alineados, puntuación desglosada) y
// las comprobaciones previas que pasa o no. Las limitaciones del cálculo se
// enseñan, no se esconden: solo hay precios diarios.
//
// Recibe el idioma como prop (lo pinta SignalAnalysis, servidor o cliente);
// los textos que vienen del pipeline se traducen con lib/planText.ts.
import type { TechnicalPlan } from "@/lib/queries";
import { makeT, type Locale, type T } from "@/lib/i18n";
import { planReason, planText } from "@/lib/planText";

const checkLabels = (t: T): Record<string, string> => ({
  catalyst_confirmed: t("Catalizador confirmado (no especulativo)", "Confirmed catalyst (not speculative)"),
  indicators_aligned: t("Al menos 2 indicadores alineados", "At least 2 aligned indicators"),
  risk_reward_ok: t("Riesgo/beneficio de 1:2 o mejor", "Risk/reward of 1:2 or better"),
  stop_on_support: t("Stop apoyado en un nivel real", "Stop placed on a real level"),
  enough_history: t("Histórico suficiente", "Enough history"),
});

const componentLabels = (t: T): Record<string, { label: string; max: number }> => ({
  catalyst: { label: t("Catalizador confirmado", "Confirmed catalyst"), max: 40 },
  indicators: { label: t("3 o más indicadores alineados", "3 or more aligned indicators"), max: 30 },
  confluence: { label: t("Confluencia de niveles", "Level confluence"), max: 20 },
  volume: { label: t("Volumen de confirmación", "Confirming volume"), max: 10 },
});

const price = (v: number | null) => (v === null ? "—" : v.toFixed(2));

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="text-xs text-text-secondary">{label}</p>
      <p className="num text-lg font-semibold text-foreground">{value}</p>
      {hint && <p className="text-xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

export function TechnicalPlanCard({ plan, locale = "es" }: { plan: TechnicalPlan; locale?: Locale }) {
  const t = makeT(locale);
  const CHECK_LABELS = checkLabels(t);
  const COMPONENT_LABELS = componentLabels(t);
  const tr = (items: string[]) => items.map((x) => planText(x, locale));
  const d = plan.details;
  const checks = Object.entries(d.checks ?? {});
  const long = plan.direction === "LONG";

  return (
    <section className="mb-4 border border-border-strong bg-surface p-3" aria-labelledby="plan-tecnico">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="plan-tecnico" className="text-xs font-semibold uppercase tracking-wide text-foreground">
          {t("Plan técnico", "Technical plan")}
        </h3>
        <p className={`text-sm font-medium ${plan.passes_filters ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}>
          {plan.passes_filters ? t("Pasa los filtros de riesgo", "Passes the risk filters") : t("No pasa los filtros de riesgo", "Does not pass the risk filters")} ·{" "}
          {t("confianza", "confidence")} {plan.confidence}/100
        </p>
      </div>
      {!plan.passes_filters && d.reason_if_rejected && <p className="mb-3 text-sm text-text-secondary">{planReason(d.reason_if_rejected, locale)}</p>}
      {d.warnings && d.warnings.length > 0 && (
        <ul className="mb-3 space-y-0.5 text-sm text-amber-700 dark:text-amber-400">
          {d.warnings.map((w) => (
            <li key={w}>{planText(w, locale)}</li>
          ))}
        </ul>
      )}

      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        <Metric label={t("Entrada", "Entry")} value={price(plan.entry)} hint={t("cierre del día del evento", "close on the event day")} />
        <Metric label="Stop" value={price(plan.stop)} />
        <Metric label={t("Objetivo parcial", "Partial target")} value={price(plan.target)} />
        <Metric label={t("Objetivo final", "Final target")} value={price(plan.target2)} />
        <Metric label={t("Riesgo/beneficio", "Risk/reward")} value={plan.risk_reward !== null ? `1:${plan.risk_reward.toFixed(1)}` : "—"} />
        <Metric
          label={t("Horizonte", "Horizon")}
          value={plan.timeframe_days !== null ? `~${plan.timeframe_days} ${t("sesiones", "sessions")}` : "—"}
        />
        {/* Sin tamaño para un plan que no pasa los filtros: el sistema no lo
            recomienda, y un "3% del capital" junto a él se leería como si sí. */}
        <Metric
          label={t("Tamaño máximo", "Max size")}
          value={plan.passes_filters && plan.position_size_pct !== null ? `${plan.position_size_pct}%` : "—"}
          hint={plan.passes_filters ? t("del capital", "of capital") : t("no operar", "do not trade")}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 text-xs text-text-secondary lg:grid-cols-3">
        <div>
          <p className="mb-1 font-medium text-foreground">{t("Comprobaciones previas", "Pre-trade checks")}</p>
          <ul className="space-y-0.5">
            {checks.map(([key, ok]) => (
              <li key={key}>
                <span className={ok ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}>{ok ? t("Sí", "Yes") : "No"}</span> ·{" "}
                {CHECK_LABELS[key] ?? key}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 font-medium text-foreground">{t("Puntuación", "Score")}</p>
          <ul className="num space-y-0.5">
            {Object.entries(COMPONENT_LABELS).map(([key, { label, max }]) => (
              <li key={key}>
                {d.components?.[key as keyof typeof d.components] ?? 0}/{max} · {label}
              </li>
            ))}
          </ul>
        </div>
        <div className="space-y-1">
          <p>
            <span className="font-medium text-foreground">{t("Indicadores a favor:", "Indicators in favour:")} </span>
            {d.aligned && d.aligned.length > 0 ? tr(d.aligned).join(", ") : t("ninguno", "none")}
          </p>
          {d.stop_basis && d.stop_basis.length > 0 && (
            <p>
              <span className="font-medium text-foreground">
                {long ? t("Stop bajo:", "Stop below:") : t("Stop sobre:", "Stop above:")}{" "}
              </span>
              {tr(d.stop_basis).join(", ")}
            </p>
          )}
          {(d.context?.vix != null || d.context?.expected_move_pct != null) && (
            <p className="num">
              {d.context?.vix != null && (
                <>
                  {t("VIX el día del evento", "VIX on the event day")}: {d.context.vix.toFixed(1)}.{" "}
                </>
              )}
              {d.context?.expected_move_pct != null && (
                <>
                  {t("Movimiento típico en eventos parecidos", "Typical move in similar events")}: ±{Math.abs(d.context.expected_move_pct).toFixed(1)}%.
                </>
              )}
            </p>
          )}
          {d.target_basis && d.target_basis.length > 0 && (
            <p>
              <span className="font-medium text-foreground">{t("Objetivo en:", "Target at:")} </span>
              {tr(d.target_basis).join(", ")}
            </p>
          )}
        </div>
      </div>

      {d.exit_rules && d.exit_rules.length > 0 && (
        <details className="mt-3 text-xs text-text-secondary">
          <summary className="cursor-pointer">{t("Reglas de entrada y salida", "Entry and exit rules")}</summary>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            {d.exit_rules.map((r) => (
              <li key={r}>{planText(r, locale)}</li>
            ))}
          </ul>
          {d.limitations && d.limitations.length > 0 && (
            <p className="mt-2 text-text-tertiary">
              {t("Límites del análisis:", "Analysis limitations:")} {tr(d.limitations).join(" ")}
            </p>
          )}
        </details>
      )}
    </section>
  );
}
