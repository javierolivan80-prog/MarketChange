"""test_portfolio_simulator.py — mecánica de posición pura (sin BD, sin red).

Cada regla del DAILY_LOOP del spec probada de forma aislada: TP, SL,
max_holding, trailing stop (incluyendo el gap que dispara varios tramos el
mismo día), y la prioridad SL-antes-que-TP cuando ambos se cruzan el mismo día.
"""
from datetime import date, timedelta

import pytest

from pipeline.backtest.portfolio_simulator import (
    COMMISSION_BPS_ROUND_TRIP,
    DRAWDOWN_CIRCUIT_BREAKER_PCT,
    MAX_POSITION_PCT_OF_ADV,
    OpenPosition,
    _build_entry_plan,
    _resolve_forced_close,
    cap_position_dollars_by_adv,
    compute_position_mtm_dollars,
    compute_target_date,
    compute_tp_sl_prices,
    compute_trailing_adv_usd,
    consolidate_trade_record,
    gain_pct,
    is_circuit_breaker_active,
    open_position,
    step_position_forward,
)
from pipeline.backtest.portfolio_strategies import STRATEGIES, generate_trailing_stop_tiers

D0 = date(2024, 1, 2)


def _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5, trailing_tiers=None, target_days=5):
    tp, sl = compute_tp_sl_prices("LONG", entry_price, take_profit_pct, stop_loss_pct)
    return OpenPosition(
        event_id=1, version="CONSERVATIVE", execution_style="CONSERVATIVE", direction="LONG",
        ticker="TEST", entry_date=D0, entry_price=entry_price, position_size_pct=3.0,
        position_size_dollars=3000.0, target_date=D0 + timedelta(days=target_days),
        take_profit_price=tp, stop_loss_price=sl, trailing_tiers=trailing_tiers,
        confidence=80.0, ev=0.01, prediction=0.6, had_survivorship_warning=False,
    )


def _make_short_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5):
    tp, sl = compute_tp_sl_prices("SHORT", entry_price, take_profit_pct, stop_loss_pct)
    return OpenPosition(
        event_id=1, version="CONSERVATIVE", execution_style="CONSERVATIVE", direction="SHORT",
        ticker="TEST", entry_date=D0, entry_price=entry_price, position_size_pct=3.0,
        position_size_dollars=3000.0, target_date=D0 + timedelta(days=5),
        take_profit_price=tp, stop_loss_price=sl, trailing_tiers=None,
        confidence=80.0, ev=0.01, prediction=-0.6, had_survivorship_warning=False,
    )


# ---------------------------------------------------------------------------
# gain_pct / compute_tp_sl_prices
# ---------------------------------------------------------------------------


def test_gain_pct_long_and_short_are_mirrored():
    assert gain_pct("LONG", 100, 110) == pytest.approx(10.0)
    assert gain_pct("SHORT", 100, 110) == pytest.approx(-10.0)
    assert gain_pct("SHORT", 100, 90) == pytest.approx(10.0)


def test_compute_tp_sl_prices_long():
    tp, sl = compute_tp_sl_prices("LONG", 100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    assert tp == pytest.approx(102.0)
    assert sl == pytest.approx(98.5)


def test_compute_tp_sl_prices_short_are_inverted():
    tp, sl = compute_tp_sl_prices("SHORT", 100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    assert tp == pytest.approx(98.0)   # baja para dar beneficio al short
    assert sl == pytest.approx(101.5)  # sube en contra del short


def test_compute_tp_sl_prices_none_take_profit_for_trailing_strategies():
    tp, sl = compute_tp_sl_prices("LONG", 100.0, take_profit_pct=None, stop_loss_pct=5.0)
    assert tp is None
    assert sl == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# step_position_forward — cada regla aislada
# ---------------------------------------------------------------------------


def test_take_profit_triggers_on_intraday_high():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=103.0, low=99.5, close=101.0, trade_date=D0 + timedelta(days=1))
    assert pos.remaining_fraction == 0.0
    assert pos.closes[-1][3] == "TAKE_PROFIT"
    assert pos.closes[-1][2] == pytest.approx(102.0)  # se llena al precio del TP, no al close


def test_stop_loss_triggers_on_intraday_low():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=100.5, low=98.0, close=99.0, trade_date=D0 + timedelta(days=1))
    assert pos.remaining_fraction == 0.0
    assert pos.closes[-1][3] == "STOP_LOSS"
    assert pos.closes[-1][2] == pytest.approx(98.5)


def test_stop_loss_wins_when_both_tp_and_sl_cross_same_day():
    """Decisión de diseño documentada: gap grande que cruza ambos el mismo
    día -> gana el stop-loss (convención conservadora)."""
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=110.0, low=90.0, close=100.0, trade_date=D0 + timedelta(days=1))
    assert pos.closes[-1][3] == "STOP_LOSS"


def test_short_take_profit_and_stop_loss_use_correct_sides():
    pos = _make_short_position(entry_price=100.0)
    # Precio cae -> beneficio para el short -> TP (98.0) debe activarse por el LOW.
    step_position_forward(pos, high=99.5, low=97.0, close=98.0, trade_date=D0 + timedelta(days=1))
    assert pos.closes[-1][3] == "TAKE_PROFIT"


def test_hold_when_no_condition_triggers():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5, target_days=5)
    step_position_forward(pos, high=100.5, low=99.5, close=100.2, trade_date=D0 + timedelta(days=1))
    assert pos.remaining_fraction == 1.0
    assert pos.closes == []


def test_max_holding_closes_remainder_at_close_price():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5, target_days=2)
    target = pos.target_date
    step_position_forward(pos, high=100.5, low=99.8, close=100.3, trade_date=target)
    assert pos.remaining_fraction == 0.0
    assert pos.closes[-1] == (target, 1.0, 100.3, "MAX_HOLDING")


# ---------------------------------------------------------------------------
# Trailing stop (Aggressive)
# ---------------------------------------------------------------------------


def test_trailing_stop_single_tier_triggers_at_threshold_price_not_close():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers)
    step_position_forward(pos, high=125.0, low=115.0, close=120.0, trade_date=D0 + timedelta(days=3))
    assert pos.remaining_fraction == pytest.approx(0.70)  # cerró el primer tramo (30%)
    assert pos.closes[-1] == (D0 + timedelta(days=3), pytest.approx(0.30), pytest.approx(120.0), "TRAILING_STOP")


def test_trailing_stop_gap_triggers_multiple_tiers_same_day():
    tiers = generate_trailing_stop_tiers()  # (20,.3),(40,.3),(60,.3),(80,.1)
    # target_days=20: el día 10 debe seguir DENTRO del holding period, si no
    # el propio check de max_holding cerraría también el 10% restante en la
    # misma llamada (se encontró exactamente ese caso con el target_days=5
    # por defecto: el test fallaba no por un bug de trailing stop, sino
    # porque día 10 > target_date de 5 días también disparaba MAX_HOLDING).
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    # Un gap hasta +65% en un solo día cruza los tramos de 20/40/60.
    step_position_forward(pos, high=165.0, low=160.0, close=163.0, trade_date=D0 + timedelta(days=10))
    assert pos.remaining_fraction == pytest.approx(0.10)  # 3 tramos de 30% cerrados = 90%
    assert len(pos.closes) == 3
    assert [c[3] for c in pos.closes] == ["TRAILING_STOP"] * 3


def test_trailing_stop_fully_closes_across_multiple_days():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))  # +20%
    step_position_forward(pos, high=141.0, low=139.0, close=140.0, trade_date=D0 + timedelta(days=4))  # +40%
    step_position_forward(pos, high=161.0, low=159.0, close=160.0, trade_date=D0 + timedelta(days=6))  # +60%
    step_position_forward(pos, high=181.0, low=179.0, close=180.0, trade_date=D0 + timedelta(days=8))  # +80% -> cierra el resto
    assert pos.remaining_fraction == pytest.approx(0.0)
    assert len(pos.closes) == 4
    assert sum(f for _, f, _, _ in pos.closes) == pytest.approx(1.0)


def test_trailing_stop_does_not_refire_same_tier_twice():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    step_position_forward(pos, high=125.0, low=120.0, close=122.0, trade_date=D0 + timedelta(days=2))
    step_position_forward(pos, high=126.0, low=121.0, close=123.0, trade_date=D0 + timedelta(days=3))  # sigue en el mismo tramo (+20-40%)
    assert len(pos.closes) == 1  # el tramo de 20% no se vuelve a disparar


def test_stop_loss_overrides_trailing_stop_same_day():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers)
    step_position_forward(pos, high=100.5, low=94.0, close=95.0, trade_date=D0 + timedelta(days=1))
    assert pos.closes[-1][3] == "STOP_LOSS"
    assert pos.remaining_fraction == 0.0


# ---------------------------------------------------------------------------
# consolidate_trade_record
# ---------------------------------------------------------------------------


def test_consolidate_single_close_applies_commission():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=103.0, low=99.0, close=101.0, trade_date=D0 + timedelta(days=1))
    record = consolidate_trade_record(pos)
    assert record["actual_move_pct"] == pytest.approx(2.0)  # (102-100)/100*100
    assert record["pnl_pct"] == pytest.approx(2.0 - COMMISSION_BPS_ROUND_TRIP / 100)
    assert record["exit_reason"] == "TAKE_PROFIT"
    assert record["exit_date"] > record["entry_date"]  # checksum anti-look-ahead


def test_consolidate_weighted_average_across_trailing_tiers():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))
    step_position_forward(pos, high=200.0, low=195.0, close=198.0, trade_date=D0 + timedelta(days=4))  # gap grande cierra el resto
    record = consolidate_trade_record(pos)
    # exit_reason debe ser TRAILING_STOP aunque el segundo cierre haya sido un gap grande
    assert record["exit_reason"] == "TRAILING_STOP"
    # precio ponderado entre el tramo de 120 (30%) y el resto a ~140-160 (70%, varios tramos)
    assert 100.0 < record["exit_price"] < 200.0


def test_consolidate_raises_assertion_if_position_still_open():
    pos = _make_long_position()
    with pytest.raises(AssertionError):
        consolidate_trade_record(pos)


def test_consolidate_exit_reason_is_trailing_stop_even_if_remainder_closes_by_max_holding():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=5)
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))  # dispara un tramo
    step_position_forward(pos, high=121.5, low=120.5, close=121.0, trade_date=pos.target_date)  # resto por max holding
    record = consolidate_trade_record(pos)
    assert record["exit_reason"] == "TRAILING_STOP"  # el trailing stop domina, aunque el resto cerrara por tiempo


# ---------------------------------------------------------------------------
# open_position / compute_target_date
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# compute_position_mtm_dollars
# ---------------------------------------------------------------------------


def test_mtm_fully_open_position_uses_current_price():
    pos = _make_long_position(entry_price=100.0)
    pos.position_size_dollars = 1000.0
    mtm = compute_position_mtm_dollars(pos, current_price=110.0)
    assert mtm == pytest.approx(1100.0)  # +10% sobre los $1000


def test_mtm_partially_closed_position_locks_in_closed_fraction_at_its_own_price():
    """El tramo ya cerrado no debe revalorizarse con el precio de hoy — se
    encontró esto como el motivo por el que la curva de equity sobreestimaría
    el riesgo restante de un trailing stop a mitad de camino (ver docstring
    de compute_position_mtm_dollars)."""
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=generate_trailing_stop_tiers())
    pos.position_size_dollars = 1000.0
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))  # cierra 30% a ~120
    assert pos.remaining_fraction == pytest.approx(0.70)

    # Precio de hoy cae a 90 — el 30% ya cerrado a 120 NO debe verse afectado.
    mtm = compute_position_mtm_dollars(pos, current_price=90.0)
    expected = 1000.0 * 0.30 * 1.20 + 1000.0 * 0.70 * 0.90
    assert mtm == pytest.approx(expected)


def test_open_position_conservative_sizing_and_thresholds():
    pos = open_position(
        event_id=1, version="CONSERVATIVE", execution_style="CONSERVATIVE", direction="LONG",
        ticker="TEST", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=5),
        balance_for_sizing=100_000.0, confidence=100.0, ev=0.01, prediction=0.8, had_survivorship_warning=False,
    )
    assert pos.position_size_pct == pytest.approx(5.0)  # confidence=100 -> tope de la banda
    assert pos.position_size_dollars == pytest.approx(5000.0)
    assert pos.take_profit_price == pytest.approx(102.0)
    assert pos.stop_loss_price == pytest.approx(98.5)


def test_open_position_balanced_uses_fixed_sizing_not_interpolated():
    pos = open_position(
        event_id=1, version="BALANCED", execution_style="AGGRESSIVE", direction="LONG",
        ticker="TEST", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=20),
        balance_for_sizing=100_000.0, confidence=99.0, ev=0.02, prediction=0.9, had_survivorship_warning=False,
    )
    assert pos.position_size_pct == pytest.approx(5.0)  # fijo, no interpola aunque confidence sea altísima


def test_compute_target_date_counts_trading_days_not_calendar_days():
    calendar = [D0 + timedelta(days=i) for i in range(0, 15) if (D0 + timedelta(days=i)).weekday() < 5]
    entry = calendar[0]
    target = compute_target_date(entry, holding_period_max_days=5, ticker_trading_dates=calendar)
    future_trading_days = [d for d in calendar if d > entry]
    assert target == future_trading_days[4]  # el 5º día de negociación posterior


def test_compute_target_date_falls_back_to_last_available_day_when_data_runs_out():
    calendar = [D0, D0 + timedelta(days=1), D0 + timedelta(days=2)]
    target = compute_target_date(D0, holding_period_max_days=20, ticker_trading_dates=calendar)
    assert target == calendar[-1]


# ---------------------------------------------------------------------------
# _build_entry_plan — el caso "entry_date es el último día de precio
# disponible" (bug real encontrado al probar el dashboard con datos
# sintéticos: generaba exit_date == entry_date, violación anti-look-ahead).
# ticker_cache se pre-llena a mano para no necesitar una conexión real (la
# función solo toca `conn` cuando el ticker no está ya en el cache).
# ---------------------------------------------------------------------------


def _bar(o=100.0, h=101.0, l=99.0, c=100.5, warn=False):
    return {"open_raw": o, "high_raw": h, "low_raw": l, "close_raw": c, "survivorship_warning": warn}


def test_build_entry_plan_skips_event_when_entry_is_last_available_price_day():
    """d0 es el penúltimo día del panel de precios -> entry_date (D+1) es el
    ÚLTIMO día disponible, sin ningún día posterior para simular la
    posición. Antes del fix, esto generaba target_date == entry_date y un
    cierre forzado el mismo día que la entrada — una violación
    anti-look-ahead real, no un caso de borde inofensivo."""
    d0 = D0
    entry_date = D0 + timedelta(days=1)
    ticker_cache = {"EDGE": {d0: _bar(), entry_date: _bar()}}  # nada después de entry_date
    events = [
        {
            "event_id": 99, "ticker": "EDGE", "d0_close_date": d0,
            "trade_decision": "LONG", "ev_conservative": 0.01, "ev_aggressive": 0.01, "ev_balanced": 0.01,
            "confidence": 80.0, "prediction": 0.6,
        }
    ]
    plan = _build_entry_plan(conn=None, version="CONSERVATIVE", events=events, ticker_cache=ticker_cache)
    assert plan == []


def test_build_entry_plan_includes_event_when_at_least_one_day_after_entry_exists():
    d0 = D0
    entry_date = D0 + timedelta(days=1)
    one_more_day = D0 + timedelta(days=2)
    ticker_cache = {"OK": {d0: _bar(), entry_date: _bar(), one_more_day: _bar()}}
    events = [
        {
            "event_id": 100, "ticker": "OK", "d0_close_date": d0,
            "trade_decision": "LONG", "ev_conservative": 0.01, "ev_aggressive": 0.01, "ev_balanced": 0.01,
            "confidence": 80.0, "prediction": 0.6,
        }
    ]
    plan = _build_entry_plan(conn=None, version="CONSERVATIVE", events=events, ticker_cache=ticker_cache)
    assert len(plan) == 1
    assert plan[0]["entry_date"] == entry_date
    assert plan[0]["target_date"] > entry_date


# ---------------------------------------------------------------------------
# _resolve_forced_close — bug de auditoría: cierre forzado cuando la última
# fila de precios de un ticker es un centinela de survivorship_warning
# (close_raw=NULL). Ver "CIERRE FORZADO CON DATOS DE PRECIO INCOMPLETOS" en
# el docstring del módulo.
# ---------------------------------------------------------------------------


def test_resolve_forced_close_normal_case_unchanged_behavior():
    """Última fila con close_raw válido -> MAX_HOLDING a ese precio, EXACTO
    comportamiento de antes del fix (sin gap, nada cambia)."""
    entry_date = D0
    prices = {
        D0: _bar(c=100.0),
        D0 + timedelta(days=1): _bar(c=103.0),
        D0 + timedelta(days=2): _bar(c=105.0),
    }
    exit_date, exit_price, exit_reason = _resolve_forced_close(prices, entry_date, entry_price=100.0)
    assert exit_date == D0 + timedelta(days=2)
    assert exit_price == 105.0
    assert exit_reason == "MAX_HOLDING"


def test_resolve_forced_close_full_gap_sentinel_used_to_crash():
    """Reproduce el bug tal cual: la ÚLTIMA fila es un centinela de
    survivorship_warning con close_raw=NULL (yfinance_backfill.py:
    _flag_full_gap) — antes del fix, float(None) reventaba aquí."""
    entry_date = D0
    prices = {
        D0: _bar(c=100.0),
        D0 + timedelta(days=1): _bar(c=102.0),
        D0 + timedelta(days=2): {"open_raw": None, "high_raw": None, "low_raw": None, "close_raw": None, "survivorship_warning": True},
    }
    exit_date, exit_price, exit_reason = _resolve_forced_close(prices, entry_date, entry_price=100.0)
    # Cierra en la última fecha CON precio válido, no en la fecha del centinela.
    assert exit_date == D0 + timedelta(days=1)
    assert exit_price == 102.0
    assert exit_reason == "DATA_GAP"


def test_resolve_forced_close_no_valid_price_after_entry_falls_back_to_entry_price():
    """Caso extremo: el ticker se deslista al día siguiente de la entrada —
    NINGUNA fecha posterior a entry_date tiene precio válido. Cierra en la
    última fecha disponible (sigue siendo > entry_date) al precio de
    ENTRADA — retorno plano, no un precio inventado."""
    entry_date = D0
    only_gap_day = D0 + timedelta(days=1)
    prices = {
        D0: _bar(c=100.0),
        only_gap_day: {"open_raw": None, "high_raw": None, "low_raw": None, "close_raw": None, "survivorship_warning": True},
    }
    exit_date, exit_price, exit_reason = _resolve_forced_close(prices, entry_date, entry_price=100.0)
    assert exit_date == only_gap_day  # > entry_date, nunca None ni <= entry_date
    assert exit_price == 100.0  # precio de entrada, retorno plano
    assert exit_reason == "DATA_GAP"


def test_resolve_forced_close_skips_intermediate_gap_to_find_valid_price():
    """El gap está en medio, no al final: la última fila SÍ tiene precio
    válido -> MAX_HOLDING normal, el gap intermedio no afecta (ya lo maneja
    el bucle día a día de simulate_portfolio, no esta función)."""
    entry_date = D0
    prices = {
        D0: _bar(c=100.0),
        D0 + timedelta(days=1): {"open_raw": None, "high_raw": None, "low_raw": None, "close_raw": None, "survivorship_warning": True},
        D0 + timedelta(days=2): _bar(c=110.0),
    }
    exit_date, exit_price, exit_reason = _resolve_forced_close(prices, entry_date, entry_price=100.0)
    assert exit_date == D0 + timedelta(days=2)
    assert exit_price == 110.0
    assert exit_reason == "MAX_HOLDING"


# ---------------------------------------------------------------------------
# is_circuit_breaker_active — circuit-breaker de drawdown de cartera
# (hallazgo de auditoría, prioridad máxima: protección de capital). Ver
# DRAWDOWN_CIRCUIT_BREAKER_PCT en el módulo para el razonamiento de diseño
# (por qué 15%, por qué solo bloquea entradas, por qué la recuperación es
# automática).
# ---------------------------------------------------------------------------


def test_circuit_breaker_inactive_when_no_drawdown():
    assert is_circuit_breaker_active(peak_equity=100_000.0, current_equity=100_000.0) is False


def test_circuit_breaker_inactive_below_threshold():
    # -14.9% de drawdown, por debajo del 15% por defecto.
    assert is_circuit_breaker_active(peak_equity=100_000.0, current_equity=85_100.0) is False


def test_circuit_breaker_active_exactly_at_threshold():
    # >= threshold, no > — el umbral en sí ya dispara el breaker.
    assert is_circuit_breaker_active(peak_equity=100_000.0, current_equity=85_000.0) is True


def test_circuit_breaker_active_above_threshold():
    assert is_circuit_breaker_active(peak_equity=100_000.0, current_equity=70_000.0) is True


def test_circuit_breaker_respects_custom_threshold_override():
    """simulate_portfolio() puede pasar un circuit_breaker_pct distinto del
    default de producción (ver su docstring) — confirma que un umbral más
    laxo (30%) NO dispara donde el default (15%) sí lo haría."""
    assert is_circuit_breaker_active(peak_equity=100_000.0, current_equity=70_000.0, threshold=0.50) is False
    assert is_circuit_breaker_active(peak_equity=100_000.0, current_equity=70_000.0, threshold=DRAWDOWN_CIRCUIT_BREAKER_PCT) is True


def test_circuit_breaker_degenerate_zero_peak_is_treated_as_active():
    """peak_equity<=0 (cartera ya en cero o negativa) es un estado
    degenerado — se trata como breaker activo en vez de dividir por cero
    silenciosamente."""
    assert is_circuit_breaker_active(peak_equity=0.0, current_equity=0.0) is True
    assert is_circuit_breaker_active(peak_equity=-100.0, current_equity=-50.0) is True


# ---------------------------------------------------------------------------
# compute_trailing_adv_usd / cap_position_dollars_by_adv — tope de posición
# por %ADV (hallazgo de auditoría). Ver MAX_POSITION_PCT_OF_ADV en el módulo.
# ---------------------------------------------------------------------------


def _bar_with_volume(c=100.0, v=10_000):
    return {"close_raw": c, "volume": v}


def test_trailing_adv_usd_averages_the_window_before_as_of():
    as_of = D0 + timedelta(days=100)
    prices = {as_of - timedelta(days=i): _bar_with_volume(c=100.0, v=10_000) for i in range(1, 61)}
    # ADV esperado: 100.0 * 10_000 = 1_000_000 exacto (todas las filas iguales)
    adv = compute_trailing_adv_usd(prices, as_of)
    assert adv == pytest.approx(1_000_000.0)


def test_trailing_adv_usd_excludes_the_entry_day_itself():
    """El volumen del propio día de entrada no se conoce hasta el cierre, y
    la entrada es a la apertura — usarlo sería look-ahead. Una fila en
    as_of con un volumen disparatado no debe mover el ADV."""
    as_of = D0 + timedelta(days=100)
    prices = {as_of - timedelta(days=i): _bar_with_volume(c=100.0, v=10_000) for i in range(1, 61)}
    prices[as_of] = _bar_with_volume(c=100.0, v=999_999_999)  # NUNCA debe contar
    adv = compute_trailing_adv_usd(prices, as_of)
    assert adv == pytest.approx(1_000_000.0)


def test_trailing_adv_usd_ignores_gap_sentinel_rows():
    """Filas de survivorship_warning (close_raw/volume NULL) no cuentan como
    día con datos — ni suman al promedio ni rellenan la ventana."""
    as_of = D0 + timedelta(days=100)
    prices = {as_of - timedelta(days=i): _bar_with_volume(c=100.0, v=10_000) for i in range(1, 61)}
    # Sustituye 5 días por centinelas de gap — deben ignorarse, no promediarse como 0.
    for i in range(1, 6):
        prices[as_of - timedelta(days=i)] = {"close_raw": None, "volume": None}
    adv = compute_trailing_adv_usd(prices, as_of)
    assert adv == pytest.approx(1_000_000.0)  # los 55 días restantes, todos iguales


def test_trailing_adv_usd_none_when_insufficient_history():
    as_of = D0 + timedelta(days=100)
    prices = {as_of - timedelta(days=i): _bar_with_volume() for i in range(1, 5)}  # solo 4 días
    assert compute_trailing_adv_usd(prices, as_of) is None


def test_trailing_adv_usd_none_when_no_history_at_all():
    assert compute_trailing_adv_usd({}, D0) is None


def test_max_position_pct_of_adv_matches_decided_value():
    assert MAX_POSITION_PCT_OF_ADV == pytest.approx(0.05)


def test_cap_position_dollars_by_adv_no_cap_when_adv_unknown():
    """None (sin datos suficientes) = no se aplica ningún tope — este
    mecanismo AÑADE una restricción, no sustituye al filtro de liquidez de
    entrada (otro punto de la auditoría, no este)."""
    dollars, capped = cap_position_dollars_by_adv(desired_dollars=50_000.0, adv_usd=None)
    assert dollars == 50_000.0
    assert capped is False


def test_cap_position_dollars_by_adv_reduces_when_desired_exceeds_cap():
    # ADV=$1M, tope 5% = $50k. Pedido $80k -> se reduce a $50k, nunca se descarta.
    dollars, capped = cap_position_dollars_by_adv(desired_dollars=80_000.0, adv_usd=1_000_000.0)
    assert dollars == pytest.approx(50_000.0)
    assert capped is True


def test_cap_position_dollars_by_adv_untouched_when_under_cap():
    dollars, capped = cap_position_dollars_by_adv(desired_dollars=20_000.0, adv_usd=1_000_000.0)
    assert dollars == 20_000.0
    assert capped is False


def test_cap_position_dollars_by_adv_respects_custom_max_pct():
    # Tope al 10% de un ADV de $1M = $100k. Pedido $80k queda por debajo,
    # así que NO se reduce (a diferencia del tope por defecto del 5% = $50k,
    # que sí lo habría recortado — ver test de arriba).
    dollars, capped = cap_position_dollars_by_adv(desired_dollars=80_000.0, adv_usd=1_000_000.0, max_pct=0.10)
    assert dollars == 80_000.0
    assert capped is False


def test_open_position_reduces_size_and_flags_when_adv_cap_applies():
    """open_position() debe aplicar el tope de extremo a extremo: tamaño
    final reducido, position_size_pct recalculado sobre el tamaño REAL (no
    el pre-tope), y had_adv_cap_applied=True."""
    pos = open_position(
        event_id=1, version="AGGRESSIVE", execution_style="AGGRESSIVE", direction="LONG",
        ticker="ILLIQUID", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=20),
        balance_for_sizing=1_000_000.0,  # 20% pedido = $200k
        confidence=100.0, ev=0.01, prediction=0.7, had_survivorship_warning=False,
        adv_usd_60d=500_000.0,  # tope 5% = $25k, muy por debajo de los $200k pedidos
    )
    assert pos.had_adv_cap_applied is True
    assert pos.position_size_dollars == pytest.approx(25_000.0)
    assert pos.position_size_pct == pytest.approx(2.5)  # 25k / 1M * 100


def test_open_position_untouched_when_adv_is_none_or_generous():
    pos_no_adv = open_position(
        event_id=1, version="AGGRESSIVE", execution_style="AGGRESSIVE", direction="LONG",
        ticker="X", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=20),
        balance_for_sizing=1_000_000.0, confidence=100.0, ev=0.01, prediction=0.7,
        had_survivorship_warning=False, adv_usd_60d=None,
    )
    assert pos_no_adv.had_adv_cap_applied is False
    assert pos_no_adv.position_size_dollars == pytest.approx(200_000.0)  # 20% sin tocar

    pos_liquid = open_position(
        event_id=2, version="AGGRESSIVE", execution_style="AGGRESSIVE", direction="LONG",
        ticker="LIQUID", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=20),
        balance_for_sizing=1_000_000.0, confidence=100.0, ev=0.01, prediction=0.7,
        had_survivorship_warning=False, adv_usd_60d=100_000_000.0,  # ADV enorme, tope 5%=$5M >> $200k pedidos
    )
    assert pos_liquid.had_adv_cap_applied is False
    assert pos_liquid.position_size_dollars == pytest.approx(200_000.0)
