"use client";

// QualityCard.tsx — una empresa en el ranking de largo plazo: nota global,
// barra por criterio y la explicación en texto de cada uno (redactada en
// quality_score.py, no aquí).
//
// Los criterios no calculables se muestran en gris con su motivo, en vez de
// ocultarse: que falte un dato es información, y esconderlo haría parecer que
// la nota se apoya en más criterios de los que realmente usa.
import { useState } from "react";
import type { QualityScoreRow } from "@/lib/queries";

// Nota de calidad no es P&L ni dirección — se codifica con intensidad del
// acento único (más pálido = nota más baja), no con semáforo verde/rojo.
function barColorClass(score: number | null): string {
  if (score === null) return "bg-border-strong";
  if (score >= 75) return "bg-accent-600";
  if (score >= 55) return "bg-accent-400";
  if (score >= 35) return "bg-accent-300";
  return "bg-accent-200";
}

export function QualityCard({ row, rank, scoreColorClass }: { row: QualityScoreRow; rank: number; scoreColorClass: string }) {
  const [open, setOpen] = useState(false);
  const nDisponibles = row.components.filter((c) => c.score !== null).length;

  return (
    <div className="overflow-hidden border border-border-subtle">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="w-full p-4 text-left hover:bg-surface-raised">
        <div className="flex items-center gap-4">
          <span className="num w-6 text-xs text-text-tertiary">{rank}</span>
          <div className="min-w-0 flex-1">
            <p className="font-mono font-medium text-foreground">{row.ticker}</p>
            {row.company_name && <p className="truncate text-xs text-text-secondary">{row.company_name}</p>}
          </div>
          <div className="text-right">
            <p className={`num text-2xl font-semibold ${scoreColorClass}`}>{row.total_score !== null ? row.total_score.toFixed(0) : "—"}</p>
            <p className="num text-[10px] text-text-tertiary">
              {nDisponibles}/5 criterios · {row.n_years} {row.n_years === 1 ? "ejercicio" : "ejercicios"}
            </p>
          </div>
          <span className="text-text-tertiary" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
        </div>
        <p className="ml-10 mt-2 text-xs text-text-secondary">{row.verdict}</p>
      </button>

      {open && (
        <div className="space-y-3 border-t border-border-subtle px-4 pb-4 pt-3">
          {row.components.map((c) => (
            <div key={c.name}>
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className={`text-sm font-medium ${c.score === null ? "text-text-tertiary" : "text-foreground"}`}>{c.name}</span>
                <span className="num font-mono text-xs text-text-secondary">{c.score !== null ? `${c.score.toFixed(0)}/100` : "sin datos"}</span>
              </div>
              <div className="mb-1 h-1.5 bg-surface-sunken">
                <div className={`h-1.5 ${barColorClass(c.score)}`} style={{ width: `${c.score ?? 0}%` }} />
              </div>
              <p className="text-xs text-text-secondary">{c.explanation}</p>
            </div>
          ))}
          {row.price_used !== null && (
            <p className="num pt-1 text-[10px] text-text-tertiary">Valorada con un precio de {row.price_used.toFixed(2)} por acción.</p>
          )}
        </div>
      )}
    </div>
  );
}
