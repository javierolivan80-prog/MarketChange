import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import {
  getLatestPortfolioRunBatchTag,
  getPortfolioReport,
  getLatestPaperTradingRunBatchTag,
  getPaperTradingReport,
  getLatestValidationRunBatchTag,
  getValidationReport,
  getRecentTradeSignals,
  getPipelineFreshness,
} from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { NotConfigured } from "@/components/ui/PageState";
import { formatPct, formatNum, formatDate, formatDateTime, formatFracAsPct, formatDrawdown } from "@/lib/format";
import { eventClassLabel, versionLabel } from "@/lib/labels";

export const dynamic = "force-dynamic";

// page.tsx (Inicio) — responde, en este orden, las tres preguntas de quien
// abre el panel: ¿está vivo? (última actualización), ¿qué ha pasado? (últimas
// señales operables) y ¿me puedo fiar? (veredicto del motor de validación,
// pipeline/validation/decision.py, en lenguaje llano). El detalle completo
// vive en /senales, /cartera y /funciona.
//
// Mismo criterio de color que /funciona: A/B/C es el veredicto del propio
// motor de validación, no P&L ni dirección.
const VERDICT_COPY: Record<string, { title: string; color: string; text: string }> = {
  A: { title: "El sistema funciona bien en las pruebas", color: "border-emerald-300 bg-emerald-50 dark:border-emerald-800/60 dark:bg-emerald-500/10", text: "text-emerald-800 dark:text-emerald-400" },
  B: { title: "Funciona, pero todavía con reservas", color: "border-amber-300 bg-amber-50 dark:border-amber-800/60 dark:bg-amber-500/10", text: "text-amber-800 dark:text-amber-400" },
  C: { title: "Todavía no funciona de forma fiable", color: "border-rose-300 bg-rose-50 dark:border-rose-800/60 dark:bg-rose-500/10", text: "text-rose-800 dark:text-rose-400" },
};

// Más de 2 días sin analizar nada (fin de semana incluido) = algo va mal.
const STALE_AFTER_HOURS = 60;

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-[140px] flex-1 border-l border-border-subtle pl-3">
      <p className="mb-1 font-mono text-xs uppercase tracking-wide text-text-secondary">{label}</p>
      <p className="num text-2xl font-semibold text-foreground">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

export default async function InicioPage() {
  if (!isDatabaseConfigured()) return <NotConfigured active="/" title="Resumen" />;

  const [portfolioTag, paperTag, validationTag, recent, freshness] = await Promise.all([
    getLatestPortfolioRunBatchTag(),
    getLatestPaperTradingRunBatchTag(),
    getLatestValidationRunBatchTag(),
    getRecentTradeSignals(5),
    getPipelineFreshness(),
  ]);

  const [portfolioReport, paperReport, validationReport] = await Promise.all([
    portfolioTag ? getPortfolioReport(portfolioTag) : Promise.resolve(null),
    paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null),
    validationTag ? getValidationReport(validationTag) : Promise.resolve(null),
  ]);

  // Las cifras de abajo son de la versión que el propio motor de validación
  // recomienda — antes se mostraba siempre BALANCED, aunque el veredicto de
  // justo encima hablara de otra versión.
  const shownVersion = validationReport?.best_version ?? "BALANCED";
  const shown = portfolioReport?.versions?.[shownVersion];
  const verdict = validationReport ? VERDICT_COPY[validationReport.best_decision.option] : null;
  const openPositions = paperReport ? Object.values(paperReport.versions).reduce((sum, v) => sum + v.n_open_positions, 0) : null;

  const lastAnalyzed = freshness.last_analyzed_at ? new Date(freshness.last_analyzed_at) : null;
  const hoursSince = lastAnalyzed ? (Date.now() - lastAnalyzed.getTime()) / 3_600_000 : null;
  const isStale = hoursSince !== null && hoursSince > STALE_AFTER_HOURS;

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/" />

      <header className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="font-mono text-2xl font-semibold tracking-tight text-foreground">Resumen</h1>
        <p className={`num text-xs ${isStale ? "text-amber-700 dark:text-amber-400" : "text-text-tertiary"}`}>
          {lastAnalyzed
            ? `Último análisis: ${formatDateTime(freshness.last_analyzed_at)} · ${freshness.analyzed_last_24h} eventos en 24 h${isStale ? " · el pipeline no ha corrido recientemente" : ""}`
            : "El pipeline todavía no ha analizado ningún evento"}
        </p>
      </header>

      {/* Últimas señales — lo accionable va primero */}
      <section className="mb-6">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-base font-semibold text-foreground">Últimas señales operables</h2>
          <Link href="/senales" className="text-sm text-accent-700 hover:underline dark:text-accent-400">
            Ver todas →
          </Link>
        </div>
        {recent.length === 0 ? (
          <p className="rounded border border-dashed border-border-subtle p-4 text-sm text-text-secondary">
            Ningún evento ha superado todavía los filtros para operar. La mayoría de eventos se descartan a propósito: el sistema solo
            señala los que tienen un valor esperado positivo después de costes.
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle rounded border border-border-subtle">
            {recent.map((s) => (
              <li key={s.event_id}>
                <Link
                  href={`/senales?ticker=${encodeURIComponent(s.ticker)}`}
                  className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-0.5 px-3 py-2 text-sm hover:bg-surface-raised sm:grid-cols-[6rem_5rem_auto_1fr_auto]"
                >
                  <span className="num text-text-tertiary">{formatDate(s.d0_close_date)}</span>
                  <span className="font-mono font-medium text-foreground">{s.ticker}</span>
                  <DirectionBadge value={s.direction} className="justify-self-end sm:justify-self-start" />
                  <span className="col-span-2 truncate text-text-secondary sm:col-span-1">{eventClassLabel(s.event_class)}</span>
                  <span className="num justify-self-end text-xs text-text-tertiary">
                    {s.confidence.toFixed(0)}% · EV {formatFracAsPct(s.ev_balanced)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {portfolioReport && (portfolioReport.sample || portfolioReport.oos_warning) && (
        <div className="mb-3">
          <SampleBadge sample={portfolioReport.sample} warning={portfolioReport.oos_warning} />
        </div>
      )}

      {/* Semáforo */}
      {verdict ? (
        <section className={`mb-5 rounded border p-4 ${verdict.color}`}>
          <p className={`mb-1.5 text-lg font-semibold ${verdict.text}`}>{verdict.title}</p>
          <p className="text-sm text-foreground">{validationReport!.best_decision.recommendation}</p>
          <Link href="/funciona" className="mt-1.5 inline-block text-sm text-accent-700 hover:underline dark:text-accent-400">
            Ver por qué →
          </Link>
        </section>
      ) : (
        <section className="mb-5 rounded border border-border-subtle p-4">
          <p className="mb-1.5 text-lg font-semibold text-foreground">Todavía acumulando datos</p>
          <p className="text-sm text-text-secondary">
            Hacen falta más eventos reales antes de poder decir con confianza si el sistema funciona. Al principio es normal.
          </p>
        </section>
      )}

      {shown && (
        <section>
          <p className="mb-2 text-xs text-text-tertiary">
            Backtest de la versión {versionLabel(shownVersion).toLowerCase()} — simulado, sin dinero real.
          </p>
          <div className="flex flex-wrap gap-y-4">
            <Stat
              label="Acierto"
              value={shown.trade_metrics.win_rate !== null ? `${(shown.trade_metrics.win_rate * 100).toFixed(0)}%` : "—"}
              hint={`de ${shown.trade_metrics.total_trades} operaciones`}
            />
            <Stat
              label="Resultado acumulado"
              value={shown.equity_metrics.total_return !== null ? formatPct(shown.equity_metrics.total_return * 100, 1) : "—"}
              hint="costes incluidos"
            />
            <Stat
              label="Peor caída"
              value={formatDrawdown(shown.equity_metrics.max_drawdown)}
              hint="pérdida temporal máxima"
            />
            <Stat label="Abiertas en papel" value={openPositions !== null ? formatNum(openPositions, 0) : "—"} hint="simulación de esta semana" />
          </div>
        </section>
      )}
    </main>
  );
}
