import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import { getRecommendedVersion, getSignalHistory } from "@/lib/data";
import { Nav } from "@/components/Nav";
import { NotConfigured, NoDataYet } from "@/components/ui/PageState";
import { DirectionBadge, SignedPct } from "@/components/ui/DirectionBadge";
import { formatDate, formatDateTime } from "@/lib/format";
import { eventClassLabel, versionLabel } from "@/lib/labels";

export const dynamic = "force-dynamic";
export const metadata = { title: "Historial" };

// historial/page.tsx — el historial HACIA DELANTE: cada señal que la versión
// recomendada decidió operar, con la hora a la que se calculó (y se avisó por
// Telegram, si se avisó) y su resultado en la simulación en papel sobre los
// precios reales que vinieron después. A diferencia del backtest de /cartera,
// estos números no pueden estar ajustados al pasado: es la prueba que importa.
const STATUS_LABELS: Record<string, string> = {
  OPEN: "Abierta",
  CLOSED_TP: "Objetivo alcanzado",
  CLOSED_SL: "Stop loss",
  CLOSED_TIMEOUT: "Plazo máximo",
};

export default async function HistorialPage() {
  if (!isDatabaseConfigured()) return <NotConfigured active="/historial" title="Historial" />;

  const version = await getRecommendedVersion();
  const history = await getSignalHistory(version, 200);
  if (history.rows.length === 0) {
    return <NoDataYet active="/historial" title="Historial" what="Todavía no se ha emitido ninguna señal operable." />;
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/historial" />
      <header className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Historial</h1>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          Cada señal emitida por la versión {versionLabel(version).toLowerCase()}, con la hora a la que se calculó y lo que pasó después en
          la simulación en papel con precios reales. No es el backtest: son resultados posteriores a cada decisión.
        </p>
      </header>

      <section className="mb-6 flex flex-wrap gap-x-8 gap-y-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">Señales</p>
          <p className="num text-2xl font-semibold text-foreground">{history.rows.length}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">Cerradas</p>
          <p className="num text-2xl font-semibold text-foreground">{history.n_closed}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">Acierto</p>
          <p className="num text-2xl font-semibold text-foreground">{history.win_rate !== null ? `${(history.win_rate * 100).toFixed(0)}%` : "—"}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-text-secondary">Resultado medio</p>
          <p className="text-2xl font-semibold">
            <SignedPct value={history.avg_pnl_pct} />
          </p>
        </div>
      </section>
      {history.n_closed < 20 && (
        <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
          Con menos de 20 señales cerradas, el acierto y el resultado medio todavía dependen mucho del azar.
        </p>
      )}

      <div className="overflow-x-auto border border-border-subtle">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <caption className="sr-only">Historial de señales emitidas y su resultado posterior</caption>
          <thead>
            <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
              <th scope="col" className="py-2 pl-3 pr-4 font-medium">Evento</th>
              <th scope="col" className="py-2 pr-4 font-medium">Ticker</th>
              <th scope="col" className="py-2 pr-4 font-medium">Tipo</th>
              <th scope="col" className="py-2 pr-4 font-medium">Señal</th>
              <th scope="col" className="py-2 pr-4 font-medium">Calculada</th>
              <th scope="col" className="py-2 pr-4 font-medium">Estado</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Resultado</th>
            </tr>
          </thead>
          <tbody>
            {history.rows.map((r) => (
              <tr key={r.event_id} className="border-b border-border-subtle hover:bg-surface-raised">
                <td className="num py-2 pl-3 pr-4 text-text-secondary">{formatDate(r.d0_close_date)}</td>
                <td className="py-2 pr-4">
                  <Link href={`/senales/${r.event_id}`} className="font-mono font-medium text-foreground hover:underline">
                    {r.ticker}
                  </Link>
                </td>
                <td className="py-2 pr-4 text-text-secondary">{eventClassLabel(r.event_class)}</td>
                <td className="py-2 pr-4">
                  <DirectionBadge value={r.direction} />
                </td>
                <td className="num py-2 pr-4 text-xs text-text-tertiary" title={r.notified_at ? `Avisada: ${formatDateTime(r.notified_at)}` : undefined}>
                  {formatDateTime(r.analyzed_at)}
                </td>
                <td className="py-2 pr-4 text-text-secondary">{r.status ? (STATUS_LABELS[r.status] ?? r.status) : "Sin simular"}</td>
                <td className="py-2 pr-3 text-right">
                  <SignedPct value={r.pnl_pct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
