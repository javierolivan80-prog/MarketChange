"use client";

// SignalsFilterForm.tsx — filtra ticker/tipo de evento/señal/rango de fechas/
// confianza mínima. El filtrado real ocurre en el SERVIDOR (SQL en
// getSignalsFeed) — este formulario solo actualiza los searchParams de la
// URL y deja que page.tsx (server component) vuelva a pedir los datos ya
// filtrados; SignalsTable (Tanstack Table) solo ordena/pagina lo que ya
// llegó filtrado, no re-filtra en el cliente.
import { useRouter, useSearchParams } from "next/navigation";
import { useId, useState, useTransition } from "react";
import { Button } from "@/components/ui/Button";
import { eventClassLabel } from "@/lib/labels";
import { useLocale, useT } from "@/components/i18n/LocaleProvider";

const INPUT =
  " border border-border-strong bg-surface px-2 py-1.5 text-sm text-foreground placeholder:text-text-tertiary focus-visible:border-accent-600";
const LABEL = "text-xs font-medium text-text-secondary";

export function SignalsFilterForm({ eventClasses }: { eventClasses: string[] }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [ticker, setTicker] = useState(searchParams.get("ticker") ?? "");
  const [eventClass, setEventClass] = useState(searchParams.get("eventClass") ?? "");
  const [signal, setSignal] = useState(searchParams.get("signal") ?? "");
  const [dateFrom, setDateFrom] = useState(searchParams.get("dateFrom") ?? "");
  const [dateTo, setDateTo] = useState(searchParams.get("dateTo") ?? "");
  const [minConfidence, setMinConfidence] = useState(searchParams.get("minConfidence") ?? "");
  // Filtrar recarga la página en el servidor: sin estado pendiente, el botón
  // no respondía durante esa espera y parecía que no había pasado nada.
  const [pending, startTransition] = useTransition();
  const t = useT();
  const locale = useLocale();

  const SIGNAL_LABELS: Record<string, string> = { LONG: "Long", SHORT: "Short", NO_TRADE: t("Sin operar", "No trade") };
  const active: { key: string; label: string }[] = [
    { key: "ticker", label: `Ticker: ${searchParams.get("ticker") ?? ""}` },
    { key: "eventClass", label: `${t("Evento", "Event")}: ${eventClassLabel(searchParams.get("eventClass") ?? "", locale)}` },
    { key: "signal", label: `${t("Señal", "Signal")}: ${SIGNAL_LABELS[searchParams.get("signal") ?? ""] ?? ""}` },
    { key: "dateFrom", label: `${t("Desde", "From")} ${searchParams.get("dateFrom") ?? ""}` },
    { key: "dateTo", label: `${t("Hasta", "To")} ${searchParams.get("dateTo") ?? ""}` },
    { key: "minConfidence", label: `${t("Confianza", "Confidence")} ≥ ${searchParams.get("minConfidence") ?? ""}%` },
  ].filter((f) => searchParams.get(f.key));

  function removeFilter(key: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete(key);
    ({ ticker: setTicker, eventClass: setEventClass, signal: setSignal, dateFrom: setDateFrom, dateTo: setDateTo, minConfidence: setMinConfidence } as Record<string, (v: string) => void>)[key]?.("");
    startTransition(() => router.push(`/senales${params.size ? `?${params.toString()}` : ""}`));
  }

  const ids = {
    ticker: useId(),
    eventClass: useId(),
    signal: useId(),
    dateFrom: useId(),
    dateTo: useId(),
    minConfidence: useId(),
  };

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const params = new URLSearchParams();
    if (ticker) params.set("ticker", ticker);
    if (eventClass) params.set("eventClass", eventClass);
    if (signal) params.set("signal", signal);
    if (dateFrom) params.set("dateFrom", dateFrom);
    if (dateTo) params.set("dateTo", dateTo);
    if (minConfidence) params.set("minConfidence", minConfidence);
    startTransition(() => router.push(`/senales?${params.toString()}`));
  }

  function clear() {
    setTicker("");
    setEventClass("");
    setSignal("");
    setDateFrom("");
    setDateTo("");
    setMinConfidence("");
    startTransition(() => router.push("/senales"));
  }

  return (
    <form onSubmit={apply} className="mb-5 flex flex-wrap items-end gap-3 border border-border-subtle bg-surface-raised p-3 text-sm">
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.ticker} className={LABEL}>
          Ticker
        </label>
        <input id={ids.ticker} className={`${INPUT} w-28`} value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL" />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.eventClass} className={LABEL}>
          {t("Tipo de evento", "Event type")}
        </label>
        <select id={ids.eventClass} className={INPUT} value={eventClass} onChange={(e) => setEventClass(e.target.value)}>
          <option value="">{t("Todos", "All")}</option>
          {eventClasses.map((c) => (
            <option key={c} value={c}>
              {eventClassLabel(c, locale)}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.signal} className={LABEL}>
          {t("Señal", "Signal")}
        </label>
        <select id={ids.signal} className={INPUT} value={signal} onChange={(e) => setSignal(e.target.value)}>
          <option value="">{t("Todas", "All")}</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
          <option value="NO_TRADE">{t("Sin operar", "No trade")}</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.dateFrom} className={LABEL}>
          {t("Desde", "From")}
        </label>
        <input id={ids.dateFrom} type="date" className={INPUT} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.dateTo} className={LABEL}>
          {t("Hasta", "To")}
        </label>
        <input id={ids.dateTo} type="date" className={INPUT} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.minConfidence} className={LABEL}>
          {t("Confianza mín. (%)", "Min. confidence (%)")}
        </label>
        <input
          id={ids.minConfidence}
          type="number"
          min={0}
          max={100}
          className={`${INPUT} num w-20`}
          value={minConfidence}
          onChange={(e) => setMinConfidence(e.target.value)}
        />
      </div>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={pending}>
          {pending ? t("Filtrando…", "Filtering…") : t("Filtrar", "Filter")}
        </Button>
        <Button variant="ghost" onClick={clear} disabled={pending}>
          {t("Limpiar", "Clear")}
        </Button>
      </div>
      {active.length > 0 && (
        <div className="flex w-full flex-wrap items-center gap-2 border-t border-border-subtle pt-3" aria-label={t("Filtros activos", "Active filters")}>
          <span className="text-xs text-text-secondary">{t("Filtros activos:", "Active filters:")}</span>
          {active.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => removeFilter(f.key)}
              aria-label={`${t("Quitar filtro", "Remove filter")} ${f.label}`}
              className="inline-flex items-center gap-1 border border-accent-500/60 px-2 py-0.5 text-xs text-foreground transition-colors hover:border-accent-500 hover:bg-surface"
            >
              {f.label}
              <span aria-hidden="true" className="text-text-tertiary">×</span>
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
