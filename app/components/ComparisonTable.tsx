"use client";

// ComparisonTable.tsx — TAB 4 del spec: "Side-by-side comparison de las 3
// versiones". Primer uso de Tanstack Table en el proyecto (pedido
// explícitamente por el spec de la Fase 5) — para esta tabla en concreto
// (6 filas fijas, sin filtros) es más aparato del que hace falta, pero se
// usa aquí para tener un ejemplo mínimo antes de la tabla que sí lo
// necesita de verdad (signals feed, con sort/filtro/paginación reales).
import { flexRender, getCoreRowModel, useReactTable, createColumnHelper } from "@tanstack/react-table";

export interface ComparisonRow {
  metric: string;
  conservative: string;
  aggressive: string;
  balanced: string;
}

const columnHelper = createColumnHelper<ComparisonRow>();
const columns = [
  columnHelper.accessor("metric", { header: "Métrica" }),
  columnHelper.accessor("conservative", { header: "Conservative" }),
  columnHelper.accessor("aggressive", { header: "Aggressive" }),
  columnHelper.accessor("balanced", { header: "Balanced" }),
];

export function ComparisonTable({ rows }: { rows: ComparisonRow[] }) {
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <div className="overflow-x-auto rounded border border-border-subtle">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-border-strong bg-surface-raised text-text-secondary">
              {hg.headers.map((h) => (
                <th key={h.id} className="py-2 pl-3 pr-4 font-medium first:pl-3 last:pr-3">
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-b border-border-subtle">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="num py-2 pl-3 pr-4 font-mono text-foreground first:pl-3 last:pr-3">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
