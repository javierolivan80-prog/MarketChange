import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import { getAbstentionSummary, getHomeSummary, getPipelineFreshness, getRecentTradeSignals, getRecommendedVersion } from "@/lib/data";
import type { AbstentionCategory } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { WelcomeNote } from "@/components/WelcomeNote";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { NotConfigured } from "@/components/ui/PageState";
import { formatPct, formatNum, formatDate, formatDateTime, formatFracAsPct, formatDrawdown } from "@/lib/format";
import { RELIABILITY, eventClassLabel, versionLabel } from "@/lib/labels";

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

const ABSTENTION_LABELS: Record<AbstentionCategory, string> = {
  priced_in: "El mercado ya lo sabía",
  low_confidence: "El debate no fue concluyente",
  low_ev: "Valor esperado insuficiente tras costes",
  delisting: "Riesgo de exclusión de bolsa",
  contradictory: "Datos contradictorios",
  fda_unconfirmed: "FDA aún no confirmado por la empresa",
  illiquid: "Acción poco líquida",
  other: "Otros motivos",
};

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
  if (!isDatabaseConfigured()) return <NotConfigured active="/" title="Resumen" />;

  const version = await getRecommendedVersion();
  const [summary, recent, freshness, abstention] = await Promise.all([
    getHomeSummary(version),
    getRecentTradeSignals(version, 5),
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
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Resumen</h1>
        <p className={`num text-xs ${isStale ? "text-amber-700 dark:text-amber-400" : "text-text-tertiary"}`}>
          {lastAnalyzed
            ? `Actualizado: ${formatDateTime(freshness.last_analyzed_at)} · ${freshness.analyzed_last_24h} eventos analizados en 24 h${isStale ? " · la actualización lleva retraso" : ""}`
            : "Todavía no hay eventos analizados"}
        </p>
      </header>

      <WelcomeNote />

      {/* Últimas señales — lo accionable va primero */}
      <section className="mb-6">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-base font-semibold text-foreground">Últimas señales operables</h2>
          <Link href="/senales" className="text-sm text-accent-700 hover:underline dark:text-accent-400">
            Ver todas →
          </Link>
        </div>
        {recent.length === 0 ? (
          <p className="border border-dashed border-border-subtle p-4 text-sm text-text-secondary">
            Ningún evento ha superado todavía los filtros para operar. La mayoría de eventos se descartan a propósito: el sistema solo
            señala los que tienen un valor esperado positivo después de costes.
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle border border-border-subtle">
            {recent.map((s) => (
              <li key={s.event_id}>
                <Link
                  href={`/senales/${s.event_id}`}
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

      {/* Lo que NO se ha operado y por qué — tan informativo como lo que sí */}
      {abstention.analyzed > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-base font-semibold text-foreground">Últimos {abstention.days} días</h2>
          <p className="num mb-2 text-sm text-text-secondary">
            {abstention.analyzed} eventos analizados · {abstention.traded} superaron los filtros · {abstention.analyzed - abstention.traded}{" "}
            descartados
          </p>
          {abstention.reasons.length > 0 && (
            <ul className="space-y-1 text-sm">
              {abstention.reasons.map((r) => (
                <li key={r.category} className="flex items-center gap-3">
                  <span className="num w-10 text-right text-text-tertiary">{r.n}</span>
                  <span className="h-1.5 bg-border-strong" style={{ width: `${Math.max(4, (r.n / (abstention.analyzed - abstention.traded)) * 160)}px` }} aria-hidden="true" />
                  <span className="text-text-secondary">{ABSTENTION_LABELS[r.category]}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {summary.portfolio && (summary.portfolio.sample || summary.portfolio.oos_warning) && (
        <div className="mb-3">
          <SampleBadge sample={summary.portfolio.sample} warning={summary.portfolio.oos_warning ?? undefined} />
        </div>
      )}

      {/* Semáforo */}
      {verdict ? (
        <section className={`mb-5 border p-4 ${verdict.color}`}>
          <p className={`mb-1.5 text-lg font-semibold ${verdict.text}`}>{RELIABILITY[summary.validation!.best_decision.option].title}</p>
          <p className="text-sm text-foreground">{RELIABILITY[summary.validation!.best_decision.option].summary}</p>
          <Link href="/funciona" className="mt-1.5 inline-block text-sm text-accent-700 hover:underline dark:text-accent-400">
            Ver por qué →
          </Link>
        </section>
      ) : (
        <section className="mb-5 border border-border-subtle p-4">
          <p className="mb-1.5 text-lg font-semibold text-foreground">Evaluación de fiabilidad en curso</p>
          <p className="text-sm text-text-secondary">
            La calificación aparece cuando hay suficientes eventos para medirla con rigor.
          </p>
        </section>
      )}

      {shown && shown.trade_metrics && shown.equity_metrics && (
        <section>
          <p className="mb-2 text-xs text-text-tertiary">
            Rentabilidad histórica de la estrategia {versionLabel(shownVersion).toLowerCase()}, aplicada a todos los eventos pasados.
          </p>
          <div className="flex flex-wrap gap-y-4">
            <Stat
              label="Acierto"
              value={shown.trade_metrics.win_rate !== null ? `${(shown.trade_metrics.win_rate * 100).toFixed(0)}%` : "—"}
              hint={`en ${shown.trade_metrics.total_trades} operaciones`}
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
            <Stat label="Posiciones abiertas" value={openPositions !== null ? formatNum(openPositions, 0) : "—"} hint="señales en seguimiento" />
          </div>
        </section>
      )}
    </main>
  );
}
