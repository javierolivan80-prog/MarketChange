"""test_quality_score_lookahead.py — disciplina anti-look-ahead de quality_score.py.

Hallazgo de auditoría (IMPROVEMENT_PLAN.md R11): fetch_annual_rows y
_fetch_latest_price tenían un default as_of_date=None con una rama SIN
acotar temporalmente, documentada solo con una advertencia en el docstring
("nunca desde un backtest") — nada en el código lo impedía. Se eliminó esa
rama: as_of_date es ahora obligatoria en ambas funciones, y run_quality_screen
siempre les pasa la effective_date ya resuelta (nunca el as_of_date original,
que podía ser None).

Hasta esta sesión no existía NINGÚN test contra Postgres real de estas tres
funciones (test_quality_score.py solo cubre compute_quality_score, pura, sin
BD) — este fichero cierra ese hueco a la vez que fija el comportamiento
anti-look-ahead que motivó el cambio.
"""
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
        cur.execute("TRUNCATE quality_scores, fundamentals, prices, universe RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.close()


def _empresa(conn, cik: str, ticker: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,%s,'2020-01-01','2020-01-01')",
            (cik, ticker, f"{ticker} Inc"),
        )
    conn.commit()


def _ejercicio(conn, cik: str, fiscal_period_end: date, filed_at: date, revenue: float = 1_000_000.0) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fundamentals (cik, fiscal_period_end, filed_at, form, revenue, net_income,
                stockholders_equity, total_assets, total_liabilities, long_term_debt,
                operating_cash_flow, capex, shares_outstanding)
            VALUES (%s,%s,%s,'10-K',%s,150000,750000,1500000,750000,200000,180000,20000,100000)
            """,
            (cik, fiscal_period_end, filed_at, revenue),
        )
    conn.commit()


def _precio(conn, ticker: str, trade_date: date, close: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume) VALUES (%s,%s,%s,1,1000)",
            (ticker, trade_date, close),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# fetch_annual_rows
# ---------------------------------------------------------------------------


def test_fetch_annual_rows_excluye_ejercicios_presentados_despues_de_as_of_date(conn):
    from pipeline.analyze.quality_score import fetch_annual_rows

    _empresa(conn, "1", "TESTCO")
    _ejercicio(conn, "1", date(2022, 12, 31), date(2023, 2, 15), revenue=1_000_000.0)
    _ejercicio(conn, "1", date(2023, 12, 31), date(2024, 2, 15), revenue=1_200_000.0)  # aún no presentado en 2023-06

    rows = fetch_annual_rows(conn, "1", as_of_date=date(2023, 6, 1))

    assert len(rows) == 1
    assert rows[0]["revenue"] == 1_000_000.0


def test_fetch_annual_rows_requiere_as_of_date(conn):
    """R11: as_of_date ya no tiene default — llamarla sin ella es un
    TypeError en vez de un look-ahead silencioso."""
    from pipeline.analyze.quality_score import fetch_annual_rows

    with pytest.raises(TypeError):
        fetch_annual_rows(conn, "1")


# ---------------------------------------------------------------------------
# _fetch_latest_price
# ---------------------------------------------------------------------------


def test_fetch_latest_price_ignora_precios_posteriores_a_as_of_date(conn):
    from pipeline.analyze.quality_score import _fetch_latest_price

    _precio(conn, "TESTCO", date(2023, 1, 1), 50.0)
    _precio(conn, "TESTCO", date(2023, 6, 1), 80.0)  # futuro respecto al as_of_date de abajo

    price = _fetch_latest_price(conn, "TESTCO", as_of_date=date(2023, 3, 1))

    assert price == pytest.approx(50.0)


def test_fetch_latest_price_requiere_as_of_date(conn):
    from pipeline.analyze.quality_score import _fetch_latest_price

    with pytest.raises(TypeError):
        _fetch_latest_price(conn, "TESTCO")


# ---------------------------------------------------------------------------
# run_quality_screen — integración de extremo a extremo
# ---------------------------------------------------------------------------


def test_run_quality_screen_con_as_of_date_pasada_no_usa_cuentas_futuras(conn):
    """Regresión directa del hallazgo R11: simula un run_quality_screen en el
    pasado (como haría un backtest) y confirma que la nota se calcula SOLO
    con lo que ya se sabía en esa fecha, no con el ejercicio presentado
    después ni con el precio posterior."""
    from pipeline.analyze.quality_score import run_quality_screen

    _empresa(conn, "1", "TESTCO")
    _ejercicio(conn, "1", date(2022, 12, 31), date(2023, 2, 15), revenue=1_000_000.0)
    _ejercicio(conn, "1", date(2023, 12, 31), date(2024, 2, 15), revenue=999_000_000.0)  # futuro, revenue disparatado
    _precio(conn, "TESTCO", date(2023, 1, 1), 50.0)
    _precio(conn, "TESTCO", date(2023, 6, 1), 999.0)  # futuro, precio disparatado

    resultado = run_quality_screen(conn, as_of_date=date(2023, 3, 1))

    assert resultado["n_scored"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT n_years, price_used FROM quality_scores WHERE cik = '1'")
        row = cur.fetchone()
    assert row["n_years"] == 1  # solo el ejercicio de 2022, no el de 2023
    assert float(row["price_used"]) == pytest.approx(50.0)  # no el precio "futuro" de 999


def test_run_quality_screen_empresa_sin_cuentas_presentadas_todavia_no_aparece(conn):
    """Mismo espíritu que el guard de run_quality_screen sobre la cola de
    empresas (ver su comentario): si TODAS las cuentas de una empresa se
    presentaron después de as_of_date, esa empresa no debe ni intentarse
    puntuar (fetch_annual_rows devolvería [] y se perdería la distinción
    entre 'sin datos' y 'todavía no se sabía')."""
    from pipeline.analyze.quality_score import run_quality_screen

    _empresa(conn, "1", "NUEVA")
    _ejercicio(conn, "1", date(2023, 12, 31), date(2024, 2, 15))

    resultado = run_quality_screen(conn, as_of_date=date(2023, 6, 1))

    assert resultado["n_companies"] == 0
    assert resultado["n_scored"] == 0


def test_run_quality_screen_sin_as_of_date_usa_hoy(conn):
    """as_of_date=None (el default) sigue siendo la vista en vivo correcta —
    R11 no cambió ESTE comportamiento, solo cerró el hueco de más abajo."""
    from pipeline.analyze.quality_score import run_quality_screen

    _empresa(conn, "1", "HOY")
    _ejercicio(conn, "1", date(2022, 12, 31), date(2023, 2, 15))
    _precio(conn, "HOY", date.today() - timedelta(days=1), 42.0)

    resultado = run_quality_screen(conn)

    assert resultado["as_of_date"] == date.today().isoformat()
    assert resultado["n_scored"] == 1
