// SignalAnalysis.tsx — el razonamiento COMPLETO que el pipeline calculó para
// un evento: Bull/Bear/Judge, novedad, análogos históricos, EV y, sobre todo,
// el porqué de cada decisión de operar o no (abstention_engine.py), más la
// traza de origen (filing, cuándo se analizó, con qué modelo).
//
// Se usa en dos sitios: la fila desplegada de la tabla de Señales (cargado
// bajo demanda) y la página propia de cada señal (/senales/[id]).
//
// Nunca se muestra una probabilidad o un EV sin el tamaño de muestra al lado
// (n_historical_analogues): "82%" sin decir sobre cuántos casos es justo la
// falsa certeza que el resto del pipeline evita.
import type { SignalDetailData, StrategyVersion } from "@/lib/queries";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { TechnicalPlanCard } from "@/components/TechnicalPlanCard";
import { formatDateTime } from "@/lib/format";
import { VERSION_LABELS, VERSION_ORDER, exitReasonLabel } from "@/lib/labels";

function Section({ title, accent, children }: { title: string; accent?: "long" | "short"; children: React.ReactNode }) {
  const accentClass = accent === "long" ? "text-emerald-700 dark:text-emerald-400" : accent === "short" ? "text-rose-700 dark:text-rose-400" : "text-foreground";
  return (
    <div className="border border-border-subtle bg-surface p-3">
      <h3 className={`mb-1.5 text-xs font-semibold uppercase tracking-wide ${accentClass}`}>{title}</h3>
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

const Empty = () => <p className="italic text-text-tertiary">Sin datos.</p>;

// La decisión sale de las columnas trade_decision_* (fuente de verdad, NOT
// NULL); el JSON de abstención solo aporta el motivo y la confianza.
function decisionOf(detail: SignalDetailData, version: StrategyVersion): string {
  const column = { CONSERVATIVE: detail.trade_decision_conservative, BALANCED: detail.trade_decision_balanced, AGGRESSIVE: detail.trade_decision_aggressive }[version];
  return column ?? detail.abstention_decision?.[version]?.trade_decision ?? "NO_TRADE";
}

export function SignalAnalysis({ detail, recommended }: { detail: SignalDetailData; recommended: StrategyVersion }) {
  const { bull_output: bull, bear_output: bear, judge_output: judge, novelty_reasoning: novelty, impact_estimation: impact, ev_calculation: ev, abstention_decision: abstention } = detail;
  const rec = abstention?.[recommended];
  const recDecision = decisionOf(detail, recommended);

  return (
    <div className="text-sm">
      {/* Decisión recomendada primero: es la respuesta; lo demás es el porqué */}
      <div className="mb-4 border border-border-strong bg-surface p-3">
        <p className="mb-1 text-xs uppercase tracking-wide text-text-tertiary">Decisión de la estrategia recomendada ({VERSION_LABELS[recommended].toLowerCase()})</p>
        <div className="flex flex-wrap items-center gap-2">
          <DirectionBadge value={recDecision} />
          <span className="text-foreground">
            {recDecision !== "NO_TRADE"
              ? `Pasa todos los filtros · confianza ${detail.confidence.toFixed(0)}%`
              : rec?.reason_if_no_trade
                ? `No se opera: ${rec.reason_if_no_trade}`
                : "No se opera."}
          </span>
        </div>
        {judge?.key_uncertainty && <p className="mt-1.5 text-text-secondary">Qué lo cambiaría: {judge.key_uncertainty}</p>}
      </div>

      {detail.technical && (
        <div id="plan" className="scroll-mt-4">
          <TechnicalPlanCard plan={detail.technical} />
        </div>
      )}

      <div id="analisis" className="grid scroll-mt-4 grid-cols-1 gap-3 lg:grid-cols-2">
        <Section title="A favor (Bull)" accent="long">
          {bull ? (
            <>
              <p>{bull.thesis}</p>
              {bull.upside_drivers?.length > 0 && (
                <>
                  <p className="mt-1 font-medium text-foreground">Motores:</p>
                  <BulletList items={bull.upside_drivers} />
                </>
              )}
              {bull.catalysts_forward?.length > 0 && (
                <>
                  <p className="mt-1 font-medium text-foreground">Próximos catalizadores:</p>
                  <BulletList items={bull.catalysts_forward} />
                </>
              )}
              {bull.addressable_market && <p className="mt-1 text-text-tertiary">Mercado: {bull.addressable_market}</p>}
              {bull.comparable_events && <p className="text-text-tertiary">Comparables: {bull.comparable_events}</p>}
            </>
          ) : (
            <Empty />
          )}
        </Section>

        <Section title="En contra (Bear)" accent="short">
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
              {bear.valuation_concern && <p className="mt-1 text-text-tertiary">¿Ya en el precio?: {bear.valuation_concern}</p>}
              {bear.historical_precedent && <p className="text-text-tertiary">Precedente: {bear.historical_precedent}</p>}
            </>
          ) : (
            <Empty />
          )}
        </Section>

        <Section title="Veredicto (Judge)">
          {judge ? (
            <>
              <p className="num">
                Convicción neta {judge.net_conviction.toFixed(2)} · confianza {judge.confidence_in_conviction.toFixed(0)}%
              </p>
              {judge.overriding_concern && <p className="text-text-tertiary">Preocupación dominante: {judge.overriding_concern}</p>}
            </>
          ) : (
            <Empty />
          )}
        </Section>

        <Section title="¿Era una sorpresa?">
          {novelty ? (
            <>
              <p className="num">Novedad: {novelty.score}/100</p>
              {novelty.components_used?.length > 0 && <p className="text-text-tertiary">Calculada con: {novelty.components_used.join(", ")}</p>}
              {novelty.components_unavailable?.length > 0 && (
                <p className="text-amber-700 dark:text-amber-400">
                  Sin datos de: {novelty.components_unavailable.join(", ")}. No penaliza: el peso se reparte entre el resto.
                </p>
              )}
            </>
          ) : (
            <Empty />
          )}
        </Section>

        <Section title="Casos parecidos en el pasado">
          {impact ? (
            <>
              <p className="num font-medium text-foreground">
                {detail.n_historical_analogues ?? "?"} casos · confianza {impact.confidence.toFixed(0)}/100
              </p>
              <p>{impact.expected_magnitude}</p>
              <p className="num text-text-tertiary">
                Prob. de moverse ±5%: {impact.probability_5pct_move.toFixed(0)}% · ±10%: {impact.probability_10pct_move.toFixed(0)}% · ±20%:{" "}
                {impact.probability_20pct_move.toFixed(0)}%
              </p>
              {(detail.n_historical_analogues ?? 0) < 5 && (
                <p className="text-amber-700 dark:text-amber-400">Pocos casos: la estimación se acerca a la media de su tipo de evento.</p>
              )}
            </>
          ) : (
            <Empty />
          )}
        </Section>

        <Section title="Valor esperado">
          {ev ? (
            <>
              <p className="text-text-tertiary">{ev.reasoning}</p>
              <p className="num mt-1">{ev.threshold_balanced}</p>
            </>
          ) : (
            <Empty />
          )}
        </Section>
      </div>

      <div id="versiones" className="mt-4 scroll-mt-4 border-t border-border-subtle pt-3">
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-foreground">Las tres estrategias</h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {VERSION_ORDER.map((strategy) => {
            const d = abstention?.[strategy];
            const decision = decisionOf(detail, strategy);
            const isRec = strategy === recommended;
            return (
              <div key={strategy} className={`border p-2 ${isRec ? "border-accent-500" : "border-border-subtle"} bg-surface`}>
                <p className="mb-1 flex items-center justify-between font-medium text-foreground">
                  <span>
                    {VERSION_LABELS[strategy]}
                    {isRec && <span className="ml-1.5 text-xs font-normal text-accent-700 dark:text-accent-400">recomendada</span>}
                  </span>
                  <DirectionBadge value={decision} />
                </p>
                {decision !== "NO_TRADE" ? (
                  <p className="num text-xs text-text-tertiary">Pasa los 7 filtros · confianza {(d?.confidence ?? detail.confidence).toFixed(0)}%</p>
                ) : d?.reason_if_no_trade ? (
                  <p className="text-xs text-text-tertiary">{d.reason_if_no_trade}</p>
                ) : null}
              </div>
            );
          })}
        </div>
        {(detail.entry_date || detail.exit_date) && (
          <p className="num mt-2 text-xs text-text-tertiary">
            En el histórico: {detail.entry_date && `entrada ${detail.entry_date}`}
            {detail.exit_date && ` · salida ${detail.exit_date} (${exitReasonLabel(detail.exit_reason)})`}
            {detail.pnl_pct !== null && ` · resultado ${detail.pnl_pct.toFixed(2)}%`}
          </p>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-tertiary">
        {detail.source_url && (
          <a href={detail.source_url} target="_blank" rel="noopener noreferrer" className="text-accent-700 underline decoration-dotted hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300">
            Filing original ↗
          </a>
        )}
        <span>Publicado: {formatDateTime(detail.filed_at)}</span>
        <span>Analizado: {formatDateTime(detail.analyzed_at)}</span>
        <span>
          Modelos: {detail.model_version_bull_bear} (debate) · {detail.model_version_judge} (veredicto)
        </span>
      </div>
    </div>
  );
}
