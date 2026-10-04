// PortfolioEquityCurve.tsx — mismo patrón de SVG a mano que EquityCurve.tsx
// (sin librería de charting, ver su comentario), pero sobre balance en
// dólares en vez de retorno acumulado en % — son escalas distintas
// (portfolio_equity_curve.balance parte de starting_capital, no de 0), así
// que no comparten componente: forzar una unión hubiera significado una
// prop de "modo" innecesaria para dos usos que no cambian de forma.
import type { PortfolioEquityPoint } from "@/lib/queries";
import { makeT, type Locale } from "@/lib/i18n";

export function PortfolioEquityCurve({
  points,
  startingCapital,
  locale = "es",
}: {
  points: PortfolioEquityPoint[];
  startingCapital: number;
  locale?: Locale;
}) {
  const t = makeT(locale);
  if (points.length === 0) {
    return <div className="text-sm italic text-text-tertiary">{t("Sin curva de equity todavía.", "No equity curve yet.")}</div>;
  }

  const width = 320;
  const height = 120;
  const padding = 8;

  const values = points.map((p) => p.balance);
  const min = Math.min(startingCapital, ...values);
  const max = Math.max(startingCapital, ...values);
  const range = max - min || 1;

  const toXY = (i: number, v: number): [number, number] => {
    const x = padding + (i / Math.max(points.length - 1, 1)) * (width - 2 * padding);
    const y = height - padding - ((v - min) / range) * (height - 2 * padding);
    return [x, y];
  };

  const pathD = points
    .map((p, i) => {
      const [x, y] = toXY(i, p.balance);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const [baseX1, baseY] = toXY(0, startingCapital);
  const [baseX2] = toXY(points.length - 1, startingCapital);
  const last = values[values.length - 1];
  const [endX, endY] = toXY(points.length - 1, last);
  const positive = last >= startingCapital;
  // Línea y punto con el par de gráficos (--gain/--loss, validado para
  // daltonismo); la cifra, que es texto, con el mismo par que SignedPct: en
  // claro --loss no llega al contraste de texto (ver globals.css).
  const strokeColor = positive ? "var(--gain)" : "var(--loss)";
  const labelClass = positive ? "fill-emerald-700 dark:fill-emerald-400" : "fill-rose-700 dark:fill-rose-400";
  // Mismo cálculo que "Resultado" de la tarjeta (total_return = final /
  // capital inicial - 1): la cifra de arriba y el final de la curva tienen
  // que decir lo mismo. Antes la curva no tenía ninguna etiqueta y su color
  // podía parecer contradecir el número.
  const totalPct = (last / startingCapital - 1) * 100;
  const label = `${totalPct >= 0 ? "+" : ""}${totalPct.toFixed(1)}%`;

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={t(
          `Evolución de la rentabilidad acumulada: ${label} al final del periodo`,
          `Cumulative return over time: ${label} at the end of the period`,
        )}
      >
        <line x1={baseX1} y1={baseY} x2={baseX2} y2={baseY} stroke="currentColor" strokeOpacity={0.2} strokeDasharray="4 3" />
        <text x={baseX1} y={Math.max(baseY - 3, 9)} fontSize={8} fill="currentColor" opacity={0.5}>
          0%
        </text>
        {/* La curva se dibuja de izquierda a derecha y la cifra final aparece
            al terminar: es la conclusión del recorrido. */}
        <path d={pathD} pathLength={1} className="m-draw" fill="none" stroke={strokeColor} strokeWidth={1.5} />
        <circle className="m-fade m-after-data" cx={endX} cy={endY} r={2.5} fill={strokeColor} />
        <text
          className={`m-fade m-after-data ${labelClass}`}
          x={endX - 4}
          y={endY < 14 ? endY + 12 : endY - 5}
          textAnchor="end"
          fontSize={9}
          fontWeight={600}
        >
          {label}
        </text>
      </svg>
      <figcaption className="num mt-0.5 flex justify-between text-[10px] text-text-tertiary">
        <span>{points[0].trade_date.slice(0, 10)}</span>
        <span>{points[points.length - 1].trade_date.slice(0, 10)}</span>
      </figcaption>
    </figure>
  );
}
