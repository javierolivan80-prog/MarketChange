import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import { getAbstentionSummary, getHomeSummary, getPipelineFreshness, getRecentTradeSignals, getRecommendedVersion } from "@/lib/data";
import type { AbstentionCategory } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { WelcomeNote } from "@/components/WelcomeNote";
import { SectionHeader } from "@/components/ui/SectionHeader";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { NotConfigured } from "@/components/ui/PageState";
import { formatPct, formatNum, formatDate, formatDateTime, formatDrawdown } from "@/lib/format";
import { eventClassLabel, reliability, versionLabel } from "@/lib/labels";
import { getT } from "@/lib/locale";
import type { T } from "@/lib/i18n";

export const dynamic = "force-dynamic";

// page.tsx (Inicio) — responde, en este orden, las tres preguntas de quien
// abre el panel: ¿está vivo? (última actualización), ¿qué ha pasado? (últimas
// señales operables) y ¿me puedo fiar? (veredicto del motor de validación,
// pipeline/validation/decision.py, en lenguaje llano). El detalle completo
// vive en /senales, /cartera y /funciona.
//
// Mismo criterio de color que /funciona: A/B/C es el veredicto del propio
// motor de validación, no P&L ni dirección.
const VERDICT_COPY: Record<string, { color: string; text: string }> = {
  A: { color: "border-emerald-300 bg-emerald-50 dark:border-emerald-800/60 dark:bg-emerald-500/10", text: "text-emerald-800 dark:text-emerald-400" },
  B: { color: "border-amber-300 bg-amber-50 dark:border-amber-800/60 dark:bg-amber-500/10", text: "text-amber-800 dark:text-amber-400" },
  C: { color: "border-rose-300 bg-rose-50 dark:border-rose-800/60 dark:bg-rose-500/10", text: "text-rose-800 dark:text-rose-400" },
};

function abstentionLabel(category: AbstentionCategory, t: T): string {
  const labels: Record<AbstentionCategory, string> = {
    priced_in: t("El mercado ya lo sabía", "The market already knew"),
    low_confidence: t("El debate no fue concluyente", "The debate was inconclusive"),
    low_ev: t("Valor esperado insuficiente tras costes", "Expected value too low after costs"),
    delisting: t("Riesgo de exclusión de bolsa", "Delisting risk"),
    contradictory: t("Datos contradictorios", "Contradictory data"),
    fda_unconfirmed: t("FDA aún no confirmado por la empresa", "FDA news not yet confirmed by the company"),
    illiquid: t("Acción poco líquida", "Illiquid stock"),
    other: t("Otros motivos", "Other reasons"),
  };
  return labels[category];
}

// Más de 2 días sin analizar nada (fin de semana incluido) = algo va mal.
const STALE_AFTER_HOURS = 60;

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-[140px] flex-1 border-l border-border-subtle pl-3">
      <p className="mb-1 text-xs uppercase tracking-wide text-text-secondary">{label}</p>
      <p className="num text-2xl font-semibold text-foreground">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

export default async function InicioPage() {
  const { locale, t } = await getT();
  if (!isDatabaseConfigured()) return <NotConfigured active="/" title={t("Resumen", "Overview")} />;
  const RELIABILITY = reliability(locale);

  const version = await getRecommendedVersion();
  const [summary, recent, freshness, abstention] = await Promise.all([
    getHomeSummary(version),
    getRecentTradeSignals(version, 8),
    getPipelineFreshness(),
    getAbstentionSummary(version, 7),
  ]);

  // Las cifras y señales de esta página son las de la versión que el propio
  // motor de validación recomienda — no siempre BALANCED.
  const shownVersion = version;
  const shown = summary.portfolio?.trade_metrics && summary.portfolio.equity_metrics ? summary.portfolio : null;
  const verdict = summary.validation ? VERDICT_COPY[summary.validation.best_decision.option] : null;
  const openPositions = summary.open_paper_positions;

  const lastAnalyzed = freshness.last_analyzed_at ? new Date(freshness.last_analyzed_at) : null;
  const hoursSince = lastAnalyzed ? (Date.now() - lastAnalyzed.getTime()) / 3_600_000 : null;
  const isStale = hoursSince !== null && hoursSince > STALE_AFTER_HOURS;

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/" />

      <header className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("Resumen", "Overview")}</h1>
        <p className={`num text-xs ${isStale ? "text-amber-700 dark:text-amber-400" : "text-text-tertiary"}`}>
          {lastAnalyzed
            ? `${t("Actualizado", "Updated")}: ${formatDateTime(freshness.last_analyzed_at, locale)} · ${freshness.analyzed_last_24h} ${t(
                "eventos analizados en 24 h",
                "events analysed in 24 h",
              )}${isStale ? t(" · la actualización lleva retraso", " · updates are running late") : ""}`
            : t("Todavía no hay eventos analizados", "No events analysed yet")}
        </p>
      </header>

      <WelcomeNote />

      {/* Últimas señales — lo accionable va primero */}
      <section className="mb-6">
        <SectionHeader
          id="senales-operables"
          title={t("Señales operables", "Tradable signals")}
          description={t(
            "Las más recientes en las que la estrategia recomendada decidió operar, con su plan técnico.",
            "The latest ones the recommended strategy decided to trade, with their technical plan.",
          )}
          action={{ href: "/senales", label: t("Ver todas", "See all") }}
        />
        {recent.length === 0 ? (
          <p className="border border-dashed border-border-subtle p-4 text-sm text-text-secondary">
            {t(
              "Ningún evento ha superado todavía los filtros para operar. La mayoría de eventos se descartan a propósito: el sistema solo señala los que tienen un valor esperado positivo después de costes.",
              "No event has passed the trading filters yet. Most events are discarded on purpose: the system only flags those with a positive expected value after costs.",
            )}
          </p>
        ) : (
          <>
          <div className="overflow-x-auto border border-border-subtle">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <caption className="sr-only">{t("Señales operables más recientes con su plan técnico", "Latest tradable signals with their technical plan")}</caption>
              <thead>
                <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
                  <th scope="col" className="py-2 pl-3 pr-3 font-medium">{t("Símbolo", "Ticker")}</th>
                  <th scope="col" className="py-2 pr-3 font-medium">{t("Catalizador", "Catalyst")}</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">{t("Confianza técnica", "Technical confidence")}</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">{t("Entrada", "Entry")}</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">Stop</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">{t("Objetivo", "Target")}</th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    <abbr
                      title={t(
                        "Riesgo/beneficio: lo que se puede ganar hasta el objetivo por cada unidad que se arriesga hasta el stop",
                        "Risk/reward: what can be gained up to the target for each unit risked down to the stop",
                      )}
                      className="no-underline"
                    >
                      {t("Riesgo/benef.", "Risk/reward")}
                    </abbr>
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">{t("Horizonte", "Horizon")}</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((s) => (
                  <tr key={s.event_id} className="border-b border-border-subtle hover:bg-surface-raised">
                    <td className="py-2 pl-3 pr-3">
                      <Link href={`/senales/${s.event_id}`} className="inline-flex items-center gap-2 hover:underline">
                        <span className="font-mono font-medium text-foreground">{s.ticker}</span>
                        <DirectionBadge value={s.direction} />
                      </Link>
                    </td>
                    <td className="py-2 pr-3 text-text-secondary">
                      {eventClassLabel(s.event_class, locale)}
                      <span className="num ml-1 text-xs text-text-tertiary">{formatDate(s.d0_close_date, locale)}</span>
                    </td>
                    <td className="num py-2 pr-3 text-right">
                      {s.tech_confidence !== null ? (
                        <span className={s.tech_passes ? "text-emerald-700 dark:text-emerald-400" : "text-text-secondary"}>
                          {s.tech_confidence}/100{s.tech_passes ? " ✓" : ""}
                        </span>
                      ) : (
                        <span className="text-text-tertiary">—</span>
                      )}
                    </td>
                    <td className="num py-2 pr-3 text-right text-foreground">{s.entry !== null ? s.entry.toFixed(2) : "—"}</td>
                    <td className="num py-2 pr-3 text-right text-foreground">{s.stop !== null ? s.stop.toFixed(2) : "—"}</td>
                    <td className="num py-2 pr-3 text-right text-foreground">{s.target !== null ? s.target.toFixed(2) : "—"}</td>
                    <td className="num py-2 pr-3 text-right text-foreground">{s.risk_reward !== null ? `1:${s.risk_reward.toFixed(1)}` : "—"}</td>
                    <td className="num py-2 pr-3 text-right text-text-secondary">{s.timeframe_days !== null ? `~${s.timeframe_days} ${t("sesiones", "sessions")}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1.5 text-xs text-text-tertiary">
            {t(
              "Confianza técnica 0-100; ✓ = pasa los filtros de riesgo (catalizador confirmado, indicadores alineados, riesgo/beneficio de 1:2 o mejor y stop sobre un nivel real). Entrada de referencia: cierre del día del evento.",
              "Technical confidence 0-100; ✓ = passes the risk filters (confirmed catalyst, aligned indicators, risk/reward of 1:2 or better and a stop on a real level). Reference entry: close on the event day.",
            )}
          </p>
          </>
        )}
      </section>

      {/* Lo que NO se ha operado y por qué — tan informativo como lo que sí */}
      {abstention.analyzed > 0 && (
        <section className="mb-6">
          <SectionHeader
            title={t(`Últimos ${abstention.days} días`, `Last ${abstention.days} days`)}
            description={t(
              "Tan importante como lo que se opera: cuántos eventos se descartaron y por qué.",
              "As important as what gets traded: how many events were discarded and why.",
            )}
          />
          <p className="num mb-2 text-sm text-text-secondary">
            {abstention.analyzed} {t("eventos analizados", "events analysed")} · {abstention.traded} {t("superaron los filtros", "passed the filters")} ·{" "}
            {abstention.analyzed - abstention.traded} {t("descartados", "discarded")}
          </p>
          {abstention.reasons.length > 0 && (
            <ul className="space-y-1 text-sm">
              {abstention.reasons.map((r) => (
                <li key={r.category} className="flex items-center gap-3">
                  <span className="num w-10 text-right text-text-tertiary">{r.n}</span>
                  <span className="h-1.5 bg-border-strong" style={{ width: `${Math.max(4, (r.n / (abstention.analyzed - abstention.traded)) * 160)}px` }} aria-hidden="true" />
                  <span className="text-text-secondary">{abstentionLabel(r.category, t)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Calificación, periodo y cifras que la respaldan, juntos: antes eran
          tres bloques sueltos (aviso de periodo, recuadro de color y cifras
          debajo, sin título) y no se veía que las cifras justificaban la nota. */}
      <section aria-labelledby="fiabilidad" className="mb-2">
        <SectionHeader
          id="fiabilidad"
          title={t("¿Me puedo fiar?", "Can I trust it?")}
          description={t(
            "Calificación del motor de validación y las cifras históricas en que se apoya.",
            "The validation engine's rating and the historical figures behind it.",
          )}
          action={{ href: "/funciona", label: t("Ver por qué", "See why") }}
        />
        <div className={`border ${verdict ? verdict.color : "border-border-subtle"}`}>
          <div className="p-4">
            {verdict ? (
              <>
                <p className={`mb-1 text-lg font-semibold ${verdict.text}`}>{RELIABILITY[summary.validation!.best_decision.option].title}</p>
                <p className="text-sm text-foreground">{RELIABILITY[summary.validation!.best_decision.option].summary}</p>
              </>
            ) : (
              <>
                <p className="mb-1 text-lg font-semibold text-foreground">{t("Evaluación de fiabilidad en curso", "Reliability assessment in progress")}</p>
                <p className="text-sm text-text-secondary">
                  {t(
                    "La calificación aparece cuando hay suficientes eventos para medirla con rigor.",
                    "The rating appears once there are enough events to measure it rigorously.",
                  )}
                </p>
              </>
            )}
          </div>
          {shown && shown.trade_metrics && shown.equity_metrics && (
            <div className="border-t border-border-subtle bg-surface p-4">
              <p className="mb-3 text-xs text-text-tertiary">
                {t(
                  `Rentabilidad histórica de la estrategia ${versionLabel(shownVersion, locale).toLowerCase()}, aplicada a todos los eventos pasados.`,
                  `Historical performance of the ${versionLabel(shownVersion, locale).toLowerCase()} strategy, applied to all past events.`,
                )}
              </p>
              <div className="flex flex-wrap gap-y-4">
                <Stat
                  label={t("Acierto", "Win rate")}
                  value={shown.trade_metrics.win_rate !== null ? `${(shown.trade_metrics.win_rate * 100).toFixed(0)}%` : "—"}
                  hint={t(`en ${shown.trade_metrics.total_trades} operaciones`, `over ${shown.trade_metrics.total_trades} trades`)}
                />
                <Stat
                  label={t("Resultado acumulado", "Cumulative return")}
                  value={shown.equity_metrics.total_return !== null ? formatPct(shown.equity_metrics.total_return * 100, 1) : "—"}
                  hint={t("costes incluidos", "after costs")}
                />
                <Stat
                  label={t("Peor caída", "Max drawdown")}
                  value={formatDrawdown(shown.equity_metrics.max_drawdown)}
                  hint={t("pérdida temporal máxima", "largest temporary loss")}
                />
                <Stat
                  label={t("Posiciones abiertas", "Open positions")}
                  value={openPositions !== null ? formatNum(openPositions, 0) : "—"}
                  hint={t("señales en seguimiento", "signals being tracked")}
                />
              </div>
            </div>
          )}
          {summary.portfolio && (summary.portfolio.sample || summary.portfolio.oos_warning) && (
            <div className="border-t border-border-subtle">
              <SampleBadge sample={summary.portfolio.sample} inset />
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
