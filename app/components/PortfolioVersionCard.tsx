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
import { formatFracAsPct, formatPct, formatUsd, formatNum, formatDrawdown } from "@/lib/format";
import { eventClassLabel, exitReasonLabel, versionLabel } from "@/lib/labels";

export function PortfolioVersionCard({
  report,
  startingCapital,
}: {
  report: PortfolioVersionReport;
  startingCapital: number;
}) {
  const { trade_metrics: tm, equity_metrics: em, calibration: cal, asymmetry, no_lookahead_violations, temporal_stability } = report;
  const eventTypeRows = Object.values(report.metrics_by_event_type);

  return (
    <div className="flex-1 min-w-[300px] rounded border border-border-subtle bg-surface p-4">
      <h2 className="mb-3 text-base font-semibold tracking-tight text-foreground">{versionLabel(report.version)}</h2>

      {no_lookahead_violations.length > 0 && (
        <Callout kind="critical" className="mb-3">
          <p className="font-medium">{no_lookahead_violations.length} violación(es) anti-look-ahead — resultado no fiable.</p>
          <ul className="mt-1 list-inside list-disc font-normal normal-case">
            {no_lookahead_violations.slice(0, 3).map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </Callout>
      )}

      <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        <dt className="text-text-secondary">Operaciones</dt>
        <dd className="num text-right text-foreground">{tm.total_trades}</dd>

        <dt className="text-text-secondary">Acierto</dt>
        <dd className="num text-right text-foreground">{tm.win_rate !== null ? `${(tm.win_rate * 100).toFixed(1)}%` : "—"}</dd>

        <dt className="text-text-secondary">Factor de beneficio</dt>
        <dd className="num text-right text-foreground">{formatNum(tm.profit_factor)}</dd>

        <dt className="text-text-secondary">Esperanza por operación</dt>
        <dd className="num text-right text-foreground">{formatPct(tm.expectancy)}</dd>

        <dt className="text-text-secondary">Balance final</dt>
        <dd className="num text-right text-foreground">{formatUsd(em.final_balance, 0)}</dd>

        <dt className="text-text-secondary">Resultado total</dt>
        <dd className="num text-right text-foreground">{formatFracAsPct(em.total_return, 1)}</dd>

        <dt className="text-text-secondary">Sharpe</dt>
        <dd className="num text-right text-foreground">{formatNum(em.sharpe_ratio)}</dd>

        <dt className="text-text-secondary">Sortino</dt>
        <dd className="num text-right text-foreground">{formatNum(em.sortino_ratio)}</dd>

        <dt className="text-text-secondary">Calmar</dt>
        <dd className="num text-right text-foreground">{formatNum(em.calmar_ratio)}</dd>

        <dt className="text-text-secondary">Peor caída</dt>
        <dd className="num text-right text-foreground">{formatDrawdown(em.max_drawdown)}</dd>

        <dt className="text-text-secondary">Factor de recuperación</dt>
        <dd className="num text-right text-foreground">{formatNum(em.recovery_factor)}</dd>

        <dt className="text-text-secondary">Rachas (G/P)</dt>
        <dd className="num text-right text-foreground">
          {tm.consecutive_wins}/{tm.consecutive_losses}
        </dd>

        <dt className="text-text-secondary">Calibración</dt>
        <dd className={`num text-right ${cal.meets_target ? "text-emerald-700 dark:text-emerald-400" : "text-foreground"}`}>
          {formatNum(cal.calibration_score)}
        </dd>

        <dt className="text-text-secondary">R² predicho-real</dt>
        <dd className="num text-right text-foreground">{formatNum(report.prediction_regression.r_squared)}</dd>
      </dl>

      {asymmetry.n_trades > 0 && (
        <p className="num mb-3 text-xs text-text-tertiary">
          Asimetría (umbral ±{asymmetry.threshold_pct}%): {formatPct(asymmetry.pct_reaching_positive_threshold, 0)} alcanzan +umbral vs{" "}
          {formatPct(asymmetry.pct_reaching_negative_threshold, 0)} -umbral
          {asymmetry.asymmetric_favoring_gains !== null &&
            (asymmetry.asymmetric_favoring_gains ? " (favorece ganancias)" : " (favorece pérdidas)")}
        </p>
      )}

      <div className="mb-4">
        <PortfolioEquityCurve points={report.equity_curve} startingCapital={startingCapital} />
      </div>

      <details className="mb-3 text-xs">
        <summary className="mb-2 cursor-pointer text-text-secondary">Drawdown (underwater plot)</summary>
        <DrawdownChart equityCurve={report.equity_curve} />
      </details>

      <details className="mb-3 text-xs">
        <summary className="mb-2 cursor-pointer text-text-secondary">Distribución de retornos</summary>
        <ReturnHistogram pnlPcts={report.all_trades.map((t) => t.pnl_pct)} />
      </details>

      <details className="mb-3 text-xs">
        <summary className="mb-2 cursor-pointer text-text-secondary">Predicho vs. real (scatter)</summary>
        <ScatterPredictedActual points={report.prediction_regression.scatter} rSquared={report.prediction_regression.r_squared} />
      </details>

      <details className="mb-3 text-xs">
        <summary className="mb-2 cursor-pointer text-text-secondary">
          Win rate por banda de confidence {report.confidence_calibration.correlation !== null && `(correl=${report.confidence_calibration.correlation.toFixed(2)})`}
        </summary>
        <ConfidenceBucketBars buckets={report.confidence_calibration.buckets} />
      </details>

      {temporal_stability && (
        <details className="mb-3 text-xs">
          <summary className="mb-1 cursor-pointer text-text-secondary">Estabilidad temporal (split {temporal_stability.split_date})</summary>
          <p className={temporal_stability.stable === false ? "text-amber-700 dark:text-amber-400" : "text-text-secondary"}>
            {temporal_stability.stable === null
              ? "Muestra insuficiente para comparar"
              : temporal_stability.stable
                ? "Estable entre periodos"
                : "Diverge entre periodos"}
          </p>
          {temporal_stability.warnings.map((w, i) => (
            <Callout key={i} kind="warning" className="mt-1">
              {w}
            </Callout>
          ))}
        </details>
      )}

      {eventTypeRows.length > 0 && (
        <details className="mb-3 text-xs">
          <summary className="mb-2 cursor-pointer text-text-secondary">Por tipo de evento ({eventTypeRows.length})</summary>
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="py-1 pr-2 font-medium text-text-secondary">Tipo</th>
                <th className="py-1 pr-2 text-right font-medium text-text-secondary">N</th>
                <th className="py-1 pr-2 text-right font-medium text-text-secondary">Acierto</th>
                <th className="py-1 pr-2 text-right font-medium text-text-secondary">Retorno medio</th>
              </tr>
            </thead>
            <tbody>
              {eventTypeRows.map((row) => (
                <tr key={row.event_type} className="border-b border-border-subtle">
                  <td className="py-1 pr-2 text-foreground">
                    {eventClassLabel(row.event_type)}
                    {row.insufficient_sample && (
                      <span
                        className="ml-1.5 rounded bg-amber-50 px-1 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:bg-amber-500/10 dark:text-amber-400"
                        title="n < 20 — muestra insuficiente"
                      >
                        n bajo
                      </span>
                    )}
                  </td>
                  <td className="num py-1 pr-2 text-right text-foreground">{row.n_trades}</td>
                  <td className="num py-1 pr-2 text-right text-foreground">{formatFracAsPct(row.win_rate, 1)}</td>
                  <td className="num py-1 pr-2 text-right text-foreground">{formatPct(row.avg_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <details className="text-xs">
        <summary className="mb-2 cursor-pointer text-text-secondary">Top 10 ganadores / perdedores</summary>
        <TradeMiniTable trades={report.top_10_winners} />
        <div className="mt-2" />
        <TradeMiniTable trades={report.top_10_losers} />
      </details>
    </div>
  );
}

function TradeMiniTable({ trades }: { trades: PortfolioVersionReport["top_10_winners"] }) {
  if (trades.length === 0) {
    return <p className="italic text-text-tertiary">Sin operaciones.</p>;
  }
  return (
    <table className="w-full border-collapse text-left">
      <thead>
        <tr className="border-b border-border-subtle">
          <th className="py-1 pr-2 font-medium text-text-secondary">Ticker</th>
          <th className="py-1 pr-2 font-medium text-text-secondary">Salida</th>
          <th className="py-1 pr-2 text-right font-medium text-text-secondary">Resultado</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={`${t.event_id}-${t.exit_date}`} className="border-b border-border-subtle">
            <td className="py-1 pr-2 font-mono text-foreground">{t.ticker ?? "—"}</td>
            <td className="py-1 pr-2 text-text-secondary">{exitReasonLabel(t.exit_reason)}</td>
            <td className="py-1 pr-2 text-right">
              <SignedPct value={t.pnl_pct} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
