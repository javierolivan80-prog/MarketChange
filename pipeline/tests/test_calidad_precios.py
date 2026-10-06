"""Controles de calidad de precios (Tanda 4; decisión del usuario, 2026-10-06):
velas imposibles y picos de más del 50 % que se deshacen al día siguiente se
marcan; un salto que se mantiene no. Los CAR y las operaciones cuya ventana
toca una vela marcada se excluyen."""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE calidad_precios_revision, car_results, event_analyses, event_enrichment, events, prices, universe "
            "RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _dias(inicio: date, n: int) -> list[date]:
    out, d = [], inicio
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _serie(conn, ticker: str, cierres: list[float], inicio: date = date(2024, 1, 2), velas: dict | None = None) -> list[date]:
    dias = _dias(inicio, len(cierres))
    with conn.cursor() as cur:
        for i, (d, c) in enumerate(zip(dias, cierres)):
            hi, lo = c * 1.01, c * 0.99
            if velas and i in velas:
                hi, lo = velas[i]
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, close_raw, high_raw, low_raw, adj_factor, volume) "
                "VALUES (%s, %s, %s, %s, %s, 1.0, 1000)",
                (ticker, d, c, hi, lo),
            )
    conn.commit()
    return dias


def _motivos(conn, ticker: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT trade_date, calidad_motivo FROM prices WHERE ticker = %s AND calidad_motivo IS NOT NULL", (ticker,))
        return {r["trade_date"]: r["calidad_motivo"] for r in cur.fetchall()}


def test_pico_que_se_deshace_se_marca_y_un_salto_que_se_mantiene_no(conn):
    from pipeline.ingest.calidad_precios import revisar_calidad

    pico = _serie(conn, "PICO", [10, 10, 10, 30, 10.5, 10])
    _serie(conn, "FDA", [10, 10, 10, 30, 31, 30])  # aprobación: sube y se queda
    _serie(conn, "CAIDA", [10, 10, 4, 10, 10])  # baja más del 50 % y vuelve
    revisar_calidad(conn)
    assert _motivos(conn, "PICO") == {pico[3]: "pico de más del 50 % que se deshace al día siguiente"}
    assert _motivos(conn, "FDA") == {}
    assert len(_motivos(conn, "CAIDA")) == 1


def test_velas_imposibles(conn):
    from pipeline.ingest.calidad_precios import revisar_calidad

    dias = _serie(conn, "MALA", [10, 10, 10, 10], velas={1: (9.0, 11.0), 2: (12.0, 11.0)})
    revisar_calidad(conn)
    motivos = _motivos(conn, "MALA")
    assert motivos[dias[1]] == "máximo por debajo del mínimo"
    assert motivos[dias[2]] == "cierre por debajo del mínimo"
    assert dias[0] not in motivos and dias[3] not in motivos


def test_solo_revisa_lo_nuevo_y_una_correccion_desmarca(conn):
    from pipeline.ingest.calidad_precios import revisar_calidad

    dias = _serie(conn, "PICO", [10, 10, 10, 30, 10.5, 10])
    assert revisar_calidad(conn)["velas_marcadas"] == 1
    # Sin descargas nuevas el ticker no se vuelve a mirar: aunque se borre la
    # marca a mano, no se recalcula (prueba de que se salta, no de que no cambia).
    with conn.cursor() as cur:
        cur.execute("UPDATE prices SET calidad_motivo = NULL WHERE ticker = 'PICO'")
    conn.commit()
    assert revisar_calidad(conn)["velas_marcadas"] == 0
    with conn.cursor() as cur:
        cur.execute("UPDATE prices SET captured_at = now() + interval '1 minute' WHERE ticker = 'PICO' AND trade_date = %s", (dias[5],))
    conn.commit()
    assert revisar_calidad(conn)["velas_marcadas"] == 1  # descarga nueva: se vuelve a mirar
    with conn.cursor() as cur:  # Yahoo corrige el dato: nueva descarga
        cur.execute("UPDATE prices SET close_raw = 10, high_raw = 10.1, low_raw = 9.9, captured_at = now() + interval '2 minutes' WHERE ticker = 'PICO' AND trade_date = %s", (dias[3],))
    conn.commit()
    assert revisar_calidad(conn)["velas_marcadas"] == 0


def _evento(conn, cik: str, ticker: str, d0: date, regla: str = "LONG") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) VALUES (%s, %s, 'X', %s, %s) "
            "ON CONFLICT (cik) DO NOTHING",
            (cik, ticker, d0, d0),
        )
        cur.execute(
            """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes, accession_number,
                   source_url, filed_at, d0_close_date, classification_method, classification_confidence, raw_text_hash)
               VALUES (%s, %s, 'EDGAR', FALSE, '8K_2.02_EARNINGS', ARRAY['2.02'], %s, 'https://x', %s, %s, 'RULE', 1.0, %s)
               RETURNING event_id""",
            (cik, ticker, f"acc-{cik}-{d0}", d0, d0, f"h-{cik}-{d0}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, 20, 0.05, 200)",
            (event_id,),
        )
        cur.execute(
            """INSERT INTO event_analyses (event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                   judge_output, net_conviction, confidence_in_conviction, impact_estimation, n_historical_analogues,
                   ev_calculation, ev_conservative, ev_aggressive, ev_balanced, abstention_decision,
                   trade_decision_conservative, trade_decision_aggressive, trade_decision_balanced,
                   model_version_bull_bear, model_version_judge, decision_sin_ia)
               VALUES (%s, 80, '{}', '{}', '{}', '{}', 0, 0, '{}', 20, '{}', 0.03, 0.03, 0.03, '{}',
                   'NO_TRADE', 'NO_TRADE', 'NO_TRADE', 'm', 'm', %s)""",
            (event_id, '{"regla_historica": {"net_conviction": 1, "confidence_in_conviction": 100, "ev_conservative": 0.03, '
             '"ev_balanced": 0.03, "ev_aggressive": 0.03, "decisiones": {"BALANCED": {"trade_decision": "%s"}}}}' % regla),
        )
    conn.commit()
    return event_id


def test_car_y_operaciones_que_tocan_una_vela_marcada_se_excluyen(conn):
    from pipeline.analyze.historical_analogues import get_historical_analogues
    from pipeline.backtest.portfolio_report import excluidos_por_calidad
    from pipeline.backtest.portfolio_simulator import fetch_events_for_version
    from pipeline.ingest.calidad_precios import revisar_calidad

    dias = _serie(conn, "PICO", [10] * 10 + [30, 10.5] + [10] * 10)
    _serie(conn, "LIMPIA", [10] * 22)
    malo = _evento(conn, "1", "PICO", dias[5])  # el pico cae en su ventana
    limpio = _evento(conn, "2", "LIMPIA", dias[5])
    revisar_calidad(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, calidad_excluido FROM car_results")
        car = {r["event_id"]: r["calidad_excluido"] for r in cur.fetchall()}
    assert car[malo].startswith("vela marcada el") and car[limpio] is None
    analogos = get_historical_analogues(conn, "8K_2.02_EARNINGS", date(2025, 1, 1), -1, window_days=20)
    assert len(analogos) == 1  # solo el limpio
    assert [r["event_id"] for r in fetch_events_for_version(conn, "BALANCED")] == [limpio]
    assert excluidos_por_calidad(conn, None) == 1


def test_los_indices_no_se_marcan_por_picos_que_se_deshacen(conn):
    from pipeline.ingest.calidad_precios import revisar_calidad

    _serie(conn, "^VIX", [16, 16, 38, 19, 18])  # 5-8-2024: pico real
    revisar_calidad(conn)
    assert _motivos(conn, "^VIX") == {}


def test_un_car_nuevo_se_marca_aunque_su_ticker_no_tenga_precios_nuevos(conn):
    from pipeline.ingest.calidad_precios import revisar_calidad

    dias = _serie(conn, "PICO", [10] * 10 + [30, 10.5] + [10] * 10)
    revisar_calidad(conn)
    malo = _evento(conn, "1", "PICO", dias[5])  # CAR calculado después
    revisar_calidad(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT calidad_excluido, calidad_revisada FROM car_results WHERE event_id = %s", (malo,))
        fila = cur.fetchone()
    assert fila["calidad_revisada"] and fila["calidad_excluido"].startswith("vela marcada")
