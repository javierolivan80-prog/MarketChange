import { isDatabaseConfigured } from "@/lib/db";
import { getEventClasses, getRecommendedVersion, getSignalsFeed } from "@/lib/data";
import { VERSION_LABELS } from "@/lib/labels";
import { Nav } from "@/components/Nav";
import { SignalsFilterForm } from "@/components/SignalsFilterForm";
import { SignalsTable } from "@/components/SignalsTable";
import { NotConfigured } from "@/components/ui/PageState";

export const dynamic = "force-dynamic";
export const metadata = { title: "Señales" };

// Los searchParams llegan tal cual de la URL (también desde el enlace de
// Telegram): un valor mal formado no debe llegar a Postgres como error 500.
const SIGNALS = new Set(["LONG", "SHORT", "NO_TRADE"]);
const isDate = (v: string | undefined) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);

export default async function SenalesPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  if (!isDatabaseConfigured()) return <NotConfigured active="/senales" title="Señales" />;

  const params = await searchParams;
  const version = await getRecommendedVersion();
  const minConfidence = params.minConfidence ? Number(params.minConfidence) : undefined;
  const [eventClasses, rows] = await Promise.all([
    getEventClasses(),
    getSignalsFeed({
      ticker: params.ticker?.slice(0, 12),
      eventClass: params.eventClass,
      signal: SIGNALS.has(params.signal ?? "") ? (params.signal as "LONG" | "SHORT" | "NO_TRADE") : undefined,
      dateFrom: isDate(params.dateFrom),
      dateTo: isDate(params.dateTo),
      minConfidence: minConfidence !== undefined && Number.isFinite(minConfidence) ? minConfidence : undefined,
      limit: 500,
      version,
    }),
  ]);

  return (
    <main className="mx-auto max-w-7xl px-4 py-4 sm:px-6">
      <Nav active="/senales" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Señales</h1>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          Cada evento analizado, el más reciente primero. Despliega una fila para ver el debate a favor y en contra, los casos
          parecidos del pasado y por qué cada estrategia opera o no. La columna Señal es la decisión de la estrategia recomendada
          ({VERSION_LABELS[version].toLowerCase()}). Hasta 500 eventos.
        </p>
      </header>

      <SignalsFilterForm eventClasses={eventClasses} />

      {rows.length === 0 ? (
        <p className="border border-dashed border-border-subtle p-6 text-center text-sm text-text-tertiary">
          Sin eventos que cumplan estos filtros.
        </p>
      ) : (
        <SignalsTable rows={rows} />
      )}
    </main>
  );
}
