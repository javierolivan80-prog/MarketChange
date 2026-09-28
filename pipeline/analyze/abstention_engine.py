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


def _illiquid_reason(high_low_range_pct: float | None, adv_usd_60d: float | None) -> str | None:
    """Proxy de liquidez con DOS componentes (ver nota 1 del docstring del
    módulo) — cualquiera de los dos basta, mismo patrón que
    _is_contradictory. Devuelve el motivo si es ilíquido, None si pasa
    ambos componentes.

    Recibe los dos floats directamente (no un AbstentionInputs completo) a
    propósito: ninguno de los dos depende del Judge, así que
    objective_no_trade_reason() también la llama, antes de que exista un
    AbstentionInputs completo (antes de invocar Bull/Bear/Judge siquiera) —
    ver su docstring."""
    if high_low_range_pct is None:
        return "sin datos de high/low para estimar liquidez (proxy de spread no disponible)"
    if high_low_range_pct > SPREAD_CEILING_PCT:
        return f"proxy de spread (high-low)/close={high_low_range_pct:.2f}% > {SPREAD_CEILING_PCT}% (ilíquido)"
    if adv_usd_60d is None:
        return "sin datos de volumen suficientes para estimar ADV (proxy de liquidez no disponible)"
    if adv_usd_60d < LIQUIDITY_ADV_FLOOR_USD:
        return f"ADV≈${adv_usd_60d:,.0f} < ${LIQUIDITY_ADV_FLOOR_USD:,.0f} (ilíquido, proxy de volumen en el momento del evento)"
    return None


def objective_no_trade_reason(
    novelty_score: float,
    had_survivorship_warning: bool,
    beta_available: bool,
    high_low_range_pct: float | None,
    adv_usd_60d: float | None,
    is_fda_crl_without_8k: bool,
) -> str | None:
    """Hallazgo de auditoría (IMPROVEMENT_PLAN.md R5): de las 7 reglas de
    decide_for_strategy(), 4 (novelty, survivorship, el componente (a) —
    beta_available— de "contradictorios", FDA CRL sin 8-K, e iliquidez) NO
    dependen en absoluto del Judge (Etapas 3-5) — son puro `enrichment` +
    una consulta a `events`. Si CUALQUIERA de ellas dispara, el resultado es
    NO_TRADE garantizado en las 3 versiones de estrategia SIN IMPORTAR qué
    diga el Judge, exactamente igual que ya se explotaba para `novelty` en
    event_analysis_pipeline.py — esta función generaliza ese pre-filtro a
    las otras 3, para no gastar una llamada a Bull/Bear/Judge en un evento
    cuyo NO_TRADE ya está decidido antes de preguntarle nada a la IA.

    Las reglas 2 (confidence) y 3 (EV) SIEMPRE necesitan el Judge — no se
    evalúan aquí, y su ausencia no invalida el resultado: basta con que UNA
    regla cualquiera garantice NO_TRADE (es un OR de 7 condiciones), así que
    demostrar que una de las 5 aquí evaluadas ya es cierta es suficiente sin
    tener que mirar las otras dos. El orden de estas 5 sigue el mismo orden
    relativo que decide_for_strategy() (1, 4, 5a, 6, 7) para que el motivo
    reportado sea el mismo que se habría visto si se hubiera llegado hasta
    ahí con el Judge real.

    Devuelve el motivo textual (mismo formato que reason_if_no_trade) o None
    si ninguna de estas 5 condiciones dispara — en ese caso SÍ hace falta
    invocar al Judge, porque las reglas 2/3/5b son las únicas que podrían
    decidir NO_TRADE."""
    if novelty_score < NOVELTY_FLOOR:
        return f"novelty_score={novelty_score:.0f} < {NOVELTY_FLOOR} (evento completamente descontado por el mercado)"
    if had_survivorship_warning:
        return "ticker con WARNING de posible deslistado (flag de yfinance)"
    if not beta_available:
        return "datos contradictorios entre fuentes (proxy): modelo de factores sin datos suficientes para ajustarse"
    if is_fda_crl_without_8k:
        return "CRL de FDA sin 8-K correspondiente — aún no comunicado oficialmente por la empresa"
    return _illiquid_reason(high_low_range_pct, adv_usd_60d)


def ev_ceiling_no_trade_reason(best_case_ev_by_strategy: dict[str, float]) -> str | None:
    """Hallazgo de auditoría (IMPROVEMENT_PLAN.md A1): además de las 5
    condiciones objetivas de arriba, la Etapa 6 (impact estimation) TAMPOCO
    depende del Judge (solo de event_class/d0_close_date/histórico de
    análogos) — así que, calculada de antemano, permite acotar el EV
    MÁXIMO POSIBLE de un evento sin conocer el veredicto real del Judge:
    basta con evaluar ev_engine.compute_ev con el mejor caso posible
    (net_conviction=+1.0, confidence_in_conviction=100.0), que el caller
    debe haber calculado y pasado aquí ya troceado por versión de estrategia.

    Por qué ese "mejor caso" es realmente el máximo: en
    ev_engine._raw_point_estimate, el punto central es
    `net_conviction * abs(expected_magnitude_pct)/100 * (confidence/100) *
    (impact_confidence/100)` — con expected_magnitude_pct e impact_confidence
    ya fijos (vienen de Etapa 6, no del Judge), esto es lineal en
    net_conviction y en confidence_in_conviction, ambos con coeficiente no
    negativo cuando net_conviction es positivo. El máximo sobre
    net_conviction∈[-1,1] y confidence∈[0,100] se alcanza entonces en
    net_conviction=+1, confidence=100 — CUALQUIER veredicto real del Judge
    (incluida la convicción más alcista y más segura posible) produce un EV
    igual o menor a este techo, para las 3 versiones simultáneamente (misma
    Etapa 6, multiplicadores de magnitud ya aplicados por versión dentro de
    compute_ev).

    Si ni siquiera ese techo cruza el umbral con buffer de NINGUNA versión,
    el resultado es NO_TRADE garantizado en las 3 estrategias sin importar
    qué diga el Judge de verdad — exactamente el mismo principio que
    objective_no_trade_reason, aplicado a la regla 3 (EV) en vez de a las
    reglas 1/4/5a/6/7.

    Devuelve el motivo textual o None si el techo SÍ cruza el umbral con
    buffer en alguna versión (en ese caso, y solo en ese, hace falta el
    Judge real para saber si de verdad lo cruza)."""
    failing = []
    for strategy, ev in best_case_ev_by_strategy.items():
        threshold = EV_THRESHOLDS[strategy] + EV_ABSTENTION_BUFFER
        if ev >= threshold:
            return None
        failing.append(f"{strategy.lower()}: techo={ev * 100:.2f}% < {threshold * 100:.2f}%")
    return (
        "EV máximo alcanzable (mejor caso posible del Judge: net_conviction=+1.0, "
        "confidence=100) por debajo del umbral+buffer en las 3 versiones (" + "; ".join(failing) +
        ") — ningún resultado real del Judge podría cambiar esto"
    )


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
    if ev < buffered_threshold:
        return AbstentionDecision(
            "NO_TRADE",
            f"ev={ev * 100:.2f}% < umbral {strategy.lower()} ({strategy_threshold * 100:.1f}%) + buffer 50bps "
            f"= {buffered_threshold * 100:.2f}% (EV negativo hasta después de fees)",
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

    illiquid_reason = _illiquid_reason(inputs.high_low_range_pct, inputs.adv_usd_60d)
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
