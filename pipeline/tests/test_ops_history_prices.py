import os
from datetime import date

import pytest

from pipeline.ops_history_prices import plan_gaps

TODAY = date(2026, 10, 2)


def test_ticker_nuevo_baja_su_ventana_redondeada_al_mes():
    gaps = plan_gaps({"NEW": (date(2020, 3, 17), date(2021, 2, 10))}, {}, TODAY)
    assert gaps == [("NEW", date(2020, 3, 1), date(2021, 2, 28))]


def test_rellena_hacia_atras_lo_anterior_al_primer_dia_guardado():
    gaps = plan_gaps(
        {"OLD": (date(2019, 11, 20), date(2026, 9, 30))},
        {"OLD": (date(2021, 1, 4), date(2026, 9, 30))},
        TODAY,
    )
    assert gaps == [("OLD", date(2019, 11, 1), date(2021, 1, 3))]


def test_ticker_al_dia_no_pide_nada():
    assert plan_gaps(
        {"OK": (date(2021, 3, 5), date(2021, 6, 1))},
        {"OK": (date(2021, 2, 26), date(2026, 10, 1))},
        TODAY,
    ) == []


def test_fin_nunca_pasa_de_hoy():
    gaps = plan_gaps({"X": (date(2026, 8, 10), date(2026, 10, 2))}, {}, TODAY)
    assert gaps == [("X", date(2026, 8, 1), TODAY)]


def test_simbolos_operables():
    from pipeline.ingest.yfinance_backfill import is_tradable_symbol

    for ok in ["AAPL", "GOOGL", "MRNA", "F", "BRKB"]:
        assert is_tradable_symbol(ok), ok
    for no in ["PCG-PB", "BRK.B", "CELG-RI", "ACMRW", "SPACU", "ABCDR", "XYZQQ", "BEIGF", "", "^VIX"]:
        assert not is_tradable_symbol(no), no


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
def test_run_solo_pide_precios_para_eventos_sin_car(monkeypatch):
    """Un ticker cuyos eventos ya tienen CAR no se vuelve a descargar:
    ops_prune puede haber borrado sus precios y no deben volver en bucle."""
    from pipeline import ops_history_prices
    from pipeline.db.connection import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE universe, events, prices RESTART IDENTITY CASCADE")
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES ('1','DONE','Done','2024-01-01','2024-01-01'), ('2','TODO','Todo','2024-01-01','2024-01-01')"
        )
        for cik, t in (("1", "DONE"), ("2", "TODO")):
            cur.execute(
                "INSERT INTO events (cik, ticker, source, event_class, accession_number, source_url, filed_at, "
                "d0_close_date, classification_method, raw_text_hash) VALUES (%s, %s, 'EDGAR', '8K_2.02_EARNINGS', "
                "%s, 'u', '2024-03-01', '2024-03-01', 'RULE', 'h') RETURNING event_id",
                (cik, t, "acc" + t),
            )
            eid = cur.fetchone()["event_id"]
            if t == "DONE":
                cur.execute(
                    "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, 20, 0, 100)",
                    (eid,),
                )
    conn.commit()

    pedidos = []
    monkeypatch.setattr(
        "pipeline.ingest.yfinance_backfill._descargar_grupo",
        lambda conn, tickers, start, end, expected, **kw: pedidos.extend(tickers) or True,
    )
    monkeypatch.setattr("pipeline.analyze.enrichment.BENCHMARK_TICKERS", [])
    assert ops_history_prices.run(conn, date(2023, 1, 1), max_db_mb=1e9) is True
    assert pedidos == ["TODO"]
    conn.close()


def test_tandas_paran_cuando_una_no_calcula_ningun_car(monkeypatch):
    from pipeline import ops_history_rounds

    cars = iter([10, 50, 50, 50])  # antes/después de cada tanda
    llamadas = []
    monkeypatch.setattr(ops_history_rounds, "_car_events", lambda conn: next(cars))
    monkeypatch.setattr("pipeline.ops_history_prices.run", lambda *a: llamadas.append("precios") or False)
    monkeypatch.setattr("pipeline.backtest.populate_car_results.populate_missing_car_results", lambda conn: 0)
    monkeypatch.setattr("pipeline.ops_prune.run", lambda conn, **kw: llamadas.append("prune"))
    ops_history_rounds.run(None, date(2020, 1, 1), 400, minutes=60)
    assert llamadas == ["precios", "prune", "precios", "prune"]  # 2.ª tanda sin CAR nuevo: para
