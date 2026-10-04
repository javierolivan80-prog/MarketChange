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
//
// Recibe el idioma como prop (no por contexto): lo pinta tanto un componente
// de servidor (/senales/[id]) como uno de cliente (la fila desplegada).
// El texto que escribe la IA (tesis, riesgos, motivos) viene del pipeline en
// español; en inglés se avisa una vez en vez de dejar al lector adivinarlo.
import type { SignalDetailData, StrategyVersion } from "@/lib/queries";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { TechnicalPlanCard } from "@/components/TechnicalPlanCard";
import { formatDateTime } from "@/lib/format";
import { VERSION_ORDER, exitReasonLabel, versionLabel } from "@/lib/labels";
import { makeT, type Locale } from "@/lib/i18n";
import { planText } from "@/lib/planText";
import { HBars } from "@/components/viz/Bars";

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

const Empty = ({ locale }: { locale: Locale }) => <p className="italic text-text-tertiary">{makeT(locale)("Sin datos.", "No data.")}</p>;

// La decisión sale de las columnas trade_decision_* (fuente de verdad, NOT
// NULL); el JSON de abstención solo aporta el motivo y la confianza.
function decisionOf(detail: SignalDetailData, version: StrategyVersion): string {
  const column = { CONSERVATIVE: detail.trade_decision_conservative, BALANCED: detail.trade_decision_balanced, AGGRESSIVE: detail.trade_decision_aggressive }[version];
  return column ?? detail.abstention_decision?.[version]?.trade_decision ?? "NO_TRADE";
}

export function SignalAnalysis({ detail, recommended, locale = "es" }: { detail: SignalDetailData; recommended: StrategyVersion; locale?: Locale }) {
  const t = makeT(locale);
  const { bull_output: bull, bear_output: bear, judge_output: judge, novelty_reasoning: novelty, impact_estimation: impact, ev_calculation: ev, abstention_decision: abstention } = detail;
  const rec = abstention?.[recommended];
  const recDecision = decisionOf(detail, recommended);

  return (
    <div className="text-sm">
      {locale === "en" && (
        <p className="mb-3 text-xs text-text-tertiary">
          The written analysis (thesis, risks and reasons) is generated in Spanish by the pipeline; figures and labels are translated.
        </p>
      )}
      {/* Decisión recomendada primero: es la respuesta; lo demás es el porqué */}
      <div className="mb-4 border border-border-strong bg-surface p-3">
        <p className="mb-1 text-xs uppercase tracking-wide text-text-tertiary">
          {t("Decisión de la estrategia recomendada", "Recommended strategy's decision")} ({versionLabel(recommended, locale).toLowerCase()})
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <DirectionBadge value={recDecision} />
          <span className="text-foreground">
            {recDecision !== "NO_TRADE"
              ? `${t("Pasa todos los filtros · confianza", "Passes every filter · confidence")} ${detail.confidence.toFixed(0)}%`
              : rec?.reason_if_no_trade
                ? `${t("No se opera", "Not traded")}: ${rec.reason_if_no_trade}`
                : t("No se opera.", "Not traded.")}
          </span>
        </div>
        {judge?.key_uncertainty && <p className="mt-1.5 text-text-secondary">{t("Qué lo cambiaría", "What would change it")}: {judge.key_uncertainty}</p>}
      </div>

      {detail.technical && (
        <div id="plan" className="scroll-mt-4">
          <TechnicalPlanCard plan={detail.technical} locale={locale} />
        </div>
      )}

      <div id="analisis" className="grid scroll-mt-4 grid-cols-1 gap-3 lg:grid-cols-2">
        <Section title={t("A favor (Bull)", "The case for (Bull)")} accent="long">
          {bull ? (
            <>
              <p>{bull.thesis}</p>
              {bull.upside_drivers?.length > 0 && (
                <>
                  <p className="mt-1 font-medium text-foreground">{t("Motores:", "Drivers:")}</p>
                  <BulletList items={bull.upside_drivers} />
                </>
              )}
              {bull.catalysts_forward?.length > 0 && (
                <>
                  <p className="mt-1 font-medium text-foreground">{t("Próximos catalizadores:", "Upcoming catalysts:")}</p>
                  <BulletList items={bull.catalysts_forward} />
                </>
              )}
              {bull.addressable_market && <p className="mt-1 text-text-tertiary">{t("Mercado", "Market")}: {bull.addressable_market}</p>}
              {bull.comparable_events && <p className="text-text-tertiary">{t("Comparables", "Comparables")}: {bull.comparable_events}</p>}
            </>
          ) : (
            <Empty locale={locale} />
          )}
        </Section>

        <Section title={t("En contra (Bear)", "The case against (Bear)")} accent="short">
          {bear ? (
            <>
              <p>{bear.counter_thesis}</p>
              {bear.downside_risks?.length > 0 && (
                <>
                  <p className="mt-1 font-medium text-foreground">{t("Riesgos:", "Risks:")}</p>
                  <BulletList items={bear.downside_risks} />
                </>
              )}
              {bear.negative_catalysts?.length > 0 && (
                <>
                  <p className="mt-1 font-medium text-foreground">{t("Catalizadores negativos:", "Negative catalysts:")}</p>
                  <BulletList items={bear.negative_catalysts} />
                </>
              )}
              {bear.valuation_concern && <p className="mt-1 text-text-tertiary">{t("¿Ya en el precio?", "Already priced in?")}: {bear.valuation_concern}</p>}
              {bear.historical_precedent && <p className="text-text-tertiary">{t("Precedente", "Precedent")}: {bear.historical_precedent}</p>}
            </>
          ) : (
            <Empty locale={locale} />
          )}
        </Section>

        <Section title={t("Veredicto (Judge)", "Verdict (Judge)")}>
          {judge ? (
            <>
              <p className="num">
                {t("Convicción neta", "Net conviction")} {judge.net_conviction.toFixed(2)} · {t("confianza", "confidence")}{" "}
                {judge.confidence_in_conviction.toFixed(0)}%
              </p>
              {judge.overriding_concern && <p className="text-text-tertiary">{t("Preocupación dominante", "Overriding concern")}: {judge.overriding_concern}</p>}
            </>
          ) : (
            <Empty locale={locale} />
          )}
        </Section>

        <Section title={t("¿Era una sorpresa?", "Was it a surprise?")}>
          {novelty ? (
            <>
              <p className="num">{t("Novedad", "Novelty")}: {novelty.score}/100</p>
              {novelty.components_used?.length > 0 && <p className="text-text-tertiary">{t("Calculada con", "Computed with")}: {novelty.components_used.join(", ")}</p>}
              {novelty.components_unavailable?.length > 0 && (
                <p className="text-amber-700 dark:text-amber-400">
                  {t("Sin datos de", "No data for")}: {novelty.components_unavailable.join(", ")}.{" "}
                  {t("No penaliza: el peso se reparte entre el resto.", "No penalty: its weight is spread across the rest.")}
                </p>
              )}
            </>
          ) : (
            <Empty locale={locale} />
          )}
        </Section>

        <Section title={t("Casos parecidos en el pasado", "Similar past cases")}>
          {impact ? (
            <>
              <p className="num font-medium text-foreground">
                {detail.n_historical_analogues ?? "?"} {t("casos · confianza", "cases · confidence")} {impact.confidence.toFixed(0)}/100
              </p>
              <p>{planText(impact.expected_magnitude, locale)}</p>
              <p className="mt-1 text-xs text-text-tertiary">{t("Probabilidad de que se mueva al menos:", "Probability of moving at least:")}</p>
              <HBars
                max={100}
                labelWidth="3rem"
                rows={[
                  { label: "±5%", value: impact.probability_5pct_move },
                  { label: "±10%", value: impact.probability_10pct_move },
                  { label: "±20%", value: impact.probability_20pct_move },
                ]}
                format={(v) => `${v.toFixed(0)}%`}
              />
              {(detail.n_historical_analogues ?? 0) < 5 && (
                <p className="text-amber-700 dark:text-amber-400">
                  {t(
                    "Pocos casos: la estimación se acerca a la media de su tipo de evento.",
                    "Few cases: the estimate is pulled towards the average for its event type.",
                  )}
                </p>
              )}
            </>
          ) : (
            <Empty locale={locale} />
          )}
        </Section>

        <Section title={t("Valor esperado", "Expected value")}>
          {ev ? (
            <>
              <p className="text-text-tertiary">{ev.reasoning}</p>
              <p className="num mt-1">{ev.threshold_balanced}</p>
            </>
          ) : (
            <Empty locale={locale} />
          )}
        </Section>
      </div>

      <div id="versiones" className="mt-4 scroll-mt-4 border-t border-border-subtle pt-3">
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-foreground">{t("Las tres estrategias", "The three strategies")}</h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {VERSION_ORDER.map((strategy) => {
            const d = abstention?.[strategy];
            const decision = decisionOf(detail, strategy);
            const isRec = strategy === recommended;
            return (
              <div key={strategy} className={`border p-2 ${isRec ? "border-accent-500" : "border-border-subtle"} bg-surface`}>
                <p className="mb-1 flex items-center justify-between font-medium text-foreground">
                  <span>
                    {versionLabel(strategy, locale)}
                    {isRec && <span className="ml-1.5 text-xs font-normal text-accent-700 dark:text-accent-400">{t("recomendada", "recommended")}</span>}
                  </span>
                  <DirectionBadge value={decision} />
                </p>
                {decision !== "NO_TRADE" ? (
                  <p className="num text-xs text-text-tertiary">
                    {t("Pasa los 7 filtros · confianza", "Passes all 7 filters · confidence")} {(d?.confidence ?? detail.confidence).toFixed(0)}%
                  </p>
                ) : d?.reason_if_no_trade ? (
                  <p className="text-xs text-text-tertiary">{d.reason_if_no_trade}</p>
                ) : null}
              </div>
            );
          })}
        </div>
        {(detail.entry_date || detail.exit_date) && (
          <p className="num mt-2 text-xs text-text-tertiary">
            {t("En el histórico", "In the backtest")}: {detail.entry_date && `${t("entrada", "entry")} ${detail.entry_date}`}
            {detail.exit_date && ` · ${t("salida", "exit")} ${detail.exit_date} (${exitReasonLabel(detail.exit_reason, locale)})`}
            {detail.pnl_pct !== null && ` · ${t("resultado", "result")} ${detail.pnl_pct.toFixed(2)}%`}
          </p>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-tertiary">
        {detail.source_url && (
          <a href={detail.source_url} target="_blank" rel="noopener noreferrer" className="text-accent-700 underline decoration-dotted hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300">
            {t("Filing original ↗", "Original filing ↗")}
          </a>
        )}
        <span>
          {t("Publicado", "Filed")}: {formatDateTime(detail.filed_at, locale)}
        </span>
        <span>
          {t("Analizado", "Analysed")}: {formatDateTime(detail.analyzed_at, locale)}
        </span>
        <span>
          {t("Modelos", "Models")}: {detail.model_version_bull_bear} ({t("debate", "debate")}) · {detail.model_version_judge} ({t("veredicto", "verdict")})
        </span>
      </div>
    </div>
  );
}
