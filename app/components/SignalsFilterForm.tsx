"use client";

// SignalsFilterForm.tsx — filtra ticker/tipo de evento/señal/rango de fechas/
// confianza mínima. El filtrado real ocurre en el SERVIDOR (SQL en
// getSignalsFeed) — este formulario solo actualiza los searchParams de la
// URL y deja que page.tsx (server component) vuelva a pedir los datos ya
// filtrados; SignalsTable (Tanstack Table) solo ordena/pagina lo que ya
// llegó filtrado, no re-filtra en el cliente.
import { useRouter, useSearchParams } from "next/navigation";
import { useId, useRef, useState, useTransition } from "react";
import { Button } from "@/components/ui/Button";
import { eventClassLabel } from "@/lib/labels";
import { useLocale, useT } from "@/components/i18n/LocaleProvider";

// Con el dedo: 44px de alto y 16px de letra (con menos, Safari en iPhone
// amplía la página al tocar el campo y hay que deshacer el zoom a mano).
// Tanda 12c: campos como los de Apple, con relleno translúcido y esquinas
// suaves en vez de borde; el borde (transparente) solo se ve con un error.
const INPUT =
  " min-h-9 rounded-control border border-transparent bg-fill px-2.5 py-1.5 text-sm text-foreground placeholder:text-text-tertiary aria-[invalid=true]:border-rose-600 dark:aria-[invalid=true]:border-rose-400 contrast-more:border-border-strong pointer-coarse:min-h-11 pointer-coarse:text-base";
const LABEL = "text-[0.8125rem] font-medium text-text-secondary";

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
    if (key === "minConfidence") setConfianzaIlegible(false);
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
    errores: useId(),
  };

  // Validación mientras se escribe (Tanda 12b, guía de Apple: avisar en el
  // momento, no al enviar). Antes un rango de fechas al revés devolvía una
  // tabla vacía sin decir por qué, y una confianza de 150 solo la frenaba el
  // globo del navegador, en su idioma y al pulsar Filtrar. El formulario va
  // con noValidate: los avisos son estos, no los nativos.
  const fechasAlReves = dateFrom !== "" && dateTo !== "" && Date.parse(dateFrom) > Date.parse(dateTo);
  // Un campo numérico con texto que no es un número ("-", "1e") da value ""
  // y validity.badInput: sin esto se enviaría como si estuviera vacío.
  const [confianzaIlegible, setConfianzaIlegible] = useState(false);
  const confianzaNum = Number(minConfidence);
  const confianzaMal =
    confianzaIlegible || (minConfidence !== "" && (!Number.isFinite(confianzaNum) || confianzaNum < 0 || confianzaNum > 100));
  const errores = [
    fechasAlReves && t("«Desde» es posterior a «Hasta».", "«From» is later than «To»."),
    confianzaMal && t("Escribe una confianza mínima de 0 a 100.", "Enter a minimum confidence from 0 to 100."),
  ].filter(Boolean) as string[];
  const refDesde = useRef<HTMLInputElement>(null);
  const refConfianza = useRef<HTMLInputElement>(null);

  function apply(e: React.FormEvent) {
    e.preventDefault();
    if (fechasAlReves) return refDesde.current?.focus();
    if (confianzaMal) return refConfianza.current?.focus();
    const params = new URLSearchParams();
    if (ticker) params.set("ticker", ticker);
    if (eventClass) params.set("eventClass", eventClass);
    if (signal) params.set("signal", signal);
    if (dateFrom) params.set("dateFrom", dateFrom);
    if (dateTo) params.set("dateTo", dateTo);
    // Normalizado: "1e2" o "-0" llegaban tal cual a la URL y al chip.
    if (minConfidence) params.set("minConfidence", String(confianzaNum));
    startTransition(() => router.push(`/senales?${params.toString()}`));
  }

  function clear() {
    setTicker("");
    setEventClass("");
    setSignal("");
    setDateFrom("");
    setDateTo("");
    setMinConfidence("");
    setConfianzaIlegible(false);
    startTransition(() => router.push("/senales"));
  }

  return (
    <form noValidate onSubmit={apply} className="tarjeta mb-5 flex flex-wrap items-end gap-3 p-4 text-sm">
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.ticker} className={LABEL}>
          Ticker
        </label>
        {/* Teclado del móvil en mayúsculas y sin autocorrector: un ticker no es una palabra. */}
        <input
          id={ids.ticker}
          className={`${INPUT} w-28`}
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="AAPL"
          autoCapitalize="characters"
          autoCorrect="off"
          autoComplete="off"
          spellCheck={false}
          enterKeyHint="search"
        />
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
        {/* max/min: el calendario ya no ofrece días que dejarían el rango al revés. */}
        <input
          ref={refDesde}
          id={ids.dateFrom}
          type="date"
          className={INPUT}
          value={dateFrom}
          max={dateTo || undefined}
          onChange={(e) => setDateFrom(e.target.value)}
          aria-invalid={fechasAlReves || undefined}
          aria-describedby={fechasAlReves ? ids.errores : undefined}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.dateTo} className={LABEL}>
          {t("Hasta", "To")}
        </label>
        <input
          id={ids.dateTo}
          type="date"
          className={INPUT}
          value={dateTo}
          min={dateFrom || undefined}
          onChange={(e) => setDateTo(e.target.value)}
          aria-invalid={fechasAlReves || undefined}
          aria-describedby={fechasAlReves ? ids.errores : undefined}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.minConfidence} className={LABEL}>
          {t("Confianza mín. (%)", "Min. confidence (%)")}
        </label>
        <input
          ref={refConfianza}
          id={ids.minConfidence}
          type="number"
          inputMode="decimal"
          min={0}
          max={100}
          step="any"
          className={`${INPUT} num w-20`}
          value={minConfidence}
          onChange={(e) => setMinConfidence(e.target.value)}
          // onInput y no onChange: al escribir "-" el valor sigue siendo "" y
          // React no emite onChange, pero el campo ya es ilegible.
          onInput={(e) => setConfianzaIlegible(e.currentTarget.validity.badInput)}
          aria-invalid={confianzaMal || undefined}
          aria-describedby={confianzaMal ? ids.errores : undefined}
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
      {errores.length > 0 && (
        // aria-hidden: lo anuncia la región de abajo y lo leen los campos por
        // aria-describedby; sin esto, en modo lectura se oía dos veces.
        <p id={ids.errores} aria-hidden="true" className="w-full text-xs text-rose-700 dark:text-rose-400">
          {errores.join(" ")}
        </p>
      )}
      {/* Región de aviso siempre montada (una que aparece de golpe no siempre
          se anuncia); fuera del flujo, así no añade hueco al formulario. */}
      <span aria-live="polite" className="sr-only">
        {errores.join(" ")}
      </span>
      {active.length > 0 && (
        <div role="group" className="flex w-full flex-wrap items-center gap-2 border-t border-border-subtle pt-3" aria-label={t("Filtros activos", "Active filters")}>
          <span className="text-xs text-text-secondary">{t("Filtros activos:", "Active filters:")}</span>
          {active.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => removeFilter(f.key)}
              aria-label={`${t("Quitar filtro", "Remove filter")} ${f.label}`}
              className="inline-flex min-h-7 items-center gap-1 rounded-full border border-transparent bg-accent-500/12 px-3 py-0.5 text-xs font-medium text-accent-700 transition-colors duration-(--motion-duration-micro) ease-(--motion-ease-out) hover:bg-accent-500/20 active:bg-accent-500/25 dark:bg-accent-400/15 dark:text-accent-300 pointer-coarse:min-h-11 pointer-coarse:px-3.5"
            >
              {f.label}
              <span aria-hidden="true" className="opacity-70">×</span>
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
