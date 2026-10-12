"use client";

import { useEffect, useRef, useState } from "react";
import { downloadCsv } from "@/lib/csv";
import { Button } from "@/components/ui/Button";
import type { SignalFeedRow } from "@/lib/queries";
import { useT } from "@/components/i18n/LocaleProvider";

// ExportCsvButton.tsx — exporta exactamente las filas visibles en la tabla
// (ya filtradas en servidor), no una llamada aparte a la BD: lo que el
// usuario ve es lo que descarga, sin una segunda fuente de verdad.
export function ExportCsvButton({ rows }: { rows: SignalFeedRow[] }) {
  // Sin confirmación, el clic no tenía ninguna respuesta visible: el CSV
  // aparecía (o no) en las descargas del navegador, fuera de la página.
  // "Descargado" solo si la descarga se lanzó de verdad; si falla, se dice.
  const [estado, setEstado] = useState<"reposo" | "hecho" | "error">("reposo");
  const temporizador = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(temporizador.current), []);
  const t = useT();

  function handleExport() {
    try {
      generar();
      setEstado("hecho");
    } catch (e) {
      console.error("No se pudo generar el CSV", e);
      setEstado("error");
    }
    clearTimeout(temporizador.current);
    temporizador.current = setTimeout(() => setEstado("reposo"), 4000);
  }

  function generar() {
    downloadCsv(
      `marketchange-${t("senales", "signals")}-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        t("Fecha", "Date"),
        "Ticker",
        t("Evento", "Event"),
        t("Fuente", "Source"),
        t("Sorpresa", "Surprise"),
        t("Señal", "Signal"),
        t("Confianza (%)", "Confidence (%)"),
        t("EV esperado (%)", "Expected value (%)"),
        t("Confianza técnica", "Technical confidence"),
        t("Pasa filtros técnicos", "Passes technical filters"),
        t("Entrada", "Entry"),
        t("Salida", "Exit"),
        t("Motivo salida", "Exit reason"),
        t("P&L frente al S&P 500 (%)", "P&L vs S&P 500 (%)"),
        t("Cubierta con SPY", "Hedged with SPY"),
      ],
      rows.map((r) => [
        r.d0_close_date,
        r.ticker,
        r.event_class,
        r.source,
        r.novelty_score,
        r.signal,
        r.confidence.toFixed(1),
        (r.ev_balanced * 100).toFixed(2),
        r.tech_confidence,
        r.tech_passes === null ? null : r.tech_passes ? t("Sí", "Yes") : "No",
        r.entry_date,
        r.exit_date,
        r.exit_reason,
        r.pnl_pct !== null ? r.pnl_pct.toFixed(2) : null,
        r.cubierta === null ? null : r.cubierta ? t("Sí", "Yes") : "No",
      ])
    );
  }

  const aviso =
    estado === "hecho"
      ? `${t("Descargado", "Downloaded")} (${rows.length})`
      : estado === "error"
        ? t("Error al exportar", "Export failed")
        : "";

  return (
    <>
      {/* Ancho mínimo fijo: el botón no cambia de tamaño al cambiar el texto. */}
      <Button onClick={handleExport} className="min-w-40">
        {/* Crossfade solo al confirmar: la etiqueta de reposo no se anima al cargar la página. */}
        <span key={estado} className={estado === "reposo" ? undefined : estado === "error" ? "m-fade text-rose-700 dark:text-rose-400" : "m-fade"}>
          {aviso || t("Exportar (CSV)", "Export (CSV)")}
        </span>
      </Button>
      {/* Aviso para lectores de pantalla fuera del botón: un aria-live sobre el
          propio botón también se leía al enfocarlo y no siempre anunciaba el cambio. */}
      <span role="status" className="sr-only">
        {aviso}
      </span>
    </>
  );
}
