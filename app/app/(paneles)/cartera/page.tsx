import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport, getLatestPaperTradingRunBatchTag, getPaperTradingReport } from "@/lib/data";
import { PortfolioVersionCard } from "@/components/PortfolioVersionCard";
import { Nav } from "@/components/Nav";
import { ExportPdfButton } from "@/components/ExportPdfButton";
import { DailyPnLChart } from "@/components/DailyPnLChart";
import { ConfidenceBucketBars } from "@/components/ConfidenceBucketBars";
import { Callout } from "@/components/ui/Callout";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { SectionHeader } from "@/components/ui/SectionHeader";
import { NoDataYet, NotConfigured, StateBox, StatePage } from "@/components/ui/PageState";
import Link from "next/link";
import { formatDate, formatShare } from "@/lib/format";
import { VERSION_LABELS, VERSION_ORDER } from "@/lib/labels";

export const dynamic = "force-dynamic";
export const metadata = { title: "Cartera" };


// cartera/page.tsx — fusiona lo que antes eran dos pestañas separadas
// ("Backtest Analysis" y "Signals This Week"): ambas responden la misma
// pregunta de fondo ("¿cómo le va a la cartera?"), solo que una mira el
// histórico completo y la otra la semana en curso — tiene más sentido
// como dos secciones de una misma pantalla que como dos pestañas sueltas.

export default async function CarteraPage() {
  if (!isDatabaseConfigured()) return <NotConfigured active="/cartera" title="Cartera" />;

  const [portfolioTag, paperTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestPaperTradingRunBatchTag()]);

  if (!portfolioTag) return <NoDataYet active="/cartera" title="Cartera" what="Todavía no hay resultados de la cartera." />;

  const [report, paperReport] = await Promise.all([getPortfolioReport(portfolioTag), paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null)]);

  if (!report) {
    return (
      <StatePage active="/cartera" title="Cartera">
        <StateBox title="Los resultados de la cartera no están disponibles ahora mismo.">
          Inténtalo de nuevo en unos minutos.
        </StateBox>
      </StatePage>
    );
  }

  const { bias_report } = report;

  return (
    <main className="mx-auto max-w-7xl px-4 py-4 sm:px-6">
      <Nav active="/cartera" />
      <header className="mb-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Cartera</h1>
          <ExportPdfButton report={report} />
        </div>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          Rentabilidad de cada estrategia aplicada a todos los eventos pasados, y el seguimiento de las señales de esta semana con precios de
          mercado. Rentabilidades pasadas no garantizan resultados futuros.
        </p>
      </header>

      {/* Sección 1: histórico */}
      <section className="mb-10">
        <SectionHeader title="Rentabilidad histórica" description="Cada estrategia aplicada a todos los eventos pasados, con costes." />

        <SampleBadge sample={report.sample} warning={report.oos_warning} />
        <div className="mb-4" />

        <div className="flex flex-col gap-4 md:flex-row">
          {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => (
            <PortfolioVersionCard key={version} report={report.versions[version]} startingCapital={report.starting_capital} />
          ))}
        </div>

        {/* Lo que el usuario necesita para leer bien las cifras, sin ocupar la
            pantalla: el veredicto interno de go/no-go del motor vive en
            /funciona, y el capital nominal del backtest no se muestra (las
            cifras van en %). */}
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-text-secondary">Cómo leer estas cifras</summary>
          <div className="mt-2 space-y-1 text-text-secondary">
            <p>
              Cada estrategia se aplica a los eventos pasados con las mismas reglas que hoy: entrada el día siguiente al evento, costes
              incluidos y sin usar información posterior a cada fecha.
            </p>
            <p className="num">
              {bias_report.n_delisted} de {bias_report.n_total_tickers} empresas del histórico dejaron de cotizar ({formatShare(bias_report.survivorship_bias_pct, 1)}) y
              el {formatShare(bias_report.data_gap_pct, 1)} de los precios tiene huecos; ambas cosas pueden desviar ligeramente los resultados.
            </p>
            <p>
              <Link href="/funciona" className="text-accent-700 hover:underline dark:text-accent-400">
                Fiabilidad del sistema →
              </Link>
            </p>
          </div>
        </details>
      </section>

      {/* Sección 2: esta semana */}
      <section>
        <SectionHeader
          title="Seguimiento de esta semana"
          description="Las señales de la semana medidas con precios reales de mercado desde que se emitieron."
          action={{ href: "/historial", label: "Historial completo" }}
        />
        {!paperReport ? (
          <p className="text-sm text-text-tertiary">Todavía no hay señales en seguimiento esta semana.</p>
        ) : (
          <>
            <p className="num mb-3 text-xs text-text-tertiary">
              Del {formatDate(paperReport.week_start)} al {formatDate(paperReport.week_end)}, con precios reales de mercado desde cada señal.
            </p>
            <div className="flex flex-col gap-4 md:flex-row">
              {VERSION_ORDER.filter((v) => paperReport.versions[v]).map((version) => {
                const v = paperReport.versions[version];
                return (
                  <div key={version} className="min-w-[300px] flex-1 border border-border-subtle bg-surface p-4">
                    <h3 className="mb-3 text-base font-semibold text-foreground">{VERSION_LABELS[version]}</h3>

                    <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                      <dt className="text-text-secondary">Operaciones esta semana</dt>
                      <dd className="num text-right text-foreground">{v.n_open_positions + v.n_closed_trades}</dd>
                      <dt className="text-text-secondary">Abiertas ahora</dt>
                      <dd className="num text-right text-foreground">{v.n_open_positions}</dd>
                      <dt className="text-text-secondary">Cerradas</dt>
                      <dd className="num text-right text-foreground">{v.n_closed_trades}</dd>
                      <dt className="text-text-secondary">Acierto esta semana</dt>
                      <dd className="num text-right text-foreground">{v.trade_metrics.win_rate !== null ? formatShare(v.trade_metrics.win_rate * 100, 0) : "—"}</dd>
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
                      <Callout kind="warning" className="mt-1">
                        <span className="font-normal normal-case">{v.comparison_with_historical_backtest.caveat}</span>
                      </Callout>
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
