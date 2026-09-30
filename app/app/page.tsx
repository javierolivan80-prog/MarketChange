import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import {
  getLatestPortfolioRunBatchTag,
  getPortfolioReport,
  getLatestPaperTradingRunBatchTag,
  getPaperTradingReport,
  getLatestValidationRunBatchTag,
  getValidationReport,
} from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { SampleBadge } from "@/components/ui/SampleBadge";
import { formatPct, formatNum } from "@/lib/format";

export const dynamic = "force-dynamic";

// page.tsx (Inicio) — reescrita como pantalla "semáforo": la pregunta que
// alguien sin conocer el proyecto se hace al abrir esto es "¿funciona esto
// o no?", no "dame los 40 números del backtest". Esa respuesta ya la
// calcula pipeline/validation/decision.py (GREENLIGHT/YELLOWLIGHT/REDLIGHT)
// — aquí solo se traduce a lenguaje llano y se pone delante de todo lo
// demás. El detalle completo sigue disponible en /funciona y /cartera para
// quien quiera profundizar.
//
// Mismo criterio de color que /funciona: A/B/C es el veredicto del propio
// motor de validación, no P&L ni dirección — usa el mismo verde/ámbar/rojo
// que el resto de veredictos "¿te puedes fiar de esto?", nunca emoji.
const VERDICT_COPY: Record<string, { title: string; color: string; text: string }> = {
  A: { title: "El sistema funciona bien en las pruebas", color: "border-emerald-300 bg-emerald-50 dark:border-emerald-800/60 dark:bg-emerald-500/10", text: "text-emerald-800 dark:text-emerald-400" },
  B: { title: "Funciona, pero todavía con reservas", color: "border-amber-300 bg-amber-50 dark:border-amber-800/60 dark:bg-amber-500/10", text: "text-amber-800 dark:text-amber-400" },
  C: { title: "Todavía no funciona de forma fiable", color: "border-rose-300 bg-rose-50 dark:border-rose-800/60 dark:bg-rose-500/10", text: "text-rose-800 dark:text-rose-400" },
};

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-[160px] flex-1 rounded border border-border-subtle p-4">
      <p className="mb-1 text-xs text-text-secondary">{label}</p>
      <p className="num text-2xl font-semibold text-foreground">{value}</p>
      {hint && <p className="mt-1 text-xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

function QuickLink({ href, title, description }: { href: string; title: string; description: string }) {
  return (
    <Link href={href} className="rounded border border-border-subtle p-4 hover:border-accent-600 dark:hover:border-accent-400">
      <p className="mb-1 font-medium text-foreground">{title} →</p>
      <p className="text-xs text-text-secondary">{description}</p>
    </Link>
  );
}

export default async function InicioPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">MarketChange — Panel</h1>
        <div className="rounded border border-amber-300 bg-amber-50 p-4 dark:border-amber-800/60 dark:bg-amber-500/10">
          <p className="mb-2 font-medium text-amber-800 dark:text-amber-400">DATABASE_URL no está configurada.</p>
          <p className="text-sm text-text-secondary">
            Ver <code className="font-mono">RUNBOOK.md</code> para provisionar una base gratuita y configurar la variable de entorno.
          </p>
        </div>
      </main>
    );
  }

  const [portfolioTag, paperTag, validationTag] = await Promise.all([
    getLatestPortfolioRunBatchTag(),
    getLatestPaperTradingRunBatchTag(),
    getLatestValidationRunBatchTag(),
  ]);

  if (!portfolioTag) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">MarketChange — Panel</h1>
        <div className="rounded border border-border-subtle p-4">
          <p className="mb-2 font-medium text-foreground">Todavía no hay ningún resultado calculado.</p>
          <p className="text-sm text-text-secondary">
            El pipeline nocturno todavía no ha corrido, o acaba de empezar a recoger datos. Vuelve en unas horas.
          </p>
        </div>
      </main>
    );
  }

  const [portfolioReport, paperReport, validationReport] = await Promise.all([
    getPortfolioReport(portfolioTag),
    paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null),
    validationTag ? getValidationReport(validationTag) : Promise.resolve(null),
  ]);

  const balanced = portfolioReport?.versions.BALANCED;
  const verdict = validationReport ? VERDICT_COPY[validationReport.best_decision.option] : null;

  const openPositions = paperReport ? Object.values(paperReport.versions).reduce((sum, v) => sum + v.n_open_positions, 0) : 0;

  return (
    <main className="mx-auto max-w-5xl p-6">
      <Nav active="/" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">MarketChange — Panel</h1>
        <p className="mt-1 text-sm text-text-secondary">Resumen de un vistazo. Todo lo de aquí tiene el detalle completo en las otras pestañas.</p>
      </header>

      {portfolioReport && (portfolioReport.sample || portfolioReport.oos_warning) && (
        <div className="mb-4">
          <SampleBadge sample={portfolioReport.sample} warning={portfolioReport.oos_warning} />
        </div>
      )}

      {/* Semáforo */}
      {verdict ? (
        <section className={`mb-6 rounded border-2 p-5 ${verdict.color}`}>
          <p className={`mb-2 text-xl font-semibold ${verdict.text}`}>{verdict.title}</p>
          <p className="text-sm text-foreground">{validationReport!.best_decision.recommendation}</p>
          <Link href="/funciona" className="mt-2 inline-block text-sm text-accent-700 underline decoration-dotted hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300">
            Ver por qué →
          </Link>
        </section>
      ) : (
        <section className="mb-6 rounded border-2 border-border-subtle p-5">
          <p className="mb-2 text-xl font-semibold text-foreground">Todavía acumulando datos</p>
          <p className="text-sm text-text-secondary">
            Hacen falta más días de eventos reales antes de poder decir con confianza si el sistema funciona. Esto es normal al
            principio — no es un fallo.
          </p>
        </section>
      )}

      {/* Stats clave, en lenguaje llano */}
      <section className="mb-6 flex flex-wrap gap-4">
        <StatCard
          label="Aciertos históricos"
          value={balanced?.trade_metrics.win_rate !== null && balanced?.trade_metrics.win_rate !== undefined ? formatPct(balanced.trade_metrics.win_rate * 100, 0) : "—"}
          hint={`de ${balanced?.trade_metrics.total_trades ?? 0} operaciones simuladas`}
        />
        <StatCard
          label="Resultado acumulado"
          value={
            balanced?.equity_metrics.total_return !== null && balanced?.equity_metrics.total_return !== undefined
              ? formatPct(balanced.equity_metrics.total_return * 100, 1)
              : "—"
          }
          hint="sobre el capital simulado, coste incluido"
        />
        <StatCard label="Operaciones abiertas ahora" value={formatNum(openPositions, 0)} hint="simuladas esta semana (papel, sin dinero real)" />
        <StatCard
          label="Peor caída sufrida"
          value={balanced?.equity_metrics.max_drawdown !== null && balanced?.equity_metrics.max_drawdown !== undefined ? formatPct(balanced.equity_metrics.max_drawdown * 100, 1) : "—"}
          hint="máxima pérdida temporal en la simulación"
        />
      </section>

      {/* Accesos rápidos */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <QuickLink href="/senales" title="Ver señales" description="Cada evento detectado, con su análisis completo y por qué se opera o no." />
        <QuickLink href="/cartera" title="Ver resultados" description="Cómo le ha ido históricamente y qué está pasando esta semana." />
        <QuickLink href="/como-funciona" title="¿Cómo funciona esto?" description="Explicación paso a paso del motor, sin necesitar conocer el proyecto de antes." />
      </section>
    </main>
  );
}
