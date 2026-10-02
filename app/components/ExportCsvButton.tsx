"use client";

import { useState } from "react";
import { downloadCsv } from "@/lib/csv";
import { Button } from "@/components/ui/Button";
import type { SignalFeedRow } from "@/lib/queries";

// ExportCsvButton.tsx — exporta exactamente las filas visibles en la tabla
// (ya filtradas en servidor), no una llamada aparte a la BD: lo que el
// usuario ve es lo que descarga, sin una segunda fuente de verdad.
export function ExportCsvButton({ rows }: { rows: SignalFeedRow[] }) {
  // Sin confirmación, el clic no tenía ninguna respuesta visible: el CSV
  // aparecía (o no) en las descargas del navegador, fuera de la página.
  const [done, setDone] = useState(false);

  function handleExport() {
    setDone(true);
    setTimeout(() => setDone(false), 2500);
    downloadCsv(
      `marketchange-senales-${new Date().toISOString().slice(0, 10)}.csv`,
      ["Fecha", "Ticker", "Evento", "Fuente", "Sorpresa", "Señal", "Confianza (%)", "EV esperado (%)", "Confianza técnica", "Pasa filtros técnicos", "Entrada", "Salida", "Motivo salida", "P&L (%)"],
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
        r.tech_passes === null ? null : r.tech_passes ? "Sí" : "No",
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
      {done ? `Descargado (${rows.length})` : "Exportar (CSV)"}
    </Button>
  );
}
