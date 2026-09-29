// SignalDetail.tsx — fila expandida de SignalsTable: el razonamiento COMPLETO
// que el pipeline ya calcula por evento (Bull/Bear/Judge, análogos
// históricos, EV, y sobre todo el "por qué NO_TRADE" de abstention_engine.py),
// más la traza de origen (filing, cuándo se analizó, qué modelo) que da
// credibilidad a la señal.
//
// Diseño: nunca mostrar una probabilidad/EV sin su contexto de muestra al
// lado (n_historical_analogues) — mostrar "82%" sin decir sobre cuántos
// análogos se calculó es exactamente el tipo de falsa certeza que este
// proyecto evita en el resto del pipeline (ver historical_analogues.py).
import type { SignalFeedRow } from "@/lib/queries";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { formatDateTime } from "@/lib/format";

const STRATEGIES = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const STRATEGY_LABELS: Record<(typeof STRATEGIES)[number], string> = {
  CONSERVATIVE: "Conservador",
  BALANCED: "Balanceado",
  AGGRESSIVE: "Agresivo",
};

function Section({ title, accent, children }: { title: string; accent?: "long" | "short"; children: React.ReactNode }) {
  const accentClass = accent === "long" ? "text-emerald-700 dark:text-emerald-400" : accent === "short" ? "text-rose-700 dark:text-rose-400" : "text-foreground";
  return (
    <div className="rounded border border-border-subtle bg-surface p-3">
      <p className={`mb-1.5 text-xs font-semibold uppercase tracking-wide ${accentClass}`}>{title}</p>
      <div className="space-y-1 text-text-secondary">{children}</div>
    </div>
  );
}

function BulletList({ items }: { items: string[] | undefined }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="list-inside list-disc space-y-0.5">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

export function SignalDetail({ row, colSpan }: { row: SignalFeedRow; colSpan: number }) {
  const bull = row.bull_output;
  const bear = row.bear_output;
  const judge = row.judge_output;
  const novelty = row.novelty_reasoning;
  const impact = row.impact_estimation;
  const ev = row.ev_calculation;
  const abstention = row.abstention_decision;

  return (
    <tr className="border-b border-border-subtle bg-surface-raised">
      <td colSpan={colSpan} className="px-4 py-4 text-xs">
        {/* Traza de origen — de dónde viene esta señal y cuándo se calculó */}
        <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded border border-border-subtle bg-surface px-3 py-2 text-[11px] text-text-tertiary">
          {row.source_url && (
            <a href={row.source_url} target="_blank" rel="noopener noreferrer" className="text-accent-700 underline decoration-dotted hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300">
              Ver filing original ↗
            </a>
          )}
          <span>Publicado: {formatDateTime(row.filed_at)}</span>
          <span>Analizado: {formatDateTime(row.analyzed_at)}</span>
          <span>
            Modelos: {row.model_version_bull_bear} (Bull/Bear) · {row.model_version_judge} (Judge)
          </span>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Section title="Bull" accent="long">
            {bull ? (
              <>
                <p>{bull.thesis}</p>
                {bull.upside_drivers?.length > 0 && (
                  <>
                    <p className="mt-1 font-medium text-foreground">Drivers:</p>
                    <BulletList items={bull.upside_drivers} />
                  </>
                )}
                {bull.catalysts_forward?.length > 0 && (
                  <>
                    <p className="mt-1 font-medium text-foreground">Catalizadores próximos:</p>
                    <BulletList items={bull.catalysts_forward} />
                  </>
                )}
                <p className="mt-1 text-text-tertiary">Mercado direccionable: {bull.addressable_market}</p>
                <p className="text-text-tertiary">Comparables: {bull.comparable_events}</p>
              </>
            ) : (
              <p className="italic text-text-tertiary">—</p>
            )}
          </Section>

          <Section title="Bear" accent="short">
            {bear ? (
              <>
                <p>{bear.counter_thesis}</p>
                {bear.downside_risks?.length > 0 && (
                  <>
                    <p className="mt-1 font-medium text-foreground">Riesgos:</p>
                    <BulletList items={bear.downside_risks} />
                  </>
                )}
                {bear.negative_catalysts?.length > 0 && (
                  <>
                    <p className="mt-1 font-medium text-foreground">Catalizadores negativos:</p>
                    <BulletList items={bear.negative_catalysts} />
                  </>
                )}
                <p className="mt-1 text-text-tertiary">¿Ya descontado?: {bear.valuation_concern}</p>
                <p className="text-text-tertiary">Precedente: {bear.historical_precedent}</p>
              </>
            ) : (
              <p className="italic text-text-tertiary">—</p>
            )}
          </Section>

          <Section title="Judge (veredicto)">
            {judge ? (
              <>
                <p className="num">
                  Convicción neta {judge.net_conviction.toFixed(2)} · Confianza {judge.confidence_in_conviction.toFixed(0)}%
                </p>
                <p className="text-text-tertiary">Incertidumbre clave: {judge.key_uncertainty}</p>
                {judge.overriding_concern && <p className="text-text-tertiary">Preocupación dominante: {judge.overriding_concern}</p>}
              </>
            ) : (
              <p className="italic text-text-tertiary">—</p>
            )}
          </Section>

          <Section title="Novedad (¿qué tan sorpresa fue?)">
            {novelty ? (
              <>
                <p className="num">Puntuación: {novelty.score}/100</p>
                {novelty.components_used?.length > 0 && <p className="text-text-tertiary">Con datos de: {novelty.components_used.join(", ")}</p>}
                {novelty.components_unavailable?.length > 0 && (
                  <p className="text-amber-700 dark:text-amber-400">
                    Sin datos de: {novelty.components_unavailable.join(", ")} (no penaliza ni favorece — se renormaliza)
                  </p>
                )}
              </>
            ) : (
              <p className="italic text-text-tertiary">—</p>
            )}
          </Section>

          <Section title="Análogos históricos (estimación de impacto)">
            {impact ? (
              <>
                <p className="num font-medium text-foreground">
                  n={row.n_historical_analogues ?? "?"} análogos · confianza {impact.confidence.toFixed(0)}/100
                </p>
                <p>{impact.expected_magnitude}</p>
                <p className="num text-text-tertiary">
                  P(±5%)={impact.probability_5pct_move.toFixed(0)}% · P(±10%)={impact.probability_10pct_move.toFixed(0)}% · P(±20%)=
                  {impact.probability_20pct_move.toFixed(0)}%
                </p>
                {(row.n_historical_analogues ?? 0) < 5 && (
                  <p className="text-amber-700 dark:text-amber-400">Muestra pequeña — estimación contraída hacia el prior de la clase (shrinkage).</p>
                )}
              </>
            ) : (
              <p className="italic text-text-tertiary">—</p>
            )}
          </Section>

          <Section title="Valor esperado (EV)">
            {ev ? (
              <>
                <p className="text-text-tertiary">{ev.reasoning}</p>
                <p className="num mt-1">{ev.threshold_balanced}</p>
              </>
            ) : (
              <p className="italic text-text-tertiary">—</p>
            )}
          </Section>
        </div>

        {/* Abstención — el "por qué NO SIGNAL" y las condiciones que invalidan cada versión */}
        <div className="mt-4 border-t border-border-subtle pt-3">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-foreground">Decisión por versión de estrategia</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {STRATEGIES.map((strategy) => {
              const d = abstention?.[strategy];
              return (
                <div key={strategy} className="rounded border border-border-subtle bg-surface p-2">
                  <p className="mb-1 flex items-center justify-between font-medium text-foreground">
                    {STRATEGY_LABELS[strategy]}
                    <DirectionBadge value={d?.trade_decision ?? "NO_TRADE"} />
                  </p>
                  {d?.reason_if_no_trade ? (
                    <p className="text-text-tertiary">Condición de invalidación: {d.reason_if_no_trade}</p>
                  ) : d?.trade_decision && d.trade_decision !== "NO_TRADE" ? (
                    <p className="num text-text-tertiary">Pasó los 7 filtros de abstención · confianza {d.confidence.toFixed(0)}%</p>
                  ) : null}
                </div>
              );
            })}
          </div>
          {(row.entry_date || row.exit_date) && (
            <p className="num mt-2 text-text-tertiary">
              {row.entry_date && `Entrada ${row.entry_date}`}
              {row.exit_date && ` · Salida ${row.exit_date} (${row.exit_reason})`}
              {row.pnl_pct !== null && ` · P&L ${row.pnl_pct.toFixed(2)}%`}
            </p>
          )}
        </div>
      </td>
    </tr>
  );
}
