"""backtester.py — event study clásico (CAR vs Fama-French 3).

DOS NIVELES DE VALIDACIÓN, y no deben confundirse (AUDIT_LEAN.md §2.2.3):
  - `compute_car()` (este módulo): event study clásico, sobre TODOS los
    eventos de una clase (n en miles/cientos). Responde "¿existe un efecto?".
  - El backtest de cartera real, que decide si operar y con qué reglas de
    entrada/salida/sizing, vive en `pipeline/backtest/portfolio_simulator.py`
    — responde "¿es operable?". El resultado de compute_car() NUNCA se usa
    para simular trades reales, solo para medir significancia estadística
    del efecto (vía `populate_car_results.py` → `analyze/historical_analogues.py`).

LIMPIEZA (auditoría de código, IMPROVEMENT_PLAN.md Q1): este módulo tenía
además un motor de backtest por-evento completo (`run_backtest_for_event`,
`decide_trade`, `compute_trade_return`, `summarize_run`, `BacktestTrade`,
`STRATEGY_THRESHOLDS`) que nunca llegó a usarse en producción — el camino
real siempre fue `portfolio_simulator.py`, con su propio sizing, TP/SL,
trailing stop y umbrales de estrategia (`portfolio_strategies.py`), no los
`STRATEGY_THRESHOLDS`/slippage simple de aquí. Confirmado por grep exhaustivo
(cero callers fuera de este archivo y de sus propios tests) antes de borrar
— se eliminó, junto con sus tests dedicados, para no dejar un segundo motor
de backtest con sus propios umbrales que alguien pueda confundir con el real.
`compute_car`/`CAREstimate`/`fit_factor_model` SÍ son código vivo (los usa
`populate_car_results.py`; `fit_factor_model` también lo usa `enrichment.py`
de forma independiente) y se conservan tal cual.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import statsmodels.api as sm

from pipeline.backtest.factor_model import fit_factor_model

logger = logging.getLogger(__name__)


@dataclass
class CAREstimate:
    event_id: int
    window_days: int
    car: float
    abnormal_volume_ratio: float
    n_estimation_days: int


def compute_car(
    event_prices: pd.DataFrame,
    factor_returns: pd.DataFrame,
    d0_close_date: date,
    window_days: int,
    estimation_window: tuple[int, int] = (-250, -30),
) -> CAREstimate | None:
    """Calcula el retorno anormal acumulado (CAR) vs. modelo de mercado FF3.

    event_prices: DataFrame indexado por fecha con columna 'ret' (retorno diario
        simple, YA calculado por el caller a partir de close_raw*adj_factor).
    factor_returns: DataFrame indexado por fecha con columnas mkt_rf, smb, hml, rf.

    Metodología estándar de event study (ARCHITECTURE_LEAN.md §3):
      1. Regresión OLS de (ret - rf) contra (mkt_rf, smb, hml) en la ventana de
         estimación [-250, -30] respecto a D0.
      2. Retorno esperado en la ventana de evento = predicción del modelo.
      3. Retorno anormal = retorno real - retorno esperado.
      4. CAR = suma de retornos anormales en [D+1, D+window_days].

    Devuelve None si no hay suficientes días de estimación (mínimo 60, umbral
    conservador para que la regresión no sea puro ruido) — un None debe
    tratarse como "no evaluable", NUNCA como CAR=0.
    """
    merged = event_prices.join(factor_returns, how="inner")
    fit = fit_factor_model(merged, d0_close_date, estimation_window)
    if fit is None:
        return None

    event_end = pd.Timestamp(d0_close_date) + pd.Timedelta(days=window_days)
    event_data = merged[(merged.index > pd.Timestamp(d0_close_date)) & (merged.index <= event_end)]
    if event_data.empty:
        return None

    X_event = sm.add_constant(event_data[["mkt_rf", "smb", "hml"]], has_constant="add")
    expected_ret = fit.model.predict(X_event) + event_data["rf"]
    abnormal_ret = event_data["ret"] - expected_ret
    car = abnormal_ret.sum()

    baseline_vol = merged["ret"].rolling(60).std().iloc[-1] if "ret" in merged else np.nan
    event_vol = event_data["ret"].std() if len(event_data) > 1 else np.nan
    abnormal_volume_ratio = (event_vol / baseline_vol) if baseline_vol and baseline_vol > 0 else np.nan

    return CAREstimate(
        event_id=-1,  # el caller lo rellena
        window_days=window_days,
        car=float(car),
        abnormal_volume_ratio=float(abnormal_volume_ratio) if not np.isnan(abnormal_volume_ratio) else None,
        n_estimation_days=fit.n_estimation_days,
    )
