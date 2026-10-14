// PortfolioVersionCard.tsx — una columna por versión (Conservative/
// Aggressive/Balanced) del reporte de backtest_report.py. Renderiza el JSON
// ya calculado por Python (portfolio_metrics.py) tal cual llega — este
// componente no recalcula ni una fórmula, solo formatea unidades vía
// lib/format.ts (el JSON mezcla fracciones 0-1 con puntos porcentuales ya
// multiplicados por 100, fiel a como cada función de portfolio_metrics.py
// los devuelve — ver los comentarios de unidad en cada fmt* de abajo).
import type { PortfolioVersionReport } from "@/lib/queries";
import { PortfolioEquityCurve } from "./PortfolioEquityCurve";
import { ScatterPredictedActual } from "./ScatterPredictedActual";
import { ConfidenceBucketBars } from "./ConfidenceBucketBars";
import { ReturnHistogram } from "./ReturnHistogram";
import { DrawdownChart } from "./DrawdownChart";
import { Callout } from "@/components/ui/Callout";
import { SignedPct } from "@/components/ui/DirectionBadge";
import { formatFracAsPct, formatIntervalo, formatPct, formatShare, formatNum, formatDrawdown } from "@/lib/format";
import { MIN_GRUPOS_EFECTIVOS, MIN_OPERACIONES } from "@/lib/incertidumbre";
import { eventClassLabel, exitReasonLabel, versionLabel } from "@/lib/labels";
import { makeT, type Locale } from "@/lib/i18n";

// Componente de servidor: el idioma llega como prop (hacerlo de cliente para
// leerlo del contexto mandaría al navegador todas las operaciones del informe).
export function PortfolioVersionCard({
  report,
  startingCapital,
  locale = "es",
}: {
  report: PortfolioVersionReport;
  startingCapital: number;
  locale?: Locale;
}) {
  const t = makeT(locale);
  const { trade_metrics: tm, equity_metrics: em, calibration: cal, asymmetry, no_lookahead_violations, temporal_stability } = report;
  const eventTypeRows = Object.values(report.metrics_by_event_type);

  return (
    <div className="min-w-0 tarjeta p-4">
      <h2 className="mb-3 text-base font-semibold text-foreground">{versionLabel(report.version, locale)}</h2>

      {no_lookahead_violations.length > 0 && (
        <Callout kind="critical" className="mb-3">
          <p className="font-medium">
            {no_lookahead_violations.length}{" "}
            {t("violación(es) anti-look-ahead — resultado no fiable.", "look-ahead violation(s) — result not reliable.")}
          </p>
          <ul className="mt-1 list-inside list-disc font-normal normal-case">
            {no_lookahead_violations.slice(0, 3).map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </Callout>
      )}

      {/* Las tres cifras que responden "¿cómo le fue?"; el resto, plegado. Antes
          eran 14 métricas con el mismo peso visual y nada indicaba cuál mirar. */}
      <div className="mb-3 grid grid-cols-3 gap-2">
        <div>
          <p className="text-xs text-text-secondary">{t("Resultado", "Return")}</p>
          <p className="num text-xl leading-tight font-semibold tracking-tight text-foreground">{formatFracAsPct(em.total_return, 1)}</p>
        </div>
        <div>
          <p className="text-xs text-text-secondary">{t("Acierto", "Win rate")}</p>
          <p className="num text-xl leading-tight font-semibold tracking-tight text-foreground">{tm.win_rate !== null ? `${(tm.win_rate * 100).toFixed(0)}%` : "—"}</p>
        </div>
        <div>
          <p className="text-xs text-text-secondary">{t("Peor caída", "Max drawdown")}</p>
          <p className="num text-xl leading-tight font-semibold tracking-tight text-foreground">{formatDrawdown(em.max_drawdown)}</p>
        </div>
      </div>
      <p className="num mb-3 text-xs text-text-tertiary">
        {tm.total_trades} {t("operaciones en el histórico", "trades in the backtest")}
        {/* Tanda 11: cuánto podría variar cada cifra solo por azar. */}
        {tm.win_rate_ic95 && (
          <>
            {" · "}
            {t("acierto, con un 95 % de confianza, entre", "win rate, with 95% confidence, between")}{" "}
            {formatIntervalo(tm.win_rate_ic95, locale, { escala: 100, digits: 0 })}
          </>
        )}
        {tm.avg_pnl_ic95 ? (
          <>
            {" · "}
            {t("resultado medio por operación entre", "average result per trade between")}{" "}
            {formatIntervalo(tm.avg_pnl_ic95, locale, { digits: 2, conSigno: true })}
            {tm.avg_pnl_distinguible_de_cero === false &&
              t(": no se distingue de cero", ": not distinguishable from zero")}
          </>
        ) : tm.ic_grupos_efectivos != null && tm.ic_grupos_efectivos < MIN_GRUPOS_EFECTIVOS ? (
          // Tanda 14: los intervalos agrupan por mes de entrada; con las
          // operaciones concentradas en pocos meses no se puede medir cuánto
          // se parecen (ni el acierto ni el resultado medio). Solo en los
          // informes que traen el dato.
          <>
            {" · "}
            {t(
              "se concentran en muy pocos meses: todavía no se puede saber cuánto dependen del azar",
              "they are concentrated in very few months: it cannot yet be told how much they depend on chance",
            )}
          </>
        ) : (
          tm.total_trades > 0 &&
          tm.total_trades < MIN_OPERACIONES && (
            <>
              {" · "}
              {t(
                `con menos de ${MIN_OPERACIONES} operaciones no se puede saber si el resultado medio es distinto de cero`,
                `with fewer than ${MIN_OPERACIONES} trades it cannot be told whether the average result differs from zero`,
              )}
            </>
          )
        )}
      </p>

      <details suppressHydrationWarning className="mb-3 border-y border-border-subtle text-xs [&>*:last-child]:pb-3">
        <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">{t("Todas las métricas", "All metrics")}</summary>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
          <dt className="text-text-secondary">{t("Operaciones", "Trades")}</dt>
          <dd className="num text-right text-foreground">{tm.total_trades}</dd>

          <dt className="text-text-secondary">{t("Factor de beneficio", "Profit factor")}</dt>
          <dd className="num text-right text-foreground">{formatNum(tm.profit_factor)}</dd>

          <dt className="text-text-secondary">{t("Esperanza por operación", "Expectancy per trade")}</dt>
          <dd className="num text-right text-foreground">{formatPct(tm.expectancy)}</dd>

          <dt className="text-text-secondary">Sharpe</dt>
          <dd className="num text-right text-foreground">{formatNum(em.sharpe_ratio)}</dd>

          <dt className="text-text-secondary">Sortino</dt>
          <dd className="num text-right text-foreground">{formatNum(em.sortino_ratio)}</dd>

          <dt className="text-text-secondary">Calmar</dt>
          <dd className="num text-right text-foreground">{formatNum(em.calmar_ratio)}</dd>

          <dt className="text-text-secondary">{t("Factor de recuperación", "Recovery factor")}</dt>
          <dd className="num text-right text-foreground">{formatNum(em.recovery_factor)}</dd>

          <dt className="text-text-secondary">{t("Rachas (G/P)", "Streaks (W/L)")}</dt>
          <dd className="num text-right text-foreground">
            {tm.consecutive_wins}/{tm.consecutive_losses}
          </dd>

          <dt className="text-text-secondary">{t("Calibración", "Calibration")}</dt>
          <dd className={`num text-right ${cal.meets_target ? "text-emerald-700 dark:text-emerald-400" : "text-foreground"}`}>
            {formatNum(cal.calibration_score)}
          </dd>

          <dt className="text-text-secondary">{t("R² predicho-real", "R² predicted vs actual")}</dt>
          <dd className="num text-right text-foreground">{formatNum(report.prediction_regression.r_squared)}</dd>
        </dl>
      </details>

      {asymmetry.n_trades > 0 && (
        <p className="num mb-3 text-xs text-text-tertiary">
          {t(
            `${formatShare(asymmetry.pct_reaching_positive_threshold, 0)} de las operaciones llegó a ganar un ${asymmetry.threshold_pct}% y ${formatShare(asymmetry.pct_reaching_negative_threshold, 0)} llegó a perderlo.`,
            `${formatShare(asymmetry.pct_reaching_positive_threshold, 0)} of trades were up ${asymmetry.threshold_pct}% at some point and ${formatShare(asymmetry.pct_reaching_negative_threshold, 0)} were down by as much.`,
          )}
        </p>
      )}

      <div className="mb-4">
        <PortfolioEquityCurve points={report.equity_curve} startingCapital={startingCapital} locale={locale} />
      </div>

      <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
        <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">{t("Caídas a lo largo del tiempo", "Drawdowns over time")}</summary>
        <DrawdownChart equityCurve={report.equity_curve} />
      </details>

      <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
        <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">{t("Distribución de retornos", "Return distribution")}</summary>
        <ReturnHistogram pnlPcts={report.all_trades.map((t) => t.pnl_pct)} />
      </details>

      <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
        <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">{t("Predicho frente a real", "Predicted vs actual")}</summary>
        <ScatterPredictedActual points={report.prediction_regression.scatter} rSquared={report.prediction_regression.r_squared} />
      </details>

      {!report.confidence_calibration.no_aplica && (
      <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
        <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">
          {t("Acierto según la confianza declarada", "Win rate by stated confidence")}{" "}
          {report.confidence_calibration.correlation !== null &&
            `(${t("correlación", "correlation")} ${report.confidence_calibration.correlation.toFixed(2)})`}
        </summary>
        <ConfidenceBucketBars buckets={report.confidence_calibration.buckets} />
      </details>
      )}

      {temporal_stability && (
        <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
          <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">
            {t("Estabilidad temporal", "Stability over time")} (split {temporal_stability.split_date})
          </summary>
          <p className={temporal_stability.stable === false ? "text-amber-700 dark:text-amber-400" : "text-text-secondary"}>
            {temporal_stability.stable === null
              ? t("Muestra insuficiente para comparar", "Sample too small to compare")
              : temporal_stability.stable
                ? t("Estable entre periodos", "Stable across periods")
                : t("Diverge entre periodos", "Diverges across periods")}
          </p>
          {temporal_stability.warnings.map((w, i) => (
            <Callout key={i} kind="warning" className="mt-1">
              {w}
            </Callout>
          ))}
        </details>
      )}

      {eventTypeRows.length > 0 && (
        <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
          <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">
            {t("Por tipo de evento", "By event type")} ({eventTypeRows.length})
          </summary>
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="py-1 pr-2 font-medium text-text-secondary">{t("Tipo", "Type")}</th>
                <th className="py-1 pr-2 text-right font-medium text-text-secondary">N</th>
                <th className="py-1 pr-2 text-right font-medium text-text-secondary">{t("Acierto", "Win rate")}</th>
                <th className="py-1 pr-2 text-right font-medium text-text-secondary">{t("Retorno medio", "Average return")}</th>
              </tr>
            </thead>
            <tbody>
              {eventTypeRows.map((row) => (
                <tr key={row.event_type} className="border-b border-border-subtle">
                  <td className="py-1 pr-2 text-foreground">
                    {eventClassLabel(row.event_type, locale)}
                    {row.insufficient_sample && (
                      <span
                        className="ml-1.5 rounded-full bg-amber-500/12 px-2 text-xs font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-400"
                        title={t("n < 20 — muestra insuficiente", "n < 20 — sample too small")}
                      >
                        {t("n bajo", "low n")}
                      </span>
                    )}
                  </td>
                  <td className="num py-1 pr-2 text-right text-foreground">{row.n_trades}</td>
                  <td className="num py-1 pr-2 text-right text-foreground">{formatShare(row.win_rate * 100, 1)}</td>
                  <td className="num py-1 pr-2 text-right text-foreground">{formatPct(row.avg_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <details suppressHydrationWarning className="border-t border-border-subtle text-xs [&>*:last-child]:pb-3">
        <summary className="py-2.5 text-sm text-foreground pointer-coarse:py-3">{t("Las 10 mejores y peores operaciones", "Top 10 best and worst trades")}</summary>
        <TradeMiniTable trades={report.top_10_winners} locale={locale} />
        <div className="mt-2" />
        <TradeMiniTable trades={report.top_10_losers} locale={locale} />
      </details>
    </div>
  );
}

function TradeMiniTable({ trades, locale }: { trades: PortfolioVersionReport["top_10_winners"]; locale: Locale }) {
  const t = makeT(locale);
  if (trades.length === 0) {
    return <p className="italic text-text-tertiary">{t("Sin operaciones.", "No trades.")}</p>;
  }
  return (
    <table className="w-full border-collapse text-left">
      <thead>
        <tr className="border-b border-border-subtle">
          <th className="py-1 pr-2 font-medium text-text-secondary">Ticker</th>
          <th className="py-1 pr-2 font-medium text-text-secondary">{t("Salida", "Exit")}</th>
          <th className="py-1 pr-2 text-right font-medium text-text-secondary">{t("Resultado", "Result")}</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((tr) => (
          <tr key={`${tr.event_id}-${tr.exit_date}`} className="border-b border-border-subtle">
            <td className="py-1 pr-2 font-mono text-foreground">{tr.ticker ?? "—"}</td>
            <td className="py-1 pr-2 text-text-secondary">{exitReasonLabel(tr.exit_reason, locale)}</td>
            <td className="py-1 pr-2 text-right">
              <SignedPct value={tr.pnl_pct} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
