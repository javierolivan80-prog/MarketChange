import { isDatabaseConfigured } from "@/lib/db";
import { getLatestQualityScoreDate, getQualityScores } from "@/lib/data";
import type { QualityScoreRow } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { QualityCard } from "@/components/QualityCard";
import { NoDataYet, NotConfigured } from "@/components/ui/PageState";
import { getT } from "@/lib/locale";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";
export async function generateMetadata() {
  const { t } = await getT();
  return { title: t("Largo plazo", "Long term") };
}


// largo-plazo/page.tsx — la pestaña de análisis fundamental. A diferencia del
// resto del dashboard (que gira alrededor de eventos y del corto plazo), esto
// ordena empresas por calidad del negocio y precio, con horizonte de años.
//
// Todas las explicaciones que se muestran vienen redactadas desde Python
// (quality_score.py) — aquí no se reformula ni se recalcula nada, por el
// mismo motivo que el resto del dashboard: una sola fuente de verdad por
// número y por frase.
//
// Nota de color: la nota de calidad NO es P&L ni dirección (long/short), así
// que no usa verde/rojo (reservados a eso en toda la app) — se codifica con
// intensidad del acento único, de más pálido (nota baja) a más saturado
// (nota alta).

function scoreColorClass(score: number | null): string {
  if (score === null) return "text-text-tertiary";
  if (score >= 75) return "text-accent-700 dark:text-accent-400";
  if (score >= 55) return "text-foreground";
  if (score >= 35) return "text-text-secondary";
  return "text-text-tertiary";
}

export default async function LargoPlazoPage() {
  const { locale, t } = await getT();
  const title = t("Largo plazo", "Long term");
  if (!isDatabaseConfigured()) return <NotConfigured active="/largo-plazo" title={title} />;

  const asOfDate = await getLatestQualityScoreDate();

  if (!asOfDate)
    return (
      <NoDataYet active="/largo-plazo" title={title} what={t("Todavía no se han analizado las cuentas de ninguna empresa.", "No company accounts have been analysed yet.")} />
    );

  const scores: QualityScoreRow[] = await getQualityScores(asOfDate);
  const conNota = scores.filter((s) => s.total_score !== null);
  const sinNota = scores.filter((s) => s.total_score === null);

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/largo-plazo" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        <p className="num mt-1 text-sm text-text-secondary">
          {t(
            `Empresas ordenadas por calidad de negocio y precio, según sus cuentas anuales auditadas. Horizonte de años, no de días. Datos a ${formatDate(asOfDate, locale)} · ${scores.length} empresas analizadas.`,
            `Companies ranked by business quality and price, based on their audited annual accounts. A horizon of years, not days. Data as of ${formatDate(asOfDate, locale)} · ${scores.length} companies analysed.`,
          )}
        </p>
      </header>

      {/* Aviso: esto no es asesoramiento */}
      <div className="mb-6 border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800/60 dark:bg-amber-500/10">
        <p className="mb-1 font-medium text-amber-800 dark:text-amber-400">{t("Esto no es una recomendación de inversión", "This is not an investment recommendation")}</p>
        <p className="text-text-secondary">
          {t(
            "Es un resumen estructurado de cuentas públicas, calculado automáticamente. Una nota alta significa que la empresa cumple criterios clásicos de calidad y valoración — no que su acción vaya a subir. Los criterios son convenciones del análisis fundamental, no reglas optimizadas sobre este histórico.",
            "It is a structured, automatically calculated summary of public accounts. A high score means the company meets classic quality and valuation criteria — not that its stock will rise. The criteria are conventions of fundamental analysis, not rules optimised on this history.",
          )}
        </p>
      </div>

      {/* Cómo se lee la nota */}
      <section className="mb-6 border border-border-subtle p-4">
        <p className="mb-2 text-sm font-medium text-foreground">{t("Cómo se calcula la nota", "How the score is calculated")}</p>
        <ul className="space-y-1 text-xs text-text-secondary">
          {[
            [t("Rentabilidad", "Profitability"), t("¿cuánto gana por cada dólar de capital propio? (más es mejor)", "how much does it earn per dollar of equity? (more is better)")],
            [t("Solidez financiera", "Financial strength"), t("¿cuánta deuda arrastra? (menos es mejor)", "how much debt does it carry? (less is better)")],
            [
              t("Calidad del beneficio", "Earnings quality"),
              t("¿el beneficio contable se convierte en caja real? (si no, mala señal)", "does accounting profit turn into real cash? (if not, a bad sign)"),
            ],
            [t("Crecimiento", "Growth"), t("¿vende más cada año?", "does it sell more every year?")],
            [t("Precio", "Price"), t("¿cuánto se paga por cada dólar de beneficio? (el PER; menos es mejor)", "how much do you pay per dollar of earnings? (the P/E; less is better)")],
          ].map(([name, text]) => (
            <li key={name}>
              <strong className="text-foreground">{name}</strong> — {text}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-text-tertiary">
          {t("Si un criterio no se puede calcular porque la empresa no publica ese dato, ", "If a criterion cannot be calculated because the company does not publish that figure, ")}
          <strong className="text-text-secondary">{t("no puntúa cero", "it does not score zero")}</strong>
          {t(
            ": se excluye y los demás se reparten el peso. Penalizar la falta de información castigaría justo a las empresas peor documentadas.",
            ": it is excluded and the rest share its weight. Penalising missing information would punish precisely the least-documented companies.",
          )}
        </p>
      </section>

      {/* Ranking */}
      {conNota.length === 0 ? (
        <p className="mb-6 text-sm italic text-text-tertiary">
          {t("Ninguna empresa tiene datos suficientes para una nota todavía.", "No company has enough data for a score yet.")}
        </p>
      ) : (
        <section className="mb-8 space-y-3">
          {conNota.map((row, i) => (
            <QualityCard key={row.cik} row={row} rank={i + 1} scoreColorClass={scoreColorClass(row.total_score)} />
          ))}
        </section>
      )}

      {/* Sin datos suficientes — visibles, no escondidas */}
      {sinNota.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-text-secondary">
            {t("Sin datos suficientes", "Not enough data")} ({sinNota.length})
          </h2>
          <p className="mb-2 text-xs text-text-tertiary">
            {t(
              "Estas empresas no publican (todavía) suficientes magnitudes en sus cuentas para calcular ni un criterio. No significa que sean malas — significa que no se sabe.",
              "These companies do not (yet) publish enough figures in their accounts to calculate a single criterion. It does not mean they are bad — it means we cannot tell.",
            )}
          </p>
          <p className="font-mono text-xs text-text-tertiary">{sinNota.map((s) => s.ticker).join(" · ")}</p>
        </section>
      )}
    </main>
  );
}
