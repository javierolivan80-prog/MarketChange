"""portfolio_simulator.py — bucle diario de gestión de posiciones (spec:
"Backtesting loop"), sin look-ahead.

DISCIPLINA ANTI-LOOK-AHEAD (la misma que backtest_runs en la Fase 1, pero
aplicada día a día en vez de en una sola ventana fija):
  - T_decisión = cierre de D0 (evento ya público, ver events.d0_close_date).
  - T_entrada = APERTURA de D+1, nunca D0. entry_price viene de prices.open_raw
    del primer día de negociación estrictamente posterior a d0_close_date.
  - Cada día del bucle solo usa el high/low/close de ESE día — nunca se
    consulta un precio de una fecha posterior a la que se está evaluando.
  - target_date (fin de holding period) se cuenta en DÍAS DE NEGOCIACIÓN
    reales del propio ticker, no días de calendario.

ORDEN DE PRIORIDAD EN UN MISMO DÍA (decisión de diseño, no está en el spec):
si el low/high de un día cruza TANTO el stop-loss COMO el take-profit (gap
grande), se asume que el stop-loss se ejecutó primero — es la convención
estándar y conservadora en backtesting (nunca asumir el mejor caso posible
cuando el orden intradía real es desconocido). Los tramos de trailing stop
(Aggressive) pueden disparar varios el mismo día si hay un gap; se procesan
en orden de umbral ascendente.

CONSOLIDACIÓN: una posición con cierres parciales por trailing stop se
guarda como UNA fila en portfolio_trades (el spec pide un registro por
trade con un solo entry/exit) — exit_price y pnl son el promedio ponderado
de los tramos; exit_reason es 'TRAILING_STOP' si CUALQUIER tramo disparó,
aunque el resto se cerrara por max_holding (ver schema.sql).

COMISIONES: 10 bps por vuelta completa (entrada+salida), constante y
documentada — no es un barrido de sensibilidad como el slippage de la Fase 1
(ARCHITECTURE_LEAN.md T7); aquí es un único supuesto fijo para poder generar
la curva de equity. Ajustable en COMMISSION_BPS_ROUND_TRIP.

CIERRE FORZADO CON DATOS DE PRECIO INCOMPLETOS (bug encontrado en auditoría,
corregido aquí — ver _resolve_forced_close): el cierre forzado al final del
panel de precios (lo que siga abierto cuando se acaba master_calendar) asumía
que la fila más reciente de `prices` para ese ticker SIEMPRE tiene un
close_raw válido. Falso en el caso real que motiva survivorship_warning
(yfinance_backfill.py): un ticker que se deslista o entra en halt A MITAD de
una posición abierta deja como última fila un centinela con close_raw=NULL
(_flag_full_gap / _store_with_gap_detection). `float(None)` ahí revienta la
corrida entera — no un resultado sesgado, un crash. La corrección busca hacia
atrás la última fecha con precio válido; si no hay ninguna después de la
entrada (deslistado al día siguiente de entrar), cierra en la última fecha
disponible al precio de ENTRADA (retorno plano, mismo criterio conservador
que ya usa portfolio_mtm() para marcar a mercado un día sin dato — "no
inventar una ganancia ni una pérdida que no se puede observar"). Ambos casos
se etiquetan 'DATA_GAP', distinto de 'MAX_HOLDING', para que no se confundan
con un cierre normal por fin de holding period en los reportes.

COBERTURA CON SPY (Tanda 8; decisión del usuario, 2026-10-12): la regla y la
IA predicen el movimiento de la acción FRENTE AL MERCADO (CAR anormal de los
análogos), así que cada operación abre a la vez una pata contraria en SPY
con el mismo nocional: comprar la acción = vender SPY y al revés. Así lo que
se opera es lo que se predice. Detalles:
  - SPY entra a su apertura de la misma sesión que la acción (D+1). Sin
    apertura de SPY ese día la operación no se abre (no se inventa la pata).
  - Los niveles (stop, objetivo, tramos) se miden SOLO sobre la acción,
    dentro de la sesión, como siempre. SPY sale el mismo día que cada
    cierre: a su apertura si la acción salió a la apertura (hueco o cierre
    por tesis) y a su cierre en cualquier otro caso. Es una aproximación
    declarada: solo hay velas diarias de SPY, no su precio en el momento
    intradía en que la acción tocó el nivel.
  - Resultado = movimiento de la acción − movimiento de SPY (ambos en el
    sentido de la operación), menos comisión y deslizamiento de CADA pata
    (SPY paga el de gran capitalización: 10 pb por lado).
  - Capital: cada pata se paga con efectivo (2× nocional, sin margen ni
    apalancamiento); position_size_dollars es el nocional de UNA pata.
  - Sesgo conocido: precios sin ajustar, así que los dividendos de SPY no
    se cuentan (vender SPY los pagaría, ~0,03 % en 5 sesiones); tampoco el
    coste de préstamo de los cortos (BUGS_REPORT.md).
"""
from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date

from pipeline import config
from pipeline.config import SLIPPAGE_BPS_PER_SIDE_SMALL, slippage_bps_por_lado
from pipeline.analyze.historical_analogues import estimate_impact_for_event, get_historical_analogues
from pipeline.backtest import thesis_engine
from pipeline.backtest.portfolio_strategies import (
    BALANCED_MAX_CONCURRENT,
    STRATEGIES,
    classify_balanced_execution_style,
    compute_balanced_position_size_pct,
    compute_ev_weighted_position_size_pct,
    compute_position_size_pct,
)
from pipeline.backtest.sample_split import OOS_START, SAMPLE_IN_SAMPLE, date_bounds
from pipeline.ingest.hora_publicacion import HORA_CONOCIDA_SQL
from pipeline.ingest.splits import PRECIO_NEGOCIADO_SQL

logger = logging.getLogger(__name__)

COMMISSION_BPS_ROUND_TRIP = config.COMMISSION_BPS_ROUND_TRIP  # 0.10% por posición, ver docstring del módulo

# Capitalización en D0 para el deslizamiento (H-32): acciones del último 10-K
# PUBLICADO antes de D0 (fundamentals.filed_at, sin look-ahead) × precio
# NEGOCIADO en D0 (H-04, ingest/splits.py): close_raw viene reexpresado por
# los splits posteriores y las acciones XBRL son las de entonces.
#
# Si el ticker aún no tiene el historial de splits revisado no hay precio
# negociado: se usa close_raw y, para no favorecer el error (un contrasplit
# 1:10 posterior multiplica por 10 el precio de D0 y convertiría en "grande"
# a una microcap en apuros), se toma la MENOR de esa cifra y la capitalización
# actual (universe.market_cap_last_usd): el error que queda va hacia el
# deslizamiento alto (25 pb), el conservador. Sin cifra en D0 no se usa la de
# hoy: NULL, que también es 25 pb.
MARKET_CAP_D0_SQL = """
    (SELECT CASE WHEN a.acciones IS NULL OR d0.cierre IS NULL THEN NULL
                 -- Acciones del 10-K llevadas a D0 con los splits entre su
                 -- presentación y D0 (un contrasplit en medio las divide).
                 WHEN d0.negociado IS NOT NULL THEN a.acciones * d0.negociado * coalesce((
                     SELECT exp(sum(ln(s.ratio))) FROM splits s
                     WHERE s.ticker = e.ticker AND s.split_date > a.presentado AND s.split_date <= e.d0_close_date
                 ), 1)
                 ELSE LEAST(a.acciones * d0.cierre, u.market_cap_last_usd) END
       FROM (SELECT f.shares_outstanding AS acciones, f.filed_at AS presentado
               FROM (SELECT 1) uno
               LEFT JOIN LATERAL (
                   SELECT f.shares_outstanding, f.filed_at FROM fundamentals f
                    WHERE f.cik = e.cik AND f.form = '10-K' AND f.filed_at <= e.d0_close_date
                      AND f.shares_outstanding IS NOT NULL
                    ORDER BY f.filed_at DESC LIMIT 1
               ) f ON TRUE) a
       LEFT JOIN LATERAL (
           SELECT p.close_raw AS cierre, """ + PRECIO_NEGOCIADO_SQL + """ AS negociado
           FROM prices p
           WHERE p.ticker = e.ticker AND p.trade_date <= e.d0_close_date AND p.close_raw IS NOT NULL
           ORDER BY p.trade_date DESC LIMIT 1
       ) d0 ON TRUE
       LEFT JOIN universe u ON u.cik = e.cik)
"""

# Circuit-breaker de drawdown de cartera (hallazgo de la auditoría — protección
# de capital, prioridad máxima). Deliberadamente MÁS BAJO que
# portfolio_report.MAX_DRAWDOWN_CEILING (0.25, el umbral al que un run se
# declara "no recomendable" en el veredicto final): el propósito de un
# circuit-breaker es frenar ANTES de llegar al punto en que la propia
# estrategia se consideraría un fracaso, no coincidir con esa línea. Medido
# pico-a-valle sobre la equity de LA VERSIÓN (Conservative/Aggressive/
# Balanced/Dynamic se simulan por separado, cada una con su propio capital y
# su propia curva de equity — no existe hoy un concepto de "cartera conjunta"
# de las 4 versiones a la vez, así que el breaker es por versión, no global).
#
# Acción al saltar: SOLO bloquea entradas nuevas — las posiciones ya abiertas
# se siguen gestionando con sus reglas normales (TP/SL/trailing/max holding).
# Deliberado, no la opción más agresiva (forzar liquidación inmediata):
# liquidar de golpe exigiría vender al close del día que dispara el breaker,
# un precio que no se sabe si es ejecutable en la realidad (mismo principio
# de "no inventar un precio" que ya sigue _resolve_forced_close) y añade su
# propio riesgo de modelado. Bloquear entradas es el mecanismo más simple y
# más difícil de implementar mal — para lo que existe, cortar la sangría de
# CAPITAL NUEVO expuesto a una racha mala, es suficiente.
#
# Recuperación (BUGS_REPORT.md H-10; decisión del usuario, auditoría
# 2026-10-06). Antes era "automática": se reanudaba al volver por encima del
# umbral. Pero sin posiciones abiertas la equity no se mueve, así que tras
# el primer drawdown > 15 % el backtest dejaba de operar para siempre sin
# avisar. Ahora:
#   1. al saltar, PAUSA de CIRCUIT_BREAKER_PAUSE_SESSIONS sesiones sin
#      entradas nuevas (el día del disparo cuenta como la primera);
#   2. después se vuelve a operar a CIRCUIT_BREAKER_REDUCED_SIZE del tamaño
#      normal hasta marcar un NUEVO MÁXIMO de equity, momento en que vuelve
#      el tamaño completo;
#   3. mientras se opera a medio tamaño, un nuevo disparo se mide contra el
#      nivel de equity al reanudar (no contra el máximo, que dispararía al
#      instante): si se cae un 15 % por debajo de ese nivel, nueva pausa.
DRAWDOWN_CIRCUIT_BREAKER_PCT = 0.15  # 15%
CIRCUIT_BREAKER_PAUSE_SESSIONS = 20
CIRCUIT_BREAKER_REDUCED_SIZE = 0.5


def is_circuit_breaker_active(peak_equity: float, current_equity: float, threshold: float = DRAWDOWN_CIRCUIT_BREAKER_PCT) -> bool:
    """True si el drawdown actual (pico-a-valle, MISMA fórmula que
    portfolio_metrics.compute_equity_metrics: (peak-balance)/peak) alcanza o
    supera `threshold`. peak_equity<=0 es un estado degenerado (cartera ya
    liquidada por completo) — se trata como breaker activo, no como división
    por cero silenciosa."""
    if peak_equity <= 0:
        return True
    drawdown = (peak_equity - current_equity) / peak_equity
    return drawdown >= threshold


# Tope de posición por %ADV (hallazgo de auditoría — el backtest asumía que
# cualquier tamaño de posición que el sizing por confianza/EV pidiera era
# ejecutable, sin mirar si el ticker tenía volumen real para absorberlo).
# 5%, decidido con el usuario: estándar razonable en trading retail/small-fund
# para no mover el precio de forma apreciable al entrar/salir. Cuando el tope
# es MENOR que el tamaño pedido, se REDUCE la posición (nunca se descarta la
# señal — decisión explícita del usuario: un trade válido no se tira solo por
# liquidez, se dimensiona con más cautela).
MAX_POSITION_PCT_OF_ADV = 0.05  # 5%
ADV_TRAILING_WINDOW_DAYS = 60  # mismo horizonte que universe.adv_usd_60d (universe_maintenance.py)
ADV_MIN_TRADING_DAYS = 20  # por debajo de esto, la muestra es demasiado corta para fiarse (ver compute_trailing_adv_usd)


def compute_trailing_adv_usd(prices: dict[date, dict], as_of: date, window_days: int = ADV_TRAILING_WINDOW_DAYS) -> float | None:
    """ADV (volumen medio diario en $) de LOS `window_days` DÍAS DE
    NEGOCIACIÓN ANTERIORES a `as_of` (estrictamente < as_of — el volumen del
    propio día de entrada no se conoce hasta el cierre, y la entrada es a
    la APERTURA: usarlo sería look-ahead).

    Deliberadamente NO se usa universe.adv_usd_60d (calculado por
    universe_maintenance.py) para esto: esa columna se recalcula sobre los
    60 días MÁS RECIENTES respecto a HOY (el momento en que corre el
    refresh), no respecto a la fecha del evento — aplicarla a un trade
    histórico de hace años sería sizear una posición del pasado con la
    liquidez de HOY, un look-ahead sutil del mismo tipo que ya se corrigió
    en edgar_scraper.py (ACCEPTANCE-DATETIME). Aquí se calcula directamente
    sobre el panel de precios que el propio backtest ya tiene cargado
    (ticker_cache), con una ventana móvil anclada en `as_of` — el mismo
    principio anti-look-ahead que el resto del módulo.

    None si hay menos de ADV_MIN_TRADING_DAYS con volumen y precio válidos
    en la ventana — muestra demasiado corta para fiarse (no se inventa un
    ADV de 3 días). Filas con close_raw/volume nulos (gaps de
    survivorship_warning) se ignoran, no cuentan como día con datos."""
    valid_days = sorted(d for d in prices if d < as_of)[-window_days:]
    dollar_volumes = []
    for d in valid_days:
        bar = prices[d]
        close_raw, volume = bar.get("close_raw"), bar.get("volume")
        if close_raw is not None and volume is not None:
            dollar_volumes.append(float(close_raw) * float(volume))
    if len(dollar_volumes) < ADV_MIN_TRADING_DAYS:
        return None
    return sum(dollar_volumes) / len(dollar_volumes)


def cap_position_dollars_by_adv(desired_dollars: float, adv_usd: float | None, max_pct: float = MAX_POSITION_PCT_OF_ADV) -> tuple[float, bool]:
    """(tamaño_final, se_aplicó_el_tope). adv_usd=None (sin datos suficientes
    para estimarlo, ver compute_trailing_adv_usd) -> no se aplica ningún
    tope, se devuelve desired_dollars sin tocar: este mecanismo AÑADE una
    restricción de liquidez, no sustituye al filtro de liquidez de entrada
    (ese es otro punto de la auditoría, no este)."""
    if adv_usd is None:
        return desired_dollars, False
    cap = adv_usd * max_pct
    if desired_dollars > cap:
        return cap, True
    return desired_dollars, False


def gain_pct(direction: str, entry_price: float, price: float) -> float:
    """Retorno %, con signo, del SUBYACENTE — LONG/SHORT se calculan con la
    misma convención de signo en todo el módulo (no hay ningún otro sitio
    del proyecto que calcule retornos realizados de trades; el motor de
    backtest por-evento que existía en backtest/backtester.py se eliminó por
    no tener ningún caller en producción, ver IMPROVEMENT_PLAN.md Q1)."""
    if direction == "LONG":
        return (price - entry_price) / entry_price * 100
    return (entry_price - price) / entry_price * 100


# Cobertura con SPY (ver "COBERTURA CON SPY" en el docstring del módulo).
TICKER_COBERTURA = "SPY"
SLIPPAGE_BPS_COBERTURA_POR_LADO = config.SLIPPAGE_BPS_COBERTURA_POR_LADO
# Cierres de nivel que pueden ejecutarse a la apertura por un hueco
# (fill_con_gap); MAX_HOLDING/EXPIRED/DATA_GAP son siempre al cierre.
MOTIVOS_CON_HUECO = frozenset({"STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP"})


def direccion_contraria(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


class SerieCobertura:
    """Velas diarias de SPY para la pata de cobertura. Pura respecto a BD
    (recibe las filas ya cargadas, mismo formato que _load_ticker_prices)."""

    def __init__(self, prices: dict[date, dict]):
        self.prices = prices
        self._fechas_con_cierre = sorted(d for d, bar in prices.items() if bar.get("close_raw") is not None)

    def apertura(self, d: date) -> float | None:
        bar = self.prices.get(d)
        return float(bar["open_raw"]) if bar and bar.get("open_raw") is not None else None

    def cierre_del_dia(self, d: date) -> float | None:
        bar = self.prices.get(d)
        return float(bar["close_raw"]) if bar and bar.get("close_raw") is not None else None

    def cierre_hasta(self, d: date, desde: date, incluido: bool = True) -> float | None:
        """Último cierre conocido en [desde, d] ([desde, d) con
        incluido=False). Nunca uno anterior a `desde` (la entrada): sería un
        precio de antes de abrir la pata."""
        i = (bisect_right if incluido else bisect_left)(self._fechas_con_cierre, d)
        if i == 0 or self._fechas_con_cierre[i - 1] < desde:
            return None
        return float(self.prices[self._fechas_con_cierre[i - 1]]["close_raw"])

    def precio_de_salida(self, d: date, desde: date, a_la_apertura: bool) -> float | None:
        """A la apertura: la de `d`; sin ella, el último cierre ANTERIOR a
        `d` (el de `d` aún no se conoce a la apertura). Al cierre: el de `d`
        o, sin vela ese día, el último conocido desde la entrada."""
        if a_la_apertura:
            apertura = self.apertura(d)
            return apertura if apertura is not None else self.cierre_hasta(d, desde, incluido=False)
        return self.cierre_hasta(d, desde)


def cargar_serie_cobertura(conn, antes_de: date | None = None) -> SerieCobertura:
    """Velas de SPY sin las marcadas por calidad_precios.py (decisión del
    usuario: marcar y excluir): una vela dudosa cuenta como día sin vela.
    Con `antes_de`, solo las anteriores a esa fecha (In-Sample, H-37)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, open_raw, close_raw FROM prices "
            "WHERE ticker = %s AND calidad_motivo IS NULL AND (%s::date IS NULL OR trade_date < %s) "
            "ORDER BY trade_date",
            (TICKER_COBERTURA, antes_de, antes_de),
        )
        return SerieCobertura({r["trade_date"]: r for r in cur.fetchall()})


def cubrir_cierres_nuevos(
    position: "OpenPosition", n_antes: int, spy: SerieCobertura | None,
    open_accion: float | None = None, a_la_apertura: bool = False,
) -> None:
    """Pone precio de SPY a los cierres de la acción añadidos desde
    `n_antes` (position.spy_closes queda alineada con position.closes).
    SPY sale a su apertura si la acción salió a la apertura: todo el lote
    con a_la_apertura=True (cierres por tesis), o un nivel ejecutado por
    hueco (su precio es exactamente la apertura de la acción, fill_con_gap).
    Sin vela de SPY ese día, su último cierre desde la entrada; sin ninguno,
    el de entrada (pata plana: no se inventa un movimiento)."""
    if position.spy_entry_price is None or spy is None:
        return
    for d, _fraccion, precio, motivo in position.closes[n_antes:]:
        hueco = motivo in MOTIVOS_CON_HUECO and open_accion is not None and precio == open_accion
        p = spy.precio_de_salida(d, position.entry_date, a_la_apertura or hueco)
        position.spy_closes.append(p if p is not None else position.spy_entry_price)


def compute_tp_sl_prices(direction: str, entry_price: float, take_profit_pct: float | None, stop_loss_pct: float) -> tuple[float | None, float]:
    if direction == "LONG":
        tp = entry_price * (1 + take_profit_pct / 100) if take_profit_pct is not None else None
        sl = entry_price * (1 - stop_loss_pct / 100)
    else:
        tp = entry_price * (1 - take_profit_pct / 100) if take_profit_pct is not None else None
        sl = entry_price * (1 + stop_loss_pct / 100)
    return tp, sl


@dataclass
class OpenPosition:
    event_id: int
    version: str
    execution_style: str
    direction: str
    ticker: str
    entry_date: date
    entry_price: float
    position_size_pct: float
    position_size_dollars: float
    target_date: date
    take_profit_price: float | None
    stop_loss_price: float
    trailing_tiers: tuple[tuple[float, float], ...] | None
    confidence: float
    ev: float
    prediction: float
    had_survivorship_warning: bool
    had_adv_cap_applied: bool = False
    # Deslizamiento por lado en pb (H-32); por defecto el conservador.
    slippage_bps_por_lado: float = SLIPPAGE_BPS_PER_SIDE_SMALL
    remaining_fraction: float = 1.0
    tiers_hit: frozenset = field(default_factory=frozenset)
    used_trailing_stop: bool = False
    closes: list[tuple[date, float, float, str]] = field(default_factory=list)  # (fecha, fracción, precio, motivo)
    # Pata de SPY (cobertura): precio de entrada y un precio de salida por
    # cada elemento de `closes` (cubrir_cierres_nuevos). None = sin
    # cobertura: solo filas antiguas o pruebas de la mecánica de la acción;
    # el simulador y el paper trading siempre la abren.
    spy_entry_price: float | None = None
    spy_closes: list[float] = field(default_factory=list)

    # Memoria de tesis (config.THESIS_MEMORY_ENABLED) — todos None cuando la
    # posición no tiene tesis asociada (memoria desactivada, o versión que no
    # aplica). Ver thesis_engine.py para la lógica que los usa.
    thesis_id: int | None = None
    thesis_rationale: str | None = None
    thesis_expected_move_pct: float | None = None
    thesis_invalidation_conditions: dict | None = None
    thesis_created_at_date: date | None = None
    thesis_origin_event_class: str | None = None
    # Plazo propio de la tesis (más corto que target_date, ver
    # thesis_engine.resolve_holding_deadline) — None = sin plazo propio,
    # step_position_forward se comporta exactamente como antes.
    thesis_expiry_date: date | None = None


def fill_con_gap(direction: str, open_: float | None, nivel: float, *, a_favor: bool) -> float:
    """Precio de ejecución de una orden de nivel cuando la barra puede abrir
    ya más allá de él (BUGS_REPORT.md H-09). En eventos los gaps son la
    norma: si una acción LONG abre por debajo del stop, la venta se hace a la
    apertura, no al stop (la pérdida es mayor). Simétrico para lo que va a
    favor: un LONG que abre por encima del objetivo se cierra a la apertura
    (la ganancia es mayor). Sin apertura conocida, el nivel (como antes)."""
    if open_ is None:
        return nivel
    sube = (direction == "LONG") == a_favor  # ¿la orden salta si el precio sube?
    return max(open_, nivel) if sube else min(open_, nivel)


def step_position_forward(
    position: OpenPosition, high: float, low: float, close: float, trade_date: date, open_: float | None = None
) -> OpenPosition:
    """Aplica un día de precios a una posición abierta, mutando su estado
    (remaining_fraction, tiers_hit, closes) y devolviéndola. Pura respecto a
    I/O — no toca la base de datos.

    open_: apertura del día. Con ella, los niveles que la barra ya ha
    rebasado al abrir se ejecutan a la apertura (gap), ver fill_con_gap."""
    if position.remaining_fraction <= 1e-9:
        return position  # ya cerrada del todo, nada que hacer (defensivo)

    # 1) Stop-loss — máxima prioridad, ver docstring del módulo.
    sl_triggered = (low <= position.stop_loss_price) if position.direction == "LONG" else (high >= position.stop_loss_price)
    if sl_triggered:
        fill = fill_con_gap(position.direction, open_, position.stop_loss_price, a_favor=False)
        position.closes.append((trade_date, position.remaining_fraction, fill, "STOP_LOSS"))
        position.remaining_fraction = 0.0
        return position

    # 2) Take-profit fijo (Conservative; None para Aggressive, que usa trailing).
    if position.take_profit_price is not None:
        tp_triggered = (high >= position.take_profit_price) if position.direction == "LONG" else (low <= position.take_profit_price)
        if tp_triggered:
            fill = fill_con_gap(position.direction, open_, position.take_profit_price, a_favor=True)
            position.closes.append((trade_date, position.remaining_fraction, fill, "TAKE_PROFIT"))
            position.remaining_fraction = 0.0
            return position

    # 3) Trailing stop tiers (Aggressive) — pueden dispararse varios el mismo día.
    if position.trailing_tiers:
        best_price_today = high if position.direction == "LONG" else low
        today_gain = gain_pct(position.direction, position.entry_price, best_price_today)
        for threshold, fraction in position.trailing_tiers:
            if threshold in position.tiers_hit:
                continue
            if today_gain >= threshold:
                tier_price = (
                    position.entry_price * (1 + threshold / 100)
                    if position.direction == "LONG"
                    else position.entry_price * (1 - threshold / 100)
                )
                actual_fraction = min(fraction, position.remaining_fraction)
                tier_fill = fill_con_gap(position.direction, open_, tier_price, a_favor=True)
                position.closes.append((trade_date, actual_fraction, tier_fill, "TRAILING_STOP"))
                position.remaining_fraction -= actual_fraction
                position.tiers_hit = position.tiers_hit | {threshold}
                position.used_trailing_stop = True
                if position.remaining_fraction <= 1e-9:
                    return position

    # 4) Fin del holding period — cierra lo que quede a mercado. Aplica el
    # plazo MÁS ESTRICTO entre el de la propia estrategia (target_date) y el
    # de la tesis asociada, si la hay (thesis_expiry_date) — ver
    # thesis_engine.resolve_holding_deadline. Con thesis_expiry_date=None
    # (memoria desactivada, o versión sin tesis) el comportamiento es
    # exactamente el de antes, byte a byte.
    if position.remaining_fraction > 1e-9:
        effective_target_date, holding_reason = thesis_engine.resolve_holding_deadline(
            position.target_date, position.thesis_expiry_date
        )
        if trade_date >= effective_target_date:
            position.closes.append((trade_date, position.remaining_fraction, close, holding_reason))
            position.remaining_fraction = 0.0

    return position


def compute_position_mtm_dollars(position: OpenPosition, current_price: float, spy_price: float | None = None) -> float:
    """Valor a mercado de una posición, en $, incluyendo el caso de cierres
    parciales ya realizados (trailing stop a mitad de camino): cada tramo ya
    cerrado se valora a SU PROPIO precio de cierre (ganancia ya asegurada, no
    sujeta al precio de hoy); lo que queda abierto se valora al precio de
    hoy. Sin esto, la curva de equity durante los días entre tramos de un
    trailing stop trataría el 100% del tamaño original como expuesto al
    precio actual, sobreestimando el riesgo de lo que ya se aseguró.

    Con cobertura suma también la pata de SPY (sentido contrario, mismo
    nocional): `spy_price` es su cierre de hoy; sin él, su precio de entrada."""
    size = position.position_size_dollars
    cubierta = position.spy_entry_price is not None
    contraria = direccion_contraria(position.direction)
    realized = 0.0
    for i, (_, fraction, price, _) in enumerate(position.closes):
        move = gain_pct(position.direction, position.entry_price, price)
        realized += size * fraction * (1 + move / 100)
        if cubierta:
            realized += size * fraction * (1 + gain_pct(contraria, position.spy_entry_price, position.spy_closes[i]) / 100)
    if position.remaining_fraction > 1e-9:
        move = gain_pct(position.direction, position.entry_price, current_price)
        realized += size * position.remaining_fraction * (1 + move / 100)
        if cubierta:
            spy_hoy = spy_price if spy_price is not None else position.spy_entry_price
            realized += size * position.remaining_fraction * (1 + gain_pct(contraria, position.spy_entry_price, spy_hoy) / 100)
    return realized


def consolidate_trade_record(position: OpenPosition) -> dict:
    """Colapsa una posición TOTALMENTE cerrada (remaining_fraction==0) en el
    registro único que pide el spec — ver docstring del módulo sobre la
    consolidación de cierres parciales."""
    assert position.remaining_fraction <= 1e-9, "consolidate_trade_record llamado sobre una posición aún abierta"
    assert position.closes, "una posición cerrada debe tener al menos un cierre registrado"

    total_fraction = sum(f for _, f, _, _ in position.closes)
    weighted_exit_price = sum(f * p for _, f, p, _ in position.closes) / total_fraction
    final_exit_date = position.closes[-1][0]
    last_reason = position.closes[-1][3]
    if position.used_trailing_stop:
        exit_reason = "TRAILING_STOP"
    elif last_reason == "THESIS_REDUCE":
        # Caso límite (memoria de tesis, ver thesis_engine.py): la posición se
        # agotó del todo por reducciones sucesivas (WEAK_CONTRADICTION) sin
        # que ningún cierre "normal" interviniera antes. En la práctica casi
        # nunca ocurre (THESIS_REDUCE_FRACTION=0.5 necesita muchas
        # reconciliaciones seguidas para agotar remaining_fraction), pero si
        # pasa, la etiqueta más honesta dentro del vocabulario aprobado de
        # exit_reason (schema.sql) es INVALIDATED: la tesis se erosionó hasta
        # quedar en nada por evidencia sucesiva en contra. "THESIS_REDUCE" en
        # sí mismo NUNCA se escribe en portfolio_trades.
        exit_reason = "INVALIDATED"
    else:
        exit_reason = last_reason

    # La salida puede ser la MISMA sesión de entrada (H-31: se entra a la
    # apertura y el stop u objetivo se toca después), nunca antes.
    assert final_exit_date >= position.entry_date, "VIOLACIÓN ANTI-LOOK-AHEAD: exit_date anterior a entry_date"

    stock_move_pct = gain_pct(position.direction, position.entry_price, weighted_exit_price)
    commission_pct = COMMISSION_BPS_ROUND_TRIP / 100
    # Deslizamiento (H-32): un lado al entrar y otro al salir, restado del P&L
    # igual que la comisión; los niveles de TP/SL no se mueven.
    slippage_pct = 2 * position.slippage_bps_por_lado / 100
    spy_exit_price = spy_move_pct = None
    if position.spy_entry_price is not None:
        # Cobertura (docstring del módulo): el movimiento es el de la acción
        # frente a SPY, y la pata de SPY paga su propia comisión y su
        # deslizamiento. Todo en % del nocional de una pata.
        assert len(position.spy_closes) == len(position.closes), "cada cierre de la acción necesita su precio de SPY"
        spy_exit_price = sum(f * p for (_, f, _, _), p in zip(position.closes, position.spy_closes)) / total_fraction
        spy_move_pct = gain_pct(position.direction, position.spy_entry_price, spy_exit_price)
        actual_move_pct = stock_move_pct - spy_move_pct
        commission_pct *= 2
        slippage_pct += 2 * SLIPPAGE_BPS_COBERTURA_POR_LADO / 100
    else:
        actual_move_pct = stock_move_pct
    pnl_pct = actual_move_pct - commission_pct - slippage_pct
    pnl_abs = position.position_size_dollars * (pnl_pct / 100)

    return {
        "event_id": position.event_id,
        "version": position.version,
        "execution_style": position.execution_style,
        "direction": position.direction,
        "entry_date": position.entry_date,
        "entry_price": position.entry_price,
        "exit_date": final_exit_date,
        "exit_price": weighted_exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_pct,
        "pnl_abs": pnl_abs,
        "position_size_pct": position.position_size_pct,
        "position_size_dollars": position.position_size_dollars,
        "confidence": position.confidence,
        "ev": position.ev,
        "prediction": position.prediction,
        "actual_move_pct": actual_move_pct,
        "spy_entry_price": position.spy_entry_price,
        "spy_exit_price": spy_exit_price,
        "spy_move_pct": spy_move_pct,
        "had_survivorship_warning": position.had_survivorship_warning,
        "had_adv_cap_applied": position.had_adv_cap_applied,
        "thesis_id": position.thesis_id,
    }


def open_position(
    event_id: int,
    version: str,
    execution_style: str,
    direction: str,
    ticker: str,
    entry_date: date,
    entry_price: float,
    target_date: date,
    balance_for_sizing: float,
    confidence: float,
    ev: float,
    prediction: float,
    had_survivorship_warning: bool,
    adv_usd_60d: float | None = None,
    slippage_bps_por_lado: float = SLIPPAGE_BPS_PER_SIDE_SMALL,
    size_multiplier: float = 1.0,
    spy_entry_price: float | None = None,
) -> OpenPosition:
    """Construye una OpenPosition con el sizing y los umbrales TP/SL/trailing
    de execution_style (STRATEGIES[execution_style] — para BALANCED, ver
    compute_balanced_position_size_pct; para DYNAMIC, ver
    compute_ev_weighted_position_size_pct; ambas en vez de
    compute_position_size_pct).

    `adv_usd_60d`: ADV histórico as-of la entrada (ver
    compute_trailing_adv_usd — el caller lo calcula, esta función no toca la
    BD). Si el tamaño pedido por confianza/EV supera MAX_POSITION_PCT_OF_ADV
    de esa cifra, se reduce (nunca se descarta el trade) — ver
    cap_position_dollars_by_adv. None (sin datos suficientes) = sin tope."""
    config = STRATEGIES[execution_style]
    if version == "DYNAMIC":
        size_pct = compute_ev_weighted_position_size_pct(ev, confidence, execution_style)
    elif version == "BALANCED":
        size_pct = compute_balanced_position_size_pct(execution_style)
    else:
        size_pct = compute_position_size_pct(confidence, config)
    # size_multiplier: 0,5 mientras el freno de pérdidas tiene la cartera a
    # medio tamaño tras una pausa (H-10); 1 el resto del tiempo.
    size_dollars = balance_for_sizing * (size_pct / 100) * size_multiplier
    size_dollars, adv_cap_applied = cap_position_dollars_by_adv(size_dollars, adv_usd_60d)
    if balance_for_sizing > 0:
        size_pct = size_dollars / balance_for_sizing * 100  # refleja el tamaño REAL, no el pre-tope
    tp_price, sl_price = compute_tp_sl_prices(direction, entry_price, config.take_profit_pct, config.stop_loss_pct)

    return OpenPosition(
        event_id=event_id,
        version=version,
        execution_style=execution_style,
        direction=direction,
        ticker=ticker,
        entry_date=entry_date,
        entry_price=entry_price,
        position_size_pct=size_pct,
        position_size_dollars=size_dollars,
        target_date=target_date,
        take_profit_price=tp_price,
        stop_loss_price=sl_price,
        trailing_tiers=config.trailing_stop_tiers,
        confidence=confidence,
        ev=ev,
        prediction=prediction,
        had_survivorship_warning=had_survivorship_warning,
        had_adv_cap_applied=adv_cap_applied,
        slippage_bps_por_lado=slippage_bps_por_lado,
        spy_entry_price=spy_entry_price,
    )


def compute_target_date(entry_date: date, holding_period_max_days: int, ticker_trading_dates: list[date]) -> date:
    """El Nº-ésimo día de negociación posterior a entry_date, usando el
    calendario REAL del propio ticker (no días de calendario — un holding de
    "5 días" cruzando un fin de semana no debe encogerse). Si el ticker no
    tiene suficientes días futuros en los datos (fin del panel de precios),
    se usa el último día disponible — el caller debe forzar el cierre ahí."""
    future = sorted(d for d in ticker_trading_dates if d > entry_date)
    if len(future) >= holding_period_max_days:
        return future[holding_period_max_days - 1]
    return future[-1] if future else entry_date


# ============================================================================
# Orquestación contra Postgres — fetch de eventos, bucle día a día, escritura.
# ============================================================================

VERSIONS = ("CONSERVATIVE", "AGGRESSIVE", "BALANCED", "DYNAMIC")

# DYNAMIC no tiene su propia columna trade_decision_dynamic (no hay 4ª
# columna en event_analyses, y no hace falta una — ver nota 5 del docstring
# de portfolio_strategies.py): reutiliza trade_decision_balanced, el mismo
# criterio de SI operar que Balanced. Solo cambia el sizing.
_TRADE_DECISION_SOURCE_VERSION = {"DYNAMIC": "BALANCED"}

# Precio mínimo en D0 (config.MIN_PRICE_USD, ARCHITECTURE_LEAN.md §10) con el
# precio NEGOCIADO de entonces (H-04; decisión del usuario, 2026-10-06), no
# el de hoy, que es el que usa el universo. Sin historial de splits revisado
# se usa close_raw (lo único disponible) hasta que llegue.
PRECIO_D0_MINIMO_SQL = """
    (SELECT coalesce(""" + PRECIO_NEGOCIADO_SQL + """, p.close_raw)
       FROM prices p
      WHERE p.ticker = e.ticker AND p.trade_date <= e.d0_close_date AND p.close_raw IS NOT NULL
      ORDER BY p.trade_date DESC LIMIT 1) >= """ + str(float(config.MIN_PRICE_USD)) + """
"""

# Calidad de precios (calidad_precios.py; decisión del usuario, 2026-10-06):
# una operación cuya ventana toca una vela marcada se excluye. La ventana va
# desde los días previos a D0 (la base de la decisión) hasta el máximo de
# días de tenencia (20 sesiones), con margen en días naturales.
DIAS_ANTES_DE_D0 = 5
DIAS_VENTANA_OPERACION = 45
VELA_MARCADA_EN_LA_OPERACION_SQL = f"""
    SELECT 1 FROM prices pq
    WHERE pq.ticker = e.ticker AND pq.calidad_motivo IS NOT NULL
      AND pq.trade_date BETWEEN e.d0_close_date - {DIAS_ANTES_DE_D0} AND e.d0_close_date + {DIAS_VENTANA_OPERACION}
"""


def fetch_events_for_version(
    conn, version: str, sample: str | None = None, *, con_hora_desconocida: bool = False
) -> list[dict]:
    """Eventos con trade_decision != NO_TRADE para `version`. Trae SIEMPRE
    los 3 ev_* (no solo el de la versión) porque BALANCED y DYNAMIC necesitan
    ev_conservative Y ev_aggressive para decidir el estilo de ejecución
    (classify_balanced_execution_style) — pedirlos todos es más simple que
    dos queries distintas según la versión.

    `sample`: None (default) = sin filtro, todo el rango — el comportamiento
    de siempre, para no romper ninguna llamada existente. 'in_sample' /
    'oos' acotan por d0_close_date según pipeline/backtest/sample_split.py
    (que a su vez lee config.IN_SAMPLE_END / OOS_START).

    La decisión es la de la REGLA HISTÓRICA sin IA (decision_sin_ia ->
    regla_historica: signo de los análogos y confianza fija), no la de la IA
    (BUGS_REPORT.md H-06): casi todo el histórico es anterior al corte de
    entrenamiento de los modelos, que pudieron haber leído qué pasó después.
    La IA se valida aparte, solo con eventos posteriores al corte (paper
    trading). Un análisis sin regla histórica todavía (pendiente del relleno
    de backfill_decision_sin_ia) no entra."""
    assert version in VERSIONS, f"versión desconocida: {version}"
    from pipeline.analyze.event_analysis_pipeline import METODO_SIN_IA

    estrategia = _TRADE_DECISION_SOURCE_VERSION.get(version, version)
    ev_col = f"ev_{estrategia.lower()}"
    start, end = date_bounds(sample)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT * FROM (
                SELECT DISTINCT ON (e.ticker, e.d0_close_date)
                       e.event_id, e.ticker, e.d0_close_date, e.event_class,
                       rh.decision AS trade_decision,
                       rh.ev_conservative, rh.ev_aggressive, rh.ev_balanced,
                       rh.confidence, rh.prediction,
                       {MARKET_CAP_D0_SQL} AS market_cap_d0
                FROM events e
                JOIN event_analyses ea ON ea.event_id = e.event_id
                CROSS JOIN LATERAL (
                    SELECT r->'decisiones'->%(estrategia)s->>'trade_decision' AS decision,
                           (r->>'ev_conservative')::float AS ev_conservative,
                           (r->>'ev_aggressive')::float AS ev_aggressive,
                           (r->>'ev_balanced')::float AS ev_balanced,
                           (r->>'confidence_in_conviction')::float AS confidence,
                           (r->>'net_conviction')::float AS prediction
                    FROM (SELECT ea.decision_sin_ia->'regla_historica' AS r) x
                ) rh
                WHERE rh.decision != 'NO_TRADE'
                  -- Solo la regla del método vigente: mientras el relleno
                  -- recalcula la de un método anterior, esa fila no entra
                  -- (el informe lo dice en cobertura_regla), no se mezcla.
                  AND ea.decision_sin_ia->>'metodo' = %(metodo)s
                  AND NOT EXISTS ({VELA_MARCADA_EN_LA_OPERACION_SQL})
                  AND {PRECIO_D0_MINIMO_SQL}
                  -- Sin hora de publicación, D0 es dudoso (H-17, hora_publicacion.py).
                  -- con_hora_desconocida=True solo sirve para contar cuántas se pierden.
                  AND ({HORA_CONOCIDA_SQL} OR %(con_hora_desconocida)s)
                  AND (%(start)s::date IS NULL OR e.d0_close_date >= %(start)s)
                  AND (%(end)s::date IS NULL OR e.d0_close_date <= %(end)s)
                ORDER BY e.ticker, e.d0_close_date, abs(rh.{ev_col}) DESC, e.event_id
            ) unicos
            ORDER BY d0_close_date, abs({ev_col}) DESC, event_id
            """,
            {"start": start, "end": end, "estrategia": estrategia, "metodo": METODO_SIN_IA,
             "con_hora_desconocida": con_hora_desconocida},
        )
        return cur.fetchall()


def _load_ticker_prices(conn, ticker: str, antes_de: date | None = None) -> dict[date, dict]:
    """Velas del ticker; con `antes_de`, solo las anteriores a esa fecha (el
    In-Sample no ve precios del OOS, H-37)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, open_raw, high_raw, low_raw, close_raw, volume, survivorship_warning "
            "FROM prices WHERE ticker = %s AND (%s::date IS NULL OR trade_date < %s) ORDER BY trade_date",
            (ticker, antes_de, antes_de),
        )
        return {r["trade_date"]: r for r in cur.fetchall()}


def _load_ticker_new_events(conn, ticker: str, prices: dict[date, dict]) -> dict[date, list[dict]]:
    """TODOS los eventos de este ticker (cualquier trade_decision, incluso
    NO_TRADE para la versión que se está simulando) indexados por su fecha de
    entrada D+1 (mismo criterio de _build_entry_plan) — necesario para la
    memoria de tesis (config.THESIS_MEMORY_ENABLED): un evento nuevo puede
    contradecir una tesis abierta aunque por sí mismo no hubiera generado una
    entrada nueva en esta versión (el Judge lo vio, simplemente no llegó al
    umbral de EV/confianza de ESTA estrategia). Solo se llama cuando la
    memoria está activada — coste de una query extra por ticker, cero cuando
    está desactivada (comportamiento y coste de siempre)."""
    from pipeline.analyze.event_analysis_pipeline import METODO_SIN_IA

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.event_class, e.d0_close_date,
                   (ea.decision_sin_ia->'regla_historica'->>'net_conviction')::float AS net_conviction,
                   (ea.decision_sin_ia->'regla_historica'->>'confidence_in_conviction')::float AS confidence_in_conviction
            FROM events e
            JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE e.ticker = %s
              -- La misma regla sin IA que decide las entradas (H-06).
              AND ea.decision_sin_ia->'regla_historica' IS NOT NULL
              AND ea.decision_sin_ia->>'metodo' = %s
            ORDER BY e.d0_close_date
            """,
            (ticker, METODO_SIN_IA),
        )
        rows = cur.fetchall()
    by_entry_date: dict[date, list[dict]] = {}
    for row in rows:
        future_dates = sorted(d for d in prices if d > row["d0_close_date"])
        if not future_dates:
            continue
        by_entry_date.setdefault(future_dates[0], []).append(row)
    return by_entry_date


def _compute_saturation_threshold_for_event_class(conn, event_class: str, as_of_date: date, exclude_event_id: int) -> float | None:
    """"Movimiento típico" de la clase de evento QUE ORIGINÓ la tesis,
    point-in-time respecto al día de la reconciliación (as_of_date), no
    respecto al día en que se creó la tesis — más análogos se acumulan
    legítimamente cuanto más tiempo lleva abierta, sin ningún look-ahead
    (analyze.historical_analogues.get_historical_analogues ya garantiza
    d0_close_date < as_of_date). window_days=20, la misma ventana de CAR que
    usa el resto de la Etapa 6 para esta clase de evento."""
    analogues = get_historical_analogues(conn, event_class, as_of_date, exclude_event_id, window_days=20)
    abs_cars_pct = [abs(float(a["car"])) * 100 for a in analogues]
    return thesis_engine.compute_saturation_threshold_pct(abs_cars_pct)


def _store_new_thesis(
    conn, ticker: str, event_id: int, version: str, execution_style: str, direction: str,
    created_at_date: date, entry_price: float, rationale: str, expected_move_pct: float,
    expected_horizon_days: int, invalidation_conditions: dict, run_batch_tag: str,
) -> int:
    import json as _json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO theses (
                ticker, event_id, version, execution_style, direction, created_at_date, entry_price,
                rationale, expected_move_pct, expected_horizon_days, invalidation_conditions, status, run_batch_tag
            ) VALUES (
                %(ticker)s, %(event_id)s, %(version)s, %(execution_style)s, %(direction)s, %(created_at_date)s,
                %(entry_price)s, %(rationale)s, %(expected_move_pct)s, %(expected_horizon_days)s,
                %(invalidation_conditions)s, 'open', %(run_batch_tag)s
            )
            -- Igual que portfolio_trades: reejecutar simulate_portfolio() con
            -- el MISMO run_batch_tag actualiza la tesis existente en vez de
            -- duplicarla (el backtest recalcula la cartera completa desde
            -- cero en cada corrida).
            ON CONFLICT (event_id, version, run_batch_tag) DO UPDATE SET
                status = 'open', closed_at_date = NULL, close_reason_code = NULL, close_rationale = NULL,
                entry_price = EXCLUDED.entry_price, rationale = EXCLUDED.rationale,
                expected_move_pct = EXCLUDED.expected_move_pct, expected_horizon_days = EXCLUDED.expected_horizon_days,
                invalidation_conditions = EXCLUDED.invalidation_conditions, created_at_date = EXCLUDED.created_at_date
            RETURNING thesis_id
            """,
            {
                "ticker": ticker, "event_id": event_id, "version": version, "execution_style": execution_style,
                "direction": direction, "created_at_date": created_at_date, "entry_price": entry_price,
                "rationale": rationale, "expected_move_pct": expected_move_pct,
                "expected_horizon_days": expected_horizon_days,
                "invalidation_conditions": _json.dumps(invalidation_conditions), "run_batch_tag": run_batch_tag,
            },
        )
        thesis_id = cur.fetchone()["thesis_id"]
    conn.commit()
    return thesis_id


def _store_thesis_update(
    conn, thesis_id: int, as_of_date: date, blind_judgment_event_id: int,
    result: "thesis_engine.ReconciliationResult", metrics_snapshot: dict, run_batch_tag: str,
) -> None:
    import json as _json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO thesis_updates (
                thesis_id, as_of_date, trigger, blind_judgment_event_id, action, reason_code, rationale,
                metrics_snapshot, run_batch_tag
            ) VALUES (
                %(thesis_id)s, %(as_of_date)s, 'new_event', %(blind_judgment_event_id)s, %(action)s,
                %(reason_code)s, %(rationale)s, %(metrics_snapshot)s, %(run_batch_tag)s
            )
            ON CONFLICT (thesis_id, as_of_date, blind_judgment_event_id, run_batch_tag) DO UPDATE SET
                action = EXCLUDED.action, reason_code = EXCLUDED.reason_code, rationale = EXCLUDED.rationale,
                metrics_snapshot = EXCLUDED.metrics_snapshot
            """,
            {
                "thesis_id": thesis_id, "as_of_date": as_of_date, "blind_judgment_event_id": blind_judgment_event_id,
                "action": result.action, "reason_code": result.reason_code, "rationale": result.rationale,
                "metrics_snapshot": _json.dumps(metrics_snapshot), "run_batch_tag": run_batch_tag,
            },
        )
    conn.commit()


def _update_thesis_status(conn, thesis_id: int, status: str, closed_at_date: date, close_reason_code: str, close_rationale: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE theses SET status=%s, closed_at_date=%s, close_reason_code=%s, close_rationale=%s WHERE thesis_id=%s",
            (status, closed_at_date, close_reason_code, close_rationale, thesis_id),
        )
    conn.commit()


def _maybe_close_thesis(
    conn, position: OpenPosition, record: dict, close_rationale: str | None = None, close_reason_code: str | None = None,
) -> None:
    """Tras CUALQUIER consolidate_trade_record() de una posición con tesis
    asociada (cierre normal por TP/SL/trailing/tiempo, cierre por
    thesis_engine, o cierre forzado de fin de datos) — actualiza
    theses.status con el mapeo de thesis_engine.thesis_status_for_exit_reason.
    Un solo sitio que conoce ese mapeo, llamado desde los 3 puntos de
    consolidación del módulo. `close_rationale`/`close_reason_code`: los de
    ReconciliationResult cuando el cierre vino de thesis_engine (más
    específico que record['exit_reason'] — p.ej. "INVALIDATED_EVENT_CLASS"
    en vez del "INVALIDATED" genérico que sí exige el CHECK de
    portfolio_trades); una plantilla genérica y el propio exit_reason en
    cualquier otro caso (TP/SL/trailing/tiempo/gap de datos)."""
    if position.thesis_id is None:
        return
    status = thesis_engine.thesis_status_for_exit_reason(record["exit_reason"])
    if close_rationale is None:
        close_rationale = f"Cerrada por mecánica ordinaria de la posición: {record['exit_reason']}."
    if close_reason_code is None:
        close_reason_code = record["exit_reason"]
    _update_thesis_status(conn, position.thesis_id, status, record["exit_date"], close_reason_code, close_rationale)


def _build_entry_plan(conn, version: str, events: list[dict], ticker_cache: dict[str, dict[date, dict]]) -> list[dict]:
    """Precalcula, por evento, la fecha de entrada real (primer día de
    negociación del TICKER estrictamente posterior a d0_close_date — no el
    calendario maestro, por si ese ticker en concreto tiene un hueco justo
    ahí), el estilo de ejecución, y target_date. Eventos sin datos de precio
    suficientes se omiten con un warning, no con un crash."""
    plan = []
    for ev in events:
        ticker = ev["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = _load_ticker_prices(conn, ticker)
        prices = ticker_cache[ticker]

        future_dates = sorted(d for d in prices if d > ev["d0_close_date"])
        if not future_dates:
            logger.warning("Evento %d (%s): sin precios posteriores a D0, se omite", ev["event_id"], ticker)
            continue
        entry_date = future_dates[0]
        entry_bar = prices[entry_date]
        if entry_bar["open_raw"] is None:
            logger.warning("Evento %d (%s): sin precio de apertura en la fecha de entrada, se omite", ev["event_id"], ticker)
            continue
        if not any(d > entry_date for d in prices):
            # entry_date es el ÚLTIMO día de negociación disponible para este
            # ticker: no hay ni un solo día posterior para observar la
            # posición. Sin esto, compute_target_date() degenera a
            # target_date == entry_date, y el cierre forzado al final del
            # panel de precios (simulate_portfolio) generaría exit_date ==
            # entry_date — una violación anti-look-ahead real, no un caso de
            # borde inofensivo (pasaría en producción cada vez que un evento
            # se analice el mismo día en que termina el backfill de precios
            # disponible). Se omite con el mismo criterio que "sin precios
            # posteriores a D0": no hay nada que simular todavía.
            logger.warning("Evento %d (%s): la entrada D+1 (%s) es el último día de precio disponible, sin días posteriores para simular — se omite", ev["event_id"], ticker, entry_date)
            continue

        direction = "LONG" if float(ev["prediction"]) > 0 else "SHORT"
        if version in ("BALANCED", "DYNAMIC"):
            style = classify_balanced_execution_style(float(ev["confidence"]), float(ev["ev_conservative"]), float(ev["ev_aggressive"]))
            ev_value = float(ev["ev_conservative"]) if style == "CONSERVATIVE" else float(ev["ev_aggressive"])
        else:
            style = version
            ev_value = float(ev["ev_conservative"] if version == "CONSERVATIVE" else ev["ev_aggressive"])

        config = STRATEGIES[style]
        target_date = compute_target_date(entry_date, config.holding_period_max_days, sorted(prices.keys()))

        plan.append(
            {
                "event_id": ev["event_id"],
                "ticker": ticker,
                "entry_date": entry_date,
                "execution_style": style,
                "direction": direction,
                "ev": ev_value,
                "confidence": float(ev["confidence"]),
                "prediction": float(ev["prediction"]),
                "target_date": target_date,
                "event_class": ev["event_class"],
                "d0_close_date": ev["d0_close_date"],
                "slippage_bps_por_lado": slippage_bps_por_lado(
                    float(ev["market_cap_d0"]) if ev.get("market_cap_d0") is not None else None
                ),
            }
        )
    return plan


def _resolve_forced_close(prices: dict[date, dict], entry_date: date, entry_price: float) -> tuple[date, float, str]:
    """(exit_date, exit_price, exit_reason) para una posición que sigue
    abierta al final de master_calendar — ver "CIERRE FORZADO CON DATOS DE
    PRECIO INCOMPLETOS" en el docstring del módulo para el bug que esto
    corrige.

    `prices` es el ticker_cache de ESE ticker (fecha -> fila de prices).
    entry_date/entry_price son los de la posición — necesarios porque el
    fallback de "sin ningún precio válido tras la entrada" usa entry_price
    (retorno plano), nunca un precio inventado.

    Devuelve SIEMPRE una fecha > entry_date (nunca None): _build_entry_plan
    ya garantiza que existe al menos una fecha posterior a entry_date en
    `prices` antes de abrir la posición (ver su propio comentario sobre "sin
    días posteriores para simular"), así que last_date de aquí abajo es una
    cota superior segura incluso en el peor caso."""
    last_date = max(prices.keys())
    last_close = prices[last_date]["close_raw"]
    if last_close is not None:
        # Caso normal (sin gap en la última fila) — MISMO comportamiento que
        # antes de este fix, byte a byte, para no alterar ningún resultado
        # ya validado.
        return last_date, float(last_close), "MAX_HOLDING"

    # La última fila es un centinela de survivorship_warning (deslistado/halt
    # sin resolver antes de que se acabaran los datos) — busca hacia atrás la
    # fecha válida más reciente, siempre que siga siendo posterior a la
    # entrada. Un cierre FORZADO (sin stop ni objetivo) el mismo día de la
    # entrada no tendría precio ejecutable fiable, así que se exige un día
    # posterior; las salidas por stop/objetivo sí pueden ser el mismo día (H-31).
    for d in sorted((d for d in prices if d > entry_date), reverse=True):
        close = prices[d]["close_raw"]
        if close is not None:
            return d, float(close), "DATA_GAP"

    # Ningún precio válido en absoluto tras la entrada (el ticker se deslistó
    # antes de que hubiera un solo día de cotización posterior) — cierra en
    # la última fecha disponible (> entry_date, garantizado más arriba) al
    # precio de ENTRADA: retorno plano, no una ganancia o pérdida inventada.
    return last_date, entry_price, "DATA_GAP"


def _efectivo_comprometido(pos: OpenPosition) -> float:
    """Efectivo que inmoviliza una posición: las dos patas con cobertura
    (2× nocional, sin margen), una sin ella."""
    return pos.position_size_dollars * (2 if pos.spy_entry_price is not None else 1)


def simulate_portfolio(
    conn,
    version: str,
    run_batch_tag: str,
    starting_capital: float = 100_000.0,
    sample: str | None = None,
    circuit_breaker_pct: float = DRAWDOWN_CIRCUIT_BREAKER_PCT,
    thesis_memory_enabled: bool = config.THESIS_MEMORY_ENABLED,
    circuit_breaker_pause_sessions: int = CIRCUIT_BREAKER_PAUSE_SESSIONS,
) -> dict:
    """Punto de entrada del backtest de cartera para UNA versión de
    estrategia. Ver docstring del módulo para la disciplina anti-look-ahead
    y las decisiones de diseño (orden de prioridad TP/SL/trailing, MTM de
    posiciones parcialmente cerradas, etc.). `sample`: ver
    fetch_events_for_version — None (default) no filtra nada.
    `circuit_breaker_pct`: umbral del circuit-breaker de drawdown (ver
    DRAWDOWN_CIRCUIT_BREAKER_PCT) — parametrizado explícitamente en vez de
    leer siempre la constante, para poder probarlo con un umbral distinto
    sin tocar el default de producción, y para que un operador pueda
    experimentar con otro umbral sin editar código.
    `thesis_memory_enabled`: memoria de tesis (ver thesis_engine.py), False
    por defecto (config.THESIS_MEMORY_ENABLED) — con el flag desactivado esta
    función se comporta EXACTAMENTE igual que antes de que existiera esta
    funcionalidad, byte a byte; se expone como parámetro explícito (en vez de
    leer siempre la constante) para poder comparar con/sin memoria en el
    mismo backtest sin tocar el default de producción."""
    assert version in VERSIONS

    events = fetch_events_for_version(conn, version, sample=sample)
    if not events:
        logger.warning("Sin eventos con trade_decision para %s — nada que simular", version)
        return {"version": version, "n_trades": 0, "n_equity_days": 0}

    # En el In-Sample, los precios se cortan antes del OOS (H-37, revisión de
    # la Tanda 13): el embargo cuenta sesiones de la bolsa, pero el holding
    # cuenta las del propio ticker, y con una vela de menos la operación del
    # último D0 se cerraba con precio del OOS. Además, sin el corte la curva
    # de capital seguía hasta el último precio de la base (años de días sin
    # operaciones que hundían la rentabilidad anual y el Sharpe). Lo que siga
    # abierto se cierra en la última vela del In-Sample (cierre forzado).
    antes_de = OOS_START if sample == SAMPLE_IN_SAMPLE else None
    ticker_cache: dict[str, dict[date, dict]] = {}
    if antes_de is not None:
        for e in events:
            if e["ticker"] not in ticker_cache:
                ticker_cache[e["ticker"]] = _load_ticker_prices(conn, e["ticker"], antes_de=antes_de)
    entry_plan = _build_entry_plan(conn, version, events, ticker_cache)
    if not entry_plan:
        logger.warning("Ningún evento de %s tiene un plan de entrada válido", version)
        return {"version": version, "n_trades": 0, "n_equity_days": 0}

    # Pata de cobertura (docstring del módulo): SPY se carga aparte, no entra
    # en ticker_cache ni en el calendario maestro.
    spy = cargar_serie_cobertura(conn, antes_de=antes_de)
    n_sin_cobertura = 0

    # Memoria de tesis: calendario de TODOS los eventos nuevos por ticker (no
    # solo los que generan entrada en esta versión — ver
    # _load_ticker_new_events). Coste solo cuando la memoria está activada.
    new_events_by_ticker: dict[str, dict[date, list[dict]]] = (
        {ticker: _load_ticker_new_events(conn, ticker, prices) for ticker, prices in ticker_cache.items()}
        if thesis_memory_enabled
        else {}
    )

    entries_by_date: dict[date, list[dict]] = {}
    for p in entry_plan:
        entries_by_date.setdefault(p["entry_date"], []).append(p)

    all_dates: set[date] = set()
    for prices in ticker_cache.values():
        all_dates.update(prices.keys())
    first_entry = min(p["entry_date"] for p in entry_plan)
    master_calendar = sorted(d for d in all_dates if d >= first_entry)

    if version in ("BALANCED", "DYNAMIC"):
        # Mismo reparto 3+2 que BALANCED: ambas comparten fuente de trade
        # decision y clasificación de estilo (ver _TRADE_DECISION_SOURCE_VERSION),
        # así que no hay un tercer criterio de concurrencia que inventar.
        max_concurrent = dict(BALANCED_MAX_CONCURRENT)
    elif version == "CONSERVATIVE":
        max_concurrent = {"CONSERVATIVE": STRATEGIES["CONSERVATIVE"].max_concurrent, "AGGRESSIVE": 0}
    else:
        max_concurrent = {"CONSERVATIVE": 0, "AGGRESSIVE": STRATEGIES["AGGRESSIVE"].max_concurrent}

    cash = starting_capital
    open_positions: dict[str, list[OpenPosition]] = {"CONSERVATIVE": [], "AGGRESSIVE": []}
    completed_trades: list[dict] = []
    equity_rows: list[dict] = []
    peak_equity = starting_capital  # ver DRAWDOWN_CIRCUIT_BREAKER_PCT arriba
    n_days_circuit_breaker_active = 0
    n_days_reduced_size = 0
    # Estado del freno (H-10): sesiones de pausa que quedan, si se opera a
    # medio tamaño y el nivel de equity al reanudar.
    pause_sessions_left = 0
    reduced_size = False
    resume_level: float | None = None

    def portfolio_mtm(today: date) -> float:
        total = 0.0
        for style_positions in open_positions.values():
            for pos in style_positions:
                bar = ticker_cache[pos.ticker].get(today)
                price = float(bar["close_raw"]) if bar and bar["close_raw"] is not None else pos.entry_price
                total += compute_position_mtm_dollars(pos, price, spy.cierre_hasta(today, pos.entry_date))
        return total

    for today in master_calendar:
        # Estado AL ABRIR: las entradas se ejecutan a la apertura, así que su
        # tamaño, el freno, los huecos libres y el dinero disponible salen de
        # cómo estaba la cartera al cierre anterior, no de salidas que pasan
        # DESPUÉS dentro de la sesión (revisión adversarial, Tanda 2).
        cash_al_abrir = cash
        equity_al_abrir = equity_rows[-1]["balance"] if equity_rows else starting_capital
        ocupados_al_abrir = {s: len(v) for s, v in open_positions.items()}

        # 1) Salidas — se procesan antes que las entradas del mismo día.
        for style in ("CONSERVATIVE", "AGGRESSIVE"):
            still_open = []
            for pos in open_positions[style]:
                bar = ticker_cache[pos.ticker].get(today)
                if bar is None or bar["high_raw"] is None or bar["low_raw"] is None or bar["close_raw"] is None:
                    still_open.append(pos)  # sin dato ese día (festivo local/halt) — se mantiene abierta
                    continue
                open_hoy = float(bar["open_raw"]) if bar["open_raw"] is not None else None
                n_antes = len(pos.closes)
                step_position_forward(
                    pos, float(bar["high_raw"]), float(bar["low_raw"]), float(bar["close_raw"]), today, open_=open_hoy,
                )
                cubrir_cierres_nuevos(pos, n_antes, spy, open_accion=open_hoy)
                if pos.remaining_fraction <= 1e-9:
                    record = consolidate_trade_record(pos)
                    record["version"] = version
                    record["run_batch_tag"] = run_batch_tag
                    completed_trades.append(record)
                    cash += _efectivo_comprometido(pos) + record["pnl_abs"]
                    _maybe_close_thesis(conn, pos, record)
                else:
                    still_open.append(pos)
            open_positions[style] = still_open

        # 1.5) Reconciliación de tesis (memoria de tesis — config.THESIS_MEMORY_ENABLED).
        # SIEMPRE después de las salidas normales de arriba: si STOP_LOSS (u
        # otro cierre normal) ya vació la posición hoy, ya no está en
        # open_positions y no se reconcilia — es la forma en que el orden
        # existente del bucle ya garantiza, sin código adicional, que
        # STOP_LOSS siempre gana si ambos aplicarían el mismo día (ajuste
        # acordado con el usuario).
        if thesis_memory_enabled:
            for style in ("CONSERVATIVE", "AGGRESSIVE"):
                still_open = []
                for pos in open_positions[style]:
                    if pos.thesis_id is None:
                        still_open.append(pos)
                        continue
                    new_events_today = new_events_by_ticker.get(pos.ticker, {}).get(today, [])
                    bar = ticker_cache[pos.ticker].get(today)
                    if not new_events_today or bar is None or bar["open_raw"] is None:
                        still_open.append(pos)
                        continue
                    current_price = float(bar["open_raw"])
                    n_antes = len(pos.closes)
                    for new_event in new_events_today:
                        # Con cobertura, el movimiento realizado es frente a
                        # SPY (el mismo que se predice y que mide la
                        # saturación, sobre CAR anormales).
                        realized_move_pct = gain_pct(pos.direction, pos.entry_price, current_price)
                        spy_ahora = spy.precio_de_salida(today, pos.entry_date, a_la_apertura=True)
                        if pos.spy_entry_price is not None and spy_ahora is not None:
                            realized_move_pct -= gain_pct(pos.direction, pos.spy_entry_price, spy_ahora)
                        saturation_threshold_pct = _compute_saturation_threshold_for_event_class(
                            conn, pos.thesis_origin_event_class, today, pos.event_id
                        )
                        volume_ratio = thesis_engine.compute_volume_ratio(
                            ticker_cache[pos.ticker], today, ADV_TRAILING_WINDOW_DAYS, ADV_MIN_TRADING_DAYS
                        )
                        thesis_snapshot = thesis_engine.ThesisSnapshot(
                            thesis_id=pos.thesis_id, ticker=pos.ticker, direction=pos.direction,
                            entry_price=pos.entry_price, rationale=pos.thesis_rationale,
                            expected_move_pct=pos.thesis_expected_move_pct,
                            invalidation_conditions=pos.thesis_invalidation_conditions,
                            created_at_date=pos.thesis_created_at_date,
                        )
                        blind_judgment = thesis_engine.BlindJudgment(
                            event_id=new_event["event_id"], event_class=new_event["event_class"],
                            net_conviction=float(new_event["net_conviction"]),
                            confidence_in_conviction=float(new_event["confidence_in_conviction"]),
                        )
                        metrics = thesis_engine.ObjectiveMetrics(
                            realized_move_pct=realized_move_pct, current_price=current_price,
                            saturation_threshold_pct=saturation_threshold_pct, volume_ratio=volume_ratio,
                        )
                        result = thesis_engine.reconcile(thesis_snapshot, blind_judgment, metrics)
                        _store_thesis_update(
                            conn, pos.thesis_id, today, new_event["event_id"], result,
                            {
                                "realized_move_pct": realized_move_pct, "current_price": current_price,
                                "saturation_threshold_pct": saturation_threshold_pct, "volume_ratio": volume_ratio,
                                "net_conviction": blind_judgment.net_conviction,
                                "confidence_in_conviction": blind_judgment.confidence_in_conviction,
                            },
                            run_batch_tag,
                        )
                        if result.action == "SELL":
                            exit_tag = "INVALIDATED" if result.reason_code.startswith("INVALIDATED") else result.reason_code
                            pos.closes.append((today, pos.remaining_fraction, current_price, exit_tag))
                            pos.remaining_fraction = 0.0
                            break  # la tesis se cerró — no hay más que reconciliar hoy
                        if result.action == "REDUCE":
                            reduce_amount = min(result.reduce_fraction, pos.remaining_fraction)
                            pos.closes.append((today, reduce_amount, current_price, "THESIS_REDUCE"))
                            pos.remaining_fraction -= reduce_amount
                            if pos.remaining_fraction <= 1e-9:
                                break
                        # HOLD: nada que cambiar; se sigue con el siguiente evento nuevo de hoy si lo hubiera.
                    # Los cierres por tesis son a la apertura: SPY también.
                    cubrir_cierres_nuevos(pos, n_antes, spy, a_la_apertura=True)
                    if pos.remaining_fraction <= 1e-9:
                        record = consolidate_trade_record(pos)
                        record["version"] = version
                        record["run_batch_tag"] = run_batch_tag
                        completed_trades.append(record)
                        cash += _efectivo_comprometido(pos) + record["pnl_abs"]
                        _maybe_close_thesis(conn, pos, record, close_rationale=result.rationale, close_reason_code=result.reason_code)
                    else:
                        still_open.append(pos)
                open_positions[style] = still_open

        # 2) Entradas — dimensionadas contra la equity al cierre anterior
        # (spec: "rebalance: noche antes de apertura").
        equity_for_sizing = equity_al_abrir
        # Freno de pérdidas (H-10, ver DRAWDOWN_CIRCUIT_BREAKER_PCT): pausa,
        # después medio tamaño hasta un nuevo máximo.
        if pause_sessions_left > 0:
            pause_sessions_left -= 1
            circuit_breaker_active = True
            if pause_sessions_left == 0:
                reduced_size, resume_level = True, None
        else:
            if reduced_size:
                if resume_level is None:
                    resume_level = equity_for_sizing
                if equity_for_sizing > peak_equity:
                    reduced_size, resume_level = False, None
                    peak_equity = equity_for_sizing
                reference = resume_level if reduced_size else peak_equity
            else:
                peak_equity = max(peak_equity, equity_for_sizing)
                reference = peak_equity
            circuit_breaker_active = is_circuit_breaker_active(reference, equity_for_sizing, threshold=circuit_breaker_pct)
            if circuit_breaker_active:
                pause_sessions_left = max(circuit_breaker_pause_sessions - 1, 0)  # hoy es la primera sesión de pausa
                reduced_size, resume_level = pause_sessions_left == 0, None
        if circuit_breaker_active:
            n_days_circuit_breaker_active += 1
        else:
            if reduced_size:
                n_days_reduced_size += 1
            nuevas_hoy: dict[str, list[OpenPosition]] = {"CONSERVATIVE": [], "AGGRESSIVE": []}
            gastado_hoy = 0.0
            for plan in entries_by_date.get(today, []):
                style = plan["execution_style"]
                if ocupados_al_abrir[style] + len(nuevas_hoy[style]) >= max_concurrent[style]:
                    continue  # sin hueco al abrir — la señal se descarta (max_concurrent del spec)
                if thesis_memory_enabled and any(p.ticker == plan["ticker"] for p in open_positions[style] + nuevas_hoy[style]):
                    # Ya hay una tesis abierta en este ticker/estilo: el
                    # evento nuevo se reconcilia contra ELLA (paso 1.5), no
                    # abre una segunda posición independiente (ajuste
                    # acordado con el usuario). Con la memoria desactivada
                    # esta rama nunca se evalúa — comportamiento de siempre.
                    continue
                bar = ticker_cache[plan["ticker"]].get(today)
                if bar is None or bar["open_raw"] is None:
                    continue
                spy_entrada = spy.apertura(today)
                if spy_entrada is None:
                    # Sin apertura de SPY no hay pata de cobertura: la
                    # operación no se abre (no se inventa la pata ni se
                    # mezcla una operación sin cubrir con las cubiertas).
                    logger.warning("Evento %d (%s): sin apertura de %s el %s, no se puede cubrir — se omite", plan["event_id"], plan["ticker"], TICKER_COBERTURA, today)
                    n_sin_cobertura += 1
                    continue
                adv_usd_60d = compute_trailing_adv_usd(ticker_cache[plan["ticker"]], today)
                pos = open_position(
                    event_id=plan["event_id"], version=version, execution_style=style, direction=plan["direction"],
                    ticker=plan["ticker"], entry_date=today, entry_price=float(bar["open_raw"]), target_date=plan["target_date"],
                    balance_for_sizing=equity_for_sizing, confidence=plan["confidence"], ev=plan["ev"], prediction=plan["prediction"],
                    had_survivorship_warning=bool(bar["survivorship_warning"]), adv_usd_60d=adv_usd_60d,
                    slippage_bps_por_lado=plan.get("slippage_bps_por_lado", SLIPPAGE_BPS_PER_SIDE_SMALL),
                    size_multiplier=CIRCUIT_BREAKER_REDUCED_SIZE if reduced_size else 1.0,
                    spy_entry_price=spy_entrada,
                )
                # Las dos patas se pagan con efectivo (2× nocional).
                disponible = cash_al_abrir - gastado_hoy
                if _efectivo_comprometido(pos) > disponible:
                    logger.warning("Evento %d: tamaño deseado %.2f (dos patas) excede el cash disponible %.2f — se reduce", plan["event_id"], _efectivo_comprometido(pos), disponible)
                    pos.position_size_dollars = max(disponible, 0.0) / 2
                cash -= _efectivo_comprometido(pos)
                gastado_hoy += _efectivo_comprometido(pos)
                if thesis_memory_enabled:
                    expected_move_pct = abs(
                        estimate_impact_for_event(conn, plan["event_class"], plan["d0_close_date"], plan["event_id"], window_days=20).expected_magnitude_pct
                    )
                    expected_horizon_days = config.EVENT_WINDOWS_DAYS[0]
                    thesis_expiry_date = compute_target_date(today, expected_horizon_days, sorted(ticker_cache[plan["ticker"]].keys()))
                    rationale = thesis_engine.build_thesis_rationale(
                        plan["direction"], plan["ticker"], plan["event_class"], plan["prediction"], plan["confidence"]
                    )
                    invalidation_conditions = thesis_engine.default_invalidation_conditions(plan["direction"], expected_horizon_days)
                    thesis_id = _store_new_thesis(
                        conn, plan["ticker"], plan["event_id"], version, style, plan["direction"], today,
                        pos.entry_price, rationale, expected_move_pct, expected_horizon_days,
                        invalidation_conditions, run_batch_tag,
                    )
                    pos.thesis_id = thesis_id
                    pos.thesis_rationale = rationale
                    pos.thesis_expected_move_pct = expected_move_pct
                    pos.thesis_invalidation_conditions = invalidation_conditions
                    pos.thesis_created_at_date = today
                    pos.thesis_origin_event_class = plan["event_class"]
                    pos.thesis_expiry_date = thesis_expiry_date
                nuevas_hoy[style].append(pos)

            # La sesión de entrada también cuenta (BUGS_REPORT.md H-31): se
            # entra a la apertura, así que todo el rango del día es posterior
            # a la entrada y un stop u objetivo tocado ese día se ejecuta ese
            # día. Va DESPUÉS de abrir todas las entradas del día: lo que se
            # cierra a media sesión no puede liberar hueco ni dinero para otra
            # entrada a la misma apertura.
            for style, nuevas in nuevas_hoy.items():
                for pos in nuevas:
                    bar = ticker_cache[pos.ticker].get(today)
                    if bar["high_raw"] is not None and bar["low_raw"] is not None and bar["close_raw"] is not None:
                        step_position_forward(
                            pos, float(bar["high_raw"]), float(bar["low_raw"]), float(bar["close_raw"]), today,
                            open_=pos.entry_price,
                        )
                        cubrir_cierres_nuevos(pos, 0, spy, open_accion=pos.entry_price)
                    if pos.remaining_fraction <= 1e-9:
                        record = consolidate_trade_record(pos)
                        record["version"] = version
                        record["run_batch_tag"] = run_batch_tag
                        completed_trades.append(record)
                        cash += _efectivo_comprometido(pos) + record["pnl_abs"]
                        _maybe_close_thesis(conn, pos, record)
                    else:
                        open_positions[style].append(pos)

        # 3) Curva de equity de hoy (tras salidas Y entradas de hoy, si el
        # circuit-breaker no las bloqueó).
        n_open = sum(len(v) for v in open_positions.values())
        equity_rows.append({
            "trade_date": today,
            "balance": cash + portfolio_mtm(today),
            "n_open_positions": n_open,
            "circuit_breaker_active": circuit_breaker_active,
        })

    # Cierre forzado de lo que siga abierto al final de los datos disponibles.
    for style_positions in open_positions.values():
        for pos in style_positions:
            close_date, close_price, close_reason = _resolve_forced_close(
                ticker_cache[pos.ticker], pos.entry_date, pos.entry_price
            )
            n_antes = len(pos.closes)
            pos.closes.append((close_date, pos.remaining_fraction, close_price, close_reason))
            pos.remaining_fraction = 0.0
            cubrir_cierres_nuevos(pos, n_antes, spy)
            record = consolidate_trade_record(pos)
            record["version"] = version
            record["run_batch_tag"] = run_batch_tag
            completed_trades.append(record)
            # Antes este efectivo no volvía a la caja (no afectaba a la curva,
            # ya escrita, pero dejaba mal el saldo final).
            cash += _efectivo_comprometido(pos) + record["pnl_abs"]
            _maybe_close_thesis(conn, pos, record)

    _store_portfolio_results(conn, version, run_batch_tag, completed_trades, equity_rows)
    return {
        "version": version,
        "n_trades": len(completed_trades),
        "n_equity_days": len(equity_rows),
        "n_days_circuit_breaker_active": n_days_circuit_breaker_active,
        "n_days_reduced_size": n_days_reduced_size,
        "n_sin_cobertura": n_sin_cobertura,
        "final_cash": cash,
    }


def _store_portfolio_results(conn, version: str, run_batch_tag: str, trades: list[dict], equity_rows: list[dict]) -> None:
    with conn.cursor() as cur:
        for t in trades:
            cur.execute(
                """
                INSERT INTO portfolio_trades (
                    event_id, version, execution_style, direction, entry_date, entry_price,
                    exit_date, exit_price, exit_reason, pnl_pct, pnl_abs, position_size_pct,
                    position_size_dollars, confidence, ev, prediction, actual_move_pct,
                    spy_entry_price, spy_exit_price, spy_move_pct,
                    had_survivorship_warning, had_adv_cap_applied, thesis_id, run_batch_tag
                ) VALUES (
                    %(event_id)s, %(version)s, %(execution_style)s, %(direction)s, %(entry_date)s, %(entry_price)s,
                    %(exit_date)s, %(exit_price)s, %(exit_reason)s, %(pnl_pct)s, %(pnl_abs)s, %(position_size_pct)s,
                    %(position_size_dollars)s, %(confidence)s, %(ev)s, %(prediction)s, %(actual_move_pct)s,
                    %(spy_entry_price)s, %(spy_exit_price)s, %(spy_move_pct)s,
                    %(had_survivorship_warning)s, %(had_adv_cap_applied)s, %(thesis_id)s, %(run_batch_tag)s
                )
                ON CONFLICT (event_id, version, run_batch_tag) DO UPDATE SET
                    execution_style = EXCLUDED.execution_style, direction = EXCLUDED.direction,
                    entry_date = EXCLUDED.entry_date, entry_price = EXCLUDED.entry_price,
                    exit_date = EXCLUDED.exit_date, exit_price = EXCLUDED.exit_price,
                    exit_reason = EXCLUDED.exit_reason, pnl_pct = EXCLUDED.pnl_pct, pnl_abs = EXCLUDED.pnl_abs,
                    position_size_pct = EXCLUDED.position_size_pct, position_size_dollars = EXCLUDED.position_size_dollars,
                    confidence = EXCLUDED.confidence, ev = EXCLUDED.ev, prediction = EXCLUDED.prediction,
                    actual_move_pct = EXCLUDED.actual_move_pct, spy_entry_price = EXCLUDED.spy_entry_price,
                    spy_exit_price = EXCLUDED.spy_exit_price, spy_move_pct = EXCLUDED.spy_move_pct,
                    had_survivorship_warning = EXCLUDED.had_survivorship_warning,
                    had_adv_cap_applied = EXCLUDED.had_adv_cap_applied, thesis_id = EXCLUDED.thesis_id
                """,
                t,
            )
        for row in equity_rows:
            cur.execute(
                """
                INSERT INTO portfolio_equity_curve (version, trade_date, balance, n_open_positions, circuit_breaker_active, run_batch_tag)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (version, trade_date, run_batch_tag) DO UPDATE SET
                    balance = EXCLUDED.balance, n_open_positions = EXCLUDED.n_open_positions,
                    circuit_breaker_active = EXCLUDED.circuit_breaker_active
                """,
                (version, row["trade_date"], row["balance"], row["n_open_positions"], row["circuit_breaker_active"], run_batch_tag),
            )
    conn.commit()
