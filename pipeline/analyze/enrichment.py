"""enrichment.py — Etapa 1: features de mercado por evento.

Todo lo calculado aquí usa SOLO datos en o antes de d0_close_date — es la
misma disciplina anti-look-ahead que backtest/backtester.py (ARCHITECTURE_LEAN.md
§4). beta_vs_spy/ff_size_exposure/ff_value_exposure reutilizan
backtest/factor_model.py (ver ese módulo para el porqué de compartirlo con
compute_car en vez de reajustar la regresión aquí).

Mapeo sector -> ETF: por código SIC de 2 dígitos (el más basto de los niveles
de SIC, pero es lo que hay gratis — AUDIT_LEAN.md §2.3: "códigos SIC de
EDGAR, más bastos pero utilizables, en vez de GICS"). No es exhaustivo; los
SIC no mapeados caen a un ETF de mercado amplio (SPY) en vez de fallar, así
que sector_mood degrada a ~0 (sector = mercado) en vez de reventar el pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from pipeline.backtest.factor_model import fit_factor_model
from pipeline.backtest.portfolio_simulator import ADV_MIN_TRADING_DAYS, ADV_TRAILING_WINDOW_DAYS

logger = logging.getLogger(__name__)

MAX_LOOKBACK_DAYS_FOR_PRICE = 5  # tolerancia para fines de semana/festivos al buscar D-5/D-20/D0

# SIC de 2 dígitos (los dos primeros del código de 4) -> ETF sectorial SPDR.
# Cobertura deliberadamente aproximada — ver docstring del módulo.
_SIC_PREFIX_TO_ETF = {
    "01": "XLB", "02": "XLB", "10": "XLB", "12": "XLE", "13": "XLE", "14": "XLB",
    "15": "XLI", "16": "XLI", "17": "XLI",
    "20": "XLP", "21": "XLP", "22": "XLY", "23": "XLY", "24": "XLB", "25": "XLY",
    "26": "XLB", "27": "XLC", "28": "XLV", "29": "XLE",
    "30": "XLY", "32": "XLB", "33": "XLB", "34": "XLI", "35": "XLI", "36": "XLK",
    "37": "XLY", "38": "XLV",
    "40": "XLI", "41": "XLI", "42": "XLI", "44": "XLI", "45": "XLI", "48": "XLC",
    "49": "XLU",
    "50": "XLI", "51": "XLI",
    "52": "XLY", "53": "XLY", "54": "XLP", "55": "XLY", "56": "XLY", "57": "XLY",
    "58": "XLY", "59": "XLY",
    "60": "XLF", "61": "XLF", "62": "XLF", "63": "XLF", "64": "XLF", "65": "XLRE",
    "67": "XLF",
    "70": "XLY", "72": "XLY", "73": "XLK", "75": "XLY", "76": "XLY", "78": "XLC",
    "79": "XLC",
    "80": "XLV", "81": "XLV", "82": "XLV", "83": "XLV", "87": "XLK",
}
DEFAULT_SECTOR_ETF = "SPY"  # sin mapeo conocido: sector_mood degrada a ~0, no revienta

# Series de referencia que este módulo lee de `prices` y que NO son empresas
# del universo: si no se descargan explícitamente, nunca llegan (el backfill
# de yfinance del workflow solo baja los tickers de `universe`), y beta,
# vix_d0 y sector_mood salen vacíos para TODOS los eventos. El workflow las
# añade a la lista de descarga importando esta constante.
BENCHMARK_TICKERS = sorted({"SPY", "^VIX", DEFAULT_SECTOR_ETF, *_SIC_PREFIX_TO_ETF.values()})


def sic_to_sector_etf(sic_code: str | None) -> str:
    if not sic_code or len(sic_code) < 2:
        return DEFAULT_SECTOR_ETF
    return _SIC_PREFIX_TO_ETF.get(sic_code[:2], DEFAULT_SECTOR_ETF)


@dataclass
class EnrichmentResult:
    price_d0: float | None
    price_d_minus_5: float | None
    price_d_minus_20: float | None
    volume_d0: int | None
    volume_avg_20d: float | None
    volume_ratio: float | None
    beta_vs_spy: float | None
    ff_size_exposure: float | None
    ff_value_exposure: float | None
    vix_d0: float | None
    sector_etf_ticker: str
    sector_mood: float | None
    pre_event_drift_pct: float | None
    high_low_range_pct: float | None
    adv_usd_60d: float | None
    n_estimation_days: int
    had_survivorship_warning: bool


def _adjusted_close(prices: pd.DataFrame) -> pd.Series:
    return prices["close_raw"] * prices["adj_factor"].fillna(1.0)


def _nearest_at_or_before(series: pd.Series, target: pd.Timestamp, max_lookback: int = MAX_LOOKBACK_DAYS_FOR_PRICE):
    for delta in range(max_lookback + 1):
        d = target - pd.Timedelta(days=delta)
        if d in series.index:
            return series.loc[d]
    return None


def _one_day_return(series: pd.Series, target: pd.Timestamp) -> float | None:
    """Retorno simple del día `target` respecto al día hábil anterior disponible."""
    if target not in series.index:
        return None
    idx = series.index.get_loc(target)
    if idx == 0:
        return None
    prev = series.iloc[idx - 1]
    curr = series.iloc[idx]
    return (curr / prev - 1) if prev else None


def compute_enrichment(
    ticker_prices: pd.DataFrame,
    spy_prices: pd.DataFrame,
    sector_prices: pd.DataFrame,
    vix_prices: pd.DataFrame,
    factor_returns: pd.DataFrame,
    d0_close_date: date,
    sector_etf_ticker: str,
) -> EnrichmentResult:
    """Todos los *_prices vienen indexados por trade_date, con columnas
    close_raw/high_raw/low_raw/adj_factor/volume/survivorship_warning — la
    misma forma que devuelve una consulta a la tabla `prices` (ver
    fetch_and_compute_enrichment para el ensamblado real desde Postgres).
    """
    d0_ts = pd.Timestamp(d0_close_date)
    if ticker_prices.empty:
        # Ticker sin ningún precio todavía (p. ej. recién aparecido en una
        # pasada intradía, antes del backfill nocturno). Antes: KeyError
        # DESPUÉS de haber pagado Bull/Bear/Judge, y vuelta a pagar la noche
        # siguiente. Ahora: todo None, que abstention_engine ya trata como
        # "no evaluable".
        ticker_prices = pd.DataFrame(
            columns=["close_raw", "high_raw", "low_raw", "adj_factor", "volume", "survivorship_warning"],
            index=pd.DatetimeIndex([], name="trade_date"),
        )
    adj_close = _adjusted_close(ticker_prices)

    price_d0 = _nearest_at_or_before(adj_close, d0_ts)
    price_d_minus_5 = _nearest_at_or_before(adj_close, d0_ts - pd.Timedelta(days=5))
    price_d_minus_20 = _nearest_at_or_before(adj_close, d0_ts - pd.Timedelta(days=20))
    price_d_minus_1 = _nearest_at_or_before(adj_close, d0_ts - pd.Timedelta(days=1))

    pre_event_drift_pct = None
    if price_d_minus_5 and price_d_minus_1 and price_d_minus_5 > 0:
        pre_event_drift_pct = (price_d_minus_1 / price_d_minus_5 - 1) * 100

    volume_d0 = None
    if "volume" in ticker_prices and d0_ts in ticker_prices.index:
        v = ticker_prices.loc[d0_ts, "volume"]
        volume_d0 = int(v) if pd.notna(v) else None

    window_20d = ticker_prices[(ticker_prices.index < d0_ts) & (ticker_prices.index >= d0_ts - pd.Timedelta(days=40))]
    volume_avg_20d = window_20d["volume"].tail(20).mean() if not window_20d.empty else None
    volume_ratio = (volume_d0 / volume_avg_20d) if (volume_d0 and volume_avg_20d and volume_avg_20d > 0) else None

    high_low_range_pct = None
    if d0_ts in ticker_prices.index:
        row = ticker_prices.loc[d0_ts]
        if pd.notna(row.get("high_raw")) and pd.notna(row.get("low_raw")) and pd.notna(row.get("close_raw")) and row["close_raw"]:
            high_low_range_pct = (row["high_raw"] - row["low_raw"]) / row["close_raw"] * 100

    # ADV point-in-time (hallazgo de auditoría — proxy de liquidez para
    # abstention_engine.py, condición 6 junto al spread): promedio de
    # close_raw*volume de los ADV_TRAILING_WINDOW_DAYS días de negociación
    # ANTERIORES a d0 (estrictamente < d0_ts — el volumen del día del evento
    # en sí no aporta a "¿era líquido ANTES de que pasara esto?"). MISMO
    # cálculo y MISMOS umbrales que portfolio_simulator.compute_trailing_adv_usd
    # (que opera sobre un dict, no un DataFrame — de ahí la reimplementación
    # aquí en pandas, en vez de convertir formatos en cada llamada), y
    # deliberadamente NO universe.adv_usd_60d (esa columna se recalcula sobre
    # los 60 días más recientes respecto a HOY, no respecto a d0 — aplicarla
    # aquí sería el mismo look-ahead sutil que ya se documentó al construir
    # el tope de posición del backtest).
    window_adv = ticker_prices[ticker_prices.index < d0_ts].tail(ADV_TRAILING_WINDOW_DAYS)
    adv_usd_60d = None
    if not window_adv.empty and "volume" in window_adv:
        dollar_volumes = (window_adv["close_raw"] * window_adv["volume"]).dropna()
        if len(dollar_volumes) >= ADV_MIN_TRADING_DAYS:
            adv_usd_60d = float(dollar_volumes.mean())

    had_survivorship_warning = False
    nearby = ticker_prices[(ticker_prices.index >= d0_ts - pd.Timedelta(days=5)) & (ticker_prices.index <= d0_ts)]
    if "survivorship_warning" in nearby and nearby["survivorship_warning"].any():
        had_survivorship_warning = True

    # Beta / exposición a factores: reutiliza el ajuste compartido con compute_car.
    fit = None
    if not ticker_prices.empty and not factor_returns.empty:
        # Sin factores (descarga de Ken French caída esa noche) el join deja un
        # índice sin tipo fecha y fit_factor_model revienta comparándolo con
        # un Timestamp. Sin factores no hay beta: None, no una excepción.
        merged = ticker_prices.copy()
        merged["ret"] = adj_close.pct_change()
        merged = merged.join(factor_returns, how="inner")
        fit = fit_factor_model(merged, d0_close_date)
    beta_vs_spy = fit.beta_mkt if fit else None
    ff_size_exposure = fit.beta_smb if fit else None
    ff_value_exposure = fit.beta_hml if fit else None
    n_estimation_days = fit.n_estimation_days if fit else 0

    vix_d0 = None
    if not vix_prices.empty:
        vix_close = _adjusted_close(vix_prices) if "adj_factor" in vix_prices else vix_prices["close_raw"]
        vix_d0 = _nearest_at_or_before(vix_close, d0_ts)

    sector_mood = None
    if not sector_prices.empty and not spy_prices.empty:
        sector_ret = _one_day_return(_adjusted_close(sector_prices), d0_ts)
        spy_ret = _one_day_return(_adjusted_close(spy_prices), d0_ts)
        if sector_ret is not None and spy_ret is not None:
            sector_mood = (sector_ret - spy_ret) * 100

    return EnrichmentResult(
        price_d0=price_d0,
        price_d_minus_5=price_d_minus_5,
        price_d_minus_20=price_d_minus_20,
        volume_d0=volume_d0,
        volume_avg_20d=volume_avg_20d,
        volume_ratio=volume_ratio,
        beta_vs_spy=beta_vs_spy,
        ff_size_exposure=ff_size_exposure,
        ff_value_exposure=ff_value_exposure,
        vix_d0=vix_d0,
        sector_etf_ticker=sector_etf_ticker,
        sector_mood=sector_mood,
        pre_event_drift_pct=pre_event_drift_pct,
        high_low_range_pct=high_low_range_pct,
        adv_usd_60d=adv_usd_60d,
        n_estimation_days=n_estimation_days,
        had_survivorship_warning=had_survivorship_warning,
    )


def fetch_and_compute_enrichment(conn, event: dict, series_comunes: dict | None = None) -> EnrichmentResult:
    """Ensambla los paneles desde Postgres y llama a compute_enrichment().

    event: dict con al menos 'ticker', 'd0_close_date', 'sic_code' (de un JOIN
    events+universe — ver analyze/event_analysis_pipeline.py).

    series_comunes: caché de UNA corrida para las series que comparten todos
    los eventos (SPY, ETFs sectoriales, ^VIX y Fama-French). Sin ella, cada
    evento volvía a leer el histórico completo de las cuatro: hasta 500
    eventos x 4 consultas grandes por corrida (BUGS_REPORT.md H-27). El que
    llama crea el dict al empezar la corrida; dentro de una corrida esas
    series no cambian (los precios se descargan antes del análisis).
    """
    import pandas as pd

    def _load_prices(ticker: str) -> pd.DataFrame:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning "
                "FROM prices WHERE ticker = %s ORDER BY trade_date",
                (ticker,),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df.set_index("trade_date").astype({"close_raw": float, "high_raw": float, "low_raw": float, "adj_factor": float})

    def _load_factors() -> pd.DataFrame:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_date, mkt_rf, smb, hml, rf FROM fama_french_factors ORDER BY trade_date")
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df.set_index("trade_date").astype(float)

    cache = series_comunes if series_comunes is not None else {}

    def _comun(clave: str, cargar):
        if clave not in cache:
            cache[clave] = cargar()
        return cache[clave]

    sector_etf = sic_to_sector_etf(event.get("sic_code"))
    ticker_prices = _load_prices(event["ticker"])
    spy_prices = _comun("SPY", lambda: _load_prices("SPY"))
    sector_prices = _comun(sector_etf, lambda: _load_prices(sector_etf))
    vix_prices = _comun("^VIX", lambda: _load_prices("^VIX"))
    factor_returns = _comun("__fama_french__", _load_factors)

    return compute_enrichment(
        ticker_prices, spy_prices, sector_prices, vix_prices, factor_returns,
        event["d0_close_date"], sector_etf,
    )
