"""test_decision.py — Fase 6 PARTE 6. generate_decision es puro."""

from pipeline.validation.decision import generate_decision


def test_greenlight_when_all_conditions_pass():
    result = generate_decision(
        win_rate=0.60, sharpe=1.5, calibration_score=0.75, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] == "A"
    assert "GREENLIGHT" in result["label"]


def test_redlight_on_low_sharpe_even_if_other_metrics_good():
    result = generate_decision(
        win_rate=0.60, sharpe=0.5, calibration_score=0.75, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] == "C"
    assert "REDLIGHT" in result["label"]


def test_redlight_on_low_win_rate():
    result = generate_decision(
        win_rate=0.40, sharpe=1.5, calibration_score=0.75, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] == "C"


def test_redlight_on_low_calibration():
    result = generate_decision(
        win_rate=0.60, sharpe=1.5, calibration_score=0.30, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] == "C"


def test_redlight_always_wins_on_lookahead_violations_even_with_great_metrics():
    result = generate_decision(
        win_rate=0.90, sharpe=3.0, calibration_score=0.95, max_drawdown=0.02,
        n_trades=1000, walk_forward_passed=True, no_lookahead_violations=["algo violó anti-look-ahead"],
    )
    assert result["option"] == "C"
    assert "anti-look-ahead" in result["reasons"][0]


def test_yellowlight_when_neither_green_nor_red():
    # Métricas decentes (no dispara REDLIGHT) pero n insuficiente (no cumple GREENLIGHT).
    result = generate_decision(
        win_rate=0.58, sharpe=1.2, calibration_score=0.65, max_drawdown=0.10,
        n_trades=50, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] == "B"
    assert "YELLOWLIGHT" in result["label"]


def test_missing_data_never_counts_as_passing_greenlight():
    # calibration_score=None no debe colar como "> 0.6".
    result = generate_decision(
        win_rate=0.60, sharpe=1.5, calibration_score=None, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] != "A"


def test_walk_forward_none_blocks_greenlight():
    result = generate_decision(
        win_rate=0.60, sharpe=1.5, calibration_score=0.75, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=None, no_lookahead_violations=[],
    )
    assert result["option"] != "A"


def test_greenlight_boundary_is_strict_greater_than_not_equal():
    # win_rate exactamente en el umbral (0.55) no debe pasar ("> 0.55", no ">=").
    result = generate_decision(
        win_rate=0.55, sharpe=1.5, calibration_score=0.75, max_drawdown=0.10,
        n_trades=350, walk_forward_passed=True, no_lookahead_violations=[],
    )
    assert result["option"] != "A"


def test_sin_operaciones_no_hay_veredicto_positivo():
    """BUGS_REPORT.md H-40: con 0 operaciones el veredicto caía en
    YELLOWLIGHT ("condicionalmente viable"). Por debajo del mínimo, C."""
    from pipeline.validation.decision import MIN_TRADES_FOR_VERDICT, generate_decision

    for n in (0, MIN_TRADES_FOR_VERDICT - 1):
        d = generate_decision(None, None, None, None, n, None, [])
        assert d["option"] == "C" and "INSUFFICIENT" in d["label"]
        assert f"solo {n} operaciones" in d["reasons"][0]
    # Con muestra suficiente vuelve a evaluarse con las reglas de siempre.
    d = generate_decision(0.52, 0.9, 0.5, 0.2, MIN_TRADES_FOR_VERDICT, None, [])
    assert d["option"] == "B"
