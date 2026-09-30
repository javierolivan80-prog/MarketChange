import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport, getLatestPaperTradingRunBatchTag, getPaperTradingReport } from "@/lib/queries";
import { PortfolioVersionCard } from "@/components/PortfolioVersionCard";
import { Nav } from "@/components/Nav";
import { ExportPdfButton } from "@/components/ExportPdfButton";
import { DailyPnLChart } from "@/components/DailyPnLChart";
import { ConfidenceBucketBars } from "@/components/ConfidenceBucketBars";
import { Callout } from "@/components/ui/Callout";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { formatPct, formatUsd } from "@/lib/format";

export const dynamic = "force-dynamic";

// cartera/page.tsx — fusiona lo que antes eran dos pestañas separadas
// ("Backtest Analysis" y "Signals This Week"): ambas responden la misma
// pregunta de fondo ("¿cómo le va a la cartera?"), solo que una mira el
// histórico completo y la otra la semana en curso — tiene más sentido
// como dos secciones de una misma pantalla que como dos pestañas sueltas.
const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const VERSION_LABELS: Record<string, string> = { CONSERVATIVE: "Conservador", AGGRESSIVE: "Agresivo", BALANCED: "Equilibrado" };

export default async function CarteraPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/cartera" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">Cartera</h1>
        <p className="text-sm text-text-secondary">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const [portfolioTag, paperTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestPaperTradingRunBatchTag()]);

  if (!portfolioTag) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/cartera" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">Cartera</h1>
        <div className="rounded border border-border-subtle p-4">
          <p className="mb-2 font-medium text-foreground">Todavía no hay ningún backtest de cartera registrado.</p>
          <p className="text-sm text-text-secondary">Espera al pipeline nocturno para generar el primero.</p>
        </div>
      </main>
    );
  }

  const [report, paperReport] = await Promise.all([getPortfolioReport(portfolioTag), paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null)]);

  if (!report) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/cartera" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">Cartera</h1>
        <p className="text-sm text-text-secondary">
          No se pudo leer el reporte para <code className="font-mono">{portfolioTag}</code>.
        </p>
      </main>
    );
  }

  const { recommendation, bias_report } = report;
  const verdictIsYes = recommendation.verdict.startsWith("SÍ");

  return (
    <main className="mx-auto max-w-7xl p-6">
      <Nav active="/cartera" />
      <header className="mb-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Cartera</h1>
          <ExportPdfButton report={report} />
        </div>
        <p className="mt-1 text-sm text-text-secondary">Cómo le ha ido al sistema si se hubiera operado — todo simulado, nunca con dinero real.</p>
      </header>

      {/* Sección 1: histórico */}
      <section className="mb-10">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold text-foreground">Resultado histórico completo</h2>
        </div>

        <SampleBadge sample={report.sample} warning={report.oos_warning} />
        <div className="mb-4" />

        {verdictIsYes ? (
          <Callout kind="positive" className="mb-4">
            <p className="font-medium normal-case">¿Invertir dinero real? {recommendation.verdict}</p>
            <ul className="mt-1 list-inside list-disc font-normal normal-case">
              {recommendation.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </Callout>
        ) : (
          <div className="mb-4 rounded border border-border-subtle bg-surface-raised p-4">
            <p className="mb-2 font-medium text-foreground">¿Invertir dinero real? {recommendation.verdict}</p>
            <ul className="list-inside list-disc space-y-0.5 text-sm text-text-secondary">
              {recommendation.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mb-4 rounded border border-border-subtle p-4 text-sm">
          <p className="mb-1 font-medium text-foreground">Sesgos de datos a tener en cuenta</p>
          <p className="num text-text-secondary">
            {bias_report.n_delisted}/{bias_report.n_total_tickers} acciones que desaparecieron de bolsa ({formatPct(bias_report.survivorship_bias_pct, 1)}{" "}
            posible sesgo) · {bias_report.n_price_gaps}/{bias_report.n_price_rows} filas de precio con huecos ({formatPct(bias_report.data_gap_pct, 1)})
          </p>
        </div>

        <p className="num mb-3 text-xs text-text-tertiary">
          Corrida: <code className="font-mono">{portfolioTag}</code> · Capital inicial: <code className="font-mono">{formatUsd(report.starting_capital, 0)}</code>
        </p>

        <div className="flex flex-col gap-4 md:flex-row">
          {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => (
            <PortfolioVersionCard key={version} report={report.versions[version]} startingCapital={report.starting_capital} />
          ))}
        </div>
      </section>

      {/* Sección 2: esta semana */}
      <section>
        <h2 className="mb-3 text-base font-semibold text-foreground">Esta semana (simulación en papel)</h2>
        {!paperReport ? (
          <p className="italic text-sm text-text-tertiary">Todavía no hay ningún reporte de esta semana.</p>
        ) : (
          <>
            <p className="num mb-3 text-xs text-text-tertiary">
              Semana: <code className="font-mono">{paperReport.week_start}</code> a <code className="font-mono">{paperReport.week_end}</code> — datos
              reales, sin dinero real.
            </p>
            <div className="flex flex-col gap-4 md:flex-row">
              {VERSION_ORDER.filter((v) => paperReport.versions[v]).map((version) => {
                const v = paperReport.versions[version];
                return (
                  <div key={version} className="min-w-[300px] flex-1 rounded border border-border-subtle bg-surface p-4">
                    <h3 className="mb-3 text-base font-semibold text-foreground">{VERSION_LABELS[version]}</h3>

                    <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                      <dt className="text-text-secondary">Operaciones esta semana</dt>
                      <dd className="num text-right text-foreground">{v.n_open_positions + v.n_closed_trades}</dd>
                      <dt className="text-text-secondary">Abiertas ahora</dt>
                      <dd className="num text-right text-foreground">{v.n_open_positions}</dd>
                      <dt className="text-text-secondary">Cerradas</dt>
                      <dd className="num text-right text-foreground">{v.n_closed_trades}</dd>
                      <dt className="text-text-secondary">Acierto esta semana</dt>
                      <dd className="num text-right text-foreground">{v.trade_metrics.win_rate !== null ? formatPct(v.trade_metrics.win_rate * 100, 0) : "—"}</dd>
                    </dl>

                    {v.alerts.length > 0 && (
                      <div className="mb-3 space-y-1">
                        {v.alerts.map((a, i) => (
                          <Callout key={i} kind={a.type === "WARNING" ? "warning" : "positive"}>
                            <span className="normal-case font-normal">{a.message}</span>
                          </Callout>
                        ))}
                      </div>
                    )}

                    <p className="mb-1 text-xs text-text-secondary">Resultado día a día</p>
                    <DailyPnLChart trades={v.last_10_closed_trades} />

                    <p className="mb-1 mt-3 text-xs text-text-secondary">¿El sistema sabe cuándo está seguro? (n={v.calibration.n})</p>
                    <ConfidenceBucketBars buckets={v.calibration.buckets} />

                    <p className="num mt-3 text-xs text-text-tertiary">
                      {v.comparison_with_historical_backtest.available && v.comparison_with_historical_backtest.comparable
                        ? `¿Coincide con el histórico? ${v.comparison_with_historical_backtest.matches_historical ? "Sí" : "No"} (esta semana ${((v.comparison_with_historical_backtest.week_win_rate ?? 0) * 100).toFixed(0)}% vs histórico ${((v.comparison_with_historical_backtest.historical_win_rate ?? 0) * 100).toFixed(0)}%)`
                        : v.comparison_with_historical_backtest.note ?? "Sin histórico con qué comparar todavía."}
                    </p>
                    {v.comparison_with_historical_backtest.caveat && (
                      <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                        ⚠ {v.comparison_with_historical_backtest.caveat}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
