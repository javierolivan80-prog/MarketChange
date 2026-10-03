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
import { VERSION_ORDER, versionLabel } from "@/lib/labels";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";
export async function generateMetadata() {
  const { t } = await getT();
  return { title: t("Cartera", "Portfolio") };
}


// cartera/page.tsx — fusiona lo que antes eran dos pestañas separadas
// ("Backtest Analysis" y "Signals This Week"): ambas responden la misma
// pregunta de fondo ("¿cómo le va a la cartera?"), solo que una mira el
// histórico completo y la otra la semana en curso — tiene más sentido
// como dos secciones de una misma pantalla que como dos pestañas sueltas.

export default async function CarteraPage() {
  const { locale, t } = await getT();
  const title = t("Cartera", "Portfolio");
  if (!isDatabaseConfigured()) return <NotConfigured active="/cartera" title={title} />;

  const [portfolioTag, paperTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestPaperTradingRunBatchTag()]);

  if (!portfolioTag)
    return <NoDataYet active="/cartera" title={title} what={t("Todavía no hay resultados de la cartera.", "There are no portfolio results yet.")} />;

  const [report, paperReport] = await Promise.all([getPortfolioReport(portfolioTag), paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null)]);

  if (!report) {
    return (
      <StatePage active="/cartera" title={title}>
        <StateBox title={t("Los resultados de la cartera no están disponibles ahora mismo.", "Portfolio results are not available right now.")}>
          {t("Inténtalo de nuevo en unos minutos.", "Please try again in a few minutes.")}
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
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
          <ExportPdfButton report={report} />
        </div>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          {t(
            "Rentabilidad de cada estrategia aplicada a todos los eventos pasados, y el seguimiento de las señales de esta semana con precios de mercado. Rentabilidades pasadas no garantizan resultados futuros.",
            "Performance of each strategy applied to all past events, and tracking of this week's signals at market prices. Past performance does not guarantee future results.",
          )}
        </p>
      </header>

      {/* Sección 1: histórico */}
      <section className="mb-10">
        <SectionHeader
          title={t("Rentabilidad histórica", "Historical performance")}
          description={t("Cada estrategia aplicada a todos los eventos pasados, con costes.", "Each strategy applied to all past events, after costs.")}
        />

        <SampleBadge sample={report.sample} warning={report.oos_warning} />
        <div className="mb-4" />

        <div className="flex flex-col gap-4 md:flex-row">
          {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => (
            <PortfolioVersionCard key={version} report={report.versions[version]} startingCapital={report.starting_capital} locale={locale} />
          ))}
        </div>

        {/* Lo que el usuario necesita para leer bien las cifras, sin ocupar la
            pantalla: el veredicto interno de go/no-go del motor vive en
            /funciona, y el capital nominal del backtest no se muestra (las
            cifras van en %). */}
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-text-secondary">{t("Cómo leer estas cifras", "How to read these figures")}</summary>
          <div className="mt-2 space-y-1 text-text-secondary">
            <p>
              {t(
                "Cada estrategia se aplica a los eventos pasados con las mismas reglas que hoy: entrada el día siguiente al evento, costes incluidos y sin usar información posterior a cada fecha.",
                "Each strategy is applied to past events with the same rules as today: entry the day after the event, costs included and no information from after each date.",
              )}
            </p>
            <p className="num">
              {t(
                `${bias_report.n_delisted} de ${bias_report.n_total_tickers} empresas del histórico dejaron de cotizar (${formatShare(bias_report.survivorship_bias_pct, 1)}) y el ${formatShare(bias_report.data_gap_pct, 1)} de los precios tiene huecos; ambas cosas pueden desviar ligeramente los resultados.`,
                `${bias_report.n_delisted} of ${bias_report.n_total_tickers} companies in the history stopped trading (${formatShare(bias_report.survivorship_bias_pct, 1)}) and ${formatShare(bias_report.data_gap_pct, 1)} of prices have gaps; both can skew the results slightly.`,
              )}
            </p>
            <p>
              <Link href="/funciona" className="text-accent-700 hover:underline dark:text-accent-400">
                {t("Fiabilidad del sistema →", "System reliability →")}
              </Link>
            </p>
          </div>
        </details>
      </section>

      {/* Sección 2: esta semana */}
      <section>
        <SectionHeader
          title={t("Seguimiento de esta semana", "This week's tracking")}
          description={t(
            "Las señales de la semana medidas con precios reales de mercado desde que se emitieron.",
            "This week's signals measured at real market prices since they were issued.",
          )}
          action={{ href: "/historial", label: t("Historial completo", "Full track record") }}
        />
        {!paperReport ? (
          <p className="text-sm text-text-tertiary">{t("Todavía no hay señales en seguimiento esta semana.", "No signals are being tracked this week yet.")}</p>
        ) : (
          <>
            <p className="num mb-3 text-xs text-text-tertiary">
              {t(
                `Del ${formatDate(paperReport.week_start, locale)} al ${formatDate(paperReport.week_end, locale)}, con precios reales de mercado desde cada señal.`,
                `From ${formatDate(paperReport.week_start, locale)} to ${formatDate(paperReport.week_end, locale)}, at real market prices since each signal.`,
              )}
            </p>
            <div className="flex flex-col gap-4 md:flex-row">
              {VERSION_ORDER.filter((v) => paperReport.versions[v]).map((version) => {
                const v = paperReport.versions[version];
                return (
                  <div key={version} className="min-w-[300px] flex-1 border border-border-subtle bg-surface p-4">
                    <h3 className="mb-3 text-base font-semibold text-foreground">{versionLabel(version, locale)}</h3>

                    <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                      <dt className="text-text-secondary">{t("Operaciones esta semana", "Trades this week")}</dt>
                      <dd className="num text-right text-foreground">{v.n_open_positions + v.n_closed_trades}</dd>
                      <dt className="text-text-secondary">{t("Abiertas ahora", "Open now")}</dt>
                      <dd className="num text-right text-foreground">{v.n_open_positions}</dd>
                      <dt className="text-text-secondary">{t("Cerradas", "Closed")}</dt>
                      <dd className="num text-right text-foreground">{v.n_closed_trades}</dd>
                      <dt className="text-text-secondary">{t("Acierto esta semana", "Win rate this week")}</dt>
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

                    <p className="mb-1 text-xs text-text-secondary">{t("Resultado día a día", "Day-by-day result")}</p>
                    <DailyPnLChart trades={v.last_10_closed_trades} />

                    <p className="mb-1 mt-3 text-xs text-text-secondary">
                      {t("¿El sistema sabe cuándo está seguro?", "Does the system know when it is confident?")} (n={v.calibration.n})
                    </p>
                    <ConfidenceBucketBars buckets={v.calibration.buckets} />

                    <p className="num mt-3 text-xs text-text-tertiary">
                      {v.comparison_with_historical_backtest.available && v.comparison_with_historical_backtest.comparable
                        ? `${t("¿Coincide con el histórico?", "Does it match the backtest?")} ${v.comparison_with_historical_backtest.matches_historical ? t("Sí", "Yes") : "No"} (${t("esta semana", "this week")} ${((v.comparison_with_historical_backtest.week_win_rate ?? 0) * 100).toFixed(0)}% vs ${t("histórico", "backtest")} ${((v.comparison_with_historical_backtest.historical_win_rate ?? 0) * 100).toFixed(0)}%)`
                        : v.comparison_with_historical_backtest.note ?? t("Sin histórico con qué comparar todavía.", "No history to compare against yet.")}
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
