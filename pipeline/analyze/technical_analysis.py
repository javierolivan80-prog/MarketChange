"""technical_analysis.py — confirmación técnica y plan de operación por señal.

Etapa posterior a event_analysis_pipeline.py. El análisis event-driven ya
decidió QUÉ evento merece operarse y en qué dirección; esta etapa decide si el
GRÁFICO lo acompaña y CÓMO operarlo con riesgo acotado:

1. Indicadores diarios (RSI y divergencias, MACD, EMA 20/50/200, ADX/DI,
   Bollinger, Stoch RSI, ATR, OBV, Acumulación/Distribución, VWAP de 20
   sesiones y volumen relativo).
2. Niveles clave: swings de 20 y 60 sesiones, pivots clásicos, retrocesos de
   Fibonacci del último rango de 60 sesiones, EMA 50/200, bandas de Bollinger
   y VWAP. La confluencia es que dos o más niveles caigan dentro de 0,5 ATR.
3. Plan: entrada de referencia (cierre de D0; la operación real entra en
   D+1), stop por debajo del soporte confirmado más cercano (o por encima de
   la resistencia, en cortos) con un colchón de 0,25 ATR, objetivo parcial en
   la primera resistencia estructural a más de 1 ATR y objetivo final en la
   siguiente (pivots centrales, VWAP, EMA 50 y bandas no cuentan como
   objetivo: son demasiado cercanos y frecuentes), riesgo/beneficio,
   tamaño máximo de posición ajustado por volatilidad y horizonte estimado.
4. Puntuación 0-100: catalizador confirmado +40, 3 o más indicadores
   alineados +30, confluencia de niveles +20, volumen de confirmación +10.
5. Comprobaciones previas: catalizador confirmado (no especulativo), al menos
   2 indicadores alineados, riesgo/beneficio >= 1:2 y stop apoyado en un
   soporte (resistencia) real, no en una distancia arbitraria.

Disciplina anti look-ahead: TODO se calcula con precios de trade_date <=
d0_close_date del evento — la misma información que existía al cierre del
día del evento. Un precio posterior nunca entra (test dedicado).

Limitaciones declaradas (se guardan en cada plan, no se ocultan): solo hay
precios diarios, así que no hay niveles en 4H/1H ni divergencias intradía;
tampoco volatilidad implícita, posicionamiento en opciones, sentimiento en
redes, flujo institucional ni order flow — ninguna de esas fuentes está en el
pipeline. El VWAP es de 20 sesiones sobre precio típico diario, no intradía.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_BARS = 60  # por debajo, ni ATR ni swings ni Fibonacci de 60 sesiones son fiables
MIN_RISK_REWARD = 2.0
MIN_ALIGNED_FOR_ENTRY = 2
ALIGNED_FOR_FULL_SCORE = 3
CONFLUENCE_ATR = 0.5  # dos niveles a menos de 0,5 ATR cuentan como confluencia
STOP_BUFFER_ATR = 0.25
FALLBACK_STOP_ATR = 2.0  # sin soporte real a mano: stop por ATR, y el plan NO pasa los filtros
MAX_STOP_DISTANCE_ATR = 3.0
MIN_STOP_DISTANCE_ATR = 0.5
MIN_TARGET_DISTANCE_ATR = 1.0
VOLUME_CONFIRMATION_RATIO = 1.2  # volumen de D0 > 120% de la media de 20 sesiones
MAX_POSITION_PCT = 3.0
REDUCED_POSITION_PCT = 2.0  # tope si la confianza técnica no llega a HIGH_CONFIDENCE
MIN_POSITION_PCT = 0.5
HIGH_CONFIDENCE = 70
TRAILING_STOP_ATR = 2.5
CROSS_LOOKBACK = 20  # un cruce de medias cuenta si ocurrió en las últimas 20 sesiones
SQUEEZE_PERCENTILE = 0.2  # bandas en el 20% más estrecho de 120 sesiones = contracción
VOLUME_PROFILE_SESSIONS = 60
VOLUME_PROFILE_BINS = 24
VALUE_AREA_SHARE = 0.7
VIX_ELEVATED = 25.0  # por encima, tamaño x0,75
VIX_STRESS = 35.0  # por encima, tamaño x0,5

LIMITATIONS = [
    "Solo precios diarios: sin niveles ni divergencias en 4H/1H.",
    "Sin volatilidad implícita del subyacente ni posicionamiento en opciones (el VIX sí se usa).",
    "Sin sentimiento en redes, flujo institucional, operaciones de insiders ni order flow.",
    "Sin calendario de anuncios: todas las señales son posteriores al evento (entrada en D+1).",
    "VWAP de 20 sesiones y volume profile sobre precio típico diario, no intradía.",
]


# ---------------------------------------------------------------------------
# Indicadores (puros, sobre un DataFrame con open/high/low/close/volume)
# ---------------------------------------------------------------------------


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), period)
    loss = _wilder(-delta.clip(upper=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # Sin pérdidas en la ventana: RSI = 100 (y 50 si tampoco hubo ganancias).
    out = out.where(loss != 0, np.where(gain > 0, 100.0, 50.0))
    return out


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = _wilder(true_range(df), period)
    plus_di = 100 * _wilder(plus_dm, period) / tr
    minus_di = 100 * _wilder(minus_dm, period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx, period), plus_di, minus_di


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, 12) - ema(close, 26)
    signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
    return line, signal, line - signal


def stoch_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    r = rsi(close, period)
    lo, hi = r.rolling(period).min(), r.rolling(period).max()
    return (r - lo) / (hi - lo).replace(0, np.nan)


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def accumulation_distribution(df: pd.DataFrame) -> pd.Series:
    span = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / span
    return (mfm.fillna(0) * df["volume"]).cumsum()


def _slope(series: pd.Series, bars: int = 20) -> float | None:
    tail = series.dropna().tail(bars)
    if len(tail) < bars:
        return None
    x = np.arange(len(tail))
    return float(np.polyfit(x, tail.to_numpy(dtype=float), 1)[0])


def rsi_divergence(close: pd.Series, rsi_series: pd.Series, lookback: int = 30) -> str | None:
    """Divergencia entre los dos mínimos (o máximos) más recientes de las
    últimas `lookback` sesiones. Alcista: el precio marca un mínimo más bajo y
    el RSI uno más alto. Bajista: precio con máximo más alto y RSI más bajo."""
    c = close.tail(lookback)
    r = rsi_series.reindex(c.index)
    if len(c) < lookback or r.isna().any():
        return None
    half = lookback // 2
    first, second = c.iloc[:half], c.iloc[half:]
    lo1, lo2 = first.idxmin(), second.idxmin()
    if c[lo2] < c[lo1] and r[lo2] > r[lo1]:
        return "bullish"
    hi1, hi2 = first.idxmax(), second.idxmax()
    if c[hi2] > c[hi1] and r[hi2] < r[hi1]:
        return "bearish"
    return None


def _cross_days_ago(fast: pd.Series, slow: pd.Series, up: bool, lookback: int = CROSS_LOOKBACK) -> int | None:
    """Sesiones desde el último cruce de `fast` sobre `slow` (up=True: cruce
    dorado, al alza; False: cruce de la muerte), si ocurrió en las últimas
    `lookback`. None si no hubo cruce en ese plazo o faltan datos."""
    diff = (fast - slow).dropna().tail(lookback + 1)
    if len(diff) < 2:
        return None
    sign = np.sign(diff.to_numpy())
    for i in range(len(sign) - 1, 0, -1):
        if (up and sign[i - 1] <= 0 < sign[i]) or (not up and sign[i - 1] >= 0 > sign[i]):
            return len(sign) - 1 - i
    return None


def volume_profile(df: pd.DataFrame, sessions: int = VOLUME_PROFILE_SESSIONS, bins: int = VOLUME_PROFILE_BINS) -> dict | None:
    """Reparto del volumen por precio en las últimas `sessions` sesiones (precio
    típico diario, sin D0). Devuelve el punto de control (precio con más
    volumen) y el área de valor (el 70% del volumen alrededor del POC)."""
    window = df.iloc[:-1].tail(sessions)
    if len(window) < sessions // 2 or window["volume"].sum() <= 0:
        return None
    typical = ((window["high"] + window["low"] + window["close"]) / 3).to_numpy()
    lo, hi = typical.min(), typical.max()
    if hi <= lo:
        return None
    hist, edges = np.histogram(typical, bins=bins, range=(lo, hi), weights=window["volume"].to_numpy())
    centers = (edges[:-1] + edges[1:]) / 2
    poc = int(hist.argmax())
    included, total, left, right = hist[poc], hist.sum(), poc, poc
    while included < VALUE_AREA_SHARE * total and (left > 0 or right < bins - 1):
        next_left = hist[left - 1] if left > 0 else -1
        next_right = hist[right + 1] if right < bins - 1 else -1
        if next_right >= next_left:
            right += 1
            included += hist[right]
        else:
            left -= 1
            included += hist[left]
    return {"poc": float(centers[poc]), "value_area_low": float(edges[left]), "value_area_high": float(edges[right + 1])}


def _num(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else round(value, 4)


def compute_indicators(df: pd.DataFrame) -> dict:
    close, volume = df["close"], df["volume"]
    rsi_s = rsi(close)
    macd_line, macd_signal, macd_hist = macd(close)
    adx_s, plus_di, minus_di = adx(df)
    atr_s = atr(df)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap20 = (typical * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
    avg_vol20 = volume.shift(1).rolling(20).mean()  # media SIN el propio D0
    atr_pct = atr_s / close
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    width = 4 * std / mid
    ema20 = ema(close, 20)

    return {
        "close": _num(close.iloc[-1]),
        "rsi": _num(rsi_s.iloc[-1]),
        "rsi_divergence": rsi_divergence(close, rsi_s),
        "macd": _num(macd_line.iloc[-1]),
        "macd_signal": _num(macd_signal.iloc[-1]),
        "macd_hist": _num(macd_hist.iloc[-1]),
        "macd_hist_prev": _num(macd_hist.iloc[-2]) if len(macd_hist) > 1 else None,
        "ema20": _num(ema(close, 20).iloc[-1]),
        "ema50": _num(ema(close, 50).iloc[-1]),
        "ema200": _num(ema(close, 200).iloc[-1]) if len(close) >= 200 else None,
        "adx": _num(adx_s.iloc[-1]),
        "plus_di": _num(plus_di.iloc[-1]),
        "minus_di": _num(minus_di.iloc[-1]),
        "atr": _num(atr_s.iloc[-1]),
        "atr_pct": _num(atr_pct.iloc[-1]),
        "atr_pct_median_120": _num(atr_pct.tail(120).median()),
        "bb_upper": _num((mid + 2 * std).iloc[-1]),
        "bb_lower": _num((mid - 2 * std).iloc[-1]),
        "bb_width": _num((4 * std / mid).iloc[-1]),
        "bb_width_median_120": _num(width.tail(120).median()),
        # Contracción (squeeze): ancho de bandas en el 20% más estrecho de las
        # últimas 120 sesiones en algún momento de las últimas 10.
        "bb_squeeze_recent": bool((width.tail(10) <= width.tail(120).quantile(SQUEEZE_PERCENTILE)).any()) if width.notna().sum() >= 120 else None,
        "sma50": _num(sma50.iloc[-1]),
        "sma200": _num(sma200.iloc[-1]) if len(close) >= 200 else None,
        "golden_cross_days_ago": _cross_days_ago(sma50, sma200, up=True),
        "death_cross_days_ago": _cross_days_ago(sma50, sma200, up=False),
        "atr_band_upper": _num((ema20 + 2 * atr_s).iloc[-1]),
        "atr_band_lower": _num((ema20 - 2 * atr_s).iloc[-1]),
        # Ruptura del rango de 20 sesiones (sin contar D0) en el cierre de D0.
        "breakout_up": bool(close.iloc[-1] > df["high"].shift(1).rolling(20).max().iloc[-1]) if len(df) > 21 else None,
        "breakout_down": bool(close.iloc[-1] < df["low"].shift(1).rolling(20).min().iloc[-1]) if len(df) > 21 else None,
        "stoch_rsi": _num(stoch_rsi(close).iloc[-1]),
        "stoch_rsi_min_5": _num(stoch_rsi(close).tail(5).min()),
        "stoch_rsi_max_5": _num(stoch_rsi(close).tail(5).max()),
        "bb_mid": _num(mid.iloc[-1]),
        "obv_slope_20": _slope(obv(df)),
        "ad_slope_20": _slope(accumulation_distribution(df)),
        "vwap20": _num(vwap20.iloc[-1]),
        "volume_ratio": _num(volume.iloc[-1] / avg_vol20.iloc[-1]) if avg_vol20.iloc[-1] and avg_vol20.iloc[-1] > 0 else None,
    }


def aligned_indicators(ind: dict, direction: str) -> list[str]:
    """Qué indicadores acompañan a la dirección. Cada uno cuenta una vez."""
    long = direction == "LONG"
    out = []
    close = ind["close"]

    r = ind["rsi"]
    if r is not None:
        if long and (50 <= r <= 70 or ind["rsi_divergence"] == "bullish"):
            out.append("RSI")
        if not long and (30 <= r <= 50 or ind["rsi_divergence"] == "bearish"):
            out.append("RSI")
    if ind["macd"] is not None and ind["macd_signal"] is not None:
        hist_rising = ind["macd_hist_prev"] is not None and ind["macd_hist"] > ind["macd_hist_prev"]
        if long and ind["macd"] > ind["macd_signal"] and hist_rising:
            out.append("MACD")
        if not long and ind["macd"] < ind["macd_signal"] and not hist_rising:
            out.append("MACD")
    if ind["ema20"] is not None and ind["ema50"] is not None:
        ema200 = ind["ema200"]
        if long and close > ind["ema20"] > ind["ema50"] and (ema200 is None or ind["ema50"] > ema200):
            out.append("Medias móviles")
        if not long and close < ind["ema20"] < ind["ema50"] and (ema200 is None or ind["ema50"] < ema200):
            out.append("Medias móviles")
    if ind["adx"] is not None and ind["adx"] > 25 and ind["plus_di"] is not None:
        if (long and ind["plus_di"] > ind["minus_di"]) or (not long and ind["minus_di"] > ind["plus_di"]):
            out.append("ADX")
    if ind["obv_slope_20"] is not None:
        if (long and ind["obv_slope_20"] > 0) or (not long and ind["obv_slope_20"] < 0):
            out.append("OBV")
    if ind["vwap20"] is not None:
        if (long and close > ind["vwap20"]) or (not long and close < ind["vwap20"]):
            out.append("VWAP")
    if ind["stoch_rsi"] is not None and ind["stoch_rsi_min_5"] is not None:
        # Cuenta solo al SALIR del extremo contrario en las últimas 5 sesiones
        # (largos: venía de sobreventa < 0,2 y ya está por encima). Estar
        # simplemente "no sobrecomprado" pasa casi siempre y no confirma nada.
        if long and ind["stoch_rsi_min_5"] < 0.2 < ind["stoch_rsi"] < 0.8:
            out.append("Stoch RSI")
        if not long and ind["stoch_rsi_max_5"] > 0.8 > ind["stoch_rsi"] > 0.2:
            out.append("Stoch RSI")
    if ind.get("golden_cross_days_ago") is not None and long:
        out.append("Cruce dorado (SMA 50/200)")
    if ind.get("death_cross_days_ago") is not None and not long:
        out.append("Cruce de la muerte (SMA 50/200)")
    breakout = ind.get("breakout_up") if long else ind.get("breakout_down")
    if breakout and ind["volume_ratio"] is not None and ind["volume_ratio"] > VOLUME_CONFIRMATION_RATIO:
        out.append("Ruptura con volumen")
    if ind["bb_mid"] is not None and ind["bb_width"] is not None and ind["bb_width_median_120"] is not None:
        # Bandas abriéndose (volatilidad en expansión) con el precio del lado
        # de la media que corresponde a la dirección.
        expanding = ind["bb_width"] > ind["bb_width_median_120"]
        if expanding and ((long and close > ind["bb_mid"]) or (not long and close < ind["bb_mid"])):
            out.append("Bollinger")
    return out


# ---------------------------------------------------------------------------
# Niveles
# ---------------------------------------------------------------------------


def key_levels(df: pd.DataFrame, ind: dict) -> list[dict]:
    """Lista de niveles {price, kind, major}. kind identifica el TIPO de nivel,
    para que la confluencia exija tipos distintos y no dos copias del mismo.

    major distingue los niveles estructurales (máximos/mínimos de 20 y 60
    sesiones, Fibonacci, EMA 200, pivots S2/R2) de los menores (pivot central,
    S1/R1, EMA 50, Bollinger, VWAP). Solo los mayores sirven de OBJETIVO: entre
    tantos niveles menores casi siempre hay uno pegado al precio, y tomarlo
    como objetivo daría riesgo/beneficio < 1 en prácticamente todas las
    señales. Los menores sí cuentan para la confluencia del stop."""
    levels: list[dict] = []
    minor_kinds = {
        "Pivot", "Pivot S1", "Pivot R1", "EMA 50", "Bollinger superior", "Bollinger inferior", "VWAP 20",
        "Banda ATR superior", "Banda ATR inferior",
    }

    def add(price, kind):
        p = _num(price)
        if p is not None and p > 0:
            levels.append({"price": p, "kind": kind, "major": kind not in minor_kinds})

    for n in (20, 60):
        window = df.tail(n + 1).iloc[:-1]  # sin el propio D0
        if len(window) >= n:
            add(window["low"].min(), f"Mínimo {n} sesiones")
            add(window["high"].max(), f"Máximo {n} sesiones")

    prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
    pivot = (prev["high"] + prev["low"] + prev["close"]) / 3
    rng = prev["high"] - prev["low"]
    add(pivot, "Pivot")
    add(2 * pivot - prev["high"], "Pivot S1")
    add(pivot - rng, "Pivot S2")
    add(2 * pivot - prev["low"], "Pivot R1")
    add(pivot + rng, "Pivot R2")

    swing = df.tail(60)
    hi, lo = swing["high"].max(), swing["low"].min()
    for ratio in (0.382, 0.5, 0.618):
        add(hi - (hi - lo) * ratio, f"Fibonacci {ratio * 100:.1f}%")
    add(hi + (hi - lo) * 0.272, "Fibonacci 127.2%")
    add(lo - (hi - lo) * 0.272, "Fibonacci -27.2%")

    add(ind.get("ema50"), "EMA 50")
    add(ind.get("ema200"), "EMA 200")
    add(ind.get("bb_upper"), "Bollinger superior")
    add(ind.get("bb_lower"), "Bollinger inferior")
    add(ind.get("vwap20"), "VWAP 20")
    add(ind.get("atr_band_upper"), "Banda ATR superior")
    add(ind.get("atr_band_lower"), "Banda ATR inferior")
    add(ind.get("sma200"), "SMA 200")
    profile = volume_profile(df)
    if profile:
        add(profile["poc"], "Volume profile: POC")
        add(profile["value_area_low"], "Volume profile: área de valor baja")
        add(profile["value_area_high"], "Volume profile: área de valor alta")
    return levels


def _cluster_at(levels: list[dict], price: float, width: float) -> list[str]:
    return sorted({lv["kind"] for lv in levels if abs(lv["price"] - price) <= width})


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class TradePlan:
    direction: str
    entry: float | None = None
    stop: float | None = None
    target: float | None = None  # objetivo parcial: primera resistencia (soporte en cortos)
    target2: float | None = None  # objetivo final: la siguiente
    risk_reward: float | None = None
    confidence: int = 0
    passes_filters: bool = False
    position_size_pct: float | None = None
    timeframe_days: int | None = None
    components: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    aligned: list = field(default_factory=list)
    stop_basis: list = field(default_factory=list)
    target_basis: list = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    exit_rules: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    reason_if_rejected: str | None = None
    limitations: list = field(default_factory=lambda: list(LIMITATIONS))

    def as_json(self) -> dict:
        return asdict(self)


def build_trade_plan(
    df: pd.DataFrame,
    direction: str,
    catalyst_confirmed: bool,
    vix: float | None = None,
    expected_move_pct: float | None = None,
) -> TradePlan:
    """df: OHLCV diario AJUSTADO, ordenado, con D0 como última fila.

    vix: cierre del VIX en D0 (event_enrichment.vix_d0). Por encima de 25 el
    tamaño se reduce un 25%, por encima de 35 a la mitad.
    expected_move_pct: movimiento típico de los eventos parecidos del pasado
    (historical_analogues, ya con shrinkage). Si el objetivo pide más que eso,
    se avisa: es un objetivo codicioso para este tipo de evento."""
    plan = TradePlan(direction=direction)
    plan.context = {"vix": _num(vix) if vix is not None else None, "expected_move_pct": _num(expected_move_pct) if expected_move_pct is not None else None}
    if len(df) < MIN_BARS:
        plan.reason_if_rejected = f"Histórico insuficiente ({len(df)} sesiones; hacen falta {MIN_BARS})."
        plan.checks = {"catalyst_confirmed": catalyst_confirmed, "enough_history": False}
        plan.components = {"catalyst": 40 if catalyst_confirmed else 0, "indicators": 0, "confluence": 0, "volume": 0}
        plan.confidence = plan.components["catalyst"]
        return plan

    ind = compute_indicators(df)
    plan.indicators = ind
    entry, atr_v = ind["close"], ind["atr"]
    long = direction == "LONG"
    plan.entry = entry

    if not atr_v or atr_v <= 0:
        plan.reason_if_rejected = "Sin volatilidad medible (ATR nulo)."
        return plan

    levels = key_levels(df, ind)
    width = CONFLUENCE_ATR * atr_v

    # Stop: el soporte (resistencia en cortos) más cercano en [0,5 ATR, 3 ATR];
    # entre varios, el que más confluencia tiene, y a igualdad, el más cercano.
    def in_stop_zone(p):
        d = (entry - p) if long else (p - entry)
        return MIN_STOP_DISTANCE_ATR * atr_v <= d <= MAX_STOP_DISTANCE_ATR * atr_v

    stop_candidates = [lv["price"] for lv in levels if in_stop_zone(lv["price"])]
    support_confirmed = bool(stop_candidates)
    if stop_candidates:
        best = max(stop_candidates, key=lambda p: (len(_cluster_at(levels, p, width)), -abs(entry - p)))
        plan.stop_basis = _cluster_at(levels, best, width)
        plan.stop = round(best - STOP_BUFFER_ATR * atr_v if long else best + STOP_BUFFER_ATR * atr_v, 4)
    else:
        plan.stop = round(entry - FALLBACK_STOP_ATR * atr_v if long else entry + FALLBACK_STOP_ATR * atr_v, 4)
        plan.stop_basis = [f"{FALLBACK_STOP_ATR:g} ATR (sin soporte cercano)"]

    # Objetivos: primeras resistencias MAYORES (soportes en cortos) a más de
    # 1 ATR, agrupando las que están a menos de 0,5 ATR entre sí.
    beyond = sorted(
        (
            lv["price"]
            for lv in levels
            if lv.get("major", True) and ((lv["price"] - entry) if long else (entry - lv["price"])) >= MIN_TARGET_DISTANCE_ATR * atr_v
        ),
        reverse=not long,
    )
    distinct: list[float] = []
    for p in beyond:
        if not distinct or abs(p - distinct[-1]) > width:
            distinct.append(p)
    if distinct:
        plan.target = round(distinct[0], 4)
        plan.target_basis = _cluster_at(levels, distinct[0], width)
        plan.target2 = round(distinct[1], 4) if len(distinct) > 1 else None
    else:
        plan.target = round(entry + 3 * atr_v if long else entry - 3 * atr_v, 4)
        plan.target_basis = ["3 ATR (sin resistencia por encima)" if long else "3 ATR (sin soporte por debajo)"]

    risk = abs(entry - plan.stop)
    reward = abs(plan.target - entry)
    plan.risk_reward = round(reward / risk, 2) if risk > 0 else None

    plan.aligned = aligned_indicators(ind, direction)
    confluence = len(plan.stop_basis) >= 2 and support_confirmed
    volume_ok = ind["volume_ratio"] is not None and ind["volume_ratio"] > VOLUME_CONFIRMATION_RATIO
    plan.components = {
        "catalyst": 40 if catalyst_confirmed else 0,
        "indicators": 30 if len(plan.aligned) >= ALIGNED_FOR_FULL_SCORE else 0,
        "confluence": 20 if confluence else 0,
        "volume": 10 if volume_ok else 0,
    }
    plan.confidence = sum(plan.components.values())

    plan.checks = {
        "catalyst_confirmed": catalyst_confirmed,
        "indicators_aligned": len(plan.aligned) >= MIN_ALIGNED_FOR_ENTRY,
        "risk_reward_ok": plan.risk_reward is not None and plan.risk_reward >= MIN_RISK_REWARD,
        "stop_on_support": support_confirmed,
    }
    plan.passes_filters = all(plan.checks.values())
    if not plan.passes_filters:
        failed = {
            "catalyst_confirmed": "catalizador no confirmado",
            "indicators_aligned": (
                "ningún indicador alineado" if not plan.aligned
                else "solo 1 indicador alineado" if len(plan.aligned) == 1
                else f"solo {len(plan.aligned)} indicadores alineados"
            ),
            "risk_reward_ok": f"riesgo/beneficio 1:{plan.risk_reward} < 1:{MIN_RISK_REWARD:g}",
            "stop_on_support": "sin soporte real para el stop" if long else "sin resistencia real para el stop",
        }
        plan.reason_if_rejected = "; ".join(msg for key, msg in failed.items() if not plan.checks[key]).capitalize() + "."

    # Tamaño: 3% como máximo, 2% si la confianza no es alta, y reducido en
    # proporción si la volatilidad actual supera su mediana de 120 sesiones.
    cap = MAX_POSITION_PCT if plan.confidence >= HIGH_CONFIDENCE else REDUCED_POSITION_PCT
    vol_factor = 1.0
    if ind["atr_pct"] and ind["atr_pct_median_120"] and ind["atr_pct"] > ind["atr_pct_median_120"]:
        vol_factor = ind["atr_pct_median_120"] / ind["atr_pct"]
    vix_factor = 1.0
    if vix is not None and vix > VIX_STRESS:
        vix_factor = 0.5
        plan.warnings.append(f"VIX en {vix:.0f}: mercado en tensión, tamaño a la mitad.")
    elif vix is not None and vix > VIX_ELEVATED:
        vix_factor = 0.75
        plan.warnings.append(f"VIX en {vix:.0f}: volatilidad de mercado elevada, tamaño reducido un 25%.")
    plan.position_size_pct = round(max(MIN_POSITION_PCT, cap * vol_factor * vix_factor), 1)

    if expected_move_pct and entry:
        target_move_pct = abs(plan.target - entry) / entry * 100
        plan.context["target_move_pct"] = round(target_move_pct, 2)
        if target_move_pct > abs(expected_move_pct):
            plan.warnings.append(
                f"El objetivo pide un {target_move_pct:.1f}%, más que el movimiento típico de eventos parecidos "
                f"(±{abs(expected_move_pct):.1f}%): conviene tomar beneficios antes."
            )

    # Horizonte: lo que tardaría en recorrer la distancia al objetivo a ~0,5
    # ATR por sesión (ritmo medio de una tendencia), entre 3 y 20 sesiones.
    plan.timeframe_days = int(min(20, max(3, math.ceil(reward / (0.5 * atr_v)))))

    plan.exit_rules = [
        "Entrada escalonada: 50% en la apertura siguiente al evento, 50% si cierra a favor el primer día.",
        "Tomar la mitad en el objetivo parcial y subir el stop al precio de entrada.",
        f"Con la otra mitad, stop dinámico a {TRAILING_STOP_ATR:g} ATR del extremo alcanzado.",
        "Salida forzada si aparece una divergencia contraria en el RSI diario o se pierde el soporte del stop.",
    ]
    return plan


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------


def load_prices(conn, ticker: str, as_of) -> pd.DataFrame:
    """OHLCV ajustado hasta as_of INCLUSIVE (cierre de D0) — nunca después."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date, open_raw, high_raw, low_raw, close_raw, adj_factor, volume
            FROM prices
            WHERE ticker = %s AND trade_date <= %s AND close_raw IS NOT NULL
            ORDER BY trade_date
            """,
            (ticker, as_of),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("trade_date")
    adj = df["adj_factor"].astype(float).fillna(1.0)
    close = df["close_raw"].astype(float) * adj
    out = pd.DataFrame(
        {
            "close": close,
            # Sin high/low/open (datos antiguos de la Fase 1): el cierre hace de
            # todos — el ATR sale más bajo, nunca inventado.
            "high": (df["high_raw"].astype(float) * adj).fillna(close),
            "low": (df["low_raw"].astype(float) * adj).fillna(close),
            "open": (df["open_raw"].astype(float) * adj).fillna(close),
            "volume": df["volume"].astype(float).fillna(0.0),
        }
    )
    return out


def is_catalyst_confirmed(conn, event: dict) -> bool:
    """Un 8-K es oficial por definición. Una decisión de la FDA solo cuenta
    como confirmada si la empresa la ha comunicado con un 8-K entre 3 días
    antes y el propio D0 (nunca después: eso sería información futura). Un
    evento marcado como rumor por novelty.py es especulativo."""
    if (event.get("novelty_reasoning") or {}).get("rumor_flag"):
        return False
    if event["source"] == "EDGAR":
        return True
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM events
            WHERE ticker = %s AND source = 'EDGAR'
              AND d0_close_date BETWEEN %s::date - 3 AND %s::date
            LIMIT 1
            """,
            (event["ticker"], event["d0_close_date"], event["d0_close_date"]),
        )
        return cur.fetchone() is not None


def fetch_pending(conn, limit: int | None = None) -> list[dict]:
    """Eventos con alguna versión operando y sin plan técnico todavía."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.ticker, e.source, e.d0_close_date, ea.net_conviction, ea.novelty_reasoning
            FROM events e
            JOIN event_analyses ea ON ea.event_id = e.event_id
            LEFT JOIN technical_analyses ta ON ta.event_id = e.event_id
            WHERE ta.event_id IS NULL
              AND (ea.trade_decision_conservative != 'NO_TRADE'
                   OR ea.trade_decision_aggressive != 'NO_TRADE'
                   OR ea.trade_decision_balanced != 'NO_TRADE')
            ORDER BY e.d0_close_date DESC
            """
            + (" LIMIT %s" if limit else ""),
            (limit,) if limit else (),
        )
        return cur.fetchall()


def store_plan(conn, event_id: int, plan: TradePlan) -> None:
    import json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO technical_analyses (
                event_id, direction, entry_price, stop_price, target_price, target2_price, risk_reward,
                confidence, passes_filters, position_size_pct, timeframe_days, details
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET
                computed_at = now(), direction = EXCLUDED.direction, entry_price = EXCLUDED.entry_price,
                stop_price = EXCLUDED.stop_price, target_price = EXCLUDED.target_price,
                target2_price = EXCLUDED.target2_price, risk_reward = EXCLUDED.risk_reward,
                confidence = EXCLUDED.confidence, passes_filters = EXCLUDED.passes_filters,
                position_size_pct = EXCLUDED.position_size_pct, timeframe_days = EXCLUDED.timeframe_days,
                details = EXCLUDED.details
            """,
            (
                event_id, plan.direction, plan.entry, plan.stop, plan.target, plan.target2, plan.risk_reward,
                plan.confidence, plan.passes_filters, plan.position_size_pct, plan.timeframe_days,
                json.dumps(plan.as_json(), default=str),
            ),
        )
    conn.commit()


def _event_context(conn, event_id: int) -> tuple[float | None, float | None]:
    """VIX de D0 (event_enrichment) y movimiento típico de eventos parecidos
    (event_analyses.impact_estimation), ambos ya calculados point-in-time por
    las etapas anteriores."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ee.vix_d0, ea.impact_estimation
            FROM event_analyses ea
            LEFT JOIN event_enrichment ee ON ee.event_id = ea.event_id
            WHERE ea.event_id = %s
            """,
            (event_id,),
        )
        row = cur.fetchone()
    if not row:
        return None, None
    vix = float(row["vix_d0"]) if row["vix_d0"] is not None else None
    impact = row["impact_estimation"] or {}
    raw = impact.get("expected_magnitude_pct")
    if raw is None:
        # as_json() guarda la magnitud como texto ("±4.20% según histórico de N…").
        import re as _re

        match = _re.search(r"([\d.]+)%", str(impact.get("expected_magnitude") or ""))
        raw = float(match.group(1)) if match else None
    return vix, (float(raw) if raw is not None else None)


def analyze_event(conn, event: dict) -> TradePlan:
    direction = "LONG" if float(event["net_conviction"]) > 0 else "SHORT"
    prices = load_prices(conn, event["ticker"], event["d0_close_date"])
    vix, expected_move = _event_context(conn, event["event_id"])
    plan = build_trade_plan(prices, direction, is_catalyst_confirmed(conn, event), vix=vix, expected_move_pct=expected_move)
    store_plan(conn, event["event_id"], plan)
    return plan


def run(conn, limit: int | None = None) -> dict:
    pending = fetch_pending(conn, limit)
    stats = {"analyzed": 0, "passed": 0, "failed": 0}
    for event in pending:
        try:
            plan = analyze_event(conn, event)
        except Exception:  # noqa: BLE001 — un evento con datos raros no tumba el resto
            conn.rollback()
            stats["failed"] += 1
            logger.exception("Plan técnico fallido para event_id=%s (%s)", event["event_id"], event["ticker"])
            continue
        stats["analyzed"] += 1
        stats["passed"] += int(plan.passes_filters)
    logger.info(
        "Planes técnicos: %d calculados, %d pasan los filtros, %d con error",
        stats["analyzed"], stats["passed"], stats["failed"],
    )
    return stats


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    run(get_connection(), args.limit)
