import { isDatabaseConfigured } from "@/lib/db";
import { getEventClasses, getSignalsFeed } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { SignalsFilterForm } from "@/components/SignalsFilterForm";
import { SignalsTable } from "@/components/SignalsTable";

export const dynamic = "force-dynamic";

export default async function SenalesPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  if (!isDatabaseConfigured()) {
    return (
      <main className="mx-auto max-w-3xl p-8">
        <Nav active="/senales" />
        <h1 className="text-2xl font-semibold text-foreground">Señales</h1>
        <p className="mt-2 text-sm text-text-secondary">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const params = await searchParams;
  const [eventClasses, rows] = await Promise.all([
    getEventClasses(),
    getSignalsFeed({
      ticker: params.ticker,
      eventClass: params.eventClass,
      signal: params.signal as "LONG" | "SHORT" | "NO_TRADE" | undefined,
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      minConfidence: params.minConfidence ? Number(params.minConfidence) : undefined,
      limit: 500,
    }),
  ]);

  return (
    <main className="mx-auto max-w-7xl p-6">
      <Nav active="/senales" />
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground font-mono">Señales</h1>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          Cada evento analizado, más reciente primero. Abre una fila para ver el razonamiento completo — quién opina qué, cuántos casos
          parecidos hubo antes, la condición de invalidación de cada estrategia y el filing de origen. Máx. 500 eventos.
        </p>
      </header>

      <SignalsFilterForm eventClasses={eventClasses} />

      {rows.length === 0 ? (
        <p className="rounded border border-dashed border-border-subtle p-6 text-center text-sm text-text-tertiary">
          Sin eventos que cumplan estos filtros.
        </p>
      ) : (
        <SignalsTable rows={rows} />
      )}
    </main>
  );
}
