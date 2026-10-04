"""ops_prune: borra eventos de símbolos no operables SOLO si nada los usa,
y vacía el texto de filings antiguos sin tocar el evento."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE universe, events RESTART IDENTITY CASCADE")
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES ('1','ACME','Acme','2024-01-01','2024-01-01')"
        )
    c.commit()
    yield c
    c.close()


def _event(conn, ticker, acc, days_ago=10, text=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash, filing_text)
            VALUES ('1', %s, 'EDGAR', FALSE, '8K_2.02_EARNINGS', ARRAY['2.02'], %s, 'https://x',
                    current_date - %s, current_date - %s, 'RULE', 1.0, 'h', %s)
            RETURNING event_id
            """,
            (ticker, acc, days_ago, days_ago, text),
        )
        eid = cur.fetchone()["event_id"]
    conn.commit()
    return eid


def _ids(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT event_id FROM events")
        return {r["event_id"] for r in cur.fetchall()}


def test_borra_no_operables_sin_referencias_y_respeta_el_resto(conn):
    from pipeline.ops_prune import prune_untradable_events

    ok = _event(conn, "ACME", "a1")
    warrant = _event(conn, "ACMEW", "a2")
    usado = _event(conn, "ACME-PB", "a3")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO car_results (event_id, window_days, car, n_estimation_days, computed_at) "
            "VALUES (%s, 5, 0.01, 100, now())",
            (usado,),
        )
    conn.commit()

    assert prune_untradable_events(conn) == 1
    assert _ids(conn) == {ok, usado}
    assert warrant not in _ids(conn)


def test_vacia_texto_antiguo_sin_borrar_el_evento(conn):
    from pipeline.ops_prune import FILING_TEXT_KEEP_DAYS, clear_old_filing_text

    viejo = _event(conn, "ACME", "b1", days_ago=FILING_TEXT_KEEP_DAYS + 10, text="viejo")
    nuevo = _event(conn, "ACME", "b2", days_ago=5, text="nuevo")
    assert clear_old_filing_text(conn) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, filing_text FROM events")
        rows = {r["event_id"]: r["filing_text"] for r in cur.fetchall()}
    assert rows == {viejo: None, nuevo: "nuevo"}


def test_run_completo_no_falla(conn):
    from pipeline.ops_prune import run

    _event(conn, "ACMEW", "c1")
    run(conn, vacuum_full=True)
    assert _ids(conn) == set()
