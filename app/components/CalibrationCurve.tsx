"use client";

// CalibrationCurve.tsx — TAB 3 del spec: "X: confidence predicted, Y: actual
// win rate, diagonal line (perfect calibration), your line (actual
// calibration)". Un punto por bucket de compute_calibration_diagnostics
// (portfolio_metrics.py), en el mismo formato estándar de un "reliability
// diagram" (Guo et al. 2017, la misma referencia que ya cita el docstring
// de compute_calibration_diagnostics para ECE).
import { CartesianGrid, Line, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { chartSeriesMotion, chartTooltipMotion, motion } from "@/lib/motion";
import type { ConfidenceBucket } from "@/lib/queries";
import { useT } from "@/components/i18n/LocaleProvider";

export function CalibrationCurve({ buckets }: { buckets: ConfidenceBucket[] }) {
  const t = useT();
  if (buckets.length === 0) {
    return <div className="flex h-56 items-center justify-center text-sm italic text-text-tertiary">{t("Sin datos suficientes.", "Not enough data.")}</div>;
  }

  const points = buckets.map((b) => ({ confidence: b.mean_confidence, hit_rate: b.hit_rate * 100, n: b.n }));
  const diagonal = [
    { confidence: 0, hit_rate: 0 },
    { confidence: 100, hit_rate: 100 },
  ];

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="confidence" type="number" domain={[0, 100]} unit="%" tick={{ fontSize: 10 }} name={t("Confianza declarada", "Stated confidence")} />
          <YAxis dataKey="hit_rate" type="number" domain={[0, 100]} unit="%" tick={{ fontSize: 10 }} width={44} name={t("Acierto real", "Actual win rate")} />
          <ZAxis dataKey="n" range={[30, 300]} name="n" />
          <Tooltip {...chartTooltipMotion}
            formatter={(value, name) => [name === "n" ? value : `${Number(value).toFixed(1)}%`, name === "hit_rate" ? t("Acierto real", "Actual win rate") : name === "confidence" ? t("Confianza media", "Average confidence") : "n"]}
          />
          <Line isAnimationActive={false} data={diagonal} dataKey="hit_rate" stroke="#9ca3af" strokeDasharray="4 3" dot={false} activeDot={false} legendType="none" />
          <Scatter {...chartSeriesMotion} animationDuration={motion.duration.base} data={points} fill="#2563eb" fillOpacity={0.7} line={{ stroke: "#2563eb", strokeWidth: 1.5 }} lineType="joint" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
