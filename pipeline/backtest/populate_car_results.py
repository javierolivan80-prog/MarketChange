"""populate_car_results.py — calcula y guarda CAR para eventos que aún no lo tienen.

Era el hueco señalado en RUNBOOK.md tras la Fase 1 ("el orquestador que
conecta backtest/backtester.py con la tabla events") y se cierra aquí porque
analyze/historical_analogues.py (Fase 2, Etapa 6) depende directamente de que
car_results tenga filas — sin esto, todo impact_estimation de producción
tendría n_analogues=0 permanentemente, no por diseño sino por un paso que
faltaba conectar.

Calcula ambas ventanas (5 y 20 días) pedidas por backtest/backtester.py, para
cada evento con precio + factores suficientes. Un evento sin suficiente
historial (fit_factor_model devuelve None) se salta — no se guarda una fila
con CAR fabricado.

POR QUÉ ESTA FUNCIÓN NO FILTRA POR sample_split (IN_SAMPLE/OOS) — hallazgo de
auditoría investigado y descartado (IMPROVEMENT_PLAN.md R1): a diferencia de
portfolio_simulator.py y validation/event_study.py, que sí importan
`sample_split.date_bounds`, este módulo procesa TODOS los eventos pendientes
sin distinguir partición, y ESO ES CORRECTO, no un descuido:

  - El CAR de un evento es un estadístico point-in-time POR EVENTO —
    compute_car() ajusta el modelo de factores solo con datos de
    `estimation_window` (por defecto [-250,-30] días respecto al D0 de ESE
    evento) y mide el retorno anormal solo en [D+1, D+window_days] del MISMO
    evento. No hay ninguna comparación entre eventos ni ninguna estadística
    agregada aquí — nada que "mirar antes de tiempo" en el sentido de T6
    (ARCHITECTURE_LEAN.md, "ajustar parámetros solo en IN_SAMPLE, mirar OOS
    una sola vez"). Calcular el CAR de un evento fechado en el periodo OOS es
    simple procesamiento de datos ya disponibles (el precio D+20 ya existe),
    no una "mirada" a un resultado agregado de la estrategia.
  - La disciplina de partición SÍ importa, y SÍ se aplica, en el consumidor
    que agrega estos números en una conclusión: `event_study.py` filtra por
    `sample_split.date_bounds` al construir las estadísticas por clase de
    evento (media, t-test, MDE) — ahí es donde un vistazo prematuro a OOS
    invalidaría el holdout, y ahí es donde ya está bloqueado.
  - `historical_analogues.get_historical_analogues()` (el otro consumidor de
    car_results) tampoco necesita saber de "sample": ya filtra por
    `d0_close_date < as_of_date` del evento que se está analizando en ESE
    momento — point-in-time estricto y suficiente, sea ese evento IN_SAMPLE u
    OOS, porque un análogo con fecha anterior a la del evento actual siempre
    fue legítimamente "conocido" en ese momento, con independencia de en qué
    lado del split de fechas caiga.

Particionar este módulo por sample sería, además de innecesario, activamente
contraproducente: dejaría car_results sin poblar para los eventos OOS hasta
que alguien lo pida explícitamente, congelando de nuevo n_analogues=0 para
cualquier evento OOS que event_analysis_pipeline.py intente analizar antes
de esa corrida manual — exactamente el bug que este módulo se creó para
cerrar (ver el párrafo de arriba).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from pipeline.backtest.backtester import compute_car

logger = logging.getLogger(__name__)

WINDOW_DAYS_LIST = [5, 20]


def _load_ticker_returns(conn, ticker: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, close_raw, adj_factor FROM prices WHERE ticker = %s ORDER BY trade_date",
            (ticker,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").astype({"close_raw": float, "adj_factor": float})
    adj_close = df["close_raw"] * df["adj_factor"].fillna(1.0)
    return pd.DataFrame({"ret": adj_close.pct_change()}).dropna()


def _load_factor_returns(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("SELECT trade_date, mkt_rf, smb, hml, rf FROM fama_french_factors ORDER BY trade_date")
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date").astype(float)


def _pending_page(conn, after_event_id: int, limit: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.ticker, e.d0_close_date
            FROM events e
            LEFT JOIN car_results cr ON cr.event_id = e.event_id AND cr.window_days = 20
            WHERE cr.event_id IS NULL AND e.event_id > %s
            ORDER BY e.event_id
            LIMIT %s
            """,
            (after_event_id, limit),
        )
        return cur.fetchall()


def purge_incomplete_car_results(conn) -> int:
    """Borra los CAR guardados con la ventana de evento a medias, de antes de
    que compute_car lo impidiera (BUGS_REPORT.md H-02), para que se vuelvan a
    calcular cuando la ventana esté completa. Una fila es sospechosa si:
      - se calculó antes de que terminara su ventana (computed_at <= D0+window), o
      - su ventana termina después del último día con factores Fama-French
        (publicados con retraso): el join con los factores la habría cortado.
    Idempotente: lo que compute_car guarda ahora nunca cumple ninguna de las
    dos condiciones. Son eventos recientes, cuyos precios se conservan
    (ops_prune guarda siempre los últimos 400 días)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM car_results cr
            USING events e
            WHERE e.event_id = cr.event_id
              AND (cr.computed_at::date <= e.d0_close_date + cr.window_days
                   OR e.d0_close_date + cr.window_days > (SELECT max(trade_date) FROM fama_french_factors))
            """
        )
        n = cur.rowcount
    conn.commit()
    if n:
        logger.info("%d CAR con la ventana incompleta borrados para recalcularlos", n)
    return n


# Fecha del arreglo de H-11: las ratios de volatilidad guardadas antes se
# calcularon con la volatilidad de los últimos 60 días de TODA la serie
# (datos posteriores a D0).
VOL_RATIO_POINT_IN_TIME_SINCE = "2026-10-05"


def reset_lookahead_volume_ratios(conn) -> int:
    """Anula abnormal_volume_ratio en los CAR calculados antes de que la
    volatilidad base fuera point-in-time (BUGS_REPORT.md H-11).

    No se borra la fila entera para recalcularla, como en
    purge_incomplete_car_results: el CAR en sí es correcto, y recalcularlo
    exige precios que ops_prune ya puede haber borrado (se perdería). Sin la
    ratio, los análogos simplemente no la promedian y la tesis no la usa para
    saturar: mejor ningún dato que uno con información futura. Idempotente."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE car_results SET abnormal_volume_ratio = NULL "
            "WHERE abnormal_volume_ratio IS NOT NULL AND computed_at < %s::date",
            (VOL_RATIO_POINT_IN_TIME_SINCE,),
        )
        n = cur.rowcount
    conn.commit()
    if n:
        logger.info("%d ratios de volatilidad calculadas con datos futuros anuladas (H-11)", n)
    return n


def populate_missing_car_results(conn, limit: int = 1000) -> int:
    """Recorre TODOS los eventos sin CAR, en páginas de `limit`.

    Antes solo miraba los `limit` primeros por event_id. Los eventos cuyo CAR
    no se puede calcular (sin precios, deslistados, ventana aún abierta) se
    quedan pendientes para siempre, y en cuanto se juntaban 1000 al principio
    de la cola, ningún evento nuevo recibía CAR nunca más: los análogos
    históricos (Etapa 6) se congelaban sin ningún error visible.
    """
    if not _pending_page(conn, 0, 1):
        return 0

    factor_returns = _load_factor_returns(conn)
    if factor_returns.empty:
        logger.warning("Sin factores Fama-French cargados — no se puede calcular ningún CAR todavía")
        return 0

    stored = 0
    ticker_cache: dict[str, pd.DataFrame] = {}
    last_id = 0
    while True:
        pending = _pending_page(conn, last_id, limit)
        if not pending:
            break
        last_id = pending[-1]["event_id"]
        stored += _compute_page(conn, pending, factor_returns, ticker_cache)
        conn.commit()
    return stored


def _compute_page(conn, pending: list[dict], factor_returns: pd.DataFrame, ticker_cache: dict) -> int:
    stored = 0
    for row in pending:
        ticker = row["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = _load_ticker_returns(conn, ticker)
        event_prices = ticker_cache[ticker]
        if event_prices.empty:
            continue

        for window_days in WINDOW_DAYS_LIST:
            result = compute_car(event_prices, factor_returns, row["d0_close_date"], window_days)
            if result is None:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO car_results (event_id, window_days, car, abnormal_volume_ratio, n_estimation_days,
                                             resid_std, n_event_days)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id, window_days) DO UPDATE SET
                        car = EXCLUDED.car, abnormal_volume_ratio = EXCLUDED.abnormal_volume_ratio,
                        n_estimation_days = EXCLUDED.n_estimation_days, resid_std = EXCLUDED.resid_std,
                        n_event_days = EXCLUDED.n_event_days, computed_at = now()
                    """,
                    (row["event_id"], window_days, result.car, result.abnormal_volume_ratio, result.n_estimation_days,
                     result.resid_std, result.n_event_days),
                )
            stored += 1
    return stored


# Cuántos eventos sin resid_std se intentan completar por corrida: el
# histórico tiene decenas de miles y cada uno exige rehacer la regresión.
FILL_RESID_STD_MAX_EVENTS = 5000


def fill_missing_resid_std(conn, max_events: int = FILL_RESID_STD_MAX_EVENTS) -> int:
    """Completa resid_std/n_event_days de los CAR guardados antes de que
    existieran esas columnas (H-19), solo donde aún hay precios de la ventana
    de estimación (ops_prune borra los antiguos de empresas no operadas).

    No toca el CAR: si el recalculado no coincide con el guardado (precios
    reajustados desde entonces), no se mezcla un resid_std de otra serie y la
    fila se queda sin él."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT e.event_id, e.ticker, e.d0_close_date
            FROM car_results cr
            JOIN events e ON e.event_id = cr.event_id
            WHERE cr.resid_std IS NULL
              AND EXISTS (SELECT 1 FROM prices p WHERE p.ticker = e.ticker
                          AND p.trade_date <= e.d0_close_date - 200)
            ORDER BY e.event_id
            LIMIT %s
            """,
            (max_events,),
        )
        pending = cur.fetchall()
    if not pending:
        return 0
    factor_returns = _load_factor_returns(conn)
    ticker_cache: dict[str, pd.DataFrame] = {}
    filled = skipped = 0
    for row in pending:
        ticker = row["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = _load_ticker_returns(conn, ticker)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT window_days, car FROM car_results WHERE event_id = %s AND resid_std IS NULL",
                (row["event_id"],),
            )
            stored = cur.fetchall()
        for fila in stored:
            result = compute_car(ticker_cache[ticker], factor_returns, row["d0_close_date"], fila["window_days"])
            if result is None or not np.isclose(result.car, float(fila["car"]), rtol=1e-6, atol=1e-9):
                skipped += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE car_results SET resid_std = %s, n_event_days = %s WHERE event_id = %s AND window_days = %s",
                    (result.resid_std, result.n_event_days, row["event_id"], fila["window_days"]),
                )
            filled += 1
        conn.commit()
    logger.info("resid_std completado en %d CAR; %d sin precios iguales a los de entonces", filled, skipped)
    return filled


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    conn = get_connection()
    reset_lookahead_volume_ratios(conn)
    purged = purge_incomplete_car_results(conn)
    if purged:
        print(f"{purged} CAR con la ventana incompleta borrados (se recalculan cuando la ventana esté completa)")
    n = populate_missing_car_results(conn)
    print(f"{n} filas de car_results calculadas/actualizadas")
    fill_missing_resid_std(conn)
