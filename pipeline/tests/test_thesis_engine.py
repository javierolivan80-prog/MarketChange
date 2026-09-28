"""test_thesis_engine.py — memoria de tesis. Todo puro (sin Postgres, sin
LLM): reconcile() es determinista por diseño (ver el docstring del módulo),
así que estos tests cubren toda la lógica de decisión sin ningún mock."""
from datetime import date

import pytest

from pipeline import config
from pipeline.backtest.thesis_engine import (
    BlindJudgment,
    ObjectiveMetrics,
    ThesisSnapshot,
    check_fulfilled,
    check_invalidated_by_event_class,
    check_invalidated_by_price,
    check_saturated,
    classify_blind_judgment_contradiction,
    compute_saturation_threshold_pct,
    compute_volume_ratio,
    default_invalidation_conditions,
    reconcile,
    resolve_holding_deadline,
    thesis_status_for_exit_reason,
)


def _thesis(**overrides) -> ThesisSnapshot:
    base = dict(
        thesis_id=1,
        ticker="ACME",
        direction="LONG",
        entry_price=100.0,
        rationale="LONG en ACME por evento 8K_1.01_MATERIAL_AGMT (net_conviction=+0.70, confidence=85%)",
        expected_move_pct=8.0,
        invalidation_conditions=default_invalidation_conditions("LONG", 20),
        created_at_date=date(2024, 1, 2),
    )
    base.update(overrides)
    return ThesisSnapshot(**base)


def _judgment(**overrides) -> BlindJudgment:
    base = dict(event_id=999, event_class="8K_8.01_OTHER", net_conviction=0.1, confidence_in_conviction=50.0)
    base.update(overrides)
    return BlindJudgment(**base)


def _metrics(**overrides) -> ObjectiveMetrics:
    base = dict(realized_move_pct=1.0, current_price=101.0, saturation_threshold_pct=None, volume_ratio=None)
    base.update(overrides)
    return ObjectiveMetrics(**base)


# --- check_fulfilled ---


def test_check_fulfilled_true_when_realized_meets_expected():
    assert check_fulfilled(realized_move_pct=8.0, expected_move_pct=8.0) is True
    assert check_fulfilled(realized_move_pct=9.0, expected_move_pct=8.0) is True


def test_check_fulfilled_false_when_below_expected():
    assert check_fulfilled(realized_move_pct=7.9, expected_move_pct=8.0) is False


def test_check_fulfilled_false_when_expected_is_degenerate_zero():
    """expected_move_pct<=0 (impact estimate sin análogos) nunca se declara
    cumplida por esta regla — no confundir "no sé cuánto esperar" con
    "cualquier movimiento ya basta"."""
    assert check_fulfilled(realized_move_pct=5.0, expected_move_pct=0.0) is False


# --- compute_saturation_threshold_pct ---


def test_saturation_threshold_none_below_min_analogues():
    assert compute_saturation_threshold_pct([1.0, 2.0], min_analogues=5) is None


def test_saturation_threshold_uses_median_times_multiple():
    analogues = [2.0, 4.0, 6.0, 8.0, 10.0]  # mediana = 6.0
    threshold = compute_saturation_threshold_pct(analogues, multiple=2.0, percentile=50, min_analogues=5)
    assert threshold == pytest.approx(12.0)


# --- compute_volume_ratio ---


def test_volume_ratio_none_without_todays_volume():
    prices = {date(2024, 1, 1): {"volume": 1000}}
    assert compute_volume_ratio(prices, date(2024, 1, 2), window_days=60, min_days=20) is None


def test_volume_ratio_none_with_insufficient_trailing_history():
    prices = {date(2024, 1, 5): {"volume": 5000}, date(2024, 1, 4): {"volume": 1000}}
    assert compute_volume_ratio(prices, date(2024, 1, 5), window_days=60, min_days=20) is None


def test_volume_ratio_hand_calculated():
    prices = {date(2024, 1, i): {"volume": 1000} for i in range(1, 21)}
    prices[date(2024, 1, 21)] = {"volume": 5000}
    ratio = compute_volume_ratio(prices, date(2024, 1, 21), window_days=60, min_days=20)
    assert ratio == pytest.approx(5.0)


# --- check_saturated ---


def test_check_saturated_by_price_component():
    assert check_saturated(realized_move_pct=15.0, saturation_threshold_pct=12.0, volume_ratio=None) is True


def test_check_saturated_by_volume_component_alone():
    assert check_saturated(realized_move_pct=1.0, saturation_threshold_pct=None, volume_ratio=config.THESIS_ABNORMAL_VOLUME_RATIO) is True


def test_check_saturated_false_when_move_is_negative_even_above_threshold_magnitude():
    """Un movimiento EN CONTRA no satura — eso es terreno de STOP_LOSS/INVALIDATED."""
    assert check_saturated(realized_move_pct=-15.0, saturation_threshold_pct=12.0, volume_ratio=None) is False


def test_check_saturated_false_when_neither_component_fires():
    assert check_saturated(realized_move_pct=1.0, saturation_threshold_pct=12.0, volume_ratio=1.2) is False


# --- check_invalidated_by_event_class ---


def test_invalidated_by_event_class_long_thesis():
    assert check_invalidated_by_event_class("LONG", "8K_1.03_BANKRUPTCY") is True
    assert check_invalidated_by_event_class("LONG", "8K_2.02_EARNINGS") is False


def test_invalidated_by_event_class_short_thesis():
    assert check_invalidated_by_event_class("SHORT", "FDA_APPROVAL") is True
    assert check_invalidated_by_event_class("SHORT", "8K_1.03_BANKRUPTCY") is False


# --- check_invalidated_by_price ---


def test_invalidated_by_price_below():
    assert check_invalidated_by_price("LONG", 49.0, {"price_below": 50.0, "price_above": None}) is True
    assert check_invalidated_by_price("LONG", 51.0, {"price_below": 50.0, "price_above": None}) is False


def test_invalidated_by_price_above():
    assert check_invalidated_by_price("SHORT", 151.0, {"price_below": None, "price_above": 150.0}) is True


def test_invalidated_by_price_none_conditions_never_trigger():
    assert check_invalidated_by_price("LONG", 0.01, {"price_below": None, "price_above": None}) is False


# --- classify_blind_judgment_contradiction ---


def test_contradiction_strong_above_high_floor():
    assert classify_blind_judgment_contradiction("LONG", net_conviction=-0.5, confidence_in_conviction=70.0) == "STRONG"


def test_contradiction_mild_between_floors():
    assert classify_blind_judgment_contradiction("LONG", net_conviction=-0.5, confidence_in_conviction=45.0) == "MILD"


def test_contradiction_none_below_mild_floor():
    assert classify_blind_judgment_contradiction("LONG", net_conviction=-0.5, confidence_in_conviction=30.0) is None


def test_contradiction_none_when_judgment_agrees_with_direction():
    """net_conviction positivo con tesis LONG confirma, no contradice —
    aunque la confianza sea baja."""
    assert classify_blind_judgment_contradiction("LONG", net_conviction=0.5, confidence_in_conviction=90.0) is None


def test_contradiction_short_thesis_mirrors_long():
    assert classify_blind_judgment_contradiction("SHORT", net_conviction=0.5, confidence_in_conviction=70.0) == "STRONG"


# --- resolve_holding_deadline ---


def test_holding_deadline_thesis_stricter_wins_as_expired():
    strategy_target = date(2024, 2, 1)
    thesis_expiry = date(2024, 1, 20)
    d, reason = resolve_holding_deadline(strategy_target, thesis_expiry)
    assert d == thesis_expiry
    assert reason == "EXPIRED"


def test_holding_deadline_strategy_stricter_wins_as_max_holding():
    strategy_target = date(2024, 1, 10)
    thesis_expiry = date(2024, 2, 1)
    d, reason = resolve_holding_deadline(strategy_target, thesis_expiry)
    assert d == strategy_target
    assert reason == "MAX_HOLDING"


def test_holding_deadline_tie_goes_to_max_holding():
    same_date = date(2024, 1, 15)
    d, reason = resolve_holding_deadline(same_date, same_date)
    assert d == same_date
    assert reason == "MAX_HOLDING"


def test_holding_deadline_none_thesis_expiry_is_a_noop():
    strategy_target = date(2024, 1, 10)
    d, reason = resolve_holding_deadline(strategy_target, None)
    assert d == strategy_target
    assert reason == "MAX_HOLDING"


# --- thesis_status_for_exit_reason ---


@pytest.mark.parametrize(
    "exit_reason,expected_status",
    [
        ("FULFILLED", "fulfilled"),
        ("INVALIDATED", "invalidated"),
        ("SATURATED", "saturated"),
        ("EXPIRED", "expired"),
        ("STOP_LOSS", "closed_by_stop"),
        ("TAKE_PROFIT", "closed_by_stop"),
        ("TRAILING_STOP", "closed_by_stop"),
        ("MAX_HOLDING", "closed_by_stop"),
        ("DATA_GAP", "closed_by_stop"),
    ],
)
def test_thesis_status_for_exit_reason_mapping(exit_reason, expected_status):
    assert thesis_status_for_exit_reason(exit_reason) == expected_status


def test_thesis_status_for_exit_reason_rejects_unknown():
    with pytest.raises(AssertionError):
        thesis_status_for_exit_reason("SOMETHING_NEW")


# --- reconcile(): orden de prioridad completo, escenario a escenario ---


def test_reconcile_fulfilled_wins_even_with_contradicting_judgment():
    """Las condiciones objetivas ganan siempre — ver docstring del módulo."""
    thesis = _thesis(expected_move_pct=5.0)
    judgment = _judgment(net_conviction=-0.9, confidence_in_conviction=95.0)  # contradicción fuerte
    metrics = _metrics(realized_move_pct=6.0)  # ya cumplida
    result = reconcile(thesis, judgment, metrics)
    assert result.action == "SELL"
    assert result.reason_code == "FULFILLED"
    assert result.closes_thesis is True
    assert result.thesis_status_if_closed == "fulfilled"
    assert "2024-01-02" in result.rationale
    assert thesis.rationale in result.rationale


def test_reconcile_saturated_by_price_when_not_yet_fulfilled():
    thesis = _thesis(expected_move_pct=50.0)  # objetivo alto, no cumplido todavía
    judgment = _judgment()
    metrics = _metrics(realized_move_pct=20.0, saturation_threshold_pct=15.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.action == "SELL"
    assert result.reason_code == "SATURATED"
    assert result.thesis_status_if_closed == "saturated"


def test_reconcile_saturated_by_volume_only():
    thesis = _thesis(expected_move_pct=50.0)
    judgment = _judgment()
    metrics = _metrics(realized_move_pct=1.0, saturation_threshold_pct=None, volume_ratio=5.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.reason_code == "SATURATED"
    assert "volumen" in result.rationale


def test_reconcile_invalidated_by_opposite_event_class_beats_neutral_judgment():
    thesis = _thesis(direction="LONG", expected_move_pct=50.0)
    judgment = _judgment(event_class="8K_1.03_BANKRUPTCY", net_conviction=0.0, confidence_in_conviction=10.0)
    metrics = _metrics(realized_move_pct=1.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.action == "SELL"
    assert result.reason_code == "INVALIDATED_EVENT_CLASS"
    assert result.thesis_status_if_closed == "invalidated"


def test_reconcile_invalidated_by_price_level():
    thesis = _thesis(
        direction="LONG",
        expected_move_pct=50.0,
        invalidation_conditions={"price_below": 90.0, "price_above": None, "opposite_event_classes": [], "max_holding_days": 20},
    )
    judgment = _judgment()
    metrics = _metrics(realized_move_pct=-5.0, current_price=85.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.reason_code == "INVALIDATED_PRICE"


def test_reconcile_strong_blind_contradiction_sells():
    thesis = _thesis(expected_move_pct=50.0)
    judgment = _judgment(event_class="8K_8.01_OTHER", net_conviction=-0.8, confidence_in_conviction=75.0)
    metrics = _metrics(realized_move_pct=1.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.action == "SELL"
    assert result.reason_code == "INVALIDATED_BLIND_JUDGMENT"
    assert result.thesis_status_if_closed == "invalidated"


def test_reconcile_mild_blind_contradiction_reduces_not_sells():
    thesis = _thesis(expected_move_pct=50.0)
    judgment = _judgment(event_class="8K_8.01_OTHER", net_conviction=-0.5, confidence_in_conviction=45.0)
    metrics = _metrics(realized_move_pct=1.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.action == "REDUCE"
    assert result.reason_code == "WEAK_CONTRADICTION"
    assert result.closes_thesis is False
    assert result.reduce_fraction == config.THESIS_REDUCE_FRACTION


def test_reconcile_hold_when_nothing_fires():
    thesis = _thesis(expected_move_pct=50.0)
    judgment = _judgment(event_class="8K_8.01_OTHER", net_conviction=0.05, confidence_in_conviction=20.0)
    metrics = _metrics(realized_move_pct=1.0)
    result = reconcile(thesis, judgment, metrics)
    assert result.action == "HOLD"
    assert result.reason_code == "NO_CHANGE"
    assert result.closes_thesis is False


def test_reconcile_never_calls_any_llm_client():
    """reconcile() es puramente determinista — no acepta ni necesita ningún
    cliente de IA. Esta prueba documenta la garantía en forma de test: la
    firma de la función solo toma los 3 dataclasses de datos."""
    import inspect

    params = list(inspect.signature(reconcile).parameters)
    assert params == ["thesis", "blind_judgment", "metrics"]
