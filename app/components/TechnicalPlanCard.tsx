// TechnicalPlanCard.tsx — el plan técnico de una señal
// (pipeline/analyze/technical_analysis.py): entrada, stop, objetivos,
// riesgo/beneficio, tamaño y horizonte, con el porqué de cada número
// (niveles en que se apoyan, indicadores alineados, puntuación desglosada) y
// las comprobaciones previas que pasa o no. Las limitaciones del cálculo se
// enseñan, no se esconden: solo hay precios diarios.
import type { TechnicalPlan } from "@/lib/queries";

const CHECK_LABELS: Record<string, string> = {
  catalyst_confirmed: "Catalizador confirmado (no especulativo)",
  indicators_aligned: "Al menos 2 indicadores alineados",
  risk_reward_ok: "Riesgo/beneficio de 1:2 o mejor",
  stop_on_support: "Stop apoyado en un nivel real",
  enough_history: "Histórico suficiente",
};

const COMPONENT_LABELS: Record<string, { label: string; max: number }> = {
  catalyst: { label: "Catalizador confirmado", max: 40 },
  indicators: { label: "3 o más indicadores alineados", max: 30 },
  confluence: { label: "Confluencia de niveles", max: 20 },
  volume: { label: "Volumen de confirmación", max: 10 },
};

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

export function TechnicalPlanCard({ plan }: { plan: TechnicalPlan }) {
  const d = plan.details;
  const checks = Object.entries(d.checks ?? {});
  const long = plan.direction === "LONG";

  return (
    <section className="mb-4 border border-border-strong bg-surface p-3" aria-labelledby="plan-tecnico">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="plan-tecnico" className="text-xs font-semibold uppercase tracking-wide text-foreground">
          Plan técnico
        </h3>
        <p className={`text-sm font-medium ${plan.passes_filters ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}>
          {plan.passes_filters ? "Pasa los filtros de riesgo" : "No pasa los filtros de riesgo"} · confianza {plan.confidence}/100
        </p>
      </div>
      {!plan.passes_filters && d.reason_if_rejected && <p className="mb-3 text-sm text-text-secondary">{d.reason_if_rejected}</p>}
      {d.warnings && d.warnings.length > 0 && (
        <ul className="mb-3 space-y-0.5 text-sm text-amber-700 dark:text-amber-400">
          {d.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        <Metric label="Entrada" value={price(plan.entry)} hint="cierre del día del evento" />
        <Metric label="Stop" value={price(plan.stop)} />
        <Metric label="Objetivo parcial" value={price(plan.target)} />
        <Metric label="Objetivo final" value={price(plan.target2)} />
        <Metric label="Riesgo/beneficio" value={plan.risk_reward !== null ? `1:${plan.risk_reward.toFixed(1)}` : "—"} />
        <Metric label="Horizonte" value={plan.timeframe_days !== null ? `~${plan.timeframe_days} sesiones` : "—"} />
        {/* Sin tamaño para un plan que no pasa los filtros: el sistema no lo
            recomienda, y un "3% del capital" junto a él se leería como si sí. */}
        <Metric
          label="Tamaño máximo"
          value={plan.passes_filters && plan.position_size_pct !== null ? `${plan.position_size_pct}%` : "—"}
          hint={plan.passes_filters ? "del capital" : "no operar"}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 text-xs text-text-secondary lg:grid-cols-3">
        <div>
          <p className="mb-1 font-medium text-foreground">Comprobaciones previas</p>
          <ul className="space-y-0.5">
            {checks.map(([key, ok]) => (
              <li key={key}>
                <span className={ok ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}>{ok ? "Sí" : "No"}</span> ·{" "}
                {CHECK_LABELS[key] ?? key}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 font-medium text-foreground">Puntuación</p>
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
            <span className="font-medium text-foreground">Indicadores a favor: </span>
            {d.aligned && d.aligned.length > 0 ? d.aligned.join(", ") : "ninguno"}
          </p>
          {d.stop_basis && d.stop_basis.length > 0 && (
            <p>
              <span className="font-medium text-foreground">Stop {long ? "bajo" : "sobre"}: </span>
              {d.stop_basis.join(", ")}
            </p>
          )}
          {(d.context?.vix != null || d.context?.expected_move_pct != null) && (
            <p className="num">
              {d.context?.vix != null && <>VIX el día del evento: {d.context.vix.toFixed(1)}. </>}
              {d.context?.expected_move_pct != null && <>Movimiento típico en eventos parecidos: ±{Math.abs(d.context.expected_move_pct).toFixed(1)}%.</>}
            </p>
          )}
          {d.target_basis && d.target_basis.length > 0 && (
            <p>
              <span className="font-medium text-foreground">Objetivo en: </span>
              {d.target_basis.join(", ")}
            </p>
          )}
        </div>
      </div>

      {d.exit_rules && d.exit_rules.length > 0 && (
        <details className="mt-3 text-xs text-text-secondary">
          <summary className="cursor-pointer">Reglas de entrada y salida</summary>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            {d.exit_rules.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          {d.limitations && d.limitations.length > 0 && (
            <p className="mt-2 text-text-tertiary">Límites del análisis: {d.limitations.join(" ")}</p>
          )}
        </details>
      )}
    </section>
  );
}
