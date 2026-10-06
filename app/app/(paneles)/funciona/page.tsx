import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport, getLatestValidationRunBatchTag, getValidationReport } from "@/lib/data";
import type { CalibrationDiagnostics, EventStudyClassResult, PortfolioVersionReport, StrategyVersion, VersionDecision } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { CalibrationCurve } from "@/components/CalibrationCurve";
import { ComparisonTable, type ComparisonRow } from "@/components/ComparisonTable";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { NoDataYet, NotConfigured, StateBox, StatePage } from "@/components/ui/PageState";
import { SignedPct } from "@/components/ui/DirectionBadge";
import { formatPct, formatNum, formatFracAsPct, formatDrawdown } from "@/lib/format";
import { VERSION_ORDER, eventClassLabel, reliability, versionLabel } from "@/lib/labels";
import { getT } from "@/lib/locale";
import { makeT, type Locale, type T } from "@/lib/i18n";
import { validationText } from "@/lib/validationText";
import { ReliabilityScale } from "@/components/viz/ReliabilityScale";

export const dynamic = "force-dynamic";
export async function generateMetadata() {
  const { t } = await getT();
  return { title: t("Fiabilidad", "Reliability") };
}


// funciona/page.tsx — fusiona lo que antes eran tres pestañas separadas
// (Calibration, Validation, Comparison): las tres responden la misma
// pregunta de fondo, "¿me puedo fiar de esto?", solo que desde ángulos
// distintos (¿el evento mueve el precio de verdad?, ¿el modelo sabe cuándo
// está seguro?, ¿qué versión conviene más?) — tiene más sentido como tres
// secciones de una sola pantalla que como pestañas sueltas y sin conexión
// visible entre ellas.
//
// Nota de color: A/B/C es el veredicto GREENLIGHT/YELLOWLIGHT/REDLIGHT del
// propio motor de validación (pipeline/validation/report.py) — un semáforo
// de confianza en el sistema, no P&L ni dirección — así que, igual que el
// recuadro de recomendación de Cartera, usa el mismo verde/ámbar/rojo que el
// resto de veredictos de "¿te puedes fiar de esto?" en la app (nunca para UI
// genérica).

const OPTION_STYLES: Record<string, string> = {
  A: "border-emerald-300 bg-emerald-50 dark:border-emerald-800/60 dark:bg-emerald-500/10",
  B: "border-amber-300 bg-amber-50 dark:border-amber-800/60 dark:bg-amber-500/10",
  C: "border-rose-300 bg-rose-50 dark:border-rose-800/60 dark:bg-rose-500/10",
};
const OPTION_TEXT_STYLES: Record<string, string> = {
  A: "text-emerald-800 dark:text-emerald-400",
  B: "text-amber-800 dark:text-amber-400",
  C: "text-rose-800 dark:text-rose-400",
};

const scenarioLabels = (t: T): Record<string, string> => ({
  baseline: t("Situación normal", "Normal conditions"),
  "commission_plus_0.1pct": t("Con más comisiones (+0.1%)", "Higher fees (+0.1%)"),
  "spread_plus_0.2pct": t("Con más diferencia compra/venta (+0.2%)", "Wider bid/ask spread (+0.2%)"),
  latency_d_plus_2: t("Entrando un día más tarde", "Entering one day later"),
  "confidence_minus_20pct": t("Si el modelo fuera menos seguro (-20%)", "If the model were less confident (-20%)"),
  high_vix_regime: t("En mercado muy volátil", "In a very volatile market"),
  low_vix_regime: t("En mercado tranquilo", "In a calm market"),
});
const SCENARIO_ORDER = Object.keys(scenarioLabels(makeT("es")));

// El histórico decide con la regla sin IA, con la confianza fija (BUGS_REPORT.md H-06).
const NO_APLICA_CONFIANZA = (t: T) =>
  t(
    "El histórico decide con la regla sin IA, que no usa la confianza de la IA: no hay confianza que medir.",
    "The history is decided by the rule without AI, which does not use the AI's confidence: there is no confidence to measure.",
  );

const OVER_UNDER_CONFIDENCE_THRESHOLD_PP = 5.0;

function interpretCalibration(diag: CalibrationDiagnostics, t: T): { label: string; adjustment: string } {
  if (diag.buckets.length === 0 || diag.n < 5) {
    return {
      label: t("Muestra insuficiente para interpretar", "Sample too small to interpret"),
      adjustment: t("Esperar más operaciones antes de sacar conclusiones.", "Wait for more trades before drawing conclusions."),
    };
  }
  const totalN = diag.buckets.reduce((s, b) => s + b.n, 0);
  const meanConfidence = diag.buckets.reduce((s, b) => s + b.mean_confidence * b.n, 0) / totalN;
  const meanHitRatePct = (diag.buckets.reduce((s, b) => s + b.hit_rate * b.n, 0) / totalN) * 100;
  const diff = meanConfidence - meanHitRatePct;

  if (diff > OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: t(
        `Exceso de confianza — dice estar ${meanConfidence.toFixed(0)}% seguro pero acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
        `Overconfident — says it is ${meanConfidence.toFixed(0)}% sure but is right ${meanHitRatePct.toFixed(0)}% of the time`,
      ),
      adjustment: t(
        `Conviene desconfiar un poco de las confianzas altas que reporta (~${diff.toFixed(0)} puntos de más).`,
        `Its high confidence figures deserve some scepticism (~${diff.toFixed(0)} points too high).`,
      ),
    };
  }
  if (diff < -OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: t(
        `Confianza baja de más — dice ${meanConfidence.toFixed(0)}% pero acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
        `Underconfident — says ${meanConfidence.toFixed(0)}% but is right ${meanHitRatePct.toFixed(0)}% of the time`,
      ),
      adjustment: t("Acierta más de lo que dice — no hace falta corregir a la baja.", "It is right more often than it says — no need to adjust downwards."),
    };
  }
  return {
    label: t(
      `Bien calibrado — dice ${meanConfidence.toFixed(0)}% y acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
      `Well calibrated — says ${meanConfidence.toFixed(0)}% and is right ${meanHitRatePct.toFixed(0)}% of the time`,
    ),
    adjustment: t("No hace falta ningún ajuste — cuando dice que está seguro, suele tener razón.", "No adjustment needed — when it says it is confident, it is usually right."),
  };
}

function DecisionCard({ title, decision, locale }: { title: string; decision: VersionDecision; locale: Locale }) {
  const t = makeT(locale);
  const RELIABILITY = reliability(locale);
  return (
    <div className={`border p-4 ${OPTION_STYLES[decision.option]}`}>
      <p className="mb-1 text-sm text-text-secondary">{title}</p>
      <p className={`mb-2 text-lg font-semibold ${OPTION_TEXT_STYLES[decision.option]}`}>{RELIABILITY[decision.option].title}</p>
      <p className="mb-2 text-xs text-text-tertiary">{t("Criterios evaluados:", "Criteria checked:")}</p>
      <ul className="list-inside list-disc space-y-0.5 text-xs text-text-secondary">
        {decision.reasons.map((r, i) => (
          <li key={i}>{validationText(r, locale)}</li>
        ))}
      </ul>
    </div>
  );
}

function EventStudyRow({ eventClass, stats, locale }: { eventClass: string; stats: EventStudyClassResult; locale: Locale }) {
  const t = makeT(locale);
  return (
    <tr className="border-b border-border-subtle">
      <td className="py-2 pl-3 pr-4 text-foreground">{eventClassLabel(eventClass, locale)}</td>
      <td className="num py-2 pr-4 text-right text-foreground">{stats.n}</td>
      <td className="num py-2 pr-4 text-right text-foreground">{stats.median_return_pct !== null ? formatPct(stats.median_return_pct) : "—"}</td>
      <td className="num py-2 pr-4 text-right text-foreground">{stats.p_value !== null ? stats.p_value.toFixed(4) : "—"}</td>
      <td className="py-2 pr-4 text-center">
        {stats.significant === true && <span className="text-emerald-700 dark:text-emerald-400">{t("Sí", "Yes")}</span>}
        {stats.significant === false && <span className="text-rose-700 dark:text-rose-400">No</span>}
        {stats.significant === null && <span className="text-text-tertiary">{t("Aún no se sabe", "Not known yet")}</span>}
      </td>
      <td className="py-2 text-text-secondary">{validationText(stats.conclusion, locale)}</td>
    </tr>
  );
}

export default async function FuncionaPage() {
  const { locale, t } = await getT();
  const title = t("Fiabilidad", "Reliability");
  const RELIABILITY = reliability(locale);
  const SCENARIO_LABELS = scenarioLabels(t);
  if (!isDatabaseConfigured()) return <NotConfigured active="/funciona" title={title} />;

  const [portfolioTag, validationTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestValidationRunBatchTag()]);

  if (!portfolioTag)
    return <NoDataYet active="/funciona" title={title} what={t("Todavía no hay resultados para evaluar el sistema.", "There are no results to assess the system yet.")} />;

  const [report, validationReport] = await Promise.all([
    getPortfolioReport(portfolioTag),
    validationTag ? getValidationReport(validationTag) : Promise.resolve(null),
  ]);

  if (!report) {
    return (
      <StatePage active="/funciona" title={title}>
        <StateBox title={t("Estos resultados no están disponibles ahora mismo.", "These results are not available right now.")}>
          {t("Inténtalo de nuevo en unos minutos.", "Please try again in a few minutes.")}
        </StateBox>
      </StatePage>
    );
  }

  // Antes: `(x ?? 0)` en cada celda — una métrica sin calcular (null, p. ej.
  // con muestra insuficiente) se pintaba como "+0.0%", indistinguible de un
  // resultado real de cero. Y si el reporte no traía una de las versiones,
  // `v.CONSERVATIVE.equity_metrics` reventaba la página entera. Ahora un
  // dato ausente se ve como ausente ("—").
  const v: Partial<Record<StrategyVersion, PortfolioVersionReport>> = report.versions;
  const cell = (pick: (r: PortfolioVersionReport) => string) => {
    const out = {} as Record<"conservative" | "aggressive" | "balanced", string>;
    for (const ver of VERSION_ORDER) {
      const r = v[ver];
      out[ver.toLowerCase() as "conservative" | "aggressive" | "balanced"] = r ? pick(r) : "—";
    }
    return out;
  };
  const tradesPerYear = (r: PortfolioVersionReport) => {
    const nYears = r.equity_curve.length > 0 ? r.equity_curve.length / 252 : null;
    return nYears && nYears > 0 ? (r.trade_metrics.total_trades / nYears).toFixed(0) : "—";
  };

  const comparisonRows: ComparisonRow[] = [
    { metric: t("Resultado total", "Total return"), ...cell((r) => formatFracAsPct(r.equity_metrics.total_return, 1)) },
    { metric: t("Sharpe (retorno vs riesgo)", "Sharpe (return vs risk)"), ...cell((r) => formatNum(r.equity_metrics.sharpe_ratio)) },
    { metric: t("Acierto", "Win rate"), ...cell((r) => (r.trade_metrics.win_rate !== null ? `${(r.trade_metrics.win_rate * 100).toFixed(1)}%` : "—")) },
    { metric: t("Peor caída", "Max drawdown"), ...cell((r) => formatDrawdown(r.equity_metrics.max_drawdown)) },
    { metric: t("Operaciones/año", "Trades/year"), ...cell(tradesPerYear) },
    {
      metric: t("Pensada para", "Designed for"),
      conservative: t("Evitar riesgo", "Avoiding risk"),
      aggressive: t("Buscar más ganancia", "Seeking more gain"),
      balanced: t("Término medio", "Middle ground"),
    },
  ];

  const eventClasses = validationReport ? Object.keys(validationReport.event_study).sort() : [];

  return (
    <main className="mx-auto max-w-7xl px-4 py-4 sm:px-6">
      <Nav active="/funciona" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        <p className="mt-1 text-sm text-text-secondary">
          {t(
            "Cómo de fiables son las señales: si los eventos mueven el precio de verdad, si la confianza declarada se cumple y cómo aguantan los resultados en condiciones peores.",
            "How reliable the signals are: whether events really move the price, whether the stated confidence holds up, and how results stand up in worse conditions.",
          )}
        </p>
      </header>

      {validationReport && (
        <div className="mb-6">
          <SampleBadge sample={validationReport.sample} warning={validationReport.oos_warning} />
        </div>
      )}

      {/* Veredicto + decisión por versión */}
      {validationReport && (
        <section className="mb-10">
          <div className={`mb-4 border-2 p-5 ${OPTION_STYLES[validationReport.best_decision.option]}`}>
            <p className="mb-1 text-xs uppercase tracking-wide text-text-secondary">
              {t("Calificación global · estrategia recomendada", "Overall rating · recommended strategy")}: {versionLabel(validationReport.best_version, locale)}
            </p>
            <p className={`mb-2 text-2xl font-semibold ${OPTION_TEXT_STYLES[validationReport.best_decision.option]}`}>{RELIABILITY[validationReport.best_decision.option].title}</p>
            <p className="text-sm text-foreground">{RELIABILITY[validationReport.best_decision.option].summary}</p>
            <div className="mt-3">
              <ReliabilityScale
                option={validationReport.best_decision.option}
                labels={{ A: t("Alta", "High"), B: t("Media", "Medium"), C: t("Baja", "Low") }}
                ariaLabel={`${t("Fiabilidad", "Reliability")}: ${RELIABILITY[validationReport.best_decision.option].title}`}
              />
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {VERSION_ORDER.filter((ver) => validationReport.decisions[ver]).map((ver) => (
              <DecisionCard key={ver} title={versionLabel(ver, locale)} decision={validationReport.decisions[ver]} locale={locale} />
            ))}
          </div>
        </section>
      )}

      {/* Event study */}
      {validationReport && (
        <section className="mb-10">
          <h2 className="mb-1 text-base font-semibold text-foreground">{t("¿El tipo de evento mueve el precio de verdad?", "Does the event type really move the price?")}</h2>
          <p className="mb-3 text-xs text-text-secondary">
            {t(
              "Sobre TODOS los eventos detectados, no solo los que se operaron. “Sí” significa que el movimiento no parece casualidad; “aún no se sabe” significa que hacen falta más casos para estar seguros — no que no haya efecto.",
              "Across ALL detected events, not only the ones traded. “Yes” means the move does not look like chance; “not known yet” means more cases are needed to be sure — not that there is no effect.",
            )}
          </p>
          {eventClasses.length === 0 ? (
            <p className="text-sm italic text-text-tertiary">{t("Sin datos todavía.", "No data yet.")}</p>
          ) : (
            <div className="overflow-x-auto border border-border-subtle">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
                    <th className="py-2 pl-3 pr-4 font-medium">{t("Tipo de evento", "Event type")}</th>
                    <th className="py-2 pr-4 text-right font-medium">{t("Casos", "Cases")}</th>
                    <th className="py-2 pr-4 text-right font-medium">{t("Movimiento típico", "Typical move")}</th>
                    <th className="py-2 pr-4 text-right font-medium" title={t("Contraste agrupado por mes de D0, sobre CAR winsorizados", "Test clustered by month of D0, on winsorized CARs")}>
                      {t("p-valor (por mes)", "p-value (by month)")}
                    </th>
                    <th className="py-2 pr-4 text-center font-medium">{t("¿Es real?", "Is it real?")}</th>
                    <th className="py-2 pr-3 font-medium">{t("Explicación", "Explanation")}</th>
                  </tr>
                </thead>
                <tbody>
                  {eventClasses.map((ec) => (
                    <EventStudyRow key={ec} eventClass={ec} stats={validationReport.event_study[ec]} locale={locale} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Calibración */}
      <section className="mb-10">
        <h2 className="mb-1 text-base font-semibold text-foreground">{t("¿El sistema sabe cuándo está seguro?", "Does the system know when it is confident?")}</h2>
        <p className="mb-3 text-xs text-text-secondary">
          {t("Cuando dice “80% de confianza”, ¿acierta de verdad el 80% de las veces?", "When it says “80% confidence”, is it really right 80% of the time?")}
        </p>
        {VERSION_ORDER.some((ver) => report.versions[ver]?.confidence_calibration.no_aplica) ? (
          <p className="border border-border-subtle bg-surface px-3 py-2 text-xs text-text-secondary">
            <span className="mr-1 font-semibold">{t("No aplica.", "Not applicable.")}</span>
            {NO_APLICA_CONFIANZA(t)}
          </p>
        ) : (
        <div className="flex flex-col gap-4 md:flex-row">
          {VERSION_ORDER.filter((ver) => report.versions[ver]).map((ver) => {
            const diag = report.versions[ver].confidence_calibration;
            const { label, adjustment } = interpretCalibration(diag, t);
            return (
              <div key={ver} className="min-w-[300px] flex-1 border border-border-subtle bg-surface p-4">
                <h3 className="mb-3 text-base font-semibold text-foreground">{versionLabel(ver, locale)}</h3>
                <CalibrationCurve buckets={diag.buckets} />
                <p className="mb-3 mt-1 text-[10px] text-text-tertiary">
                  {t("Línea de referencia = calibración perfecta · tamaño del punto = nº de casos", "Reference line = perfect calibration · dot size = number of cases")}
                </p>
                <p className="mb-1 text-sm font-medium text-foreground">{label}</p>
                <p className="text-xs text-text-secondary">{adjustment}</p>
              </div>
            );
          })}
        </div>
        )}
      </section>

      {/* Sensibilidad */}
      {validationReport && (
        <section className="mb-10">
          <h2 className="mb-1 text-base font-semibold text-foreground">{t("¿Se rompe el resultado si las condiciones empeoran?", "Does the result break if conditions get worse?")}</h2>
          <p className="mb-3 text-xs text-text-secondary">
            {t(
              "Un resultado que se mantiene positivo en todos los escenarios es más de fiar que uno que solo funciona en el mejor de los casos.",
              "A result that stays positive in every scenario is more trustworthy than one that only works in the best case.",
            )}
          </p>
          <div className="overflow-x-auto border border-border-subtle">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
                  <th className="py-2 pl-3 pr-4 font-medium">{t("Escenario", "Scenario")}</th>
                  {VERSION_ORDER.map((ver) => (
                    <th key={ver} className="py-2 pr-4 text-right font-medium last:pr-3">
                      {versionLabel(ver, locale)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {SCENARIO_ORDER.map((key) => (
                  <tr key={key} className="border-b border-border-subtle">
                    <td className="py-2 pl-3 pr-4 text-foreground">{SCENARIO_LABELS[key]}</td>
                    {VERSION_ORDER.map((ver) => {
                      const scenario = validationReport.sensitivity.scenarios[ver]?.[key];
                      const ret = scenario?.total_return;
                      return (
                        <td key={ver} className="py-2 pr-4 text-right last:pr-3">
                          {scenario?.no_aplica ? (
                            <span className="text-text-tertiary" title={NO_APLICA_CONFIANZA(t)}>
                              {t("No aplica", "N/A")}
                            </span>
                          ) : ret !== null && ret !== undefined ? (
                            <SignedPct value={ret * 100} />
                          ) : (
                            <span className="text-text-tertiary">—</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Comparación de versiones */}
      <section>
        <h2 className="mb-3 text-base font-semibold text-foreground">{t("¿Qué estrategia conviene?", "Which strategy suits you?")}</h2>
        <ComparisonTable rows={comparisonRows} locale={locale} />
      </section>
    </main>
  );
}
