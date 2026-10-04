// Sparkline.tsx — la forma de una serie en una línea, sin ejes: acompaña a
// una cifra (el resultado acumulado) para que se vea si se llegó a ella
// subiendo poco a poco o con sustos por el camino. La línea discontinua es el
// punto de partida. La cifra exacta va siempre al lado, en texto.
export function Sparkline({ values, label, className = "h-8 w-full" }: { values: number[]; label: string; className?: string }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const W = 100;
  const H = 30;
  const x = (i: number) => (i / (values.length - 1)) * W;
  const y = (v: number) => H - 2 - ((v - lo) / span) * (H - 4);
  const d = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  const color = up ? "var(--gain)" : "var(--loss)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={className} role="img" aria-label={label}>
      <line x1={0} x2={W} y1={y(values[0])} y2={y(values[0])} stroke="currentColor" strokeOpacity={0.25} strokeWidth={1} strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
