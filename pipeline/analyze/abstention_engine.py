"""abstention_engine.py — Etapa 8: la puerta final antes de operar.

Implementa literalmente las 7 condiciones del spec: si CUALQUIERA es true,
NO_TRADE. Se evalúa POR VERSIÓN de estrategia (Conservative/Aggressive/
Balanced) porque el umbral de EV difiere por versión — el mismo evento puede
ser TRADE para Aggressive y NO_TRADE para Conservative, y eso es un resultado
válido, no un bug.

Dos de las 7 condiciones no tienen una fuente de datos gratuita limpia, y se
implementan como PROXIES documentados en vez de fingir que existe una señal
que no existe (mismo principio que el resto del proyecto — AUDIT_LEAN.md):

  - "spread > 0.5% (ilíquido)": no hay bid-ask real gratis (sin datos de
    opciones/microestructura — AUDIT_LEAN.md §2.1). Proxy con DOS
    componentes (hallazgo de auditoría, añadido después del spread
    original — mismo patrón que el proxy de "datos contradictorios" de
    abajo, cualquiera de los dos basta):
      (a) (high-low)/close del día D0, guardado en
          event_enrichment.high_low_range_pct — spread intradía.
      (b) ADV (volumen medio diario en $) de los 60 días de negociación
          ANTERIORES al evento, guardado en event_enrichment.adv_usd_60d
          — liquidez de fondo, no solo el ruido de un día concreto.
          Deliberadamente NO universe.adv_usd_60d: esa columna se
          recalcula sobre los 60 días MÁS RECIENTES respecto a HOY (el
          momento en que corre universe_maintenance.py), no respecto a
          la fecha del evento — usarla aquí evaluaría la liquidez de HOY
          para decidir si un evento de hace años era operable, el mismo
          look-ahead sutil que ya se evitó al construir el tope de
          posición del backtest (portfolio_simulator.compute_trailing_adv_usd,
          mismo cálculo, aplicado en un punto distinto del pipeline).
          Umbral: config.MIN_ADV_USD, el MISMO que ya exige
          universe.in_investable_universe para entrar al universo —
          aquí simplemente se reevalúa en el momento del evento, no en
          un snapshot de mantenimiento.

  - "datos contradictorios entre fuentes": no hay un verificador de hechos
    cruzados entre EDGAR/FDA/precios. Proxy con DOS componentes, cualquiera
    de los dos basta:
      (a) el modelo de factores no pudo ajustarse (event_enrichment sin beta,
          es decir, insuficiente historial de precios — un conflicto real
          entre lo que universe/events afirma sobre el ticker y lo que
          prices puede sustentar), o
      (b) el Judge llegó a una convicción neta casi nula (Bull y Bear se
          anularon) PESE A tener alta confianza en su propio arbitraje —
          eso es la firma de una discusión genuinamente dividida, no de un
          caso claro.
    Esto es más estrecho que "contradicción entre fuentes" en sentido
    literal, y se documenta como tal en el campo `reason_if_no_trade`.
"""
from __future__ import annotations

from dataclasses import dataclass

from pipeline import config
from pipeline.analyze.ev_engine import EV_THRESHOLDS

NOVELTY_FLOOR = 20
CONFIDENCE_FLOOR = 40
EV_ABSTENTION_BUFFER = 0.0050  # 50 bps, sumados al umbral propio de cada versión — "hasta después de fees"
SPREAD_CEILING_PCT = 0.5  # % — (high-low)/close
# Segundo componente del proxy de liquidez (ver docstring del módulo) — MISMO
# umbral que ya exige universe.in_investable_universe (config.MIN_ADV_USD),
# reevaluado en el momento del evento en vez de en un snapshot de HOY.
LIQUIDITY_ADV_FLOOR_USD = config.MIN_ADV_USD

# Componentes del proxy de "datos contradictorios" (ver docstring del módulo).
CONTRADICTION_NET_CONVICTION_CEILING = 0.15  # |net_conviction| por debajo de esto...
CONTRADICTION_CONFIDENCE_FLOOR = 60.0        # ...con confianza del Judge por encima de esto = contradictorio

STRATEGIES = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]


@dataclass
class AbstentionInputs:
    novelty_score: float                  # 0-100 (Etapa 2)
    confidence_in_conviction: float       # 0-100 (Judge, Etapa 5)
    net_conviction: float                 # -1..1 (Judge, Etapa 5)
    ev_by_strategy: dict[str, float]      # {"CONSERVATIVE": frac, "BALANCED": frac, "AGGRESSIVE": frac}
    had_survivorship_warning: bool        # de event_enrichment / prices
    beta_available: bool                  # False si el modelo de factores no pudo ajustarse (proxy de contradicción, componente a)
    high_low_range_pct: float | None      # (high-low)/close en D0, en %; None si no hay datos
    adv_usd_60d: float | None             # ADV en $ de los 60 días ANTERIORES al evento; None si no hay datos suficientes
    is_fda_crl_without_8k: bool           # calculado por el orquestador (cruce EDGAR/FDA)


@dataclass
class AbstentionDecision:
    trade_decision: str  # 'LONG' / 'SHORT' / 'NO_TRADE'
    reason_if_no_trade: str | None
    confidence: float  # 0-100 — igual a confidence_in_conviction cuando SÍ opera; ver nota abajo


def _is_contradictory(inputs: AbstentionInputs) -> bool:
    factor_model_conflict = not inputs.beta_available
    judge_split_decision = (
        abs(inputs.net_conviction) < CONTRADICTION_NET_CONVICTION_CEILING
        and inputs.confidence_in_conviction >= CONTRADICTION_CONFIDENCE_FLOOR
    )
    return factor_model_conflict or judge_split_decision


def _illiquid_reason(inputs: AbstentionInputs) -> str | None:
    """Proxy de liquidez con DOS componentes (ver nota 1 del docstring del
    módulo) — cualquiera de los dos basta, mismo patrón que
    _is_contradictory. Devuelve el motivo si es ilíquido, None si pasa
    ambos componentes."""
    if inputs.high_low_range_pct is None:
        return "sin datos de high/low para estimar liquidez (proxy de spread no disponible)"
    if inputs.high_low_range_pct > SPREAD_CEILING_PCT:
        return f"proxy de spread (high-low)/close={inputs.high_low_range_pct:.2f}% > {SPREAD_CEILING_PCT}% (ilíquido)"
    if inputs.adv_usd_60d is None:
        return "sin datos de volumen suficientes para estimar ADV (proxy de liquidez no disponible)"
    if inputs.adv_usd_60d < LIQUIDITY_ADV_FLOOR_USD:
        return f"ADV≈${inputs.adv_usd_60d:,.0f} < ${LIQUIDITY_ADV_FLOOR_USD:,.0f} (ilíquido, proxy de volumen en el momento del evento)"
    return None


def decide_for_strategy(inputs: AbstentionInputs, strategy: str) -> AbstentionDecision:
    """Aplica las 7 reglas del spec, en el orden en que aparecen, devolviendo
    la PRIMERA que dispara — el orden importa para que reason_if_no_trade sea
    determinista y explicable (dos motivos pueden ser ambos ciertos; se
    reporta el primero por prioridad declarada en el spec)."""

    if inputs.novelty_score < NOVELTY_FLOOR:
        return AbstentionDecision("NO_TRADE", f"novelty_score={inputs.novelty_score:.0f} < {NOVELTY_FLOOR} (evento completamente descontado por el mercado)", inputs.confidence_in_conviction)

    if inputs.confidence_in_conviction < CONFIDENCE_FLOOR:
        return AbstentionDecision("NO_TRADE", f"confidence_in_conviction={inputs.confidence_in_conviction:.0f} < {CONFIDENCE_FLOOR} (no sabemos qué pasa)", inputs.confidence_in_conviction)

    ev = inputs.ev_by_strategy[strategy]
    strategy_threshold = EV_THRESHOLDS[strategy]
    buffered_threshold = strategy_threshold + EV_ABSTENTION_BUFFER
    # IMPROVEMENT_PLAN.md R16: ev_engine.compute_ev propaga el SIGNO de
    # net_conviction (negativo para una convicción bajista) — es el valor
    # esperado de una posición LARGA, no el de la operación que de verdad se
    # ejecutaría. Comparar el ev CON SIGNO contra un umbral siempre positivo
    # vetaba TODO SHORT sin importar la convicción: una convicción bajista
    # fuerte da un ev muy negativo, que nunca supera un umbral positivo. Lo
    # que hay que comparar contra el umbral es la magnitud del EV en la
    # dirección que realmente se tomaría (LONG si net_conviction>0, SHORT si
    # no) — que es abs(ev), no ev. position_size_pct() de abajo ya usa
    # abs(ev) por el mismo motivo.
    if abs(ev) < buffered_threshold:
        return AbstentionDecision(
            "NO_TRADE",
            f"|ev|={abs(ev) * 100:.2f}% < umbral {strategy.lower()} ({strategy_threshold * 100:.1f}%) + buffer 50bps "
            f"= {buffered_threshold * 100:.2f}% (EV insuficiente hasta después de fees)",
            inputs.confidence_in_conviction,
        )

    if inputs.had_survivorship_warning:
        return AbstentionDecision("NO_TRADE", "ticker con WARNING de posible deslistado (flag de yfinance)", inputs.confidence_in_conviction)

    if _is_contradictory(inputs):
        reason = (
            "modelo de factores sin datos suficientes para ajustarse"
            if not inputs.beta_available
            else f"Judge dividido: net_conviction={inputs.net_conviction:+.2f} pese a confidence={inputs.confidence_in_conviction:.0f}% (Bull y Bear se anulan)"
        )
        return AbstentionDecision("NO_TRADE", f"datos contradictorios entre fuentes (proxy): {reason}", inputs.confidence_in_conviction)

    if inputs.is_fda_crl_without_8k:
        return AbstentionDecision("NO_TRADE", "CRL de FDA sin 8-K correspondiente — aún no comunicado oficialmente por la empresa", inputs.confidence_in_conviction)

    illiquid_reason = _illiquid_reason(inputs)
    if illiquid_reason is not None:
        return AbstentionDecision("NO_TRADE", illiquid_reason, inputs.confidence_in_conviction)

    direction = "LONG" if inputs.net_conviction > 0 else "SHORT"
    return AbstentionDecision(direction, None, inputs.confidence_in_conviction)


def decide_all_strategies(inputs: AbstentionInputs) -> dict[str, AbstentionDecision]:
    """Ejecuta las 3 versiones. Es intencional que esto NO sea una lista de un
    solo trade_decision global: el mismo evento puede pasar el filtro de
    Aggressive y no el de Conservative (ARCHITECTURE_LEAN.md: cada versión de
    estrategia es un umbral distinto sobre la misma señal)."""
    return {strategy: decide_for_strategy(inputs, strategy) for strategy in STRATEGIES}


def as_json(decisions: dict[str, AbstentionDecision]) -> dict:
    """Forma del campo `abstention_decision` (JSONB) en event_analyses."""
    return {
        strategy: {
            "trade_decision": d.trade_decision,
            "reason_if_no_trade": d.reason_if_no_trade,
            "confidence": d.confidence,
        }
        for strategy, d in decisions.items()
    }
