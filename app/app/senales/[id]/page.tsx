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
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

// senales/[id] — una señal, con URL propia: es a donde enlaza el aviso de
// Telegram, y se puede compartir o guardar.
export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { locale, t } = await getT();
  if (!isDatabaseConfigured() || !/^\d+$/.test(id)) return { title: t("Señal", "Signal") };
  const detail = await getSignalDetail(Number(id), await getRecommendedVersion());
  return { title: detail ? `${detail.ticker} · ${eventClassLabel(detail.event_class, locale)}` : t("Señal", "Signal") };
}

export default async function SignalPage({ params }: { params: Promise<{ id: string }> }) {
  const { locale, t } = await getT();
  if (!isDatabaseConfigured()) return <NotConfigured active="/senales" title={t("Señal", "Signal")} />;
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();

  const recommended = await getRecommendedVersion();
  const detail = await getSignalDetail(Number(id), recommended);
  if (!detail) notFound();

  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6">
      <Nav active="/senales" />
      <Breadcrumbs
        items={[{ label: t("Resumen", "Overview"), href: "/" }, { label: t("Señales", "Signals"), href: "/senales" }, { label: detail.ticker }]}
      />
      <header className="mb-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{detail.ticker}</h1>
          <DirectionBadge value={detail.signal} />
        </div>
        <p className="mt-1 text-sm text-text-secondary">
          {detail.company_name && `${detail.company_name} · `}
          {eventClassLabel(detail.event_class, locale)} · {sourceLabel(detail.source)} ·{" "}
          <span className="num">{formatDate(detail.d0_close_date, locale)}</span>
        </p>
      </header>
      {/* Índice de la ficha: es larga, y sin él no se sabía qué venía después
          del primer bloque ni dónde estaba cada parte. */}
      <nav aria-label={t("En esta página", "On this page")} className="mb-4 flex flex-wrap gap-x-4 gap-y-1 border-b border-border-subtle pb-2 text-sm">
        {detail.technical && (
          <a href="#plan" className="text-text-secondary hover:text-foreground">
            {t("Plan técnico", "Technical plan")}
          </a>
        )}
        <a href="#analisis" className="text-text-secondary hover:text-foreground">
          {t("Análisis del evento", "Event analysis")}
        </a>
        <a href="#versiones" className="text-text-secondary hover:text-foreground">
          {t("Las tres estrategias", "The three strategies")}
        </a>
        {detail.source_url && (
          <a href={detail.source_url} target="_blank" rel="noopener noreferrer" className="text-text-secondary hover:text-foreground">
            {t("Filing original ↗", "Original filing ↗")}
          </a>
        )}
      </nav>
      <SignalAnalysis detail={detail} recommended={recommended} locale={locale} />
    </main>
  );
}
