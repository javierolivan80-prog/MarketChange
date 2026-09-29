"""thesis_engine.py — memoria de tesis (hallazgo de auditoría): el sistema no
recordaba por qué emitió una alerta ayer, y la salida de una posición solo
dependía de TP/SL/tiempo. Este módulo decide qué hacer con una posición ya
abierta cuando aparece un evento NUEVO en el mismo ticker, o cuando toca su
revisión diaria — sin dejar que el LLM se dé la razón a sí mismo.

DISEÑO ANTI-SESGO DE CONFIRMACIÓN (decisión de producto, ver PR): la
reconciliación (Paso B del spec) es DETERMINISTA — pura Python sobre números
ya calculados — y NO una tercera llamada de IA. Dos motivos:

  1. El Judge ciego (Paso A) ya existe hoy tal cual, sin cambios: es
     event_analysis_pipeline.py, que nunca ha visto ni verá una tesis. Un
     Reconciler-LLM adicional no haría el sistema menos sesgado — el Paso A
     ya es ciego por construcción — solo añadiría una tercera fuente de
     coste y latencia sin reforzar la garantía anti-sesgo, que ya la da la
     separación de procesos, no un prompt.
  2. portfolio_simulator.py RECALCULA LA CARTERA COMPLETA DESDE CERO EN CADA
     CORRIDA NOCTURNA (ver el docstring de DRAWDOWN_CIRCUIT_BREAKER_PCT ahí).
     Si la reconciliación fuera una llamada de IA, cada noche se repetiría
     esa llamada para CADA evento con tesis abierta de los 5 años de
     historia, no solo para los eventos nuevos — un backtest que hoy es
     matemática pura pasaría a depender de miles de llamadas a la Batch API
     por corrida. Determinista mantiene el backtest a coste y tiempo cero,
     igual que hoy.

Punto de extensión documentado (no implementado aquí): una vez validado con
datos reales que la memoria aporta, se podría sustituir SOLO la redacción del
campo `rationale` por una llamada LLM que pula la prosa — nunca la `action`,
que seguiría decidida en código como manda el principio 2 del spec
("las condiciones objetivas se evalúan en código, no por el LLM"; "si las
condiciones objetivas dicen salir, gana el código aunque el LLM diga
mantener").

ORDEN DE PRIORIDAD DE reconcile() (código primero, siempre):
  1. FULFILLED   — el movimiento realizado ya alcanzó expected_move_pct.
  2. SATURATED   — el movimiento realizado supera un múltiplo del movimiento
                   típico histórico de la clase, y/o volumen anormal extremo.
  3. INVALIDATED (objetivo) — evento de clase contraria a la dirección de la
                   tesis, o ruptura de un nivel de precio fijado al entrar.
  4. INVALIDATED (juicio ciego con confianza ALTA que contradice la tesis).
  5. REDUCE (juicio ciego con confianza MODERADA que contradice la tesis).
  6. HOLD        — nada de lo anterior aplica.
Las 3 primeras (código puro) SIEMPRE ganan sobre lo que diga el juicio ciego
de las etapas 4-5 — nunca al revés (principio 2 del spec).

ACCIÓN 'ADD' (decisión de producto): en esta versión, `reconcile()` nunca
emite ADD — se trata como HOLD (ver acción C acordada). Sigue siendo un valor
válido en la columna `thesis_updates.action` por si se usa manualmente o en
una extensión futura.

CONDICIONES DE PRECIO (price_below/price_above en invalidation_conditions):
la lógica de comprobación está aquí, probada de forma aislada, pero en la
integración real con portfolio_simulator.py estos campos se dejan en None al
crear la tesis (ver default_invalidation_conditions). No es una limitación de
datos: STOP_LOSS se comprueba SIEMPRE primero (ver
portfolio_simulator.step_position_forward) usando el low/high intradía del
día, y para una posición LONG low<=close siempre — así que cualquier nivel de
precio de invalidación en la misma dirección que STOP_LOSS queda
matemáticamente subsumido por él: si el precio hubiera roto ese nivel, el
STOP_LOSS ya se habría disparado antes, con el low del mismo día. Documentado
aquí explícitamente para que quede claro que es una consecuencia del propio
modelo, no un descuido.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from pipeline import config
from pipeline.analyze.abstention_engine import CONFIDENCE_FLOOR, CONTRADICTION_CONFIDENCE_FLOOR


@dataclass
class ThesisSnapshot:
    thesis_id: int
    ticker: str
    direction: str  # 'LONG' / 'SHORT'
    entry_price: float
    rationale: str
    expected_move_pct: float  # magnitud positiva — de analyze.historical_analogues, nunca inventada por el LLM
    invalidation_conditions: dict
    created_at_date: date


@dataclass
class BlindJudgment:
    """Paso A — SIEMPRE el juicio ya calculado por event_analysis_pipeline.py
    para el evento NUEVO, sin ningún cambio: nunca ha visto la tesis."""
    event_id: int
    event_class: str
    net_conviction: float
    confidence_in_conviction: float


@dataclass
class ObjectiveMetrics:
    realized_move_pct: float  # gain_pct(direction, entry_price, precio_de_hoy) — con signo, a favor si es positivo
    current_price: float
    saturation_threshold_pct: float | None  # None si hay menos de THESIS_SATURATION_MIN_ANALOGUES análogos
    volume_ratio: float | None  # None si no hay suficiente historial de volumen


@dataclass
class ReconciliationResult:
    action: str  # 'HOLD' / 'REDUCE' / 'SELL' (nunca 'ADD' — ver docstring del módulo)
    reason_code: str
    rationale: str
    closes_thesis: bool
    thesis_status_if_closed: str | None  # 'fulfilled' / 'invalidated' / 'saturated', o None si no cierra
    reduce_fraction: float | None  # solo para REDUCE


def check_fulfilled(realized_move_pct: float, expected_move_pct: float) -> bool:
    """expected_move_pct<=0 es un caso degenerado (impact estimate sin
    análogos, ver historical_analogues.py) — sin nada positivo con lo que
    comparar, nunca se declara cumplida por esta regla (no confundir "no sé
    cuánto esperar" con "cualquier movimiento ya basta")."""
    if expected_move_pct <= 0:
        return False
    return realized_move_pct >= expected_move_pct


def compute_saturation_threshold_pct(
    analogue_abs_cars_pct: list[float],
    multiple: float = config.THESIS_SATURATION_MULTIPLE,
    percentile: float = config.THESIS_SATURATION_TYPICAL_MOVE_PERCENTILE,
    min_analogues: int = config.THESIS_SATURATION_MIN_ANALOGUES,
) -> float | None:
    """"Movimiento típico" de la clase = percentil (mediana por defecto) de
    |CAR| histórico de análogos ANTERIORES a la fecha de la tesis (el caller
    ya filtra eso, ver analyze.historical_analogues.get_historical_analogues).
    None si hay menos de `min_analogues` — el percentil sobre una muestra tan
    pequeña es ruido, no señal (mismo umbral y mismo razonamiento que
    historical_analogues.MIN_ANALOGUES_FOR_ANY_CONFIDENCE)."""
    if len(analogue_abs_cars_pct) < min_analogues:
        return None
    typical_move = float(np.percentile(analogue_abs_cars_pct, percentile))
    return multiple * typical_move


def compute_volume_ratio(
    prices: dict[date, dict],
    as_of: date,
    window_days: int,
    min_days: int,
) -> float | None:
    """Volumen de `as_of` (el día del evento nuevo — ya conocido en el
    momento de reconciliar, no es look-ahead: es "hoy" en la simulación, el
    mismo principio que ya usa step_position_forward con el high/low/close
    del propio día) frente a la media de los `window_days` días de
    negociación ANTERIORES. None si falta el volumen de hoy o hay menos de
    `min_days` válidos en la ventana anterior (muestra insuficiente)."""
    today_bar = prices.get(as_of)
    if today_bar is None or today_bar.get("volume") is None:
        return None
    valid_days = sorted(d for d in prices if d < as_of)[-window_days:]
    volumes = [float(prices[d]["volume"]) for d in valid_days if prices[d].get("volume") is not None]
    if len(volumes) < min_days:
        return None
    avg = sum(volumes) / len(volumes)
    if avg <= 0:
        return None
    return float(today_bar["volume"]) / avg


def check_saturated(realized_move_pct: float, saturation_threshold_pct: float | None, volume_ratio: float | None) -> bool:
    """Dos componentes independientes, cualquiera de los dos basta (mismo
    patrón que abstention_engine._illiquid_reason / _is_contradictory). El
    componente de precio solo aplica si el movimiento va A FAVOR de la tesis
    (realized_move_pct > 0) — un movimiento en contra ya es terreno de
    STOP_LOSS/INVALIDATED, no de saturación."""
    price_saturated = (
        realized_move_pct > 0
        and saturation_threshold_pct is not None
        and realized_move_pct >= saturation_threshold_pct
    )
    volume_saturated = volume_ratio is not None and volume_ratio >= config.THESIS_ABNORMAL_VOLUME_RATIO
    return price_saturated or volume_saturated


def check_invalidated_by_event_class(direction: str, new_event_class: str) -> bool:
    return new_event_class in config.THESIS_INVALIDATING_EVENT_CLASSES.get(direction, ())


def check_invalidated_by_price(direction: str, current_price: float, invalidation_conditions: dict) -> bool:
    """Genérica y probada de forma aislada — ver el docstring del módulo
    sobre por qué, en la integración real, price_below/price_above se dejan
    en None (subsumidos por STOP_LOSS)."""
    price_below = invalidation_conditions.get("price_below")
    price_above = invalidation_conditions.get("price_above")
    if price_below is not None and current_price <= price_below:
        return True
    if price_above is not None and current_price >= price_above:
        return True
    return False


def _direction_sign(direction: str) -> float:
    return 1.0 if direction == "LONG" else -1.0


def classify_blind_judgment_contradiction(direction: str, net_conviction: float, confidence_in_conviction: float) -> str | None:
    """None si no contradice. 'STRONG' o 'MILD' según el umbral de confianza
    — reutiliza abstention_engine.CONTRADICTION_CONFIDENCE_FLOOR /
    CONFIDENCE_FLOOR POR REFERENCIA (IMPROVEMENT_PLAN.md M3: antes eran
    literales independientes en config.py con el mismo valor por
    coincidencia, no por diseño — nada garantizaba que se mantuvieran
    sincronizados si uno de los dos cambiaba). Solo cuenta como
    contradicción si el signo de net_conviction es OPUESTO a la dirección de
    la tesis — un juicio ciego que confirma con menos fuerza no es una
    contradicción, es una tesis más débil pero no invalidada."""
    contradicts_direction = (_direction_sign(direction) * net_conviction) < 0
    if not contradicts_direction:
        return None
    if confidence_in_conviction >= CONTRADICTION_CONFIDENCE_FLOOR:
        return "STRONG"
    if confidence_in_conviction >= CONFIDENCE_FLOOR:
        return "MILD"
    return None


def reconcile(thesis: ThesisSnapshot, blind_judgment: BlindJudgment, metrics: ObjectiveMetrics) -> ReconciliationResult:
    """Paso B — ver el orden de prioridad en el docstring del módulo. Nunca
    llama a un LLM: blind_judgment ya viene calculado por el Paso A
    (event_analysis_pipeline.py, sin cambios)."""
    fecha = thesis.created_at_date.isoformat()

    if check_fulfilled(metrics.realized_move_pct, thesis.expected_move_pct):
        return ReconciliationResult(
            action="SELL",
            reason_code="FULFILLED",
            rationale=(
                f"La alerta del {fecha} fue por {thesis.rationale}; hoy el movimiento realizado "
                f"({metrics.realized_move_pct:+.2f}%) ya alcanzó el objetivo esperado "
                f"({thesis.expected_move_pct:.2f}%), por tanto VENDER (tesis cumplida)."
            ),
            closes_thesis=True,
            thesis_status_if_closed="fulfilled",
            reduce_fraction=None,
        )

    if check_saturated(metrics.realized_move_pct, metrics.saturation_threshold_pct, metrics.volume_ratio):
        price_triggered = (
            metrics.saturation_threshold_pct is not None
            and metrics.realized_move_pct > 0
            and metrics.realized_move_pct >= metrics.saturation_threshold_pct
        )
        detalle = (
            f"el movimiento realizado ({metrics.realized_move_pct:+.2f}%) supera "
            f"{config.THESIS_SATURATION_MULTIPLE:.0f}x el movimiento típico histórico de esta clase de evento "
            f"({metrics.saturation_threshold_pct / config.THESIS_SATURATION_MULTIPLE:.2f}%)"
            if price_triggered
            else f"el volumen de hoy es {metrics.volume_ratio:.1f}x su media reciente (anómalo)"
        )
        return ReconciliationResult(
            action="SELL",
            reason_code="SATURATED",
            rationale=(
                f"La alerta del {fecha} fue por {thesis.rationale}; hoy {detalle}, "
                f"por tanto VENDER (movimiento saturado, riesgo/beneficio ya degradado)."
            ),
            closes_thesis=True,
            thesis_status_if_closed="saturated",
            reduce_fraction=None,
        )

    if check_invalidated_by_event_class(thesis.direction, blind_judgment.event_class):
        return ReconciliationResult(
            action="SELL",
            reason_code="INVALIDATED_EVENT_CLASS",
            rationale=(
                f"La alerta del {fecha} fue por {thesis.rationale}; hoy un evento de clase "
                f"{blind_judgment.event_class} contradice objetivamente esa tesis, "
                f"por tanto VENDER (tesis invalidada)."
            ),
            closes_thesis=True,
            thesis_status_if_closed="invalidated",
            reduce_fraction=None,
        )

    if check_invalidated_by_price(thesis.direction, metrics.current_price, thesis.invalidation_conditions):
        return ReconciliationResult(
            action="SELL",
            reason_code="INVALIDATED_PRICE",
            rationale=(
                f"La alerta del {fecha} fue por {thesis.rationale}; hoy el precio rompió el nivel de "
                f"invalidación fijado al entrar, por tanto VENDER (tesis invalidada)."
            ),
            closes_thesis=True,
            thesis_status_if_closed="invalidated",
            reduce_fraction=None,
        )

    contradiction = classify_blind_judgment_contradiction(
        thesis.direction, blind_judgment.net_conviction, blind_judgment.confidence_in_conviction
    )
    if contradiction == "STRONG":
        return ReconciliationResult(
            action="SELL",
            reason_code="INVALIDATED_BLIND_JUDGMENT",
            rationale=(
                f"La alerta del {fecha} fue por {thesis.rationale}; hoy el juicio ciego sobre el evento "
                f"nuevo contradice esa tesis con alta confianza ({blind_judgment.confidence_in_conviction:.0f}%), "
                f"por tanto VENDER (tesis invalidada por nueva evidencia)."
            ),
            closes_thesis=True,
            thesis_status_if_closed="invalidated",
            reduce_fraction=None,
        )
    if contradiction == "MILD":
        return ReconciliationResult(
            action="REDUCE",
            reason_code="WEAK_CONTRADICTION",
            rationale=(
                f"La alerta del {fecha} fue por {thesis.rationale}; hoy el juicio ciego sobre el evento "
                f"nuevo contradice esa tesis con confianza moderada ({blind_judgment.confidence_in_conviction:.0f}%), "
                f"por tanto REDUCIR la posición sin liquidarla del todo."
            ),
            closes_thesis=False,
            thesis_status_if_closed=None,
            reduce_fraction=config.THESIS_REDUCE_FRACTION,
        )

    return ReconciliationResult(
        action="HOLD",
        reason_code="NO_CHANGE",
        rationale=(
            f"La alerta del {fecha} fue por {thesis.rationale}; el evento nuevo de hoy no cumple ninguna "
            f"condición objetiva de cierre ni contradice el juicio original, por tanto MANTENER."
        ),
        closes_thesis=False,
        thesis_status_if_closed=None,
        reduce_fraction=None,
    )


def build_thesis_rationale(direction: str, ticker: str, event_class: str, net_conviction: float, confidence_in_conviction: float) -> str:
    """Resumen corto y determinista del evento origen de la tesis — no el
    texto libre de Bull/Bear/Judge (extensión futura: citar
    event_analyses.bull_analyst_output/judge_output verbatim; no
    implementado aquí para no añadir una consulta extra al abrir cada
    posición sin necesidad real todavía)."""
    return f"{direction} en {ticker} por evento {event_class} (net_conviction={net_conviction:+.2f}, confidence={confidence_in_conviction:.0f}%)"


def default_invalidation_conditions(direction: str, expected_horizon_days: int) -> dict:
    """price_below/price_above en None a propósito — ver el docstring del
    módulo sobre por qué (subsumidos por STOP_LOSS)."""
    return {
        "price_below": None,
        "price_above": None,
        "opposite_event_classes": list(config.THESIS_INVALIDATING_EVENT_CLASSES.get(direction, ())),
        "max_holding_days": expected_horizon_days,
    }


def resolve_holding_deadline(strategy_target_date: date, thesis_expiry_date: date | None) -> tuple[date, str]:
    """(fecha_efectiva, motivo) para el "fin del holding period" — aplica el
    más estricto de los dos plazos (spec, ajuste acordado con el usuario).
    Empate -> MAX_HOLDING gana (es el comportamiento de siempre; el plazo de
    la tesis solo cambia algo cuando es ESTRICTAMENTE más corto)."""
    if thesis_expiry_date is not None and thesis_expiry_date < strategy_target_date:
        return thesis_expiry_date, "EXPIRED"
    return strategy_target_date, "MAX_HOLDING"


# Motivos de cierre de portfolio_trades.exit_reason que provienen de un cierre
# "normal" de la posición (TP/SL/trailing/tiempo/gap de datos) y no de la
# reconciliación de tesis — usado para poner theses.status='closed_by_stop'
# cuando una posición con tesis abierta se cierra por su propia mecánica
# ordinaria en vez de por una decisión de thesis_engine. Nombre tomado
# literalmente del spec (columna theses.status): cubre CUALQUIER cierre por
# la mecánica propia de la posición, no solo STOP_LOSS en sentido estricto.
ORDINARY_CLOSE_REASONS = frozenset({"STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "MAX_HOLDING", "DATA_GAP"})
THESIS_CLOSE_REASON_TO_STATUS = {
    "FULFILLED": "fulfilled",
    "INVALIDATED": "invalidated",
    "SATURATED": "saturated",
    "EXPIRED": "expired",
}


def thesis_status_for_exit_reason(exit_reason: str) -> str:
    if exit_reason in THESIS_CLOSE_REASON_TO_STATUS:
        return THESIS_CLOSE_REASON_TO_STATUS[exit_reason]
    assert exit_reason in ORDINARY_CLOSE_REASONS, f"exit_reason desconocido para mapear a theses.status: {exit_reason!r}"
    return "closed_by_stop"
