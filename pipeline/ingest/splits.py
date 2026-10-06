"""splits.py — historial de splits y precio negociado (BUGS_REPORT.md H-04).

Yahoo devuelve el Close ajustado por los splits posteriores, en la base del
día de la descarga: una acción a 0,50 $ en 2022 con un contrasplit 1:50 en
2024 aparece a 25 $ en 2022. Para cualquier cifra absoluta en D0 (la
capitalización que fija el deslizamiento, H-32) hace falta el precio que se
negociaba de verdad:

    negociado(D) = close_raw(D) × Π ratio de los splits con fecha en (D, captured_at]

(los splits posteriores a la descarga de esa fila no la afectan todavía).

El historial se pide a Yahoo por ticker (yf.Ticker(t).splits), por tandas:
primero los tickers que nunca se revisaron y después los que se volvieron a
descargar desde la última revisión (un split rebaja la serie, ver
yfinance_backfill.TOLERANCIA_CAMBIO_DE_BASE). Sin historial revisado, el
precio negociado no se puede calcular y la capitalización queda acotada por
la actual (LEAST en portfolio_simulator.MARKET_CAP_D0_SQL).

    python -m pipeline.ingest.splits [--max N]
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MAX_TICKERS_POR_CORRIDA = int(os.environ.get("SPLITS_MAX_TICKERS", "400"))

# Precio negociado de la fila `p` de prices (ver docstring). NULL si el
# ticker no tiene el historial de splits revisado: no se supone «sin splits».
PRECIO_NEGOCIADO_SQL = """
    CASE WHEN EXISTS (SELECT 1 FROM splits_revision sr WHERE sr.ticker = p.ticker)
         THEN p.close_raw * coalesce((
             SELECT exp(sum(ln(s.ratio))) FROM splits s
             WHERE s.ticker = p.ticker AND s.split_date > p.trade_date
               AND s.split_date <= p.captured_at::date
         ), 1)
    END
"""


REVISION_MAX_DIAS = 90  # aunque no haya cambio de base, se vuelve a pedir


def tickers_por_revisar(conn, limite: int) -> list[str]:
    """Tickers con eventos cuyo historial hay que pedir:
    - nunca revisados;
    - con la serie entera vuelta a descargar después de la revisión (la
      reparación de base de yfinance_backfill, que es lo que provoca un
      split: incluso la fila más antigua tiene captured_at posterior). La
      descarga incremental diaria solo toca los últimos días y no cuenta;
    - o revisados hace más de REVISION_MAX_DIAS.
    Los que llevan más tiempo sin revisar, primero."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.ticker
            FROM prices p
            LEFT JOIN splits_revision sr ON sr.ticker = p.ticker
            WHERE p.ticker NOT LIKE '^%%' AND p.close_raw IS NOT NULL
              AND p.ticker IN (SELECT ticker FROM events WHERE ticker IS NOT NULL)
            GROUP BY p.ticker, sr.revisado_en
            HAVING sr.revisado_en IS NULL
                OR min(p.captured_at) > sr.revisado_en
                OR sr.revisado_en < now() - make_interval(days => %s)
            ORDER BY sr.revisado_en NULLS FIRST, p.ticker
            LIMIT %s
            """,
            (REVISION_MAX_DIAS, limite),
        )
        return [r["ticker"] for r in cur.fetchall()]


def guardar_splits(conn, ticker: str, splits: list[tuple]) -> None:
    """splits: [(fecha, ratio)]. Sustituye el historial del ticker entero
    (Yahoo es la fuente; si corrige un split, se corrige aquí) y lo marca
    como revisado."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM splits WHERE ticker = %s", (ticker,))
        for fecha, ratio in splits:
            if ratio and ratio > 0:
                cur.execute(
                    "INSERT INTO splits (ticker, split_date, ratio) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (ticker, fecha, float(ratio)),
                )
        cur.execute(
            "INSERT INTO splits_revision (ticker, revisado_en) VALUES (%s, now()) "
            "ON CONFLICT (ticker) DO UPDATE SET revisado_en = EXCLUDED.revisado_en",
            (ticker,),
        )
    conn.commit()


def _pedir_splits(ticker: str) -> list[tuple]:
    """Historial de splits de Yahoo. yfinance no lanza excepción cuando falla
    (devuelve vacío), y un vacío no se puede distinguir de «sin splits»: por
    eso se pide la serie entera con sus acciones corporativas y, si viene
    vacía, se lanza para NO marcar el ticker como revisado (un deslistado,
    justo la microcap con contrasplit, conserva el tope por la capitalización
    actual en vez de quedarse sin splits)."""
    import yfinance as yf

    historia = yf.Ticker(ticker).history(period="max", actions=True, auto_adjust=False)
    if historia is None or historia.empty:
        raise RuntimeError("Yahoo no devolvió historia (¿deslistado o fallo de red?)")
    if "Stock Splits" not in historia:
        return []
    serie = historia["Stock Splits"]
    return [(idx.date(), float(v)) for idx, v in serie.items() if v and float(v) > 0]


def actualizar_splits(conn, limite: int = MAX_TICKERS_POR_CORRIDA, pedir=_pedir_splits) -> dict:
    """Pide y guarda el historial de los tickers pendientes. Un fallo de un
    ticker no para el resto (queda pendiente para la siguiente corrida)."""
    hechos, fallidos = 0, 0
    for ticker in tickers_por_revisar(conn, limite):
        try:
            guardar_splits(conn, ticker, pedir(ticker))
            hechos += 1
        except Exception as exc:  # noqa: BLE001 — un ticker no para el resto
            conn.rollback()
            fallidos += 1
            logger.warning("Sin historial de splits para %s: %s", ticker, exc)
    logger.info("Splits: %d tickers revisados, %d fallidos", hechos, fallidos)
    return {"revisados": hechos, "fallidos": fallidos}


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max", type=int, default=MAX_TICKERS_POR_CORRIDA)
    print(actualizar_splits(get_connection(), parser.parse_args().max))
