// TradeLevels.tsx — el plan de una operación dibujado sobre su escala de
// precios: stop, entrada y objetivos. El tramo entrada→stop (lo que se
// arriesga) va en el color de pérdida y entrada→objetivo (lo que se puede
// ganar) en el de ganancia, así que el riesgo/beneficio se ve sin leer cifras:
// una barra verde el doble de larga que la roja es un 1:2.
//
// Sirve igual para LONG y SHORT: los puntos se colocan por precio (más bajo a
// la izquierda), así que en un SHORT el stop queda a la derecha.
//
// Sin hooks: lo pintan componentes de servidor y de cliente. Los textos
// llegan ya traducidos. El color nunca es el único canal: la versión
// completa lleva leyenda con cifras y la compacta un title con todo.

type Labels = { stop: string; entry: string; target: string; target2: string; risk: string; reward: string };

function pctFrom(entry: number, v: number) {
  return (Math.abs(v - entry) / entry) * 100;
}

export function TradeLevels({
  entry,
  stop,
  target,
  target2 = null,
  labels,
  compact = false,
}: {
  entry: number | null;
  stop: number | null;
  target: number | null;
  target2?: number | null;
  labels: Labels;
  compact?: boolean;
}) {
  if (entry === null || stop === null || target === null || entry <= 0) return null;
  const points = [stop, entry, target, ...(target2 !== null ? [target2] : [])];
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const pos = (v: number) => ((v - lo) / span) * 100;

  const risk = pctFrom(entry, stop);
  const reward = pctFrom(entry, target);
  const reward2 = target2 !== null ? pctFrom(entry, target2) : null;
  const summary = `${labels.stop} ${stop.toFixed(2)} (−${risk.toFixed(1)}%) · ${labels.entry} ${entry.toFixed(2)} · ${labels.target} ${target.toFixed(
    2,
  )} (+${reward.toFixed(1)}%)${target2 !== null ? ` · ${labels.target2} ${target2.toFixed(2)} (+${reward2!.toFixed(1)}%)` : ""}`;

  // Tramo entre dos precios, con 1px de aire a cada lado de la entrada para
  // que riesgo y beneficio se lean como dos piezas (la "separación de 2px").
  // En la versión completa cada tramo crece desde `a` hacia `b`: riesgo y
  // beneficio salen de la entrada, que es de donde sale el plan. En la
  // compacta (filas de tabla) no se anima: diez barras a la vez son ruido.
  const segment = (a: number, b: number, className: string) => {
    const left = Math.min(pos(a), pos(b));
    const width = Math.abs(pos(a) - pos(b));
    const grow = compact ? "" : pos(b) >= pos(a) ? "m-fill-right" : "m-fill-left";
    return <span className={`absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full ${grow} ${className}`} style={{ left: `calc(${left}% + 1px)`, width: `calc(${width}% - 2px)` }} />;
  };

  const bar = (
    <span className={`relative block ${compact ? "h-3 w-24" : "h-4 w-full"}`} role="img" aria-label={summary} title={summary}>
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border-subtle" />
      {segment(entry, stop, "bg-loss")}
      {segment(entry, target, "bg-gain")}
      {target2 !== null && segment(target, target2, "bg-gain opacity-40")}
      <span className="absolute top-1/2 h-3.5 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-foreground" style={{ left: `${pos(entry)}%` }} />
    </span>
  );

  if (compact) return bar;

  return (
    <figure>
      {bar}
      <figcaption className="num mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-3 rounded-sm bg-loss" />
          {labels.risk} −{risk.toFixed(1)}% ({labels.stop} {stop.toFixed(2)})
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-3 w-0.5 bg-foreground" />
          {labels.entry} {entry.toFixed(2)}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-3 rounded-sm bg-gain" />
          {labels.reward} +{reward.toFixed(1)}% ({labels.target} {target.toFixed(2)})
        </span>
        {target2 !== null && (
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2 w-3 rounded-sm bg-gain opacity-40" />
            {labels.target2} {target2.toFixed(2)} (+{reward2!.toFixed(1)}%)
          </span>
        )}
      </figcaption>
    </figure>
  );
}
