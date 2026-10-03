// ReliabilityScale.tsx — dónde cae la calificación (baja / media / alta) en
// su escala de tres pasos, en vez de solo el nombre del nivel. Los colores
// son los de estado del veredicto (los mismos del recuadro), siempre con su
// etiqueta escrita debajo.
const STEPS = [
  { option: "C", fill: "bg-rose-500" },
  { option: "B", fill: "bg-amber-500" },
  { option: "A", fill: "bg-emerald-600 dark:bg-emerald-500" },
] as const;

export function ReliabilityScale({ option, labels, ariaLabel }: { option: string; labels: Record<"A" | "B" | "C", string>; ariaLabel: string }) {
  return (
    <div role="img" aria-label={ariaLabel} className="w-full max-w-sm">
      <div className="flex gap-0.5">
        {STEPS.map((s) => (
          <span key={s.option} className={`h-2 flex-1 rounded-sm ${s.option === option ? s.fill : "bg-border-subtle"}`} />
        ))}
      </div>
      <div className="mt-1 flex text-[11px]">
        {STEPS.map((s) => (
          <span key={s.option} className={`flex-1 ${s.option === option ? "font-semibold text-foreground" : "text-text-tertiary"}`}>
            {labels[s.option]}
          </span>
        ))}
      </div>
    </div>
  );
}
