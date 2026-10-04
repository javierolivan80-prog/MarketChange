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

Después, VACUUM ANALYZE para que ese hueco se reutilice. Con --vacuum-full
además se compacta `events` y el espacio vuelve a contar como libre en el
plan (bloquea la tabla unos minutos: solo fuera de la pasada nocturna).

    python -m pipeline.ops_prune [--vacuum-full]
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FILING_TEXT_KEEP_DAYS = 400


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


def _vacuum(conn, full: bool) -> None:
    autocommit = conn.autocommit
    conn.autocommit = True  # VACUUM no puede ir dentro de una transacción
    try:
        with conn.cursor() as cur:
            cur.execute("VACUUM (FULL, ANALYZE) events" if full else "VACUUM ANALYZE events")
    finally:
        conn.autocommit = autocommit


def run(conn, vacuum_full: bool = False) -> None:
    from pipeline.ingest.yfinance_backfill import database_mb

    before = database_mb(conn)
    prune_untradable_events(conn)
    clear_old_filing_text(conn)
    try:
        _vacuum(conn, vacuum_full)
    except Exception as exc:  # p. ej. sin sitio para la copia de VACUUM FULL: no es fatal
        logger.warning("VACUUM falló (%s); lo borrado se reutilizará igualmente", exc)
    logger.info("Base de datos: %.0f MB -> %.0f MB", before, database_mb(conn))


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vacuum-full", action="store_true")
    args = parser.parse_args()
    run(get_connection(), vacuum_full=args.vacuum_full)
