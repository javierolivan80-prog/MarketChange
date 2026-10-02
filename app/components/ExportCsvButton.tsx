"use client";

import { downloadCsv } from "@/lib/csv";
import type { SignalFeedRow } from "@/lib/queries";

// ExportCsvButton.tsx — exporta exactamente las filas visibles en la tabla
// (ya filtradas en servidor), no una llamada aparte a la BD: lo que el
// usuario ve es lo que descarga, sin una segunda fuente de verdad.
export function ExportCsvButton({ rows }: { rows: SignalFeedRow[] }) {
  function handleExport() {
    downloadCsv(
      `marketchange-senales-${new Date().toISOString().slice(0, 10)}.csv`,
      ["Fecha", "Ticker", "Evento", "Fuente", "Sorpresa", "Señal", "Confianza (%)", "EV esperado (%)", "Entrada", "Salida", "Motivo salida", "P&L (%)"],
      rows.map((r) => [
        r.d0_close_date,
        r.ticker,
        r.event_class,
        r.source,
        r.novelty_score,
        r.signal,
        r.confidence.toFixed(1),
        (r.ev_balanced * 100).toFixed(2),
        r.entry_date,
        r.exit_date,
        r.exit_reason,
        r.pnl_pct !== null ? r.pnl_pct.toFixed(2) : null,
      ])
    );
  }

  return (
    <button
      onClick={handleExport}
      className="border border-border-strong px-3 py-1.5 text-sm text-foreground transition-colors hover:border-accent-600 hover:text-accent-700 dark:hover:text-accent-400"
    >
      Exportar (CSV)
    </button>
  );
}
