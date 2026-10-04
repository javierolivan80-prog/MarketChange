"use client";

// DailyPnLChart.tsx — TAB 5 "Daily P&L chart (bar)". Agrega
// last_10_closed_trades por exit_date — para una sola semana de paper
// trading esto cubre prácticamente todos los cierres (el reporte no expone
// más de los últimos 10, documentado en paper_trading/report.py; con el
// volumen de una semana de POC, 10 alcanza casi siempre).
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { chartTooltipMotion } from "@/lib/motion";
import { useChartMotion } from "@/components/viz/useChartMotion";
import type { PaperClosedTrade } from "@/lib/queries";
import { useT } from "@/components/i18n/LocaleProvider";

export function DailyPnLChart({ trades }: { trades: PaperClosedTrade[] }) {
  const t = useT();
  const seriesMotion = useChartMotion();
  if (trades.length === 0) {
    return <div className="flex h-40 items-center justify-center text-sm italic text-text-tertiary">{t("Sin trades cerrados esta semana.", "No closed trades this week.")}</div>;
  }

  const byDate = new Map<string, number>();
  for (const tr of trades) {
    byDate.set(tr.exit_date, (byDate.get(tr.exit_date) ?? 0) + tr.pnl_pct);
  }
  const data = Array.from(byDate.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, pnl]) => ({ date, pnl }));

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={40} />
          <Tooltip {...chartTooltipMotion} formatter={(value) => `${Number(value).toFixed(2)}%`} />
          <Bar {...seriesMotion} dataKey="pnl">
            {data.map((d, i) => (
              <Cell key={i} fill={d.pnl >= 0 ? "var(--gain)" : "var(--loss)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
