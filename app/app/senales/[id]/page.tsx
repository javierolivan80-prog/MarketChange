import { notFound } from "next/navigation";
import { isDatabaseConfigured } from "@/lib/db";
import { getRecommendedVersion, getSignalDetail } from "@/lib/data";
import { Nav } from "@/components/Nav";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { SignalAnalysis } from "@/components/SignalAnalysis";
import { NotConfigured } from "@/components/ui/PageState";
import { DirectionBadge } from "@/components/ui/DirectionBadge";
import { formatDate } from "@/lib/format";
import { eventClassLabel, sourceLabel } from "@/lib/labels";

export const dynamic = "force-dynamic";

// senales/[id] — una señal, con URL propia: es a donde enlaza el aviso de
// Telegram, y se puede compartir o guardar.
export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isDatabaseConfigured() || !/^\d+$/.test(id)) return { title: "Señal" };
  const detail = await getSignalDetail(Number(id), await getRecommendedVersion());
  return { title: detail ? `${detail.ticker} · ${eventClassLabel(detail.event_class)}` : "Señal" };
}

export default async function SignalPage({ params }: { params: Promise<{ id: string }> }) {
  if (!isDatabaseConfigured()) return <NotConfigured active="/senales" title="Señal" />;
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();

  const recommended = await getRecommendedVersion();
  const detail = await getSignalDetail(Number(id), recommended);
  if (!detail) notFound();

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/senales" />
      <Breadcrumbs items={[{ label: "Resumen", href: "/" }, { label: "Señales", href: "/senales" }, { label: detail.ticker }]} />
      <header className="mb-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{detail.ticker}</h1>
          <DirectionBadge value={detail.signal} />
        </div>
        <p className="mt-1 text-sm text-text-secondary">
          {detail.company_name && `${detail.company_name} · `}
          {eventClassLabel(detail.event_class)} · {sourceLabel(detail.source)} · <span className="num">{formatDate(detail.d0_close_date)}</span>
        </p>
      </header>
      {/* Índice de la ficha: es larga, y sin él no se sabía qué venía después
          del primer bloque ni dónde estaba cada parte. */}
      <nav aria-label="En esta página" className="mb-4 flex flex-wrap gap-x-4 gap-y-1 border-b border-border-subtle pb-2 text-sm">
        {detail.technical && (
          <a href="#plan" className="text-text-secondary hover:text-foreground">
            Plan técnico
          </a>
        )}
        <a href="#analisis" className="text-text-secondary hover:text-foreground">
          Análisis del evento
        </a>
        <a href="#versiones" className="text-text-secondary hover:text-foreground">
          Las tres estrategias
        </a>
        {detail.source_url && (
          <a href={detail.source_url} target="_blank" rel="noopener noreferrer" className="text-text-secondary hover:text-foreground">
            Filing original ↗
          </a>
        )}
      </nav>
      <SignalAnalysis detail={detail} recommended={recommended} />
    </main>
  );
}
