// ComparisonTable.tsx — las 3 versiones lado a lado en /funciona.
//
// Antes era un componente de cliente con Tanstack Table para 6 filas fijas
// sin orden ni filtros: JS enviado al navegador sin ninguna interacción que
// lo justificara, y cabeceras en inglés ("Conservative/Aggressive/Balanced")
// en una app en español. Ahora es una tabla HTML renderizada en servidor,
// con el mismo orden y nombres de versión que el resto de la app.
import { VERSION_LABELS, VERSION_ORDER } from "@/lib/labels";

export interface ComparisonRow {
  metric: string;
  conservative: string;
  aggressive: string;
  balanced: string;
}

const KEY = { CONSERVATIVE: "conservative", BALANCED: "balanced", AGGRESSIVE: "aggressive" } as const;

export function ComparisonTable({ rows }: { rows: ComparisonRow[] }) {
  return (
    <div className="overflow-x-auto border border-border-subtle">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-border-strong bg-surface-raised text-text-secondary">
            <th scope="col" className="py-2 pl-3 pr-4 font-medium">
              Métrica
            </th>
            {VERSION_ORDER.map((ver) => (
              <th key={ver} scope="col" className="py-2 pr-4 text-right font-medium last:pr-3">
                {VERSION_LABELS[ver]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.metric} className="border-b border-border-subtle">
              <th scope="row" className="py-2 pl-3 pr-4 font-normal text-text-secondary">
                {row.metric}
              </th>
              {VERSION_ORDER.map((ver) => (
                <td key={ver} className="num py-2 pr-4 text-right text-foreground last:pr-3">
                  {row[KEY[ver]]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
