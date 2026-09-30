import { isDatabaseConfigured } from "@/lib/db";
import { getLatestQualityScoreDate, getQualityScores, type QualityScoreRow } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { QualityCard } from "@/components/QualityCard";

export const dynamic = "force-dynamic";

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
  if (!isDatabaseConfigured()) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/largo-plazo" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">Largo plazo</h1>
        <p className="text-sm text-text-secondary">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const asOfDate = await getLatestQualityScoreDate();

  if (!asOfDate) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/largo-plazo" />
        <h1 className="mb-4 text-2xl font-semibold text-foreground">Largo plazo</h1>
        <div className="rounded border border-border-subtle p-4">
          <p className="mb-2 font-medium text-foreground">Todavía no se han analizado las cuentas de ninguna empresa.</p>
          <p className="text-sm text-text-secondary">
            El pipeline nocturno descarga las cuentas anuales de la SEC y calcula las notas. Vuelve después de la próxima ejecución.
          </p>
        </div>
      </main>
    );
  }

  const scores: QualityScoreRow[] = await getQualityScores(asOfDate);
  const conNota = scores.filter((s) => s.total_score !== null);
  const sinNota = scores.filter((s) => s.total_score === null);

  return (
    <main className="mx-auto max-w-5xl p-6">
      <Nav active="/largo-plazo" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground font-mono">Largo plazo</h1>
        <p className="num mt-1 text-sm text-text-secondary">
          Empresas ordenadas por calidad de negocio y precio, según sus cuentas anuales auditadas. Horizonte de años, no de días. Datos
          a {asOfDate} · {scores.length} empresas analizadas.
        </p>
      </header>

      {/* Aviso: esto no es asesoramiento */}
      <div className="mb-6 rounded border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800/60 dark:bg-amber-500/10">
        <p className="mb-1 font-medium text-amber-800 dark:text-amber-400">Esto no es una recomendación de inversión</p>
        <p className="text-text-secondary">
          Es un resumen estructurado de cuentas públicas, calculado automáticamente. Una nota alta significa que la empresa cumple
          criterios clásicos de calidad y valoración — no que su acción vaya a subir. Los criterios son convenciones del análisis
          fundamental, no reglas optimizadas sobre este histórico.
        </p>
      </div>

      {/* Cómo se lee la nota */}
      <section className="mb-6 rounded border border-border-subtle p-4">
        <p className="mb-2 text-sm font-medium text-foreground">Los 5 criterios, en cristiano</p>
        <ul className="space-y-1 text-xs text-text-secondary">
          <li>
            <strong className="text-foreground">Rentabilidad</strong> — ¿cuánto gana por cada euro de capital propio? (más es mejor)
          </li>
          <li>
            <strong className="text-foreground">Solidez financiera</strong> — ¿cuánta deuda arrastra? (menos es mejor)
          </li>
          <li>
            <strong className="text-foreground">Calidad del beneficio</strong> — ¿el beneficio contable se convierte en caja real? (si
            no, mala señal)
          </li>
          <li>
            <strong className="text-foreground">Crecimiento</strong> — ¿vende más cada año?
          </li>
          <li>
            <strong className="text-foreground">Precio</strong> — ¿cuánto se paga por cada euro de beneficio? (el PER; menos es mejor)
          </li>
        </ul>
        <p className="mt-2 text-xs text-text-tertiary">
          Si un criterio no se puede calcular porque la empresa no publica ese dato, <strong className="text-text-secondary">no puntúa cero</strong>:
          se excluye y los demás se reparten el peso. Penalizar la falta de información castigaría justo a las empresas peor
          documentadas.
        </p>
      </section>

      {/* Ranking */}
      {conNota.length === 0 ? (
        <p className="mb-6 text-sm italic text-text-tertiary">Ninguna empresa tiene datos suficientes para una nota todavía.</p>
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
          <h2 className="mb-2 text-sm font-semibold text-text-secondary">Sin datos suficientes ({sinNota.length})</h2>
          <p className="mb-2 text-xs text-text-tertiary">
            Estas empresas no publican (todavía) suficientes magnitudes en sus cuentas para calcular ni un criterio. No significa que
            sean malas — significa que no se sabe.
          </p>
          <p className="font-mono text-xs text-text-tertiary">{sinNota.map((s) => s.ticker).join(" · ")}</p>
        </section>
      )}
    </main>
  );
}
