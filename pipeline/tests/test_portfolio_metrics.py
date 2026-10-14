"""test_portfolio_metrics.py — cada fórmula verificada contra un cálculo a
mano con números redondos, para poder confiar en que no hay un error de
signo o de denominador (el tipo de bug más fácil de colar en una fórmula
financiera y más difícil de notar a simple vista)."""
from datetime import date, timedelta

import pytest

from pipeline.backtest.portfolio_metrics import (
    CALIBRATION_TARGET,
    INSUFFICIENT_SAMPLE_THRESHOLD,
    MIN_TRADES_FOR_REGRESSION,
    bucket_by_confidence,
    compute_asymmetry_report,
    compute_calibration,
    compute_equity_metrics,
    compute_metrics_by_event_type,
    compute_prediction_regression,
    compute_trade_metrics,
    top_n_trades,
)

D0 = date(2024, 1, 2)


def _trade(pnl_pct, exit_date=None, event_class="8K_2.02_EARNINGS", ev=0.005, actual_move_pct=None):
    return {
        "pnl_pct": pnl_pct,
        "exit_date": exit_date or D0,
        "event_class": event_class,
        "ev": ev,
        "actual_move_pct": actual_move_pct if actual_move_pct is not None else pnl_pct,
    }


# ---------------------------------------------------------------------------
# compute_trade_metrics
# ---------------------------------------------------------------------------


def test_trade_metrics_empty_list():
    m = compute_trade_metrics([])
    assert m["total_trades"] == 0
    assert m["win_rate"] is None


def test_trade_metrics_hand_calculated_example():
    # 4 ganadores (+2,+4,+6,+8 => gross_profit=20, avg=5), 2 perdedores (-1,-3 => gross_loss=-4, avg=-2)
    trades = [_trade(p) for p in [2, 4, 6, 8, -1, -3]]
    m = compute_trade_metrics(trades)
    assert m["total_trades"] == 6
    assert m["winning_trades"] == 4
    assert m["losing_trades"] == 2
    assert m["win_rate"] == pytest.approx(4 / 6)
    assert m["profit_factor"] == pytest.approx(20 / 4)  # gross_profit / |gross_loss|
    assert m["avg_winner"] == pytest.approx(5.0)
    assert m["avg_loser"] == pytest.approx(-2.0)
    # expectancy = win_rate*avg_winner - (1-win_rate)*|avg_loser| = (4/6)*5 - (2/6)*2
    assert m["expectancy"] == pytest.approx((4 / 6) * 5 - (2 / 6) * 2)
    assert m["largest_win"] == 8
    assert m["largest_loss"] == -3


def test_trade_metrics_zero_pnl_counts_as_loser():
    trades = [_trade(0.0), _trade(5.0)]
    m = compute_trade_metrics(trades)
    assert m["winning_trades"] == 1
    assert m["losing_trades"] == 1


def test_trade_metrics_no_losers_gives_none_profit_factor_not_infinity():
    trades = [_trade(2), _trade(3)]
    m = compute_trade_metrics(trades)
    assert m["profit_factor"] is None  # no se inventa infinito
    assert m["avg_loser"] is None


def test_trade_metrics_consecutive_streaks_chronological():
    # En orden cronológico: W,W,W,L,L,W,L,L,L,L -> max win streak=3, max loss streak=4
    dates = [D0 + timedelta(days=i) for i in range(10)]
    pnls = [1, 1, 1, -1, -1, 1, -1, -1, -1, -1]
    trades = [_trade(p, exit_date=d) for p, d in zip(pnls, dates)]
    m = compute_trade_metrics(trades)
    assert m["consecutive_wins"] == 3
    assert m["consecutive_losses"] == 4


def test_trade_metrics_streaks_use_exit_date_order_not_input_order():
    """Si los trades llegan desordenados, las rachas deben calcularse por
    exit_date, no por el orden de la lista."""
    trades = [
        _trade(1, exit_date=D0 + timedelta(days=2)),
        _trade(-1, exit_date=D0),
        _trade(1, exit_date=D0 + timedelta(days=1)),
    ]
    m = compute_trade_metrics(trades)
    # Cronológico real: L, W, W -> racha de victorias = 2
    assert m["consecutive_wins"] == 2


# ---------------------------------------------------------------------------
# compute_equity_metrics
# ---------------------------------------------------------------------------


def test_equity_metrics_total_and_annual_return():
    curve = [
        {"trade_date": date(2024, 1, 1), "balance": 100_000.0},
        {"trade_date": date(2024, 1, 1) + timedelta(days=365), "balance": 110_000.0},
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["total_return"] == pytest.approx(0.10)
    assert m["annual_return"] == pytest.approx(0.10, abs=0.001)  # ~1 año exacto


def test_equity_metrics_max_drawdown_from_peak():
    curve = [
        {"trade_date": date(2024, 1, i + 1), "balance": b}
        for i, b in enumerate([100_000, 120_000, 90_000, 95_000, 130_000])
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    # Peor caída: de 120k a 90k = -25%
    assert m["max_drawdown"] == pytest.approx(0.25)


def test_equity_metrics_empty_curve_returns_all_none():
    m = compute_equity_metrics([], starting_capital=100_000.0)
    assert all(v is None for v in m.values())


def test_equity_metrics_sharpe_positive_for_steady_gains():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    balances = [100_000 * (1.001**i) for i in range(30)]  # crecimiento constante, sin volatilidad
    curve = [{"trade_date": d, "balance": b} for d, b in zip(dates, balances)]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["sharpe_ratio"] > 0


def test_equity_metrics_calmar_and_recovery_factor_use_drawdown():
    # >=MIN_DAYS_FOR_ANNUALIZATION para que annual_return no sea None aquí
    # (eso se prueba aparte, ver test_equity_metrics_curva_corta_no_anualiza).
    curve = [
        {"trade_date": date(2024, 1, 1), "balance": 100_000},
        {"trade_date": date(2024, 2, 1), "balance": 150_000},  # dd desde 150k a 120k = -20%
        {"trade_date": date(2024, 3, 1), "balance": 120_000},
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["annual_return"] is not None
    assert m["calmar_ratio"] == pytest.approx(m["annual_return"] / m["max_drawdown"])
    assert m["recovery_factor"] == pytest.approx(m["total_return"] / m["max_drawdown"])


def test_equity_metrics_curva_corta_no_anualiza():
    """REGRESIÓN (IMPROVEMENT_PLAN.md M20): con 2 días de curva, extrapolar
    linealmente a un año entero convertía un +5% en un annual_return de
    cientos de veces ese valor — un número que no informa de nada real.
    Por debajo de MIN_DAYS_FOR_ANNUALIZATION, annual_return (y calmar_ratio,
    que depende de él) deben ser None en vez de un número disparatado."""
    curve = [
        {"trade_date": date(2024, 1, 1), "balance": 100_000.0},
        {"trade_date": date(2024, 1, 2), "balance": 105_000.0},
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["total_return"] == pytest.approx(0.05)  # total_return SÍ se calcula, no es lo que explota
    assert m["annual_return"] is None
    assert m["calmar_ratio"] is None


def test_equity_metrics_justo_en_el_limite_de_dias_si_anualiza():
    from pipeline.backtest.portfolio_metrics import MIN_DAYS_FOR_ANNUALIZATION

    curve = [
        {"trade_date": date(2024, 1, 1), "balance": 100_000.0},
        {"trade_date": date(2024, 1, 1) + timedelta(days=MIN_DAYS_FOR_ANNUALIZATION), "balance": 105_000.0},
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["annual_return"] is not None


def test_equity_metrics_risk_free_rate_reduces_sharpe():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    balances = [100_000 * (1.001**i) for i in range(30)]
    curve = [{"trade_date": d, "balance": b} for d, b in zip(dates, balances)]
    rf = {d: 0.0015 for d in dates}  # tasa libre de riesgo MAYOR que el retorno diario de la cartera
    no_rf = compute_equity_metrics(curve, 100_000.0)
    with_rf = compute_equity_metrics(curve, 100_000.0, risk_free_daily=rf)
    assert with_rf["sharpe_ratio"] < no_rf["sharpe_ratio"]


# ---------------------------------------------------------------------------
# compute_metrics_by_event_type
# ---------------------------------------------------------------------------


def test_metrics_by_event_type_groups_correctly():
    trades = [_trade(5, event_class="A")] * 3 + [_trade(-2, event_class="B")] * 25
    result = compute_metrics_by_event_type(trades)
    assert result["A"]["n_trades"] == 3
    assert result["A"]["insufficient_sample"] is True  # 3 < 20
    assert result["B"]["n_trades"] == 25
    assert result["B"]["insufficient_sample"] is False


def test_metrics_by_event_type_threshold_boundary():
    trades = [_trade(1, event_class="X")] * INSUFFICIENT_SAMPLE_THRESHOLD
    result = compute_metrics_by_event_type(trades)
    assert result["X"]["insufficient_sample"] is False  # exactamente 20, no "< 20"


def test_metrics_by_event_type_hides_sharpe_when_sample_insufficient():
    """IMPROVEMENT_PLAN.md R12: sharpe_per_trade se OCULTA (None) por debajo
    de INSUFFICIENT_SAMPLE_THRESHOLD, no solo se flagea — antes se calculaba
    y mostraba un número con solo n>=2, aunque insufficient_sample ya
    dijera True."""
    trades = [_trade(p, event_class="A") for p in [5, -2, 3]]  # n=3, varianza>0, pero < 20
    result = compute_metrics_by_event_type(trades)
    assert result["A"]["insufficient_sample"] is True
    assert result["A"]["sharpe_per_trade"] is None
    # win_rate/avg_return SÍ se siguen mostrando: son directamente
    # interpretables sin necesitar un n grande para no ser engañosos.
    assert result["A"]["win_rate"] is not None
    assert result["A"]["avg_return"] is not None


def test_metrics_by_event_type_shows_sharpe_once_sample_is_sufficient():
    trades = [_trade(p % 5 - 2, event_class="B") for p in range(INSUFFICIENT_SAMPLE_THRESHOLD)]
    result = compute_metrics_by_event_type(trades)
    assert result["B"]["insufficient_sample"] is False
    assert result["B"]["sharpe_per_trade"] is not None


# ---------------------------------------------------------------------------
# compute_asymmetry_report
# ---------------------------------------------------------------------------


def test_asymmetry_report_detects_favoring_gains():
    trades = [_trade(0, actual_move_pct=m) for m in [4, 5, 6, -1, -2]]  # 3/5 llegan a +3%, 0/5 a -3%
    report = compute_asymmetry_report(trades, threshold_pct=3.0)
    assert report["pct_reaching_positive_threshold"] == pytest.approx(60.0)
    assert report["pct_reaching_negative_threshold"] == pytest.approx(0.0)
    assert report["asymmetric_favoring_gains"] is True


def test_asymmetry_report_empty_trades():
    report = compute_asymmetry_report([])
    assert report["n_trades"] == 0
    assert report["asymmetric_favoring_gains"] is None


# ---------------------------------------------------------------------------
# compute_calibration
# ---------------------------------------------------------------------------


def test_calibration_perfect_prediction_gives_score_one():
    trades = [_trade(0, ev=0.005, actual_move_pct=0.5)] * 10  # predicho 0.5%, real 0.5% exacto
    result = compute_calibration(trades)
    assert result["calibration_score"] == pytest.approx(1.0)
    assert result["meets_target"] is True


def test_calibration_way_off_gives_low_or_negative_score():
    trades = [_trade(0, ev=0.005, actual_move_pct=-5.0)] * 10  # predijo +0.5%, pasó -5%
    result = compute_calibration(trades)
    assert result["calibration_score"] < CALIBRATION_TARGET
    assert result["meets_target"] is False


def test_calibration_undefined_when_predicted_is_zero():
    trades = [_trade(0, ev=0.0, actual_move_pct=1.0)]
    result = compute_calibration(trades)
    assert result["calibration_score"] is None


def test_calibration_empty_trades():
    result = compute_calibration([])
    assert result["n_trades"] == 0
    assert result["calibration_score"] is None


# ---------------------------------------------------------------------------
# compute_prediction_regression / top_n_trades
# ---------------------------------------------------------------------------


def test_prediction_regression_perfect_linear_relationship_gives_r_squared_one():
    trades = [_trade(0, ev=e, actual_move_pct=e * 100 * 2) for e in [0.001, 0.002, 0.003, 0.004, 0.005]]
    result = compute_prediction_regression(trades)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)


def test_prediction_regression_no_relationship_gives_low_r_squared():
    trades = [_trade(0, ev=0.001, actual_move_pct=5), _trade(0, ev=0.002, actual_move_pct=-5),
              _trade(0, ev=0.003, actual_move_pct=5), _trade(0, ev=0.004, actual_move_pct=-5)]
    result = compute_prediction_regression(trades)
    assert result["r_squared"] < 0.5


def test_prediction_regression_scatter_has_one_point_per_trade():
    trades = [_trade(0, ev=0.001, actual_move_pct=1), _trade(0, ev=0.002, actual_move_pct=2)]
    result = compute_prediction_regression(trades)
    assert len(result["scatter"]) == 2


def test_prediction_regression_hides_r_squared_with_exactly_two_trades():
    """IMPROVEMENT_PLAN.md R12: con 2 puntos, una recta de un solo predictor
    pasa SIEMPRE exactamente por ambos (0 grados de libertad) — r_squared=1.0
    es una certeza matemática, no una señal real, y antes se devolvía sin
    ningún aviso. scatter SÍ se sigue devolviendo: ver los 2 puntos nunca es
    engañoso, resumirlos en un R² sí lo es."""
    trades = [_trade(0, ev=0.001, actual_move_pct=1), _trade(0, ev=0.002, actual_move_pct=2)]
    result = compute_prediction_regression(trades)
    assert result["r_squared"] is None
    assert len(result["scatter"]) == 2
    assert result["insufficient_sample"] is True


def test_prediction_regression_shows_r_squared_from_min_trades_onward():
    assert MIN_TRADES_FOR_REGRESSION == 3  # documenta el umbral que fija el test
    trades = [_trade(0, ev=e, actual_move_pct=e * 100 * 2) for e in [0.001, 0.002, 0.003]]
    result = compute_prediction_regression(trades)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert result["insufficient_sample"] is True  # 3 < 20: se calcula, pero se marca pequeño


def test_top_n_trades_winners_and_losers():
    trades = [_trade(p) for p in [5, -3, 10, -8, 1, -1]]
    top_winners = top_n_trades(trades, n=2, winners=True)
    top_losers = top_n_trades(trades, n=2, winners=False)
    assert [t["pnl_pct"] for t in top_winners] == [10, 5]
    assert [t["pnl_pct"] for t in top_losers] == [-8, -3]


# ---------------------------------------------------------------------------
# compute_bias_report — contra Postgres real
# ---------------------------------------------------------------------------

import os

import pytest as _pytest


@_pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
def test_bias_report_against_real_postgres():
    from pipeline.backtest.portfolio_metrics import compute_bias_report
    from pipeline.db.connection import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE portfolio_trades, portfolio_equity_curve, car_results, backtest_runs, "
            "event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
            "RESTART IDENTITY CASCADE"
        )
        # 4 tickers: 1 deslistado, 3 no.
        for i in range(4):
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date, is_delisted_flag) "
                "VALUES (%s,%s,'X','2024-01-01','2024-01-01',%s)",
                (str(i), f"T{i}", i == 0),
            )
        # 10 filas de precio: 2 huecos reales (marca sin precio) y una marca
        # sobre una vela con precio, que no es un hueco (Tanda 10).
        for i in range(10):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, close_raw, survivorship_warning) "
                "VALUES ('T0', %s, %s, %s)",
                (date(2024, 1, i + 1), None if i < 2 else 100.0, i < 3),
            )
    conn.commit()

    report = compute_bias_report(conn)
    assert report["n_total_tickers"] == 4
    # Tanda 10: nada rellena is_delisted_flag en producción; la cifra sería un
    # 0 % falso, así que se publica «no medido».
    assert report["deslistados_medidos"] is False
    assert report["n_delisted"] is None and report["survivorship_bias_pct"] is None
    assert report["n_price_gaps"] == 2
    assert report["n_price_rows"] == 10
    assert report["data_gap_pct"] == pytest.approx(20.0)
    conn.close()


# ============================================================================
# bucket_by_confidence — compartido entre el dashboard (Fase 5) y
# paper_trading (Fase 4).
# ============================================================================

def test_bucket_by_confidence_hand_calculated():
    records = [
        {"confidence": 95, "won": True},
        {"confidence": 92, "won": False},
        {"confidence": 85, "won": True},
        {"confidence": 55, "won": False},
        {"confidence": 51, "won": False},
    ]
    buckets = bucket_by_confidence(records, "confidence", "won", edges=(50.0, 60.0, 70.0, 80.0, 90.0, 100.0))
    by_label = {b["bucket"]: b for b in buckets}

    assert by_label["90-100"]["n"] == 2
    assert by_label["90-100"]["hit_rate"] == pytest.approx(0.5)  # 1 de 2
    assert by_label["80-90"]["n"] == 1
    assert by_label["80-90"]["hit_rate"] == pytest.approx(1.0)
    assert by_label["50-60"]["n"] == 2
    assert by_label["50-60"]["hit_rate"] == pytest.approx(0.0)
    assert "60-70" not in by_label  # banda vacía, no aparece
    assert "70-80" not in by_label


def test_bucket_by_confidence_100_falls_in_last_bucket_inclusive():
    records = [{"confidence": 100.0, "won": True}]
    buckets = bucket_by_confidence(records, "confidence", "won")
    assert len(buckets) == 1
    assert buckets[0]["bucket"] == "90-100"
    assert buckets[0]["n"] == 1


def test_bucket_by_confidence_empty_input_returns_empty_list():
    assert bucket_by_confidence([], "confidence", "won") == []


# ============================================================================
# compute_calibration_diagnostics — correlación, Brier score, ECE.
# ============================================================================

def test_calibration_diagnostics_hand_calculated():
    from pipeline.backtest.portfolio_metrics import compute_calibration_diagnostics

    records = [
        {"confidence": 100, "correct": True},
        {"confidence": 100, "correct": True},
        {"confidence": 50, "correct": False},
        {"confidence": 50, "correct": False},
    ]
    diag = compute_calibration_diagnostics(records, "confidence", "correct")
    assert diag["n"] == 4
    assert diag["correlation"] == pytest.approx(1.0)
    assert diag["brier_score"] == pytest.approx(0.125)
    assert diag["ece"] == pytest.approx(0.25)


def test_calibration_diagnostics_constant_confidence_gives_none_correlation():
    from pipeline.backtest.portfolio_metrics import compute_calibration_diagnostics

    records = [{"confidence": 80, "correct": True}, {"confidence": 80, "correct": False}]
    diag = compute_calibration_diagnostics(records, "confidence", "correct")
    assert diag["correlation"] is None  # sin varianza en confidence, indefinido, no 0
    assert diag["brier_score"] is not None


def test_calibration_diagnostics_fewer_than_2_records_returns_none():
    from pipeline.backtest.portfolio_metrics import compute_calibration_diagnostics

    diag = compute_calibration_diagnostics([{"confidence": 80, "correct": True}], "confidence", "correct")
    assert diag["n"] == 1
    assert diag["correlation"] is None
    assert diag["brier_score"] is None
    assert diag["ece"] is None


def test_bucket_by_confidence_reports_mean_confidence_per_bucket():
    records = [{"confidence": 91, "won": True}, {"confidence": 99, "won": True}]
    buckets = bucket_by_confidence(records, "confidence", "won")
    assert buckets[0]["mean_confidence"] == pytest.approx(95.0)


@_pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
def test_perdida_por_clase_cuenta_los_eventos_sin_car_con_la_ventana_cerrada():
    """Tanda 10 (auditoría T4): una clase que pierde más del 20 % de sus
    eventos por falta de precios no es fiable. Los eventos recientes, con la
    ventana aún abierta, no cuentan."""
    from datetime import timedelta

    from pipeline.backtest.portfolio_metrics import compute_bias_report
    from pipeline.db.connection import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE car_results, event_analyses, event_enrichment, events, prices, universe RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) VALUES ('1','T','X','2024-01-01','2024-01-01')")
        hoy = date.today()
        eventos = (
            [("8K_2.02_EARNINGS", True)] * 4
            + [("8K_1.03_BANKRUPTCY", True), ("8K_1.03_BANKRUPTCY", False)]
            # Justo en el límite: pierde el 20 %, sigue siendo fiable.
            + [("8K_5.02_EXEC_CHANGE", True)] * 4 + [("8K_5.02_EXEC_CHANGE", False)]
        )
        for i, (clase, con_car) in enumerate(eventos):
            cur.execute(
                "INSERT INTO events (cik, ticker, source, event_class, accession_number, source_url, filed_at, d0_close_date, "
                "classification_method, raw_text_hash) VALUES ('1','T','EDGAR',%s,%s,'https://x',%s,%s,'RULE',%s) RETURNING event_id",
                (clase, f"a{i}", hoy - timedelta(days=200 + i), hoy - timedelta(days=200 + i), f"h{i}"),
            )
            eid = cur.fetchone()["event_id"]
            if con_car:
                cur.execute("INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, 20, 0.01, 200)", (eid,))
        # Un evento reciente sin CAR (ventana aún abierta): no cuenta.
        cur.execute(
            "INSERT INTO events (cik, ticker, source, event_class, accession_number, source_url, filed_at, d0_close_date, "
            "classification_method, raw_text_hash) VALUES ('1','T','EDGAR','8K_2.02_EARNINGS','reciente','https://x',%s,%s,'RULE','hr')",
            (hoy - timedelta(days=3), hoy - timedelta(days=3)),
        )
    conn.commit()

    report = compute_bias_report(conn)
    clases = {c["event_class"]: c for c in report["perdida_por_clase"]}
    assert clases["8K_2.02_EARNINGS"]["n_eventos"] == 4 and clases["8K_2.02_EARNINGS"]["n_sin_car"] == 0
    assert clases["8K_2.02_EARNINGS"]["fiable"] is True
    assert clases["8K_1.03_BANKRUPTCY"]["pct_perdido"] == pytest.approx(50.0)
    assert clases["8K_5.02_EXEC_CHANGE"]["pct_perdido"] == pytest.approx(20.0)
    assert clases["8K_5.02_EXEC_CHANGE"]["fiable"] is True
    assert report["clases_no_fiables"] == ["8K_1.03_BANKRUPTCY"]
    conn.close()


# ---------------------------------------------------------------------------
# Incertidumbre (Tanda 11)
# ---------------------------------------------------------------------------


def test_intervalo_wilson_valores_de_referencia():
    from pipeline.backtest.portfolio_metrics import intervalo_wilson

    # Referencia: 50 aciertos de 100 -> [0,4038; 0,5962]; 0 de 10 -> [0; 0,2775].
    lo, hi = intervalo_wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4) and hi == pytest.approx(0.5962, abs=1e-4)
    lo, hi = intervalo_wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    assert intervalo_wilson(0, 0) is None


# Muestra de referencia (valores con scipy.stats.t): media 0,48 e intervalo
# [−0,00199385, 0,96199385], que incluye el 0 por muy poco.
MUESTRA_REF = [1.2, -0.4, 0.8, 2.1, -1.5, 0.3, 0.9, -0.2, 1.7, 0.0, 0.6, -0.9, 1.1, 0.4, -0.3, 2.4, 0.7, -1.1, 0.5, 1.3]


def test_intervalo_t_valores_de_referencia_y_minimo_de_20():
    from pipeline.backtest.portfolio_metrics import intervalo_t_media

    lo, hi = intervalo_t_media(MUESTRA_REF)
    assert lo == pytest.approx(-0.0019938517839761527, abs=1e-12)
    assert hi == pytest.approx(0.9619938517839761, abs=1e-12)
    assert intervalo_t_media(MUESTRA_REF[:19]) is None


def test_wilson_es_exacto_en_los_extremos():
    from pipeline.backtest.portfolio_metrics import intervalo_wilson

    assert intervalo_wilson(10, 10)[1] == 1.0
    assert intervalo_wilson(0, 3)[0] == 0.0
    # Sin simetría: 3 de 10 no es lo mismo que 7 de 10.
    lo, hi = intervalo_wilson(3, 10)
    assert lo == pytest.approx(0.10779, abs=1e-5) and hi == pytest.approx(0.60322, abs=1e-5)


def test_metricas_de_trade_con_incertidumbre():
    """El veredicto «se distingue de cero» sale del intervalo: la muestra de
    referencia lo roza (incluye el 0); desplazada +0,01, ya no. Con menos de
    20 operaciones no hay intervalo ni veredicto."""
    # Una operación por mes (Tanda 14): sin operaciones que compartan mes, el
    # intervalo agrupado del resultado medio es exactamente el de siempre.
    def _metricas(valores):
        return compute_trade_metrics([{"pnl_pct": p, "exit_date": date(2024 + i // 12, 1 + i % 12, 15)} for i, p in enumerate(valores)])

    roza = _metricas(MUESTRA_REF)
    assert roza["expectancy"] == pytest.approx(0.48)
    assert roza["avg_pnl_distinguible_de_cero"] is False
    assert roza["avg_pnl_ic95"] == pytest.approx([-0.0019938517839761527, 0.9619938517839761])
    assert _metricas([v + 0.01 for v in MUESTRA_REF])["avg_pnl_distinguible_de_cero"] is True
    # Acierto: 13 de 20 ganadoras (las que dan > 0).
    assert roza["win_rate"] == pytest.approx(13 / 20)
    # Wilson con el cuantil t de G-1 = 19 grados de libertad (Tanda 14), algo
    # más ancho que con el normal: [0,43286, 0,81880] antes. Valores calculados
    # aparte con la fórmula de Wilson y scipy.stats.t.ppf(0,975, 19).
    lo, hi = roza["win_rate_ic95"]
    assert lo == pytest.approx(0.419078, abs=1e-5) and hi == pytest.approx(0.827018, abs=1e-5)

    pocas = _metricas([0.01, 5.0])
    assert pocas["avg_pnl_ic95"] is None and pocas["avg_pnl_distinguible_de_cero"] is None

    vacio = compute_trade_metrics([])
    assert vacio["win_rate_ic95"] is None and vacio["avg_pnl_ic95"] is None


# ---------------------------------------------------------------------------
# Tanda 14: operaciones que comparten mes
# ---------------------------------------------------------------------------
# Las operaciones abiertas a la vez se mueven juntas (mismo mercado, misma
# temporada de resultados): tratarlas como independientes da un intervalo
# demasiado estrecho. Se agrupan por mes de entrada (como el event study):
# resultado medio con error agrupado (CR1, nunca menor que el independiente)
# y t de G-1 grados de libertad; acierto con Wilson sobre el n efectivo (n
# entre el efecto de diseño) y el mismo cuantil.


def _ops(pnls_por_mes: list[list[float]]) -> list[dict]:
    out = []
    for m, pnls in enumerate(pnls_por_mes):
        for j, p in enumerate(pnls):
            dia = 1 + j % 27
            out.append({"pnl_pct": p, "entry_date": date(2022 + m // 12, 1 + m % 12, dia), "exit_date": date(2022 + m // 12, 1 + m % 12, dia + 1)})
    return out


def test_operaciones_del_mismo_mes_ensanchan_el_intervalo():
    from pipeline.backtest.portfolio_metrics import intervalo_t_media

    # Seis meses: tres buenos y tres malos, cada uno con cinco operaciones
    # casi iguales. Como independientes, 30 operaciones «se distinguen de
    # cero»; en realidad son seis observaciones.
    meses = [[2.0, 2.1, 1.9, 2.2, 1.8], [1.5, 1.6, 1.4, 1.7, 1.3], [1.8, 1.9, 1.7, 2.0, 1.6],
             [-0.4, -0.3, -0.5, -0.2, -0.6], [-0.2, -0.1, -0.3, 0.0, -0.4], [-0.6, -0.5, -0.7, -0.4, -0.8]]
    pnls = [p for mes in meses for p in mes]
    lo_i, hi_i = intervalo_t_media(pnls)
    assert lo_i > 0  # independiente: «se distingue de cero»
    m = compute_trade_metrics(_ops(meses))
    lo, hi = m["avg_pnl_ic95"]
    assert (hi - lo) > 2 * (hi_i - lo_i)
    assert m["avg_pnl_distinguible_de_cero"] is False
    lo_w, hi_w = m["win_rate_ic95"]
    from pipeline.backtest.portfolio_metrics import intervalo_wilson

    lo_wi, hi_wi = intervalo_wilson(15, 30)
    assert (hi_w - lo_w) > (hi_wi - lo_wi)


def test_todas_en_el_mismo_mes_no_hay_intervalo():
    """Con un solo mes no se puede medir cuánto se parecen las operaciones."""
    m = compute_trade_metrics(_ops([[0.5 + 0.1 * i for i in range(25)]]))
    assert m["avg_pnl_ic95"] is None and m["win_rate_ic95"] is None
    assert m["avg_pnl_distinguible_de_cero"] is None


def test_agrupado_mantiene_el_5_por_ciento_con_operaciones_correlacionadas():
    """Simulación (media real 0; la mitad de la varianza es común al mes): el
    intervalo independiente se equivoca bastante más del 5 %; el agrupado no."""
    import numpy as np

    from pipeline.backtest.portfolio_metrics import intervalo_t_media, intervalo_t_media_agrupado

    rng = np.random.default_rng(14)
    falsos_iid = falsos_agr = 0
    reps = 600
    for _ in range(reps):
        grupos = np.repeat(np.arange(24), 4)
        x = rng.normal(0, np.sqrt(0.5), 24)[grupos] + rng.normal(0, np.sqrt(0.5), len(grupos))
        lo, hi = intervalo_t_media(list(x))
        falsos_iid += lo > 0 or hi < 0
        lo, hi = intervalo_t_media_agrupado(list(x), list(grupos))
        falsos_agr += lo > 0 or hi < 0
    assert falsos_iid / reps > 0.15
    assert falsos_agr / reps < 0.08


# Revisión adversarial de la Tanda 14.


def test_con_un_mes_dominante_el_intervalo_sigue_en_el_5_por_ciento():
    """Un mes con casi todas las operaciones (como al arrancar el historial)
    y un efecto común en cada mes: con la t de G-1 y el error agrupado
    clásico se equivocaba el 10-33 % de las veces."""
    import numpy as np

    from pipeline.backtest.portfolio_metrics import agrupar_por_fecha, intervalo_t_media_agrupado

    rng = np.random.default_rng(1414)
    falsos = hay = 0
    for _ in range(500):
        n_mes = rng.poisson(2, 36)
        n_mes[rng.integers(36)] += 40
        fechas, x = [], []
        for m, k in enumerate(n_mes):
            efecto = rng.normal(0, np.sqrt(0.5))
            for j in range(k):
                fechas.append(date(2021 + m // 12, 1 + m % 12, 1 + j % 28))
                x.append(efecto + rng.normal(0, np.sqrt(0.5)))
        grupos, _ = agrupar_por_fecha(fechas)
        ic = intervalo_t_media_agrupado(x, grupos)
        if ic:
            hay += 1
            falsos += ic[0] > 0 or ic[1] < 0
    # Unas dos de cada tres muestras tienen 6 o más meses efectivos (las
    # demás quedan sin intervalo, a propósito).
    assert hay > 250
    assert falsos / hay < 0.08


def test_concentradas_en_pocos_meses_no_hay_intervalo():
    """3, 40 y 8 operaciones en tres meses equivalen a menos de dos meses
    repartidos: con tan pocos grupos el intervalo no informa (la t vale
    12,7 con un grado de libertad)."""
    ops = _ops([[0.3 + 0.01 * i for i in range(k)] for k in (3, 40, 8)])
    m = compute_trade_metrics(ops)
    assert m["avg_pnl_ic95"] is None and m["win_rate_ic95"] is None
    assert m["ic_agrupacion"] == "mes" and m["ic_grupos_efectivos"] < 2


def test_con_historia_larga_se_agrupa_por_trimestre():
    from pipeline.backtest.portfolio_metrics import agrupar_por_fecha

    fechas = [date(2021 + m // 12, 1 + m % 12, 10) for m in range(36) for _ in range(3)]
    grupos, unidad = agrupar_por_fecha(fechas)
    assert unidad == "trimestre" and len(set(grupos)) == 12
    _, unidad = agrupar_por_fecha(fechas[: 3 * 18])  # 6 trimestres: por mes
    assert unidad == "mes"


def test_el_efecto_de_diseno_ensancha_el_acierto():
    """Con los mismos grados de libertad, el acierto agrupado es más ancho
    que el de Wilson con el n real cuando los aciertos se concentran en unos
    meses (antes este test pasaba solo por la t)."""
    from scipy import stats

    from pipeline.backtest.portfolio_metrics import intervalo_wilson, intervalo_wilson_agrupado

    # Seis meses de cinco: tres meses todo aciertos y tres todo fallos.
    aciertos = [True] * 15 + [False] * 15
    grupos = [m for m in range(6) for _ in range(5)]
    lo, hi = intervalo_wilson_agrupado(aciertos, grupos)
    lo_n, hi_n = intervalo_wilson(15, 30, float(stats.t.ppf(0.975, 5)))
    assert (hi - lo) > 1.5 * (hi_n - lo_n)


def test_las_metricas_agrupan_por_la_fecha_de_entrada():
    """La clave es el mes de ENTRADA: dos operaciones que salen el mismo mes
    pero entraron en meses distintos son dos grupos."""
    from pipeline.backtest.portfolio_metrics import _fecha_de_entrada

    assert _fecha_de_entrada({"entry_date": date(2024, 3, 31), "exit_date": date(2024, 4, 2)}) == date(2024, 3, 31)
    assert _fecha_de_entrada({"entry_date": "", "exit_date": "2024-04-02"}) == date(2024, 4, 2)
    assert _fecha_de_entrada({"entry_date": None, "exit_date": None}) is None
