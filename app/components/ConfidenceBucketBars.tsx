"use client";

// ConfidenceBucketBars.tsx — TAB 2 "Win rate by confidence bucket" Y TAB 3
// (barras de fondo de la curva de calibración). Reutilizado en ambos tabs:
// mismo dato (ConfidenceBucket[]), dos contextos.
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { chartTooltipMotion } from "@/lib/motion";
import { useChartMotion } from "@/components/viz/useChartMotion";
import type { ConfidenceBucket } from "@/lib/queries";
import { useT } from "@/components/i18n/LocaleProvider";

export function ConfidenceBucketBars({ buckets, label }: { buckets: ConfidenceBucket[]; label?: string }) {
  const t = useT();
  const seriesMotion = useChartMotion();
  const seriesLabel = label ?? t("Acierto", "Win rate");
  if (buckets.length === 0) {
    return <div className="flex h-40 items-center justify-center text-sm italic text-text-tertiary">{t("Sin datos suficientes.", "Not enough data.")}</div>;
  }
  const data = buckets.map((b) => ({ ...b, hit_rate_pct: b.hit_rate * 100 }));

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={40} domain={[0, 100]} />
          <Tooltip {...chartTooltipMotion}
            formatter={(value, name, item) => [
              name === "hit_rate_pct" ? `${Number(value).toFixed(1)}% (n=${item.payload.n})` : value,
              seriesLabel,
            ]}
          />
          <Bar {...seriesMotion} dataKey="hit_rate_pct" fill="var(--color-accent-600)">
            {data.map((d, i) => (
              <Cell key={i} fill={d.n < 5 ? "var(--color-accent-300)" : "var(--color-accent-600)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
