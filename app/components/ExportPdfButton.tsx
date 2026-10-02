"use client";

// ExportPdfButton.tsx — spec Fase 5: "Botón 'Download backtest report' → PDF
// con summary metrics, equity curves, top trades, event study by class,
// calibration analysis, recomendación". Genera un PDF real en el cliente
// (jsPDF + jspdf-autotable) a partir de los mismos datos ya en pantalla —
// sin capturar las curvas de equity como imagen (html2canvas sobre SVGs
// dinámicos es frágil: fuentes, tainted canvas, tamaño variable): las
// curvas se representan como su TABLA de puntos clave (balance inicial,
// final, pico, valle) en vez de un gráfico — sigue siendo "equity curves"
// en el sentido de datos, no rendering. Documentado aquí, no fingido.
//
// jsPDF + autotable pesan ~150 kB: se cargan con import() al pulsar el botón,
// no en cada visita a /cartera (antes eran el grueso de su JS inicial, para
// una acción que la mayoría de visitas nunca hace).
import { useState } from "react";
import { eventClassLabel, exitReasonLabel, versionLabel } from "@/lib/labels";
import type { PortfolioReport, StrategyVersion } from "@/lib/queries";

const VERSIONS: StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

export function ExportPdfButton({ report }: { report: PortfolioReport }) {
  const [busy, setBusy] = useState(false);

  async function handleExport() {
    setBusy(true);
    try {
      await buildPdf();
    } finally {
      setBusy(false);
    }
  }

  async function buildPdf() {
    const [{ default: jsPDF }, { default: autoTable }] = await Promise.all([import("jspdf"), import("jspdf-autotable")]);
    const doc = new jsPDF();
    let y = 15;

    doc.setFontSize(16);
    doc.text("MarketChange — Informe de backtest", 14, y);
    y += 7;
    doc.setFontSize(10);
    doc.setTextColor(100);
    doc.text(`Corrida: ${report.run_batch_tag} · Capital inicial: $${report.starting_capital.toLocaleString("en-US")}`, 14, y);
    y += 5;
    doc.text(`Generado: ${new Date().toISOString().slice(0, 19).replace("T", " ")}`, 14, y);
    y += 8;

    doc.setTextColor(0);
    doc.setFontSize(12);
    doc.text(`Recomendación: ${report.recommendation.verdict}`, 14, y);
    y += 6;
    doc.setFontSize(9);
    for (const finding of report.recommendation.findings) {
      const lines = doc.splitTextToSize(`• ${finding}`, 180);
      doc.text(lines, 14, y);
      y += lines.length * 4;
    }
    y += 4;

    autoTable(doc, {
      startY: y,
      head: [["Métrica", ...VERSIONS.map(versionLabel)]],
      body: [
        ["Operaciones", ...VERSIONS.map((v) => String(report.versions[v].trade_metrics.total_trades))],
        ["Acierto", ...VERSIONS.map((v) => fmtPct(report.versions[v].trade_metrics.win_rate))],
        ["Resultado total", ...VERSIONS.map((v) => fmtPct(report.versions[v].equity_metrics.total_return))],
        ["Sharpe", ...VERSIONS.map((v) => fmtNum(report.versions[v].equity_metrics.sharpe_ratio))],
        ["Peor caída", ...VERSIONS.map((v) => fmtPct(report.versions[v].equity_metrics.max_drawdown))],
        ["Calibración", ...VERSIONS.map((v) => fmtNum(report.versions[v].calibration.calibration_score))],
        ["Calibración (correl.)", ...VERSIONS.map((v) => fmtNum(report.versions[v].confidence_calibration.correlation))],
      ],
      styles: { fontSize: 8 },
      headStyles: { fillColor: [30, 41, 59] },
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    y = (doc as any).lastAutoTable.finalY + 8;

    for (const version of VERSIONS) {
      const v = report.versions[version];
      if (y > 250) {
        doc.addPage();
        y = 15;
      }
      doc.setFontSize(11);
      doc.text(`${versionLabel(version)} — curva de capital (puntos clave)`, 14, y);
      y += 2;
      const curve = v.equity_curve;
      const balances = curve.map((p) => p.balance);
      const peak = balances.length > 0 ? Math.max(...balances) : null;
      const trough = balances.length > 0 ? Math.min(...balances) : null;
      autoTable(doc, {
        startY: y + 2,
        head: [["Balance inicial", "Balance final", "Pico", "Valle"]],
        body: [[
          fmtDollars(balances[0] ?? null),
          fmtDollars(v.equity_metrics.final_balance),
          fmtDollars(peak),
          fmtDollars(trough),
        ]],
        styles: { fontSize: 8 },
        headStyles: { fillColor: [30, 41, 59] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      doc.setFontSize(9);
      doc.text(`Top 5 ganadores (${versionLabel(version)})`, 14, y);
      y += 2;
      autoTable(doc, {
        startY: y + 2,
        head: [["Ticker", "Tipo", "Salida", "Resultado %"]],
        body: v.top_10_winners.slice(0, 5).map((t) => [t.ticker ?? "—", eventClassLabel(t.event_class), exitReasonLabel(t.exit_reason), `${t.pnl_pct.toFixed(2)}%`]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [22, 101, 52] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      doc.text(`Top 5 perdedores (${versionLabel(version)})`, 14, y);
      y += 2;
      autoTable(doc, {
        startY: y + 2,
        head: [["Ticker", "Tipo", "Salida", "Resultado %"]],
        body: v.top_10_losers.slice(0, 5).map((t) => [t.ticker ?? "—", eventClassLabel(t.event_class), exitReasonLabel(t.exit_reason), `${t.pnl_pct.toFixed(2)}%`]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [153, 27, 27] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      const eventTypeRows = Object.values(v.metrics_by_event_type);
      if (eventTypeRows.length > 0) {
        doc.text(`Resultado por tipo de evento (${versionLabel(version)})`, 14, y);
        y += 2;
        autoTable(doc, {
          startY: y + 2,
          head: [["Tipo", "N", "Acierto", "Retorno medio", "n<20"]],
          body: eventTypeRows.map((r) => [
            eventClassLabel(r.event_type),
            String(r.n_trades),
            fmtPct(r.win_rate),
            `${r.avg_return.toFixed(2)}%`,
            r.insufficient_sample ? "sí" : "no",
          ]),
          styles: { fontSize: 8 },
          headStyles: { fillColor: [30, 41, 59] },
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        y = (doc as any).lastAutoTable.finalY + 8;
      }
    }

    doc.save(`money-poc-backtest-${report.run_batch_tag}.pdf`);
  }

  return (
    <button
      onClick={handleExport}
      disabled={busy}
      className="rounded border border-border-strong px-3 py-1.5 font-mono text-sm uppercase tracking-wide text-foreground transition-colors hover:border-accent-600 hover:text-accent-700 dark:hover:text-accent-400"
    >
      {busy ? "Generando…" : "Exportar informe (PDF)"}
    </button>
  );
}

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}
function fmtNum(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}
function fmtDollars(v: number | null): string {
  return v === null ? "—" : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
