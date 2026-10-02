"use client";

// SignalsTable.tsx — feed cronológico con sort/paginación vía Tanstack Table
// (el filtrado ya llegó hecho del servidor, ver SignalsFilterForm). Las filas
// son ligeras; el razonamiento completo de cada una se pide a
// /api/senales/[id] solo al desplegarla (SignalRowDetail).
import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import type { SignalDetailData, SignalFeedRow, StrategyVersion } from "@/lib/queries";
import { SignalAnalysis } from "@/components/SignalAnalysis";
import { DirectionBadge, SignedPct } from "@/components/ui/DirectionBadge";
import { ExportCsvButton } from "@/components/ExportCsvButton";
import { formatDate, formatFracAsPct } from "@/lib/format";
import { eventClassLabel, sourceLabel } from "@/lib/labels";

const columnHelper = createColumnHelper<SignalFeedRow>();

const columns = [
  columnHelper.accessor("d0_close_date", { header: "Fecha", cell: (c) => <span className="num">{formatDate(c.getValue())}</span> }),
  columnHelper.accessor("ticker", {
    header: "Ticker",
    cell: (c) => (
      <Link href={`/senales/${c.row.original.event_id}`} className="font-mono font-medium text-foreground hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  columnHelper.accessor("event_class", { header: "Evento", cell: (c) => eventClassLabel(c.getValue()) }),
  columnHelper.accessor("source", { header: "Fuente", cell: (c) => sourceLabel(c.getValue()) }),
  columnHelper.accessor("novelty_score", {
    header: "Sorpresa",
    cell: (c) => (
      <div className="flex w-20 items-center gap-1.5">
        <div className="h-1.5 flex-1 rounded-full bg-surface-sunken">
          <div className="h-1.5 rounded-full bg-accent-500" style={{ width: `${c.getValue()}%` }} />
        </div>
        <span className="num w-7 text-right text-xs text-text-secondary">{c.getValue()}</span>
      </div>
    ),
  }),
  columnHelper.accessor("signal", {
    header: "Señal",
    cell: (c) => <DirectionBadge value={c.getValue()} />,
  }),
  columnHelper.accessor("confidence", { header: "Confianza", cell: (c) => <span className="num">{c.getValue().toFixed(0)}%</span> }),
  columnHelper.accessor("ev_balanced", { header: "Valor esperado", cell: (c) => <span className="num">{formatFracAsPct(c.getValue())}</span> }),
  columnHelper.accessor("pnl_pct", {
    header: "Resultado (si se operó)",
    cell: (c) => <SignedPct value={c.getValue()} />,
  }),
];

export function SignalsTable({ rows }: { rows: SignalFeedRow[] }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "d0_close_date", desc: true }]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  });

  function toggle(eventId: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs text-text-tertiary">{rows.length} eventos</p>
        <ExportCsvButton rows={rows} />
      </div>

      <div className="overflow-x-auto border border-border-subtle">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <caption className="sr-only">Señales generadas por el sistema, con su convicción, confianza y resultado si se operó</caption>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-border-strong bg-surface-raised">
                <th className="w-8 py-2 pl-3"></th>
                {hg.headers.map((h) => {
                  const sortState = h.column.getIsSorted();
                  return (
                    <th key={h.id} className="py-2 pr-4 font-medium text-text-secondary" aria-sort={sortState === "asc" ? "ascending" : sortState === "desc" ? "descending" : "none"}>
                      <button
                        type="button"
                        onClick={h.column.getToggleSortingHandler()}
                        className="inline-flex select-none items-center gap-1 hover:text-foreground"
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {sortState && <span aria-hidden="true">{sortState === "asc" ? "▲" : "▼"}</span>}
                      </button>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const isExpanded = expanded.has(row.original.event_id);
              return (
                <Fragment key={row.id}>
                  <tr className="border-b border-border-subtle hover:bg-surface-raised">
                    <td className="py-2 pl-3">
                      <button
                        onClick={() => toggle(row.original.event_id)}
                        aria-expanded={isExpanded}
                        aria-label={`${isExpanded ? "Ocultar" : "Ver"} razonamiento de ${row.original.ticker}, ${formatDate(row.original.d0_close_date)}`}
                        className="-m-2 p-2 text-text-tertiary hover:text-foreground"
                      >
                        {isExpanded ? "▾" : "▸"}
                      </button>
                    </td>
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="py-2 pr-4">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                  {isExpanded && <SignalRowDetail eventId={row.original.event_id} colSpan={columns.length + 1} />}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center gap-2 text-sm">
        <button
          onClick={() => table.previousPage()}
          disabled={!table.getCanPreviousPage()}
          className="min-h-11 border border-border-strong px-3 disabled:opacity-40 sm:min-h-0 sm:py-1"
        >
          ← Anterior
        </button>
        <span className="num text-text-secondary">
          Página {table.getState().pagination.pageIndex + 1} de {table.getPageCount() || 1}
        </span>
        <button
          onClick={() => table.nextPage()}
          disabled={!table.getCanNextPage()}
          className="min-h-11 border border-border-strong px-3 disabled:opacity-40 sm:min-h-0 sm:py-1"
        >
          Siguiente →
        </button>
      </div>
    </div>
  );
}

type DetailState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ok"; detail: SignalDetailData; recommended: StrategyVersion };

function SignalRowDetail({ eventId, colSpan }: { eventId: number; colSpan: number }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/senales/${eventId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((body) => !cancelled && setState({ status: "ok", detail: body.detail, recommended: body.recommended }))
      .catch(() => !cancelled && setState({ status: "error" }));
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  return (
    <tr className="border-b border-border-subtle bg-surface-raised">
      <td colSpan={colSpan} className="px-4 py-4">
        {state.status === "loading" && <p className="text-xs text-text-tertiary">Cargando análisis…</p>}
        {state.status === "error" && (
          <p className="text-xs text-text-secondary">
            No se pudo cargar el análisis.{" "}
            <Link href={`/senales/${eventId}`} className="text-accent-700 underline dark:text-accent-400">
              Abrir la señal
            </Link>
          </p>
        )}
        {state.status === "ok" && <SignalAnalysis detail={state.detail} recommended={state.recommended} />}
      </td>
    </tr>
  );
}
