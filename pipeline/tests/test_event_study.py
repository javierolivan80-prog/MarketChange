"""test_event_study.py — Fase 6 PARTE 1. compute_mde y
compute_event_study_for_class son puros (cálculo a mano); run_event_study
necesita Postgres real (lee car_results)."""
import math
import os

import numpy as np
import pytest
from scipy import stats

from statsmodels.stats.multitest import multipletests

from pipeline.validation.event_study import (
    MDE_POWER_CONSTANT,
    MULTIPLE_COMPARISONS_METHOD,
    SIGNIFICANCE_ALPHA,
    _apply_multiple_comparisons_correction,
    compute_event_study_for_class,
    compute_mde,
)

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


# ---------------------------------------------------------------------------
# compute_mde — puro, fórmula literal de AUDIT_LEAN.md §2.2.3
# ---------------------------------------------------------------------------


def test_compute_mde_hand_calculated():
    # MDE = 2.8 * sigma / sqrt(n)
    mde = compute_mde(sigma=8.0, n=100)
    assert mde == pytest.approx(2.8 * 8.0 / 10)  # sqrt(100)=10 -> 2.24


def test_compute_mde_matches_audit_lean_example():
    # AUDIT_LEAN.md §2.2.3: earnings, n grande, sigma ~8% -> MDE de decenas de bps
    mde = compute_mde(sigma=8.0, n=10_000)
    assert mde == pytest.approx(2.8 * 8.0 / 100)  # sqrt(10000)=100 -> 0.224 puntos = ~22 bps


def test_compute_mde_none_for_zero_n():
    assert compute_mde(sigma=8.0, n=0) is None


# ---------------------------------------------------------------------------
# compute_event_study_for_class — puro
# ---------------------------------------------------------------------------


def test_event_study_hand_calculated_stats():
    car_values = [0.01, 0.02, 0.03, 0.04, 0.05]  # fracciones -> 1,2,3,4,5 en %
    result = compute_event_study_for_class(car_values)

    assert result["n"] == 5
    assert result["mean_return_pct"] == pytest.approx(3.0)
    assert result["median_return_pct"] == pytest.approx(3.0)
    expected_sigma = float(np.array([1, 2, 3, 4, 5]).std(ddof=1))
    assert result["sigma_pct"] == pytest.approx(expected_sigma)
    assert result["mde_pct"] == pytest.approx(MDE_POWER_CONSTANT * expected_sigma / np.sqrt(5))

    expected_t, expected_p = stats.ttest_1samp(np.array([1, 2, 3, 4, 5]), popmean=0.0)
    assert result["t_statistic"] == pytest.approx(float(expected_t))
    assert result["p_value"] == pytest.approx(float(expected_p))
    assert result["significant"] == (float(expected_p) < SIGNIFICANCE_ALPHA)
    # compute_event_study_for_class solo ve UNA clase — la corrección por
    # contrastes múltiples (ver run_event_study) requiere ver todas a la
    # vez, así que aquí quedan sin rellenar.
    assert result["p_value_bh_adjusted"] is None
    assert result["significant_bh"] is None


def test_event_study_insufficient_sample_returns_none_stats():
    result = compute_event_study_for_class([0.01, 0.02])  # n=2 < MIN_N_FOR_ANY_STATISTIC
    assert result["n"] == 2
    assert result["mean_return_pct"] is None
    assert result["significant"] is None
    assert "insuficiente" in result["conclusion"]


def test_event_study_zero_mean_is_not_significant():
    # Retornos simétricos alrededor de 0 -> media ~0 -> no significativo.
    car_values = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005]
    result = compute_event_study_for_class(car_values)
    assert result["significant"] is False
    assert "No significativo" in result["conclusion"]


def test_event_study_large_consistent_effect_is_significant():
    car_values = [0.05, 0.06, 0.055, 0.052, 0.058, 0.061, 0.049, 0.053]  # todos claramente positivos, poca varianza
    result = compute_event_study_for_class(car_values)
    assert result["significant"] is True
    assert "Significativo" in result["conclusion"]


def test_event_study_zero_variance_does_not_produce_infinity():
    """Regresión: con varianza cero (todos los CAR idénticos), t=media/(0/√n)
    es una división por cero — scipy devolvía Infinity, que rompía el INSERT
    a validation_reports (Postgres rechaza JSON con el token "Infinity",
    encontrado así con el pipeline nocturno real). Debe reportarse como no
    computable (None), nunca como un float infinito/NaN."""
    car_values = [0.03, 0.03, 0.03, 0.03, 0.03]
    result = compute_event_study_for_class(car_values)
    assert result["sigma_pct"] == 0.0
    assert result["t_statistic"] is None
    assert result["p_value"] is None
    assert result["significant"] is None
    assert "varianza cero" in result["conclusion"]
    # No debe quedar ningún float no-finito en el resultado — es justo lo
    # que rompía la serialización a JSON.
    for v in result.values():
        if isinstance(v, float):
            assert math.isfinite(v)


# ---------------------------------------------------------------------------
# _apply_multiple_comparisons_correction — puro (hallazgo de auditoría:
# corrección por contrastes múltiples, Benjamini-Hochberg/FDR)
# ---------------------------------------------------------------------------


def _fake_result(p_value):
    """Construye un dict con la misma forma que compute_event_study_for_class
    devuelve, para testear la corrección de forma aislada sin recalcular
    t-tests reales."""
    return {
        "n": 10, "mean_return_pct": 1.0, "median_return_pct": 1.0,
        "p25_pct": 0.5, "p75_pct": 1.5, "sigma_pct": 1.0, "mde_pct": 1.0,
        "t_statistic": 1.0, "p_value": p_value,
        "significant": (p_value < SIGNIFICANCE_ALPHA) if p_value is not None else None,
        "p_value_bh_adjusted": None, "significant_bh": None,
        "conclusion": "placeholder",
    }


def test_multiple_comparisons_correction_matches_statsmodels_directly():
    pvalues_by_class = {"A": 0.001, "B": 0.01, "C": 0.02, "D": 0.03, "E": 0.04}
    by_class_results = {c: _fake_result(p) for c, p in pvalues_by_class.items()}

    _apply_multiple_comparisons_correction(by_class_results)

    classes = list(pvalues_by_class.keys())
    expected_rejected, expected_adjusted, _, _ = multipletests(
        [pvalues_by_class[c] for c in classes], alpha=SIGNIFICANCE_ALPHA, method=MULTIPLE_COMPARISONS_METHOD
    )
    for event_class, expect_sig, expect_p in zip(classes, expected_rejected, expected_adjusted):
        assert by_class_results[event_class]["significant_bh"] == bool(expect_sig)
        assert by_class_results[event_class]["p_value_bh_adjusted"] == pytest.approx(float(expect_p))


def test_multiple_comparisons_correction_excludes_classes_without_pvalue():
    """Clases con n insuficiente o varianza cero (p_value=None) no entran en
    la corrección — ni deben, no hay hipótesis que testear — y quedan con
    p_value_bh_adjusted/significant_bh en None, no en False."""
    by_class_results = {
        "WITH_PVALUE": _fake_result(0.01),
        "NO_PVALUE_INSUFFICIENT_N": _fake_result(None),
    }
    _apply_multiple_comparisons_correction(by_class_results)

    assert by_class_results["WITH_PVALUE"]["p_value_bh_adjusted"] is not None
    assert by_class_results["NO_PVALUE_INSUFFICIENT_N"]["p_value_bh_adjusted"] is None
    assert by_class_results["NO_PVALUE_INSUFFICIENT_N"]["significant_bh"] is None


def test_multiple_comparisons_correction_can_flip_a_borderline_significant_result():
    """El caso que justifica todo el punto de auditoría: una clase con
    p=0.049 (significativa cruda, < 0.05) deja de serlo tras corregir por
    contrastes múltiples cuando se testea junto a otras 9 clases — el falso
    positivo que la corrección existe para prevenir."""
    by_class_results = {"BORDERLINE": _fake_result(0.049)}
    by_class_results.update({f"NOISE_{i}": _fake_result(0.5) for i in range(9)})

    assert by_class_results["BORDERLINE"]["significant"] is True  # crudo: sí es significativo

    _apply_multiple_comparisons_correction(by_class_results)

    assert by_class_results["BORDERLINE"]["significant_bh"] is False  # corregido: ya no
    assert "no sobrevive a la corrección" in by_class_results["BORDERLINE"]["conclusion"]
    for i in range(9):
        assert by_class_results[f"NOISE_{i}"]["significant_bh"] is False


def test_multiple_comparisons_correction_all_pvalues_none_is_a_noop():
    by_class_results = {"A": _fake_result(None), "B": _fake_result(None)}
    _apply_multiple_comparisons_correction(by_class_results)
    assert by_class_results["A"]["p_value_bh_adjusted"] is None
    assert by_class_results["B"]["p_value_bh_adjusted"] is None


# ---------------------------------------------------------------------------
# run_event_study — integración contra Postgres real
# ---------------------------------------------------------------------------


@pytestmark_db
class TestEventStudyIntegration:
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

    def _seed_event_with_car(self, conn, cik, ticker, event_class, car_value, window_days=20):
        from datetime import date

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
                (cik, ticker, date(2024, 1, 1), date(2024, 1, 1)),
            )
            cur.execute(
                """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['2.02'],%s,'https://x','2024-01-02','2024-01-02','RULE',1.0,%s)
                RETURNING event_id""",
                (cik, ticker, event_class, f"acc-{cik}", f"hash-{cik}"),
            )
            event_id = cur.fetchone()["event_id"]
            cur.execute(
                "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, %s, %s, 100)",
                (event_id, window_days, car_value),
            )
        conn.commit()

    def test_run_event_study_groups_by_class(self, conn):
        from pipeline.validation.event_study import run_event_study

        for i in range(5):
            self._seed_event_with_car(conn, f"e{i}", f"E{i}", "8K_2.02_EARNINGS", 0.02 + i * 0.001)
        for i in range(3):
            self._seed_event_with_car(conn, f"m{i}", f"M{i}", "8K_1.01_MATERIAL_AGREEMENT", -0.01)

        result = run_event_study(conn, window_days=20)
        assert set(result.keys()) == {"8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT"}
        assert result["8K_2.02_EARNINGS"]["n"] == 5
        assert result["8K_1.01_MATERIAL_AGREEMENT"]["n"] == 3

    def test_run_event_study_respects_window_days_filter(self, conn):
        from pipeline.validation.event_study import run_event_study

        self._seed_event_with_car(conn, "w1", "W1", "8K_2.02_EARNINGS", 0.05, window_days=5)
        self._seed_event_with_car(conn, "w2", "W2", "8K_2.02_EARNINGS", 0.03, window_days=20)

        result_20 = run_event_study(conn, window_days=20)
        assert result_20["8K_2.02_EARNINGS"]["n"] == 1

    def test_run_event_study_attaches_bh_correction_across_all_classes(self, conn):
        from pipeline.validation.event_study import run_event_study

        for i in range(5):
            self._seed_event_with_car(conn, f"e{i}", f"E{i}", "8K_2.02_EARNINGS", 0.05 + i * 0.001)
        for i in range(3):
            self._seed_event_with_car(conn, f"m{i}", f"M{i}", "8K_1.01_MATERIAL_AGREEMENT", 0.001 * (-1) ** i)

        result = run_event_study(conn, window_days=20)

        for event_class, st in result.items():
            if st["p_value"] is not None:
                assert st["p_value_bh_adjusted"] is not None
                assert isinstance(st["significant_bh"], bool)
            else:
                assert st["p_value_bh_adjusted"] is None
                assert st["significant_bh"] is None
