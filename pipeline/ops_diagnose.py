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
    ("Tamaño de la base de datos", """
        SELECT pg_size_pretty(pg_database_size(current_database())) AS total"""),
    ("Tablas más grandes", """
        SELECT relname AS tabla, pg_size_pretty(pg_total_relation_size(relid)) AS tamano,
               n_live_tup AS filas_aprox
        FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 8"""),
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
    ("App — análisis de la IA (lo que alimenta Señales e Inicio)", """
        SELECT count(*) AS total, min(analyzed_at)::date AS primero, max(analyzed_at) AS ultimo,
               count(*) FILTER (WHERE analyzed_at > now() - interval '7 days') AS ultimos_7_dias,
               count(*) FILTER (WHERE trade_decision_balanced <> 'NO_TRADE') AS operables_balanced,
               count(*) FILTER (WHERE trade_decision_conservative <> 'NO_TRADE') AS operables_conservative,
               count(*) FILTER (WHERE trade_decision_aggressive <> 'NO_TRADE') AS operables_aggressive
        FROM event_analyses"""),
    ("App — motivo de NO_TRADE (versión BALANCED; cifras sustituidas por N)", """
        SELECT regexp_replace(coalesce(abstention_decision->'BALANCED'->>'reason_if_no_trade', '(opera)'),
                              '[-+]?[0-9][0-9.,]*', 'N', 'g') AS motivo,
               count(*) AS n
        FROM event_analyses GROUP BY 1 ORDER BY 2 DESC LIMIT 15"""),
    ("App — motivo REAL de los descartados antes de la IA (cifras = N)", """
        SELECT regexp_replace(coalesce(bull_analyst_output->>'reason', '?'), '[-+]?[0-9][0-9.,]*', 'N', 'g') AS motivo,
               count(*) AS n
        FROM event_analyses WHERE model_version_bull_bear = 'SKIPPED_OBJECTIVE_NO_TRADE'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15"""),
    ("App — rango high-low de D0 en los descartados por spread (percentiles, %)", """
        SELECT count(*) AS n,
               round(percentile_cont(0.1) WITHIN GROUP (ORDER BY v)::numeric, 2) AS p10,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY v)::numeric, 2) AS mediana,
               round(percentile_cont(0.9) WITHIN GROUP (ORDER BY v)::numeric, 2) AS p90
        FROM (SELECT (substring(bull_analyst_output->>'reason' FROM '/close=([0-9.]+)%'))::float AS v
              FROM event_analyses WHERE bull_analyst_output->>'reason' LIKE '%proxy de spread%') x"""),
    ("App — cómo se decidió (IA real, caché o descartado antes de la IA)", """
        SELECT model_version_bull_bear AS modelo, from_cache, count(*) AS n
        FROM event_analyses GROUP BY 1, 2 ORDER BY 3 DESC"""),
    ("App — EV e impacto de los eventos que sí pasaron por la IA", """
        SELECT count(*) AS n,
               round(avg(n_historical_analogues)) AS media_analogos,
               round(avg((impact_estimation->>'confidence')::numeric), 1) AS media_conf_impacto,
               round(avg(confidence_in_conviction), 1) AS media_conf_judge,
               round(avg(abs(net_conviction)), 2) AS media_abs_conviccion,
               round(max(abs(ev_aggressive)) * 100, 3) AS max_abs_ev_aggr_pct,
               round(avg(abs(ev_balanced)) * 100, 3) AS media_abs_ev_bal_pct
        FROM event_analyses WHERE model_version_bull_bear NOT IN ('SKIPPED_OBJECTIVE_NO_TRADE', 'SIN_IA_ANTES_DEL_CORTE')"""),
    ("App — informes (Cartera, Fiabilidad, Historial, Largo plazo)", """
        SELECT 'portfolio_reports' AS tabla, count(*) AS n, max(created_at) AS ultimo FROM portfolio_reports
        UNION ALL SELECT 'validation_reports', count(*), max(created_at) FROM validation_reports
        UNION ALL SELECT 'paper_trading_reports', count(*), max(created_at) FROM paper_trading_reports
        UNION ALL SELECT 'paper_trades', count(*), max(updated_at) FROM paper_trades
        UNION ALL SELECT 'portfolio_trades', count(*), NULL FROM portfolio_trades
        UNION ALL SELECT 'quality_scores', count(*), max(as_of_date)::timestamptz FROM quality_scores"""),
    ("Gasto de IA por día (libro ai_batches; real = de los tokens de la API)", """
        SELECT submitted_at::date AS dia, kind, count(*) AS batches, sum(n_requests) AS requests,
               sum(input_tokens) AS tokens_entrada, sum(output_tokens) AS tokens_salida,
               round(sum(cost_usd), 4) AS usd_real,
               count(*) FILTER (WHERE cost_usd IS NULL) AS sin_coste_real,
               round(sum(cost_usd) / nullif(sum(n_requests) FILTER (WHERE cost_usd IS NOT NULL), 0), 5) AS usd_por_request
        FROM ai_batches GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 30"""),
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
