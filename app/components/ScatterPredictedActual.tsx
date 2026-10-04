"use client";

// ScatterPredictedActual.tsx — TAB 2 del spec: "Scatter: prediction (x) vs
// actual return (y)". La línea de referencia es la diagonal y=x (calibración
// perfecta), no una regresión OLS ajustada — R² ya viaje calculado en
// prediction_regression.r_squared (portfolio_metrics.compute_prediction_regression,
// correlación²) y se muestra como texto; dibujar la recta OLS exacta
// necesitaría pendiente/intercepto que ese cálculo no expone, y añadirlo
// solo para esta línea sería más ingeniería de la que pide el spec (que
// solo pide ver el R², no una ecuación).
import { CartesianGrid, Line, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { chartSeriesMotion, chartTooltipMotion, motion } from "@/lib/motion";
import { useT } from "@/components/i18n/LocaleProvider";

export function ScatterPredictedActual({ points, rSquared }: { points: { predicted: number; actual: number }[]; rSquared: number | null }) {
  const t = useT();
  if (points.length === 0) {
    return <div className="flex h-48 items-center justify-center text-sm italic text-text-tertiary">{t("Sin trades todavía.", "No trades yet.")}</div>;
  }

  const allValues = points.flatMap((p) => [p.predicted, p.actual]);
  const min = Math.min(...allValues, 0);
  const max = Math.max(...allValues, 0);
  const diagonal = [
    { predicted: min, actual: min },
    { predicted: max, actual: max },
  ];

  return (
    <div>
      <div className="h-48 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis
              dataKey="predicted"
              type="number"
              name={t("Predicho (EV%)", "Predicted (EV%)")}
              tick={{ fontSize: 10 }}
              unit="%"
              domain={[min, max]}
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <YAxis
              dataKey="actual"
              type="number"
              name={t("Real (%)", "Actual (%)")}
              tick={{ fontSize: 10 }}
              unit="%"
              width={40}
              domain={[min, max]}
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <ZAxis range={[20, 20]} />
            <Tooltip {...chartTooltipMotion}
              formatter={(value) => `${Number(value).toFixed(2)}%`}
              cursor={{ strokeDasharray: "3 3" }}
            />
            <Line isAnimationActive={false} data={diagonal} dataKey="actual" stroke="var(--border-strong)" strokeDasharray="4 3" dot={false} activeDot={false} legendType="none" name="y=x" />
            <Scatter {...chartSeriesMotion} animationDuration={motion.duration.base} data={points} fill="var(--color-accent-600)" fillOpacity={0.6} name={t("operaciones", "trades")} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-xs text-text-tertiary">
        R² = {rSquared !== null ? rSquared.toFixed(3) : "—"} {t("(línea de referencia = calibración perfecta, predicho == real)", "(reference line = perfect calibration, predicted == actual)")}
      </p>
    </div>
  );
}
