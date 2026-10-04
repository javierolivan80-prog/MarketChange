"""test_backtester.py — pruebas de compute_car() (event study clásico, CAR
vs Fama-French 3), con foco en T2 de ARCHITECTURE_LEAN.md §8 (falsación,
no solo tests unitarios).

Todo aquí usa datos sintéticos: no depende de red ni de EDGAR/yfinance.

LIMPIEZA (auditoría de código, IMPROVEMENT_PLAN.md Q1): este archivo tenía
además T1 (placebo) y T3 (look-ahead) sobre `run_backtest_for_event` — el
motor de backtest por-evento que se eliminó de backtester.py por no tener
ningún caller en producción (ver el docstring del módulo). Esos tests
probaban código muerto, no el backtest real (portfolio_simulator.py, que
tiene su propia suite de falsación — ver test_portfolio_simulator*.py). Se
eliminaron junto con el código que probaban.
"""
import numpy as np
import pandas as pd

from pipeline.backtest.backtester import compute_car

# ---------------------------------------------------------------------------
# T2 — réplica de un efecto conocido (ARCHITECTURE_LEAN.md §8, BLOQUEANTE)
# ---------------------------------------------------------------------------


def test_compute_car_replicates_known_effect_direction_and_significance():
    """Réplica simplificada de un resultado de manual: un evento con retorno
    anormal negativo persistente en la ventana [D+1,D+20] (simulando la
    literatura de restatements/Item 4.02, CAR típico -5% a -10%) debe producir
    un CAR negativo y de magnitud coherente cuando se le inyecta una deriva
    negativa clara por encima del ruido de los factores.

    Esto NO reemplaza la réplica real contra datos de mercado del día 5 del
    plan (ARCHITECTURE_LEAN.md §9) — esa exige datos reales de EDGAR/yfinance,
    que este sandbox no puede descargar. Esto prueba que el MOTOR de CAR
    (compute_car) recupera correctamente una señal conocida inyectada en datos
    sintéticos, que es la parte que sí se puede validar sin red."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-04", periods=320, freq="B")

    factor_returns = pd.DataFrame(
        {
            "mkt_rf": rng.normal(0.0003, 0.01, len(dates)),
            "smb": rng.normal(0.0, 0.005, len(dates)),
            "hml": rng.normal(0.0, 0.005, len(dates)),
            "rf": np.full(len(dates), 0.00005),
        },
        index=dates,
    )

    # Sensibilidad de la acción a los factores + ruido idiosincrático.
    beta_mkt, beta_smb, beta_hml = 1.1, 0.3, -0.2
    idio_noise = rng.normal(0, 0.008, len(dates))
    stock_ret = (
        factor_returns["rf"]
        + beta_mkt * factor_returns["mkt_rf"]
        + beta_smb * factor_returns["smb"]
        + beta_hml * factor_returns["hml"]
        + idio_noise
    )

    d0 = dates[280].date()
    # Inyecta la deriva negativa conocida SOLO en la ventana de evento [D+1,D+20]:
    # -7% acumulado repartido en 20 días, imitando el CAR de restatement de la
    # literatura (-5% a -10%).
    event_mask = (dates > pd.Timestamp(d0)) & (dates <= pd.Timestamp(d0) + pd.Timedelta(days=20))
    stock_ret = stock_ret.copy()
    stock_ret[event_mask] += -0.07 / event_mask.sum()

    event_prices = pd.DataFrame({"ret": stock_ret}, index=dates)

    result = compute_car(event_prices, factor_returns, d0, window_days=20)

    assert result is not None
    assert result.car < 0, "El CAR debe ser negativo: se inyectó una deriva negativa conocida"
    # Magnitud coherente con la literatura de restatements (-5% a -10%), con
    # margen amplio porque es una única simulación, no un promedio de muchas.
    assert -0.15 < result.car < -0.02, f"CAR={result.car:.4f} fuera del rango esperado para el efecto inyectado"


def test_compute_car_returns_none_when_estimation_window_too_short():
    """Un evento sin suficiente histórico de estimación debe devolver None,
    NUNCA un CAR silenciosamente calculado sobre pocos datos (que sería ruido
    disfrazado de señal)."""
    dates = pd.date_range("2021-01-04", periods=20, freq="B")  # muy por debajo del mínimo de 60
    factor_returns = pd.DataFrame(
        {"mkt_rf": [0.001] * 20, "smb": [0.0] * 20, "hml": [0.0] * 20, "rf": [0.0] * 20}, index=dates
    )
    event_prices = pd.DataFrame({"ret": [0.001] * 20}, index=dates)
    result = compute_car(event_prices, factor_returns, dates[-1].date(), window_days=5)
    assert result is None


# ---------------------------------------------------------------------------
# BUGS_REPORT.md H-02 — ventana de evento incompleta = no evaluable
# ---------------------------------------------------------------------------


def _synthetic(n_days=320, seed=11):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    factors = pd.DataFrame(
        {
            "mkt_rf": rng.normal(0.0003, 0.01, n_days),
            "smb": rng.normal(0.0, 0.005, n_days),
            "hml": rng.normal(0.0, 0.005, n_days),
            "rf": np.full(n_days, 0.00005),
        },
        index=dates,
    )
    prices = pd.DataFrame({"ret": factors["rf"] + 1.1 * factors["mkt_rf"] + rng.normal(0, 0.008, n_days)}, index=dates)
    return dates, prices, factors


def test_compute_car_returns_none_while_the_event_window_is_still_open():
    """Antes, 3 sesiones de datos se guardaban como "CAR a 20 días" y nunca se
    recalculaban (populate solo mira eventos sin fila)."""
    dates, prices, factors = _synthetic()
    d0 = dates[-4].date()  # solo quedan 3 sesiones después de D0
    assert compute_car(prices, factors, d0, window_days=20) is None


def test_compute_car_returns_none_when_factors_lag_behind_the_window():
    """Fama-French se publica con ~1-2 meses de retraso: sin factores para toda
    la ventana, el CAR tampoco está completo."""
    dates, prices, factors = _synthetic()
    d0 = dates[280].date()
    assert compute_car(prices, factors.loc[: dates[284]], d0, window_days=20) is None


def test_compute_car_returns_none_with_large_gaps_inside_the_window():
    dates, prices, factors = _synthetic()
    d0 = dates[280].date()
    window = (dates > pd.Timestamp(d0)) & (dates <= pd.Timestamp(d0) + pd.Timedelta(days=20))
    hueco = dates[window][2:9]  # 7 de ~14 sesiones sin precio (deslistado, halt...)
    assert compute_car(prices.drop(hueco), factors, d0, window_days=20) is None


def test_compute_car_still_computes_a_complete_window():
    dates, prices, factors = _synthetic()
    result = compute_car(prices, factors, dates[280].date(), window_days=20)
    assert result is not None
