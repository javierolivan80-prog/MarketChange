"""ops_diagnose.py — radiografía de solo lectura de la base de datos.

Responde, con números, a "¿por qué el pipeline no produce señales?": cuántos
eventos hay y de cuándo, si los tickers de esos eventos tienen precios ANTES
del evento (la ventana de estimación del modelo de factores pide ~60 sesiones
entre D-250 y D-30), hasta dónde llegan los factores Fama-French, y en qué
etapa se quedan los eventos (enrichment, CAR, análisis).

Solo SELECTs, en una transacción READ ONLY: lanzarlo nunca cambia nada.

    python -m pipeline.ops_diagnose
"""
from __future__ import annotations

from pipeline.db.connection import get_connection

QUERIES: list[tuple[str, str]] = [
    ("Eventos por mes (últimos 18)", """
        SELECT to_char(d0_close_date, 'YYYY-MM') AS mes, source, count(*) AS n
        FROM events GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 40"""),
    ("Rango de eventos", """
        SELECT min(d0_close_date) AS primero, max(d0_close_date) AS ultimo,
               count(*) AS total, count(DISTINCT ticker) AS tickers FROM events"""),
    ("Universo", """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE in_investable_universe) AS invertible
        FROM universe"""),
    ("Precios: cobertura global", """
        SELECT count(*) AS filas, count(DISTINCT ticker) AS tickers,
               min(trade_date) AS desde, max(trade_date) AS hasta FROM prices"""),
    ("Fama-French: cobertura", """
        SELECT count(*) AS filas, min(trade_date) AS desde, max(trade_date) AS hasta
        FROM fama_french_factors"""),
    ("Eventos según precios disponibles en su ventana de estimación (D-250..D-30)", """
        WITH w AS (
          SELECT e.event_id,
                 (SELECT count(*) FROM prices p WHERE p.ticker = e.ticker
                    AND p.trade_date BETWEEN e.d0_close_date - 250 AND e.d0_close_date - 30) AS n_px,
                 (SELECT count(*) FROM fama_french_factors f
                    WHERE f.trade_date BETWEEN e.d0_close_date - 250 AND e.d0_close_date - 30) AS n_ff,
                 (SELECT count(*) FROM prices p JOIN fama_french_factors f USING (trade_date)
                    WHERE p.ticker = e.ticker
                    AND p.trade_date BETWEEN e.d0_close_date - 250 AND e.d0_close_date - 30) AS n_both
          FROM events e
        )
        SELECT CASE WHEN n_both >= 60 THEN 'ok (>=60 sesiones con precio y factores)'
                    WHEN n_px >= 60 AND n_ff < 60 THEN 'faltan FACTORES Fama-French'
                    WHEN n_px < 60 AND n_ff >= 60 THEN 'faltan PRECIOS del ticker'
                    ELSE 'faltan precios y factores' END AS estado,
               count(*) AS eventos
        FROM w GROUP BY 1 ORDER BY 2 DESC"""),
    ("Eventos sin ningún precio de su ticker (muestra)", """
        SELECT e.ticker, count(*) AS eventos, max(e.d0_close_date) AS ultimo
        FROM events e
        WHERE NOT EXISTS (SELECT 1 FROM prices p WHERE p.ticker = e.ticker)
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15"""),
    ("Eventos con precios después de D0 (D+1..D+30)", """
        SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM prices p WHERE p.ticker = e.ticker
                                  AND p.trade_date > e.d0_close_date)) AS con_post,
               count(*) AS total FROM events e"""),
    ("car_results", """
        SELECT window_days, count(*) AS n, round(avg(n_estimation_days)) AS media_dias_est
        FROM car_results GROUP BY 1 ORDER BY 1"""),
    ("event_enrichment: días de estimación", """
        SELECT CASE WHEN n_estimation_days IS NULL THEN 'NULL'
                    WHEN n_estimation_days < 60 THEN '<60'
                    ELSE '>=60' END AS dias, count(*) AS n
        FROM event_enrichment GROUP BY 1 ORDER BY 1"""),
    ("event_enrichment: campos vacíos", """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE price_d0 IS NULL) AS sin_precio_d0,
               count(*) FILTER (WHERE vix_d0 IS NULL) AS sin_vix,
               count(*) FILTER (WHERE beta_vs_spy IS NULL) AS sin_beta
        FROM event_enrichment"""),
    ("event_analyses por estado", """
        SELECT count(*) AS total FROM event_analyses"""),
    ("Tickers de referencia (SPY, ^VIX)", """
        SELECT ticker, count(*) AS filas, min(trade_date) AS desde, max(trade_date) AS hasta
        FROM prices WHERE ticker IN ('SPY', '^VIX') GROUP BY 1"""),
    ("Análisis técnicos", """
        SELECT count(*) AS total, count(*) FILTER (WHERE passes_filters) AS pasan
        FROM technical_analyses"""),
]


def main() -> None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        for title, sql in QUERIES:
            print(f"\n=== {title} ===")
            try:
                cur.execute("SAVEPOINT q")
                cur.execute(sql)
                rows = cur.fetchall()
                cur.execute("RELEASE SAVEPOINT q")
            except Exception as exc:  # una consulta rota no tapa las demás
                cur.execute("ROLLBACK TO SAVEPOINT q")
                print(f"  (error: {exc})")
                continue
            if not rows:
                print("  (sin filas)")
            for r in rows:
                print("  " + " | ".join(f"{k}={v}" for k, v in r.items()))
    conn.rollback()
    conn.close()


if __name__ == "__main__":
    main()
