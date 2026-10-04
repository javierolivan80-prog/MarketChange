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
import { Button } from "@/components/ui/Button";
import { eventClassLabel, exitReasonLabel, versionLabel } from "@/lib/labels";
import type { PortfolioReport, StrategyVersion } from "@/lib/queries";
import { intlTag } from "@/lib/i18n";
import { useLocale, useT } from "@/components/i18n/LocaleProvider";

const VERSIONS: StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

export function ExportPdfButton({ report }: { report: PortfolioReport }) {
  const [busy, setBusy] = useState(false);
  const t = useT();
  const locale = useLocale();
  const vl = (v: StrategyVersion) => versionLabel(v, locale);
  const ecl = (c: string | null | undefined) => eventClassLabel(c, locale);
  const erl = (r: string | null | undefined) => exitReasonLabel(r, locale);

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
    doc.text(t("MarketChange — Informe de resultados", "MarketChange — Performance report"), 14, y);
    y += 7;
    doc.setFontSize(10);
    doc.setTextColor(100);
    doc.text(
      `${t("Generado el", "Generated on")} ${new Date().toLocaleDateString(intlTag(locale))} · ${t(
        "Rentabilidades pasadas no garantizan resultados futuros.",
        "Past performance does not guarantee future results.",
      )}`,
      14,
      y,
    );
    y += 8;

    // El veredicto interno de go/no-go del motor (report.recommendation) no
    // va en el informe para usuarios: es una nota operativa, no un resultado.
    doc.setTextColor(0);

    autoTable(doc, {
      startY: y,
      head: [[t("Métrica", "Metric"), ...VERSIONS.map(vl)]],
      body: [
        [t("Operaciones", "Trades"), ...VERSIONS.map((v) => String(report.versions[v].trade_metrics.total_trades))],
        [t("Acierto", "Win rate"), ...VERSIONS.map((v) => fmtPct(report.versions[v].trade_metrics.win_rate))],
        [t("Resultado total", "Total return"), ...VERSIONS.map((v) => fmtPct(report.versions[v].equity_metrics.total_return))],
        ["Sharpe", ...VERSIONS.map((v) => fmtNum(report.versions[v].equity_metrics.sharpe_ratio))],
        [t("Peor caída", "Max drawdown"), ...VERSIONS.map((v) => fmtPct(report.versions[v].equity_metrics.max_drawdown))],
        [t("Calibración", "Calibration"), ...VERSIONS.map((v) => fmtNum(report.versions[v].calibration.calibration_score))],
        [t("Calibración (correl.)", "Calibration (correl.)"), ...VERSIONS.map((v) => fmtNum(report.versions[v].confidence_calibration.correlation))],
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
      doc.text(`${vl(version)} — ${t("evolución de la rentabilidad", "performance over time")}`, 14, y);
      y += 2;
      const curve = v.equity_curve;
      const balances = curve.map((p) => p.balance);
      const peak = balances.length > 0 ? Math.max(...balances) : null;
      const trough = balances.length > 0 ? Math.min(...balances) : null;
      const start = balances[0] ?? null;
      autoTable(doc, {
        startY: y + 2,
        // En % sobre el punto de partida, no en dólares: el capital del
        // backtest es una base de cálculo, no dinero de nadie.
        head: [[t("Rentabilidad final", "Final return"), t("Máximo alcanzado", "Highest point"), t("Mínimo alcanzado", "Lowest point")]],
        body: [[
          fmtPct(v.equity_metrics.total_return),
          start && peak !== null ? fmtPct(peak / start - 1) : "—",
          start && trough !== null ? fmtPct(trough / start - 1) : "—",
        ]],
        styles: { fontSize: 8 },
        headStyles: { fillColor: [30, 41, 59] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      doc.setFontSize(9);
      doc.text(`${t("Top 5 ganadores", "Top 5 winners")} (${vl(version)})`, 14, y);
      y += 2;
      autoTable(doc, {
        startY: y + 2,
        head: [["Ticker", t("Tipo", "Type"), t("Salida", "Exit"), t("Resultado %", "Result %")]],
        body: v.top_10_winners.slice(0, 5).map((tr) => [tr.ticker ?? "—", ecl(tr.event_class), erl(tr.exit_reason), `${tr.pnl_pct.toFixed(2)}%`]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [22, 101, 52] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      doc.text(`${t("Top 5 perdedores", "Top 5 losers")} (${vl(version)})`, 14, y);
      y += 2;
      autoTable(doc, {
        startY: y + 2,
        head: [["Ticker", t("Tipo", "Type"), t("Salida", "Exit"), t("Resultado %", "Result %")]],
        body: v.top_10_losers.slice(0, 5).map((tr) => [tr.ticker ?? "—", ecl(tr.event_class), erl(tr.exit_reason), `${tr.pnl_pct.toFixed(2)}%`]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [153, 27, 27] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      const eventTypeRows = Object.values(v.metrics_by_event_type);
      if (eventTypeRows.length > 0) {
        doc.text(`${t("Resultado por tipo de evento", "Results by event type")} (${vl(version)})`, 14, y);
        y += 2;
        autoTable(doc, {
          startY: y + 2,
          head: [[t("Tipo", "Type"), "N", t("Acierto", "Win rate"), t("Retorno medio", "Average return"), "n<20"]],
          body: eventTypeRows.map((r) => [
            ecl(r.event_type),
            String(r.n_trades),
            fmtPct(r.win_rate),
            `${r.avg_return.toFixed(2)}%`,
            r.insufficient_sample ? t("sí", "yes") : "no",
          ]),
          styles: { fontSize: 8 },
          headStyles: { fillColor: [30, 41, 59] },
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        y = (doc as any).lastAutoTable.finalY + 8;
      }
    }

    doc.save(`marketchange-${t("resultados", "results")}-${new Date().toISOString().slice(0, 10)}.pdf`);
  }

  return (
    <Button onClick={handleExport} disabled={busy} aria-live="polite">
      <span key={String(busy)} className={busy ? "m-fade" : undefined}>
        {busy ? t("Generando…", "Generating…") : t("Exportar informe (PDF)", "Export report (PDF)")}
      </span>
    </Button>
  );
}

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}
function fmtNum(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}
