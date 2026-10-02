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
import { VERSION_LABELS, VERSION_ORDER, eventClassLabel } from "@/lib/labels";

export const dynamic = "force-dynamic";
export const metadata = { title: "¿Funciona?" };


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

const SCENARIO_LABELS: Record<string, string> = {
  baseline: "Situación normal",
  "commission_plus_0.1pct": "Con más comisiones (+0.1%)",
  "spread_plus_0.2pct": "Con más diferencia compra/venta (+0.2%)",
  latency_d_plus_2: "Entrando un día más tarde",
  "confidence_minus_20pct": "Si el modelo fuera menos seguro (-20%)",
  high_vix_regime: "En mercado muy volátil",
  low_vix_regime: "En mercado tranquilo",
};
const SCENARIO_ORDER = Object.keys(SCENARIO_LABELS);

const OVER_UNDER_CONFIDENCE_THRESHOLD_PP = 5.0;

function interpretCalibration(diag: CalibrationDiagnostics): { label: string; adjustment: string } {
  if (diag.buckets.length === 0 || diag.n < 5) {
    return { label: "Muestra insuficiente para interpretar", adjustment: "Esperar más operaciones antes de sacar conclusiones." };
  }
  const totalN = diag.buckets.reduce((s, b) => s + b.n, 0);
  const meanConfidence = diag.buckets.reduce((s, b) => s + b.mean_confidence * b.n, 0) / totalN;
  const meanHitRatePct = (diag.buckets.reduce((s, b) => s + b.hit_rate * b.n, 0) / totalN) * 100;
  const diff = meanConfidence - meanHitRatePct;

  if (diff > OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: `Exceso de confianza — dice estar ${meanConfidence.toFixed(0)}% seguro pero acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
      adjustment: `Conviene desconfiar un poco de las confianzas altas que reporta (~${diff.toFixed(0)} puntos de más).`,
    };
  }
  if (diff < -OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: `Confianza baja de más — dice ${meanConfidence.toFixed(0)}% pero acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
      adjustment: "Acierta más de lo que dice — no hace falta corregir a la baja.",
    };
  }
  return {
    label: `Bien calibrado — dice ${meanConfidence.toFixed(0)}% y acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
    adjustment: "No hace falta ningún ajuste — cuando dice que está seguro, suele tener razón.",
  };
}

function DecisionCard({ title, decision }: { title: string; decision: VersionDecision }) {
  return (
    <div className={` border p-4 ${OPTION_STYLES[decision.option]}`}>
      <p className="mb-1 text-sm text-text-secondary">{title}</p>
      <p className={`mb-2 text-lg font-semibold ${OPTION_TEXT_STYLES[decision.option]}`}>{decision.label}</p>
      <p className="mb-2 text-sm text-foreground">{decision.recommendation}</p>
      <ul className="list-inside list-disc space-y-0.5 text-xs text-text-secondary">
        {decision.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </div>
  );
}

function EventStudyRow({ eventClass, stats }: { eventClass: string; stats: EventStudyClassResult }) {
  return (
    <tr className="border-b border-border-subtle">
      <td className="py-2 pl-3 pr-4 text-foreground">{eventClassLabel(eventClass)}</td>
      <td className="num py-2 pr-4 text-right text-foreground">{stats.n}</td>
      <td className="num py-2 pr-4 text-right text-foreground">{stats.median_return_pct !== null ? formatPct(stats.median_return_pct) : "—"}</td>
      <td className="num py-2 pr-4 text-right text-foreground">{stats.p_value !== null ? stats.p_value.toFixed(4) : "—"}</td>
      <td className="py-2 pr-4 text-center">
        {stats.significant === true && <span className="text-emerald-700 dark:text-emerald-400">Sí</span>}
        {stats.significant === false && <span className="text-rose-700 dark:text-rose-400">No</span>}
        {stats.significant === null && <span className="text-text-tertiary">Aún no se sabe</span>}
      </td>
      <td className="py-2 text-text-secondary">{stats.conclusion}</td>
    </tr>
  );
}

export default async function FuncionaPage() {
  if (!isDatabaseConfigured()) return <NotConfigured active="/funciona" title="¿Funciona?" />;

  const [portfolioTag, validationTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestValidationRunBatchTag()]);

  if (!portfolioTag) return <NoDataYet active="/funciona" title="¿Funciona?" what="Todavía no hay ningún backtest de cartera." />;

  const [report, validationReport] = await Promise.all([
    getPortfolioReport(portfolioTag),
    validationTag ? getValidationReport(validationTag) : Promise.resolve(null),
  ]);

  if (!report) {
    return (
      <StatePage active="/funciona" title="¿Funciona?">
        <StateBox title="No se pudo leer el último informe de cartera.">
          Corrida <code className="font-mono">{portfolioTag}</code>. Revisa los logs de la última ejecución del pipeline.
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
    { metric: "Resultado total", ...cell((r) => formatFracAsPct(r.equity_metrics.total_return, 1)) },
    { metric: "Sharpe (retorno vs riesgo)", ...cell((r) => formatNum(r.equity_metrics.sharpe_ratio)) },
    { metric: "Acierto", ...cell((r) => (r.trade_metrics.win_rate !== null ? `${(r.trade_metrics.win_rate * 100).toFixed(1)}%` : "—")) },
    { metric: "Peor caída", ...cell((r) => formatDrawdown(r.equity_metrics.max_drawdown)) },
    { metric: "Operaciones/año", ...cell(tradesPerYear) },
    { metric: "Pensada para", conservative: "Evitar riesgo", aggressive: "Buscar más ganancia", balanced: "Término medio" },
  ];

  const eventClasses = validationReport ? Object.keys(validationReport.event_study).sort() : [];

  return (
    <main className="mx-auto max-w-7xl px-4 py-4 sm:px-6">
      <Nav active="/funciona" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">¿Funciona?</h1>
        <p className="mt-1 text-sm text-text-secondary">Todo lo que responde si te puedes fiar del sistema, y por qué.</p>
      </header>

      {validationReport && (
        <div className="mb-6">
          <SampleBadge sample={validationReport.sample} warning={validationReport.oos_warning} />
        </div>
      )}

      {/* Veredicto + decisión por versión */}
      {validationReport && (
        <section className="mb-10">
          <div className={`mb-4  border-2 p-5 ${OPTION_STYLES[validationReport.best_decision.option]}`}>
            <p className="mb-1 text-xs uppercase tracking-wide text-text-secondary">
              Veredicto global · versión recomendada: {VERSION_LABELS[validationReport.best_version]}
            </p>
            <p className={`mb-2 text-2xl font-semibold ${OPTION_TEXT_STYLES[validationReport.best_decision.option]}`}>{validationReport.best_decision.label}</p>
            <p className="text-sm text-foreground">{validationReport.best_decision.recommendation}</p>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {VERSION_ORDER.filter((ver) => validationReport.decisions[ver]).map((ver) => (
              <DecisionCard key={ver} title={VERSION_LABELS[ver]} decision={validationReport.decisions[ver]} />
            ))}
          </div>
        </section>
      )}

      {/* Event study */}
      {validationReport && (
        <section className="mb-10">
          <h2 className="mb-1 text-base font-semibold text-foreground">¿El tipo de evento mueve el precio de verdad?</h2>
          <p className="mb-3 text-xs text-text-secondary">
            Sobre TODOS los eventos detectados, no solo los que se operaron. “Sí” significa que el movimiento no parece casualidad; “aún
            no se sabe” significa que hacen falta más casos para estar seguros — no que no haya efecto.
          </p>
          {eventClasses.length === 0 ? (
            <p className="text-sm italic text-text-tertiary">Sin datos todavía.</p>
          ) : (
            <div className="overflow-x-auto border border-border-subtle">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
                    <th className="py-2 pl-3 pr-4 font-medium">Tipo de evento</th>
                    <th className="py-2 pr-4 text-right font-medium">Casos</th>
                    <th className="py-2 pr-4 text-right font-medium">Movimiento típico</th>
                    <th className="py-2 pr-4 text-right font-medium">p-value</th>
                    <th className="py-2 pr-4 text-center font-medium">¿Es real?</th>
                    <th className="py-2 pr-3 font-medium">Explicación</th>
                  </tr>
                </thead>
                <tbody>
                  {eventClasses.map((ec) => (
                    <EventStudyRow key={ec} eventClass={ec} stats={validationReport.event_study[ec]} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Calibración */}
      <section className="mb-10">
        <h2 className="mb-1 text-base font-semibold text-foreground">¿El sistema sabe cuándo está seguro?</h2>
        <p className="mb-3 text-xs text-text-secondary">Cuando dice “80% de confianza”, ¿acierta de verdad el 80% de las veces?</p>
        <div className="flex flex-col gap-4 md:flex-row">
          {VERSION_ORDER.filter((ver) => report.versions[ver]).map((ver) => {
            const diag = report.versions[ver].confidence_calibration;
            const { label, adjustment } = interpretCalibration(diag);
            return (
              <div key={ver} className="min-w-[300px] flex-1 border border-border-subtle bg-surface p-4">
                <h3 className="mb-3 text-base font-semibold text-foreground">{VERSION_LABELS[ver]}</h3>
                <CalibrationCurve buckets={diag.buckets} />
                <p className="mb-3 mt-1 text-[10px] text-text-tertiary">Línea de referencia = calibración perfecta · tamaño del punto = nº de casos</p>
                <p className="mb-1 text-sm font-medium text-foreground">{label}</p>
                <p className="text-xs text-text-secondary">{adjustment}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* Sensibilidad */}
      {validationReport && (
        <section className="mb-10">
          <h2 className="mb-1 text-base font-semibold text-foreground">¿Se rompe el resultado si las condiciones empeoran?</h2>
          <p className="mb-3 text-xs text-text-secondary">
            Un resultado que se mantiene positivo en todos los escenarios es más de fiar que uno que solo funciona en el mejor de los
            casos.
          </p>
          <div className="overflow-x-auto border border-border-subtle">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
                  <th className="py-2 pl-3 pr-4 font-medium">Escenario</th>
                  {VERSION_ORDER.map((ver) => (
                    <th key={ver} className="py-2 pr-4 text-right font-medium last:pr-3">
                      {VERSION_LABELS[ver]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {SCENARIO_ORDER.map((key) => (
                  <tr key={key} className="border-b border-border-subtle">
                    <td className="py-2 pl-3 pr-4 text-foreground">{SCENARIO_LABELS[key]}</td>
                    {VERSION_ORDER.map((ver) => {
                      const ret = validationReport.sensitivity.scenarios[ver]?.[key]?.total_return;
                      return (
                        <td key={ver} className="py-2 pr-4 text-right last:pr-3">
                          {ret !== null && ret !== undefined ? <SignedPct value={ret * 100} /> : <span className="text-text-tertiary">—</span>}
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
        <h2 className="mb-3 text-base font-semibold text-foreground">¿Qué versión conviene?</h2>
        <ComparisonTable rows={comparisonRows} />
      </section>
    </main>
  );
}
