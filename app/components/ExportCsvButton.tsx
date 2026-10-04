"use client";

import { useState } from "react";
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
  const [done, setDone] = useState(false);
  const t = useT();

  function handleExport() {
    setDone(true);
    setTimeout(() => setDone(false), 2500);
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
        "P&L (%)",
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
      ])
    );
  }

  return (
    // aria-live: la confirmación "Descargado" también llega a lectores de pantalla.
    <Button onClick={handleExport} aria-live="polite">
      {/* Crossfade solo al confirmar: la etiqueta de reposo no se anima al cargar la página. */}
      <span key={String(done)} className={done ? "m-fade" : undefined}>
        {done ? `${t("Descargado", "Downloaded")} (${rows.length})` : t("Exportar (CSV)", "Export (CSV)")}
      </span>
    </Button>
  );
}
