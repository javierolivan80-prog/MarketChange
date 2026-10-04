"use client";

// SignalsTable.tsx — feed cronológico con sort/paginación vía Tanstack Table
// (el filtrado ya llegó hecho del servidor, ver SignalsFilterForm). Las filas
// son ligeras; el razonamiento completo de cada una se pide a
// /api/senales/[id] solo al desplegarla (SignalRowDetail).
import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import type { SignalDetailData, SignalFeedRow, StrategyVersion } from "@/lib/queries";
import { SignalAnalysis } from "@/components/SignalAnalysis";
import { Button } from "@/components/ui/Button";
import { DirectionBadge, SignedPct } from "@/components/ui/DirectionBadge";
import { ExportCsvButton } from "@/components/ExportCsvButton";
import { formatDate, formatFracAsPct } from "@/lib/format";
import { eventClassLabel, sourceLabel } from "@/lib/labels";
import type { Locale, T } from "@/lib/i18n";
import { useLocale, useT } from "@/components/i18n/LocaleProvider";
import { Meter } from "@/components/viz/Meter";

const columnHelper = createColumnHelper<SignalFeedRow>();

const buildColumns = (t: T, locale: Locale) => [
  columnHelper.accessor("d0_close_date", { header: t("Fecha", "Date"), cell: (c) => <span className="num">{formatDate(c.getValue(), locale)}</span> }),
  columnHelper.accessor("ticker", {
    header: "Ticker",
    cell: (c) => (
      <Link href={`/senales/${c.row.original.event_id}`} className="font-mono font-medium text-foreground hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  columnHelper.accessor("event_class", { header: t("Evento", "Event"), cell: (c) => eventClassLabel(c.getValue(), locale) }),
  columnHelper.accessor("source", { header: t("Fuente", "Source"), cell: (c) => sourceLabel(c.getValue()) }),
  columnHelper.accessor("novelty_score", {
    header: t("Sorpresa", "Surprise"),
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
    header: t("Señal", "Signal"),
    cell: (c) => <DirectionBadge value={c.getValue()} />,
  }),
  columnHelper.accessor("confidence", { header: t("Confianza del análisis", "Analysis confidence"), cell: (c) => <span className="num">{c.getValue().toFixed(0)}%</span> }),
  columnHelper.accessor("tech_confidence", {
    header: t("Confianza técnica", "Technical confidence"),
    cell: (c) => {
      const v = c.getValue();
      if (v === null) return <span className="text-text-tertiary">—</span>;
      const passes = c.row.original.tech_passes;
      return (
        <span
          className="inline-flex items-center gap-2"
          title={passes ? t("Pasa los filtros de riesgo", "Passes the risk filters") : t("No pasa los filtros de riesgo", "Does not pass the risk filters")}
        >
          <Meter value={v} strong={passes === true} width="w-12" label={`${t("Confianza técnica", "Technical confidence")} ${v}/100`} />
          <span className={`num ${passes ? "text-foreground" : "text-text-secondary"}`}>
            {v}
            {passes ? " ✓" : ""}
          </span>
        </span>
      );
    },
  }),
  columnHelper.accessor("ev_balanced", { header: t("Valor esperado", "Expected value"), cell: (c) => <span className="num">{formatFracAsPct(c.getValue())}</span> }),
  columnHelper.accessor("pnl_pct", {
    header: t("Resultado (si se operó)", "Result (if traded)"),
    cell: (c) => <SignedPct value={c.getValue()} />,
  }),
];

export function SignalsTable({ rows }: { rows: SignalFeedRow[] }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "d0_close_date", desc: true }]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const t = useT();
  const locale = useLocale();
  const columns = useMemo(() => buildColumns(t, locale), [t, locale]);

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
        <p className="text-xs text-text-tertiary">
          {rows.length} {t("eventos", "events")}
        </p>
        <ExportCsvButton rows={rows} />
      </div>

      <div className="overflow-x-auto border border-border-subtle">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <caption className="sr-only">
            {t(
              "Señales generadas por el sistema, con su convicción, confianza y resultado si se operó",
              "Signals generated by the system, with their conviction, confidence and result if traded",
            )}
          </caption>
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
                        aria-label={`${isExpanded ? t("Ocultar razonamiento de", "Hide reasoning for") : t("Ver razonamiento de", "Show reasoning for")} ${row.original.ticker}, ${formatDate(row.original.d0_close_date, locale)}`}
                        className="-m-2 p-2 text-text-tertiary hover:text-foreground"
                      >
                        {/* Un solo glifo que gira: se lee como el mismo control cambiando de estado, no como dos iconos. */}
                        <span
                          aria-hidden="true"
                          className={`inline-block transition-transform duration-(--motion-duration-micro) ease-(--motion-ease-out) motion-reduce:transition-none ${isExpanded ? "rotate-90" : ""}`}
                        >
                          ▸
                        </span>
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
        <Button onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} className="min-h-11 sm:min-h-9">
          ← {t("Anterior", "Previous")}
        </Button>
        <span className="num text-text-secondary">
          {t("Página", "Page")} {table.getState().pagination.pageIndex + 1} {t("de", "of")} {table.getPageCount() || 1}
        </span>
        <Button onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} className="min-h-11 sm:min-h-9">
          {t("Siguiente", "Next")} →
        </Button>
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
  const t = useT();
  const locale = useLocale();

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
        {state.status === "loading" && <AnalysisSkeleton label={t("Cargando análisis…", "Loading analysis…")} />}
        {state.status === "error" && (
          <p className="text-xs text-text-secondary">
            {t("No se pudo cargar el análisis.", "The analysis could not be loaded.")}{" "}
            <Link href={`/senales/${eventId}`} className="text-accent-700 underline dark:text-accent-400">
              {t("Abrir la señal", "Open the signal")}
            </Link>
          </p>
        )}
        {state.status === "ok" && (
          <div className="m-fade">
            <SignalAnalysis detail={state.detail} recommended={state.recommended} locale={locale} />
          </div>
        )}
      </td>
    </tr>
  );
}

/** Hueco con la forma del análisis (decisión arriba, rejilla de secciones
 * debajo) mientras llega de /api/senales/[id]. */
function AnalysisSkeleton({ label }: { label: string }) {
  return (
    <div role="status">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="m-skeleton">
        <div className="mb-4 h-16 border border-border-subtle bg-surface" />
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 border border-border-subtle bg-surface" />
          ))}
        </div>
      </div>
    </div>
  );
}
