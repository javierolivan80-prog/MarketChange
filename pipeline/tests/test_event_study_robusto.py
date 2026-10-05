"""test_event_study_robusto.py — BUGS_REPORT.md H-19 y H-20: contraste con
errores agrupados por fecha, winsorización, BMP y una observación por
(empresa, D0)."""
import os
from datetime import date, timedelta

import numpy as np
import pytest
from scipy import stats

from pipeline.validation.event_study import (
    WINSOR_MIN_N,
    bmp_test,
    clustered_mean_test,
    compute_event_study_for_class,
)


def test_con_un_evento_por_fecha_el_agrupado_es_el_t_test_simple():
    rng = np.random.default_rng(1)
    vals = rng.normal(0.5, 2.0, 40)
    t, p, g = clustered_mean_test(vals, list(range(40)))
    t_ref, p_ref = stats.ttest_1samp(vals, 0.0)
    assert g == 40 and t == pytest.approx(t_ref) and p == pytest.approx(p_ref)


def test_eventos_amontonados_en_la_misma_fecha_dan_menos_significancia():
    """Diez fechas con un choque común cada una: el t simple trata 200
    eventos como independientes y sale muy significativo; agrupando por
    fecha quedan 10 observaciones efectivas."""
    rng = np.random.default_rng(7)
    choques = rng.normal(0.3, 3.0, 10)
    fechas = [d for d in range(10) for _ in range(20)]
    vals = np.array([choques[d] + rng.normal(0, 0.3) for d in fechas])
    _, p_simple = stats.ttest_1samp(vals, 0.0)
    _, p_agrupado, g = clustered_mean_test(vals, fechas)
    assert g == 10
    assert p_agrupado > p_simple * 10


def test_todos_en_la_misma_fecha_no_hay_contraste():
    res = compute_event_study_for_class([0.01, 0.02, 0.03, 0.05], d0_dates=[date(2024, 1, 2)] * 4)
    assert res["p_value"] is None and res["significant"] is None
    assert res["p_value_simple"] is not None  # el simple se sigue enseñando para comparar
    assert "misma fecha" in res["conclusion"]


def test_winsoriza_solo_con_muestra_grande():
    pocos = compute_event_study_for_class([0.01] * 10 + [3.0], d0_dates=list(range(11)))
    assert pocos["winsorized"] is False
    rng = np.random.default_rng(3)
    vals = list(rng.normal(0.0, 0.02, WINSOR_MIN_N)) + [5.0]  # un CAR de +500 %
    muchos = compute_event_study_for_class(vals, d0_dates=list(range(len(vals))))
    assert muchos["winsorized"] is True
    # El atípico arrastra el t simple; el winsorizado apenas se mueve.
    assert abs(muchos["t_statistic"]) < abs(muchos["t_statistic_simple"])


def test_bmp_usa_solo_los_eventos_con_car_estandarizado():
    t, p, n = bmp_test([1.0, 2.0, None, 1.5, None, 0.5])
    ref_t, ref_p = stats.ttest_1samp([1.0, 2.0, 1.5, 0.5], 0.0)
    assert n == 4 and t == pytest.approx(ref_t) and p == pytest.approx(ref_p)
    assert bmp_test([None, None, 1.0]) == (None, None, 1)


def test_compute_car_guarda_la_desviacion_de_los_residuos():
    import pandas as pd

    from pipeline.backtest.backtester import compute_car

    rng = np.random.default_rng(11)
    fechas = pd.bdate_range("2023-01-02", periods=320)
    factores = pd.DataFrame(
        {"mkt_rf": rng.normal(0.0004, 0.01, 320), "smb": rng.normal(0, 0.005, 320),
         "hml": rng.normal(0, 0.005, 320), "rf": 0.0001}, index=fechas)
    precios = pd.DataFrame({"ret": factores["mkt_rf"] * 1.2 + 0.0001 + rng.normal(0, 0.02, 320)}, index=fechas)
    res = compute_car(precios, factores, fechas[290].date(), 20)
    assert res is not None
    assert 0.01 < res.resid_std < 0.03  # el ruido sembrado es 0,02
    assert res.n_event_days >= 12


pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytestmark_db
class TestUnaObservacionPorEmpresaYDia:
    @pytest.fixture
    def conn(self):
        from pipeline.db.connection import get_connection, init_schema

        c = get_connection()
        init_schema(c)
        with c.cursor() as cur:
            cur.execute("TRUNCATE car_results, events, universe RESTART IDENTITY CASCADE")
        c.commit()
        yield c
        c.close()

    def _evento(self, conn, cik, ticker, d0, accession, car, resid_std=None):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO NOTHING",
                (cik, ticker, d0, d0),
            )
            cur.execute(
                """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES (%s,%s,'EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
                RETURNING event_id""",
                (cik, ticker, accession, d0, d0, f"h-{accession}"),
            )
            eid = cur.fetchone()["event_id"]
            cur.execute(
                "INSERT INTO car_results (event_id, window_days, car, n_estimation_days, resid_std, n_event_days) "
                "VALUES (%s, 20, %s, 100, %s, 14)",
                (eid, car, resid_std),
            )
        conn.commit()

    def test_el_mismo_ticker_el_mismo_dia_cuenta_una_vez(self, conn):
        from pipeline.validation.event_study import run_event_study

        d = date(2024, 1, 2)
        self._evento(conn, "1", "AAA", d, "a1", 0.05)
        self._evento(conn, "1", "AAA", d, "a1-enmienda", 0.05)  # misma empresa y día: mismo CAR
        for i in range(4):
            self._evento(conn, str(10 + i), f"B{i}", d + timedelta(days=i + 1), f"b{i}", 0.01 * (i + 1))
        res = run_event_study(conn, window_days=20)["8K_2.02_EARNINGS"]
        assert res["n"] == 5
        assert res["n_clusters"] == 5

    def test_bmp_se_calcula_con_los_que_tienen_resid_std(self, conn):
        from pipeline.validation.event_study import run_event_study

        for i in range(5):
            self._evento(conn, str(i), f"T{i}", date(2024, 1, 2) + timedelta(days=i), f"x{i}",
                         0.01 * (i + 1), resid_std=0.02 if i < 4 else None)
        res = run_event_study(conn, window_days=20)["8K_2.02_EARNINGS"]
        assert res["n_bmp"] == 4 and res["p_value_bmp"] is not None
