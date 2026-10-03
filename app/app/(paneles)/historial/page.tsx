import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import { getRecommendedVersion, getSignalHistory } from "@/lib/data";
import { Nav } from "@/components/Nav";
import { NotConfigured, NoDataYet } from "@/components/ui/PageState";
import { DirectionBadge, SignedPct } from "@/components/ui/DirectionBadge";
import { formatDate, formatDateTime } from "@/lib/format";
import { eventClassLabel, versionLabel } from "@/lib/labels";
import { getT } from "@/lib/locale";
import { PnlBar } from "@/components/viz/Bars";

export const dynamic = "force-dynamic";
export async function generateMetadata() {
  const { t } = await getT();
  return { title: t("Historial", "Track record") };
}

// historial/page.tsx — el historial HACIA DELANTE: cada señal que la versión
// recomendada decidió operar, con la hora a la que se calculó (y se avisó por
// Telegram, si se avisó) y su resultado en la simulación en papel sobre los
// precios reales que vinieron después. A diferencia del backtest de /cartera,
// estos números no pueden estar ajustados al pasado: es la prueba que importa.
export default async function HistorialPage() {
  const { locale, t } = await getT();
  const title = t("Historial", "Track record");
  const STATUS_LABELS: Record<string, string> = {
    OPEN: t("Abierta", "Open"),
    CLOSED_TP: t("Objetivo alcanzado", "Target reached"),
    CLOSED_SL: "Stop loss",
    CLOSED_TIMEOUT: t("Plazo máximo", "Maximum holding period"),
  };
  if (!isDatabaseConfigured()) return <NotConfigured active="/historial" title={title} />;

  const version = await getRecommendedVersion();
  const history = await getSignalHistory(version, 200);
  if (history.rows.length === 0) {
    return <NoDataYet active="/historial" title={title} what={t("Todavía no se ha emitido ninguna señal operable.", "No tradable signal has been issued yet.")} />;
  }

  // Escala común de la columna de resultados: la operación con el mayor
  // movimiento (a favor o en contra) llena media barra.
  const maxAbsPnl = Math.max(0, ...history.rows.map((r) => (r.pnl_pct !== null ? Math.abs(r.pnl_pct) : 0)));

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/historial" />
      <header className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          {t(
            `Cada señal emitida por la estrategia ${versionLabel(version, locale).toLowerCase()}, con la hora a la que se publicó y lo que hizo después la acción, medido con precios reales de mercado. Son resultados posteriores a cada señal, no una reconstrucción del pasado.`,
            `Every signal issued by the ${versionLabel(version, locale).toLowerCase()} strategy, with the time it was published and what the stock did afterwards, measured at real market prices. These are results after each signal, not a reconstruction of the past.`,
          )}
        </p>
      </header>

      <section className="mb-6 flex flex-wrap gap-x-8 gap-y-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">{t("Señales", "Signals")}</p>
          <p className="num text-2xl font-semibold text-foreground">{history.rows.length}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">{t("Cerradas", "Closed")}</p>
          <p className="num text-2xl font-semibold text-foreground">{history.n_closed}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">{t("Acierto", "Win rate")}</p>
          <p className="num text-2xl font-semibold text-foreground">{history.win_rate !== null ? `${(history.win_rate * 100).toFixed(0)}%` : "—"}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">{t("Resultado medio", "Average result")}</p>
          <p className="text-2xl font-semibold">
            <SignedPct value={history.avg_pnl_pct} />
          </p>
        </div>
      </section>
      {history.n_closed < 20 && (
        <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
          {t(
            "Con menos de 20 señales cerradas, el acierto y el resultado medio todavía dependen mucho del azar.",
            "With fewer than 20 closed signals, the win rate and average result still depend heavily on chance.",
          )}
        </p>
      )}

      <div className="overflow-x-auto border border-border-subtle">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <caption className="sr-only">{t("Historial de señales emitidas y su resultado posterior", "Issued signals and their subsequent results")}</caption>
          <thead>
            <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
              <th scope="col" className="py-2 pl-3 pr-4 font-medium">{t("Evento", "Event")}</th>
              <th scope="col" className="py-2 pr-4 font-medium">Ticker</th>
              <th scope="col" className="py-2 pr-4 font-medium">{t("Tipo", "Type")}</th>
              <th scope="col" className="py-2 pr-4 font-medium">{t("Señal", "Signal")}</th>
              <th scope="col" className="py-2 pr-4 font-medium">{t("Calculada", "Computed")}</th>
              <th scope="col" className="py-2 pr-4 font-medium">{t("Estado", "Status")}</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">{t("Resultado", "Result")}</th>
            </tr>
          </thead>
          <tbody>
            {history.rows.map((r) => (
              <tr key={r.event_id} className="border-b border-border-subtle hover:bg-surface-raised">
                <td className="num py-2 pl-3 pr-4 text-text-secondary">{formatDate(r.d0_close_date, locale)}</td>
                <td className="py-2 pr-4">
                  <Link href={`/senales/${r.event_id}`} className="font-mono font-medium text-foreground hover:underline">
                    {r.ticker}
                  </Link>
                </td>
                <td className="py-2 pr-4 text-text-secondary">{eventClassLabel(r.event_class, locale)}</td>
                <td className="py-2 pr-4">
                  <DirectionBadge value={r.direction} />
                </td>
                <td className="num py-2 pr-4 text-xs text-text-tertiary" title={r.notified_at ? `${t("Avisada", "Notified")}: ${formatDateTime(r.notified_at, locale)}` : undefined}
                >
                  {formatDateTime(r.analyzed_at, locale)}
                </td>
                <td className="py-2 pr-4 text-text-secondary">{r.status ? (STATUS_LABELS[r.status] ?? r.status) : t("Pendiente", "Pending")}</td>
                <td className="py-2 pr-3 text-right">
                  <span className="inline-flex items-center justify-end gap-2">
                    <PnlBar value={r.pnl_pct} maxAbs={maxAbsPnl} />
                    <span className="inline-block w-16 text-right">
                      <SignedPct value={r.pnl_pct} />
                    </span>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
