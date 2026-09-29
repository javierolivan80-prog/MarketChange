"""test_abstention_engine.py — Etapa 8. Cada una de las 7 reglas del spec
probada de forma aislada (todas las demás condiciones "limpias"), más los
casos combinados que importan: la divergencia entre versiones de estrategia,
y el orden determinista de reason_if_no_trade cuando varias reglas disparan
a la vez.
"""
import pytest

from pipeline.analyze.abstention_engine import (
    CONFIDENCE_FLOOR,
    LIQUIDITY_ADV_FLOOR_USD,
    NOVELTY_FLOOR,
    SPREAD_CEILING_PCT,
    AbstentionInputs,
    decide_all_strategies,
    decide_for_strategy,
    objective_no_trade_reason,
)
from pipeline.analyze.ev_engine import EV_THRESHOLDS


def _clean_inputs(**overrides) -> AbstentionInputs:
    """Inputs que NO disparan ninguna de las 7 reglas — cada test override
    exactamente el campo que quiere probar."""
    base = dict(
        novelty_score=80.0,
        confidence_in_conviction=90.0,
        net_conviction=0.7,
        ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": 0.05, "AGGRESSIVE": 0.05},  # muy por encima de todos los umbrales
        had_survivorship_warning=False,
        beta_available=True,
        high_low_range_pct=0.1,
        adv_usd_60d=LIQUIDITY_ADV_FLOOR_USD * 10,  # muy por encima del suelo
        is_fda_crl_without_8k=False,
    )
    base.update(overrides)
    return AbstentionInputs(**base)


def test_clean_inputs_produce_a_trade_not_abstention():
    decision = decide_for_strategy(_clean_inputs(), "BALANCED")
    assert decision.trade_decision in ("LONG", "SHORT")
    assert decision.reason_if_no_trade is None


# --- Las 7 reglas, cada una aislada ---


def test_rule_1_novelty_below_floor():
    decision = decide_for_strategy(_clean_inputs(novelty_score=NOVELTY_FLOOR - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "novelty_score" in decision.reason_if_no_trade


def test_rule_1_novelty_at_floor_does_not_trigger():
    """Boundary: exactamente en el floor NO debe disparar (spec: '< 20', no '<= 20')."""
    decision = decide_for_strategy(_clean_inputs(novelty_score=NOVELTY_FLOOR), "BALANCED")
    assert decision.trade_decision != "NO_TRADE" or "novelty_score" not in (decision.reason_if_no_trade or "")


def test_rule_2_confidence_below_floor():
    decision = decide_for_strategy(_clean_inputs(confidence_in_conviction=CONFIDENCE_FLOOR - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "confidence_in_conviction" in decision.reason_if_no_trade


def test_rule_3_ev_below_buffered_threshold_per_strategy():
    # EV justo por encima del umbral crudo de Balanced pero por debajo del
    # umbral + 50bps de buffer -> debe abstenerse.
    just_above_raw = EV_THRESHOLDS["BALANCED"] + 0.0010  # +10bps sobre el umbral crudo, insuficiente con el buffer de 50bps
    decision = decide_for_strategy(_clean_inputs(ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": just_above_raw, "AGGRESSIVE": 0.05}), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "|ev|=" in decision.reason_if_no_trade


def test_rule_3_ev_above_buffered_threshold_trades():
    comfortably_above = EV_THRESHOLDS["BALANCED"] + 0.0200
    decision = decide_for_strategy(_clean_inputs(ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": comfortably_above, "AGGRESSIVE": 0.05}), "BALANCED")
    assert decision.trade_decision in ("LONG", "SHORT")


def test_rule_3_un_ev_muy_negativo_de_magnitud_suficiente_no_se_abstiene():
    """REGRESIÓN (IMPROVEMENT_PLAN.md R16): ev_engine.compute_ev da al EV el
    SIGNO de net_conviction (negativo para una convicción bajista/SHORT).
    Comparar ese ev con signo contra un umbral siempre positivo vetaba TODO
    SHORT sin importar la convicción — un ev muy negativo nunca superaba un
    umbral positivo. La regla 3 debe mirar la MAGNITUD del EV en la
    dirección que se tomaría, no su signo."""
    ev_negativo_grande = -(EV_THRESHOLDS["BALANCED"] + 0.0200)
    decision = decide_for_strategy(
        _clean_inputs(
            net_conviction=-0.8,
            ev_by_strategy={"CONSERVATIVE": ev_negativo_grande, "BALANCED": ev_negativo_grande, "AGGRESSIVE": ev_negativo_grande},
        ),
        "BALANCED",
    )
    assert decision.trade_decision == "SHORT"


def test_rule_3_un_ev_negativo_pero_pequeno_si_se_abstiene():
    """El signo no debe volverse irrelevante del todo: un EV negativo cuya
    MAGNITUD no supera el umbral (SHORT débil) sigue siendo NO_TRADE, igual
    que un LONG débil."""
    ev_negativo_pequeno = -(EV_THRESHOLDS["BALANCED"] + 0.0010)  # magnitud insuficiente con el buffer
    decision = decide_for_strategy(
        _clean_inputs(
            net_conviction=-0.1,
            ev_by_strategy={"CONSERVATIVE": ev_negativo_pequeno, "BALANCED": ev_negativo_pequeno, "AGGRESSIVE": ev_negativo_pequeno},
        ),
        "BALANCED",
    )
    assert decision.trade_decision == "NO_TRADE"


def test_integracion_ev_engine_con_abstention_engine_permite_short_con_conviccion_fuerte():
    """Integración real de punta a punta (Judge -> compute_ev ->
    decide_for_strategy), no el ev_by_strategy fijo y desconectado de
    _clean_inputs: es justo el hueco de cobertura que dejó pasar R16 sin
    detectarse — el test unitario de 'SHORT' pasaba con un EV positivo que
    compute_ev jamás produciría para una convicción bajista."""
    from pipeline.analyze.ev_engine import compute_ev

    ev_result = compute_ev(
        net_conviction=-0.9,
        confidence_in_conviction=90.0,
        expected_magnitude_pct=5.0,
        impact_confidence=90.0,
    )
    assert ev_result.ev_balanced < 0  # compute_ev sí propaga el signo bajista

    decision = decide_for_strategy(
        _clean_inputs(
            net_conviction=-0.9,
            confidence_in_conviction=90.0,
            ev_by_strategy={
                "CONSERVATIVE": ev_result.ev_conservative,
                "BALANCED": ev_result.ev_balanced,
                "AGGRESSIVE": ev_result.ev_aggressive,
            },
        ),
        "BALANCED",
    )
    assert decision.trade_decision == "SHORT"


def test_rule_4_survivorship_warning():
    decision = decide_for_strategy(_clean_inputs(had_survivorship_warning=True), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "deslistado" in decision.reason_if_no_trade


def test_rule_5_contradictory_data_factor_model_unavailable():
    decision = decide_for_strategy(_clean_inputs(beta_available=False), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "contradictorios" in decision.reason_if_no_trade


def test_rule_5_contradictory_data_judge_split_decision():
    decision = decide_for_strategy(_clean_inputs(net_conviction=0.05, confidence_in_conviction=75), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "contradictorios" in decision.reason_if_no_trade


def test_rule_5_low_conviction_alone_is_not_contradictory_if_confidence_also_low():
    """Confirma que la regla 5 exige AMBAS condiciones (conviction baja Y
    confidence alta) — conviction baja con confidence también baja debe
    disparar la regla 2 (confidence floor), no la 5."""
    decision = decide_for_strategy(_clean_inputs(net_conviction=0.05, confidence_in_conviction=CONFIDENCE_FLOOR - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "confidence_in_conviction" in decision.reason_if_no_trade  # regla 2, no regla 5


def test_rule_6_fda_crl_without_8k():
    decision = decide_for_strategy(_clean_inputs(is_fda_crl_without_8k=True), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "CRL" in decision.reason_if_no_trade


def test_rule_7_spread_proxy_above_ceiling():
    decision = decide_for_strategy(_clean_inputs(high_low_range_pct=SPREAD_CEILING_PCT + 0.1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "ilíquido" in decision.reason_if_no_trade


def test_rule_7_missing_spread_data_abstains_rather_than_assumes_liquid():
    """None no debe interpretarse como 'sin problema' — la ausencia de dato
    es en sí misma un motivo de cautela, no se asume liquidez."""
    decision = decide_for_strategy(_clean_inputs(high_low_range_pct=None), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"


def test_rule_7_adv_proxy_below_floor():
    """Segundo componente del proxy de liquidez (hallazgo de auditoría):
    spread limpio pero ADV por debajo del suelo -> NO_TRADE igual."""
    decision = decide_for_strategy(_clean_inputs(adv_usd_60d=LIQUIDITY_ADV_FLOOR_USD - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "ADV" in decision.reason_if_no_trade
    assert "ilíquido" in decision.reason_if_no_trade


def test_rule_7_adv_at_floor_does_not_trigger():
    """Boundary: exactamente en el suelo NO debe disparar (misma convención
    '< suelo', no '<= suelo', que el resto de umbrales del módulo)."""
    decision = decide_for_strategy(_clean_inputs(adv_usd_60d=LIQUIDITY_ADV_FLOOR_USD), "BALANCED")
    assert decision.trade_decision != "NO_TRADE"


def test_rule_7_missing_adv_data_abstains_rather_than_assumes_liquid():
    """Igual que con high_low_range_pct=None: sin dato de ADV, cautela, no
    se asume liquidez."""
    decision = decide_for_strategy(_clean_inputs(adv_usd_60d=None), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "ADV" in decision.reason_if_no_trade


def test_rule_7_bad_spread_reported_before_checking_adv():
    """Cuando AMBOS componentes fallan, se reporta el spread primero (mismo
    principio de orden determinista que el resto del módulo — ver docstring
    de decide_for_strategy)."""
    decision = decide_for_strategy(
        _clean_inputs(high_low_range_pct=SPREAD_CEILING_PCT + 0.1, adv_usd_60d=LIQUIDITY_ADV_FLOOR_USD - 1), "BALANCED"
    )
    assert decision.trade_decision == "NO_TRADE"
    assert "spread" in decision.reason_if_no_trade
    assert "ADV" not in decision.reason_if_no_trade


# --- Casos combinados ---


def test_strategies_can_diverge_on_the_same_event():
    """El mismo evento puede ser TRADE para Aggressive y NO_TRADE para
    Conservative — es el comportamiento correcto, no un bug."""
    inputs = _clean_inputs(
        ev_by_strategy={
            "CONSERVATIVE": EV_THRESHOLDS["CONSERVATIVE"] + 0.0010,  # no supera el buffer
            "BALANCED": EV_THRESHOLDS["BALANCED"] + 0.0010,
            "AGGRESSIVE": EV_THRESHOLDS["AGGRESSIVE"] + 0.0200,  # sí supera el buffer con margen
        }
    )
    decisions = decide_all_strategies(inputs)
    assert decisions["CONSERVATIVE"].trade_decision == "NO_TRADE"
    assert decisions["AGGRESSIVE"].trade_decision in ("LONG", "SHORT")


def test_short_direction_when_net_conviction_negative():
    decision = decide_for_strategy(_clean_inputs(net_conviction=-0.6), "BALANCED")
    assert decision.trade_decision == "SHORT"


@pytest.mark.parametrize("strategy", ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"])
def test_all_three_strategies_are_covered_by_decide_all_strategies(strategy):
    decisions = decide_all_strategies(_clean_inputs())
    assert strategy in decisions


# ---------------------------------------------------------------------------
# objective_no_trade_reason — hallazgo de auditoría (IMPROVEMENT_PLAN.md R5):
# subconjunto de las 7 reglas que NO depende del Judge, usado por
# event_analysis_pipeline.py para saltarse Bull/Bear/Judge cuando el NO_TRADE
# ya está garantizado sin invocar a la IA.
# ---------------------------------------------------------------------------


def _clean_objective_kwargs(**overrides) -> dict:
    base = dict(
        novelty_score=80.0,
        had_survivorship_warning=False,
        beta_available=True,
        high_low_range_pct=0.1,
        adv_usd_60d=LIQUIDITY_ADV_FLOOR_USD * 10,
        is_fda_crl_without_8k=False,
    )
    base.update(overrides)
    return base


def test_objective_no_trade_reason_none_when_all_clean():
    assert objective_no_trade_reason(**_clean_objective_kwargs()) is None


def test_objective_novelty_below_floor():
    reason = objective_no_trade_reason(**_clean_objective_kwargs(novelty_score=NOVELTY_FLOOR - 1))
    assert reason is not None
    assert "novelty_score" in reason


def test_objective_survivorship_warning():
    reason = objective_no_trade_reason(**_clean_objective_kwargs(had_survivorship_warning=True))
    assert reason is not None
    assert "deslistado" in reason


def test_objective_beta_unavailable():
    reason = objective_no_trade_reason(**_clean_objective_kwargs(beta_available=False))
    assert reason is not None
    assert "contradictorios" in reason


def test_objective_fda_crl_without_8k():
    reason = objective_no_trade_reason(**_clean_objective_kwargs(is_fda_crl_without_8k=True))
    assert reason is not None
    assert "CRL" in reason


def test_objective_illiquid_spread():
    reason = objective_no_trade_reason(**_clean_objective_kwargs(high_low_range_pct=SPREAD_CEILING_PCT + 0.1))
    assert reason is not None
    assert "ilíquido" in reason


def test_objective_illiquid_adv():
    reason = objective_no_trade_reason(**_clean_objective_kwargs(adv_usd_60d=LIQUIDITY_ADV_FLOOR_USD - 1))
    assert reason is not None
    assert "ADV" in reason


def test_objective_judge_split_decision_is_not_covered_needs_judge():
    """La regla 5b (Judge dividido) SÍ necesita el Judge — esta función solo
    cubre las 5 condiciones objetivas, nunca la 2, la 3, ni la 5b. Con todo
    lo demás limpio, debe devolver None (hace falta invocar a la IA)."""
    assert objective_no_trade_reason(**_clean_objective_kwargs()) is None


def test_objective_reason_matches_decide_for_strategy_for_shared_rules():
    """Cuando objective_no_trade_reason dispara, decide_for_strategy() (con
    cualquier confidence/net_conviction/ev que sea, ya que estas condiciones
    ganan primero) debe reportar el MISMO motivo — ambas funciones no deben
    divergir nunca en las reglas que comparten."""
    kwargs = _clean_objective_kwargs(had_survivorship_warning=True)
    objective_reason = objective_no_trade_reason(**kwargs)
    full_inputs = _clean_inputs(had_survivorship_warning=True)
    full_decision = decide_for_strategy(full_inputs, "BALANCED")
    assert objective_reason == full_decision.reason_if_no_trade
