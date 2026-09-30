"use client";

// SignalsFilterForm.tsx — filtra ticker/tipo de evento/señal/rango de fechas/
// confianza mínima. El filtrado real ocurre en el SERVIDOR (SQL en
// getSignalsFeed) — este formulario solo actualiza los searchParams de la
// URL y deja que page.tsx (server component) vuelva a pedir los datos ya
// filtrados; SignalsTable (Tanstack Table) solo ordena/pagina lo que ya
// llegó filtrado, no re-filtra en el cliente.
import { useRouter, useSearchParams } from "next/navigation";
import { useId, useState } from "react";

const INPUT =
  "rounded border border-border-strong bg-surface px-2 py-1.5 text-sm text-foreground placeholder:text-text-tertiary focus-visible:border-accent-600";
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
    router.push(`/senales?${params.toString()}`);
  }

  function clear() {
    setTicker("");
    setEventClass("");
    setSignal("");
    setDateFrom("");
    setDateTo("");
    setMinConfidence("");
    router.push("/senales");
  }

  return (
    <form onSubmit={apply} className="mb-5 flex flex-wrap items-end gap-3 rounded border border-border-subtle bg-surface-raised p-3 text-sm">
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.ticker} className={LABEL}>
          Ticker
        </label>
        <input id={ids.ticker} className={`${INPUT} w-28`} value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL" />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.eventClass} className={LABEL}>
          Tipo de evento
        </label>
        <select id={ids.eventClass} className={INPUT} value={eventClass} onChange={(e) => setEventClass(e.target.value)}>
          <option value="">Todos</option>
          {eventClasses.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.signal} className={LABEL}>
          Señal
        </label>
        <select id={ids.signal} className={INPUT} value={signal} onChange={(e) => setSignal(e.target.value)}>
          <option value="">Todas</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
          <option value="NO_TRADE">Sin operar</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.dateFrom} className={LABEL}>
          Desde
        </label>
        <input id={ids.dateFrom} type="date" className={INPUT} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.dateTo} className={LABEL}>
          Hasta
        </label>
        <input id={ids.dateTo} type="date" className={INPUT} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.minConfidence} className={LABEL}>
          Confianza mín. (%)
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
        <button type="submit" className="rounded bg-accent-600 px-3 py-1.5 font-mono text-sm font-medium uppercase tracking-wide text-white hover:bg-accent-700">
          Filtrar
        </button>
        <button type="button" onClick={clear} className="px-2 py-1.5 font-mono text-sm uppercase tracking-wide text-text-secondary hover:text-foreground">
          Limpiar
        </button>
      </div>
    </form>
  );
}
