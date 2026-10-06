"""calidad_precios.py — controles de calidad de los precios (Tanda 4).

Marca en prices.calidad_motivo las velas que no pueden ser un dato real
(decisión del usuario, 2026-10-06):

- imposibles: cierre o apertura <= 0, máximo por debajo del mínimo, cierre
  o apertura fuera del rango [mínimo, máximo] o volumen negativo;
- picos que se deshacen: el cierre se mueve más de un 50 % respecto al día
  anterior y al día siguiente vuelve a menos de un 20 % del cierre previo.
  Un salto de verdad (una biotech tras la FDA) se mantiene; un dato malo
  sube y vuelve. Un salto que se mantiene NO se marca. No se aplica a los
  índices (^VIX...), que tienen picos reales que se deshacen.

Después marca en car_results.calidad_excluido los CAR cuya ventana
(estimación + evento, en días naturales como el resto del cálculo) toca una
vela marcada; los lectores de CAR (análogos, prior, event study) los
ignoran. El backtest excluye por su cuenta las operaciones cuya ventana toca
una vela marcada (portfolio_simulator.fetch_events_for_version).

No se borra nada: el marcado se recalcula entero para los tickers revisados,
así que una vela corregida por Yahoo deja de estar marcada sola. Solo se
revisan los tickers con precios descargados después de su última revisión.

    python -m pipeline.ingest.calidad_precios
"""
from __future__ import annotations

import logging

from pipeline import config

logger = logging.getLogger(__name__)

SALTO_SOSPECHOSO = 0.50   # más de un 50 % respecto al cierre anterior
VUELTA_MAXIMA = 0.20      # al día siguiente, a menos de un 20 % del cierre previo
TOLERANCIA_RANGO = 0.001  # redondeos de Yahoo en high/low

_REVISAR_SQL = """
CREATE TEMP TABLE calidad_revisar ON COMMIT DROP AS
SELECT p.ticker
FROM prices p
LEFT JOIN calidad_precios_revision r ON r.ticker = p.ticker
GROUP BY p.ticker, r.revisado_en
HAVING r.revisado_en IS NULL OR max(p.captured_at) > r.revisado_en
"""

_MARCAR_SQL = """
WITH serie AS (
    SELECT p.ticker, p.trade_date, p.open_raw, p.close_raw, p.high_raw, p.low_raw, p.volume,
           lag(p.close_raw) OVER w AS cierre_ant,
           lead(p.close_raw) OVER w AS cierre_sig
    FROM prices p
    JOIN calidad_revisar USING (ticker)
    WHERE p.close_raw IS NOT NULL
    WINDOW w AS (PARTITION BY p.ticker ORDER BY p.trade_date)
), motivos AS (
    SELECT ticker, trade_date,
           CASE
             WHEN close_raw <= 0 THEN 'cierre <= 0'
             WHEN open_raw <= 0 THEN 'apertura <= 0'
             WHEN high_raw IS NOT NULL AND low_raw IS NOT NULL AND high_raw < low_raw THEN 'máximo por debajo del mínimo'
             WHEN high_raw IS NOT NULL AND close_raw > high_raw * (1 + %(tol)s) THEN 'cierre por encima del máximo'
             WHEN low_raw IS NOT NULL AND close_raw < low_raw * (1 - %(tol)s) THEN 'cierre por debajo del mínimo'
             WHEN high_raw IS NOT NULL AND open_raw > high_raw * (1 + %(tol)s) THEN 'apertura por encima del máximo'
             WHEN low_raw IS NOT NULL AND open_raw < low_raw * (1 - %(tol)s) THEN 'apertura por debajo del mínimo'
             WHEN volume < 0 THEN 'volumen negativo'
             -- Los índices (^VIX...) tienen picos reales que se deshacen.
             WHEN ticker NOT LIKE '^%%' AND cierre_ant > 0 AND cierre_sig IS NOT NULL
                  AND abs(close_raw / cierre_ant - 1) > %(salto)s
                  AND abs(cierre_sig / cierre_ant - 1) < %(vuelta)s
               THEN 'pico de más del 50 %% que se deshace al día siguiente'
           END AS motivo
    FROM serie
)
UPDATE prices p SET calidad_motivo = m.motivo
FROM motivos m
WHERE p.ticker = m.ticker AND p.trade_date = m.trade_date
  AND p.calidad_motivo IS DISTINCT FROM m.motivo
"""

_REGISTRAR_SQL = """
INSERT INTO calidad_precios_revision (ticker, revisado_en)
SELECT ticker, now() FROM calidad_revisar
ON CONFLICT (ticker) DO UPDATE SET revisado_en = EXCLUDED.revisado_en
"""

# Solo los CAR de los tickers revisados ahora y los que nunca se miraron.
_MARCAR_CAR_SQL = """
UPDATE car_results cr SET calidad_excluido = sub.motivo, calidad_revisada = TRUE
FROM (
    SELECT cr2.event_id, cr2.window_days,
           (SELECT 'vela marcada el ' || p.trade_date || ': ' || p.calidad_motivo
            FROM prices p
            WHERE p.ticker = e.ticker AND p.calidad_motivo IS NOT NULL
              AND p.trade_date BETWEEN e.d0_close_date + %(est_inicio)s AND e.d0_close_date + cr2.window_days
            ORDER BY p.trade_date LIMIT 1) AS motivo
    FROM car_results cr2
    JOIN events e ON e.event_id = cr2.event_id
    WHERE NOT cr2.calidad_revisada OR e.ticker IN (SELECT ticker FROM calidad_revisar)
) sub
WHERE cr.event_id = sub.event_id AND cr.window_days = sub.window_days
  AND (cr.calidad_excluido IS DISTINCT FROM sub.motivo OR NOT cr.calidad_revisada)
"""


def revisar_calidad(conn) -> dict:
    """Marca las velas sospechosas de los tickers con precios nuevos y
    recalcula qué CAR quedan excluidos. Devuelve los totales."""
    with conn.cursor() as cur:
        cur.execute(_REVISAR_SQL)
        cur.execute(_MARCAR_SQL, {"tol": TOLERANCIA_RANGO, "salto": SALTO_SOSPECHOSO, "vuelta": VUELTA_MAXIMA})
        cambiadas = cur.rowcount
        cur.execute(_REGISTRAR_SQL)
        cur.execute(_MARCAR_CAR_SQL, {"est_inicio": config.ESTIMATION_WINDOW_DAYS[0]})
        car_cambiados = cur.rowcount
        cur.execute("SELECT count(*) AS n FROM prices WHERE calidad_motivo IS NOT NULL")
        marcadas = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM car_results WHERE calidad_excluido IS NOT NULL")
        car_excluidos = cur.fetchone()["n"]
    conn.commit()
    resultado = {
        "velas_cambiadas": cambiadas, "velas_marcadas": marcadas,
        "car_cambiados": car_cambiados, "car_excluidos": car_excluidos,
    }
    logger.info(
        "Calidad de precios: %d velas marcadas (%d cambios esta vez); %d CAR excluidos",
        marcadas, cambiadas, car_excluidos,
    )
    return resultado


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    print(revisar_calidad(get_connection()))
