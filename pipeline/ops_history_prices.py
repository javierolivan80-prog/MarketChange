"""ops_history_prices.py — precios para el histórico de eventos, acotados.

POR QUÉ NO SE USA TAL CUAL yfinance_backfill sobre el universo entero:
  1. Tamaño. Con la carga de histórico el universo pasa de ~800 a miles de
     empresas; bajar ~7 años de precios de TODAS son millones de filas
     (~200 B/fila con índice) y una base de datos de plan gratuito (~0,5 GB)
     se llenaría y tumbaría la app. Aquí cada ticker baja solo la ventana que
     sus eventos necesitan: [primer D0 - 260 días, último D0 + 45 días]
     (la ventana de estimación de factor_model es D-250..D-30 en días
     NATURALES, más el CAR a 20 sesiones, con margen).
  2. Huecos HACIA ATRÁS. yfinance_backfill solo pide lo que falta DESPUÉS del
     último día guardado. Un ticker con precios desde 2021 se daría por "al
     día" aunque a un evento de enero de 2021 le falte toda su ventana de
     estimación (2020). Aquí también se rellena lo anterior al primer día.

Y un freno: antes de cada lote mira el tamaño de la base de datos y para si
supera HISTORY_MAX_DB_MB (400 por defecto). Mejor histórico incompleto que
una base de datos llena.

    python -m pipeline.ops_history_prices --benchmark-start 2019-08-01
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, timedelta

logger = logging.getLogger(__name__)

PRE_DAYS = 260
POST_DAYS = 45
CHUNK = 100


def _month_floor(d: date) -> date:
    return d.replace(day=1)


def _month_ceil(d: date, cap: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return min(nxt - timedelta(days=1), cap)


def plan_gaps(
    needs: dict[str, tuple[date, date]], stored: dict[str, tuple[date, date]], today: date
) -> list[tuple[str, date, date]]:
    """(ticker, desde, hasta) que faltan: lo anterior al primer día guardado
    y lo posterior al último, dentro de la ventana necesaria. Las fechas de
    inicio y fin se redondean al mes para poder agrupar descargas."""
    gaps = []
    for ticker, (need_start, need_end) in needs.items():
        need_start, need_end = _month_floor(need_start), _month_ceil(need_end, today)
        have = stored.get(ticker)
        if have is None:
            gaps.append((ticker, need_start, need_end))
            continue
        first, last = have
        if need_start < first - timedelta(days=7):
            gaps.append((ticker, need_start, first - timedelta(days=1)))
        if need_end > last + timedelta(days=7):
            gaps.append((ticker, last + timedelta(days=1), need_end))
    return gaps


def _db_mb(conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database()) AS b")
        return cur.fetchone()["b"] / 1e6


def run(conn, benchmark_start: date, max_db_mb: float) -> bool:
    """Devuelve True si bajó todo lo que faltaba, False si paró por el freno
    de tamaño. Solo cuenta los eventos SIN CAR todavía: los que ya lo tienen
    no necesitan precios (y ops_prune puede haber borrado los suyos)."""
    from pipeline.analyze.enrichment import BENCHMARK_TICKERS
    from pipeline.ingest.yfinance_backfill import _descargar_grupo, _trading_days_expected, is_tradable_symbol

    today = date.today()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.ticker, min(e.d0_close_date) AS a, max(e.d0_close_date) AS b, count(*) AS n FROM events e "
            "LEFT JOIN car_results cr ON cr.event_id = e.event_id AND cr.window_days = 20 "
            "WHERE cr.event_id IS NULL GROUP BY e.ticker"
        )
        rows = [r for r in cur.fetchall() if is_tradable_symbol(r["ticker"])]
        needs = {r["ticker"]: (r["a"] - timedelta(days=PRE_DAYS), min(r["b"] + timedelta(days=POST_DAYS), today)) for r in rows}
        activity = {r["ticker"]: r["n"] for r in rows}
        cur.execute(
            "SELECT ticker, min(trade_date) AS a, max(trade_date) AS b FROM prices "
            "WHERE close_raw IS NOT NULL GROUP BY ticker"
        )
        stored = {r["ticker"]: (r["a"], r["b"]) for r in cur.fetchall()}
    for t in BENCHMARK_TICKERS:
        needs[t] = (benchmark_start, today)

    gaps = plan_gaps(needs, stored, today)
    groups: dict[tuple[date, date], list[str]] = defaultdict(list)
    for ticker, start, end in gaps:
        groups[(start, end)].append(ticker)
    logger.info("%d tickers con eventos; %d huecos en %d grupos de fechas", len(needs), len(gaps), len(groups))

    # Referencias primero (las usa el enriquecimiento de TODOS los eventos),
    # luego del hueco más reciente al más antiguo.
    order = sorted(groups.items(), key=lambda kv: (not any(t in BENCHMARK_TICKERS for t in kv[1]), -kv[0][0].toordinal()))
    for (start, end), tickers in order:
        # Dentro de cada grupo, los tickers con más eventos primero: si el
        # freno de tamaño corta, se pierden los menos útiles.
        tickers.sort(key=lambda t: -activity.get(t, 0))
        expected = _trading_days_expected(start, end)
        for i in range(0, len(tickers), CHUNK):
            size = _db_mb(conn)
            if size > max_db_mb:
                logger.warning(
                    "Base de datos en %.0f MB (> %.0f): paro la descarga para no llenarla. "
                    "Sube HISTORY_MAX_DB_MB si el plan lo permite.", size, max_db_mb,
                )
                return False
            _descargar_grupo(conn, tickers[i : i + CHUNK], start, end, expected)
    logger.info("Precios históricos completos. Base de datos: %.0f MB", _db_mb(conn))
    return True


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--benchmark-start", required=True)
    args = parser.parse_args()
    run(
        get_connection(),
        date.fromisoformat(args.benchmark_start),
        float(os.environ.get("HISTORY_MAX_DB_MB", "400")),
    )
