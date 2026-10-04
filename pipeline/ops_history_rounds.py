"""ops_history_rounds.py — precios y CAR del histórico por tandas.

En un plan gratuito (~500 MB) no caben a la vez los precios de los ~290k
eventos del histórico. Pero el CAR de cada evento, una vez calculado, ya no
necesita sus precios. Así que se repite:

  1. ops_history_prices: bajar precios para eventos SIN CAR hasta el freno
     de tamaño (o hasta acabar).
  2. populate_car_results: calcular el CAR de lo que ya se puede.
  3. ops_prune --prices --vacuum-full: borrar los precios antiguos de
     empresas no operadas (su CAR ya está guardado) y compactar.

hasta que no quede nada por bajar, una tanda no calcule ningún CAR nuevo
(lo que falta no se puede calcular con datos de Yahoo) o se acabe el tiempo.

    python -m pipeline.ops_history_rounds --benchmark-start 2017-08-01 --minutes 240
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date

logger = logging.getLogger(__name__)

MAX_ROUNDS = 20


def _car_events(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM car_results WHERE window_days = 20")
        return cur.fetchone()["n"]


def run(conn, benchmark_start: date, max_db_mb: float, minutes: float) -> None:
    from pipeline import ops_history_prices, ops_prune
    from pipeline.backtest.populate_car_results import populate_missing_car_results

    deadline = time.monotonic() + minutes * 60
    for n in range(1, MAX_ROUNDS + 1):
        before = _car_events(conn)
        done = ops_history_prices.run(conn, benchmark_start, max_db_mb)
        populate_missing_car_results(conn)
        ops_prune.run(conn, vacuum_full=True, prices=True)
        gained = _car_events(conn) - before
        logger.info("Tanda %d: %d eventos nuevos con CAR (total %d)", n, gained, before + gained)
        if done:
            logger.info("No quedan precios por bajar")
            return
        if gained == 0:
            logger.warning("Una tanda sin ningún CAR nuevo: lo pendiente no se puede calcular con estos datos")
            return
        if time.monotonic() > deadline:
            logger.info("Tiempo agotado; la siguiente corrida sigue donde se quedó")
            return


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--benchmark-start", required=True)
    parser.add_argument("--minutes", type=float, default=240)
    args = parser.parse_args()
    run(
        get_connection(),
        date.fromisoformat(args.benchmark_start),
        float(os.environ.get("HISTORY_MAX_DB_MB", "400")),
        args.minutes,
    )
