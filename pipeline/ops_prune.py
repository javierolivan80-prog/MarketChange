"""ops_prune.py — hace sitio en una base de datos de plan gratuito (~500 MB).

Borra solo lo que no puede servir para nada:
  1. Eventos de símbolos no operables (warrants, unidades, preferentes...,
     ver ticker_map.is_tradable_symbol): Yahoo no da precios de ellos, así
     que nunca tendrán CAR, análogos ni análisis. Solo se borran los que no
     tienen NINGUNA fila que los referencie (análisis, CAR, cartera...): lo
     que ya se usó en algún sitio se queda. Desde que upsert_events los
     descarta al ingerir, esto solo limpia lo que entró antes.
  2. Texto de filings de eventos de hace más de FILING_TEXT_KEEP_DAYS: la IA
     ya no los va a analizar y novelty.py mira como mucho 180 días atrás. El
     evento se queda; solo se vacía su texto (8.000 caracteres como mucho).
  3. Con --prices: precios de hace más de PRICE_KEEP_DAYS de empresas que la
     app no opera. Su único uso era calcular el CAR de sus eventos antiguos,
     que ya está guardado en car_results (unas decenas de bytes por evento
     frente a ~300 filas de precios). Se quedan SIEMPRE: las series de
     referencia (SPY, ^VIX, ETFs sectoriales), las empresas del universo
     invertible, las que tienen algún evento usado fuera de car_results
     (análisis, backtest, paper trading...) y todo lo reciente, que es lo
     que piden la ventana de estimación de los eventos nuevos y el plan
     técnico. Lo borrado se puede volver a bajar gratis de Yahoo; como
     ops_history_prices solo pide precios para eventos SIN CAR, no se vuelve
     a descargar en bucle.

Después, VACUUM ANALYZE para que ese hueco se reutilice. Con --vacuum-full
además se compactan `events` (y `prices` con --prices) y el espacio vuelve a
contar como libre en el plan, que es lo que mira el freno de tamaño
(bloquea la tabla unos minutos: solo fuera de la pasada nocturna).

    python -m pipeline.ops_prune [--vacuum-full] [--prices]
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FILING_TEXT_KEEP_DAYS = 400
# Ventana de estimación de factor_model: D-250..D-30 en días naturales. Un
# evento de hoy necesita ~250 días hacia atrás; 400 deja margen de sobra para
# los eventos recientes cuyo CAR aún no se ha podido cerrar.
PRICE_KEEP_DAYS = 400


def _referencing_tables(cur) -> list[tuple[str, str]]:
    """(tabla, columna) de cada clave foránea que apunta a events.event_id,
    leídas del catálogo: una tabla nueva que referencie eventos queda
    protegida sin tener que acordarse de añadirla aquí."""
    cur.execute(
        """
        SELECT c.conrelid::regclass::text AS tabla, a.attname AS columna
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f' AND c.confrelid = 'events'::regclass
        """
    )
    return [(r["tabla"], r["columna"]) for r in cur.fetchall()]


def prune_untradable_events(conn) -> int:
    from pipeline.ingest.ticker_map import is_tradable_symbol

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM events")
        bad = [r["ticker"] for r in cur.fetchall() if not is_tradable_symbol(r["ticker"])]
        if not bad:
            return 0
        guards = " ".join(
            f"AND NOT EXISTS (SELECT 1 FROM {t} x WHERE x.{c} = e.event_id)" for t, c in _referencing_tables(cur)
        )
        cur.execute(f"DELETE FROM events e WHERE e.ticker = ANY(%s) {guards}", (bad,))
        n = cur.rowcount
    conn.commit()
    logger.info("%d eventos borrados de %d símbolos no operables", n, len(bad))
    return n


def clear_old_filing_text(conn, keep_days: int = FILING_TEXT_KEEP_DAYS) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE events SET filing_text = NULL WHERE filing_text IS NOT NULL "
            "AND d0_close_date < current_date - %s",
            (keep_days,),
        )
        n = cur.rowcount
    conn.commit()
    logger.info("Texto vaciado en %d eventos de hace más de %d días", n, keep_days)
    return n


def prune_old_prices(conn, keep_days: int = PRICE_KEEP_DAYS) -> int:
    """Ver el punto 3 de la cabecera. Ticker a ticker, con un commit por
    ticker: un DELETE de cientos de miles de filas de golpe bloquearía la
    tabla y, si fallara a mitad, no habría borrado nada."""
    from pipeline.analyze.enrichment import BENCHMARK_TICKERS

    with conn.cursor() as cur:
        refs = [(t, c) for t, c in _referencing_tables(cur) if t != "car_results"]
        used = " ".join(
            f"AND NOT EXISTS (SELECT 1 FROM events e JOIN {t} x ON x.{c} = e.event_id WHERE e.ticker = t.ticker)"
            for t, c in refs
        )
        # Primero los tickers con algo antiguo (una pasada por el índice) y
        # luego los filtros por ticker: con las subconsultas por FILA de
        # prices serían más de un millón de comprobaciones.
        cur.execute(
            f"""
            WITH t AS (SELECT DISTINCT ticker FROM prices WHERE trade_date < current_date - %s)
            SELECT t.ticker FROM t
            WHERE NOT (t.ticker = ANY(%s))
              AND NOT EXISTS (SELECT 1 FROM universe u WHERE u.ticker = t.ticker AND u.in_investable_universe)
              {used}
            """,
            (keep_days, list(BENCHMARK_TICKERS)),
        )
        tickers = [r["ticker"] for r in cur.fetchall()]
    total = 0
    for ticker in tickers:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM prices WHERE ticker = %s AND trade_date < current_date - %s",
                (ticker, keep_days),
            )
            total += cur.rowcount
        conn.commit()
    logger.info("%d filas de precios antiguos borradas de %d empresas no operadas", total, len(tickers))
    return total


def _vacuum(conn, tables: list[str], full: bool) -> None:
    autocommit = conn.autocommit
    conn.autocommit = True  # VACUUM no puede ir dentro de una transacción
    try:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(f"VACUUM (FULL, ANALYZE) {table}" if full else f"VACUUM ANALYZE {table}")
    finally:
        conn.autocommit = autocommit


def run(conn, vacuum_full: bool = False, prices: bool = False) -> None:
    from pipeline.ingest.yfinance_backfill import database_mb

    before = database_mb(conn)
    prune_untradable_events(conn)
    clear_old_filing_text(conn)
    tables = ["events"]
    if prices:
        prune_old_prices(conn)
        tables.append("prices")
    try:
        _vacuum(conn, tables, vacuum_full)
    except Exception as exc:  # p. ej. sin sitio para la copia de VACUUM FULL: no es fatal
        logger.warning("VACUUM falló (%s); lo borrado se reutilizará igualmente", exc)
    logger.info("Base de datos: %.0f MB -> %.0f MB", before, database_mb(conn))


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vacuum-full", action="store_true")
    parser.add_argument("--prices", action="store_true", help="borra también precios antiguos de empresas no operadas")
    args = parser.parse_args()
    run(get_connection(), vacuum_full=args.vacuum_full, prices=args.prices)
