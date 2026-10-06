"""test_thesis_memory_integration.py — memoria de tesis (thesis_engine.py +
su integración en portfolio_simulator.py) de extremo a extremo contra
Postgres real. Sin llamadas a ningún LLM: el "juicio ciego" de cada evento
nuevo es simplemente la fila de event_analyses ya sembrada directamente por
SQL — exactamente el mismo patrón que el resto de la suite de integración de
este proyecto usa para no depender de la API de Anthropic (ver
test_portfolio_simulator_integration.py, del que se toma el patrón de
fixtures).
"""
import os
from datetime import date, timedelta

import pytest

from pipeline import config
from pipeline.tests.regla_historica import copiar_a_regla_historica

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE theses, thesis_updates, portfolio_trades, portfolio_equity_curve, car_results, "
            "backtest_runs, event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
            "RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _seed_price_series(conn, ticker: str, dates: list[date], closes: list[float], opens: list[float] | None = None, volumes: list[int] | None = None):
    opens = opens or closes
    volumes = volumes or [100_000] * len(dates)
    with conn.cursor() as cur:
        for d, o, c, v in zip(dates, opens, closes, volumes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,%s,FALSE) "
                "ON CONFLICT (ticker, trade_date) DO UPDATE SET open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw, "
                "high_raw=EXCLUDED.high_raw, low_raw=EXCLUDED.low_raw, volume=EXCLUDED.volume",
                (ticker, d, o, c, max(o, c) * 1.005, min(o, c) * 0.995, v),
            )
    conn.commit()


def _seed_event_with_analysis(
    conn, cik: str, ticker: str, d0: date, event_class: str,
    trade_decision_conservative: str = "NO_TRADE", trade_decision_aggressive: str = "NO_TRADE", trade_decision_balanced: str = "NO_TRADE",
    net_conviction: float = 0.0, confidence: float = 50.0,
    ev_conservative: float = 0.0, ev_aggressive: float = 0.0, ev_balanced: float = 0.0,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, d0, d0),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['1.01'],%s,'https://x',%s,%s,'RULE',1.0,%s)
            RETURNING event_id
            """,
            (cik, ticker, event_class, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (%s, 80, '{}', '{}', '{}', '{}', %s, %s, '{}', 20, '{}', %s, %s, %s, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')
            """,
            (event_id, net_conviction, confidence, ev_conservative, ev_aggressive, ev_balanced,
             trade_decision_conservative, trade_decision_aggressive, trade_decision_balanced),
        )
        copiar_a_regla_historica(cur, event_id)
    conn.commit()
    return event_id


def _seed_car_analogue(conn, cik: str, ticker: str, d0: date, event_class: str, car_value: float, window_days: int = 20) -> None:
    """Un análogo histórico (event_class, CAR) — la MISMA fuente que
    analyze/historical_analogues.py ya usa para Etapa 6 e impact estimation.
    No requiere event_analyses (esta tabla nunca se lee para analogues)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, d0, d0),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['1.01'],%s,'https://x',%s,%s,'RULE',1.0,%s)
            RETURNING event_id
            """,
            (cik, ticker, event_class, f"acc-analogue-{cik}", d0, d0, f"hash-analogue-{cik}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, %s, %s, 100)",
            (event_id, window_days, car_value),
        )
    conn.commit()


def _business_days(start: date, n: int) -> list[date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class TestFlagDisabledIsIdenticalToOldBehavior:
    def test_two_trade_events_same_ticker_open_two_independent_positions_when_disabled(self, conn):
        """Comportamiento de SIEMPRE (memoria desactivada, default): dos
        eventos TRADE del mismo ticker abren dos posiciones independientes
        sin ninguna interacción — ver el hallazgo B de la auditoría."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 40)
        d0_a, d0_b = cal[0], cal[10]
        _seed_event_with_analysis(
            conn, "a1", "DUALCO", d0_a, "8K_2.02_EARNINGS",
            trade_decision_conservative="LONG", net_conviction=0.8, confidence=90.0, ev_conservative=0.01,
        )
        _seed_event_with_analysis(
            conn, "a2", "DUALCO", d0_b, "8K_2.02_EARNINGS",
            trade_decision_conservative="LONG", net_conviction=0.8, confidence=90.0, ev_conservative=0.01,
        )
        closes = [100.0] * len(cal)
        _seed_price_series(conn, "DUALCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-flag-off", thesis_memory_enabled=False)
        assert result["n_trades"] == 2

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM theses")
            assert cur.fetchone()["n"] == 0  # ninguna tesis se crea con la memoria desactivada

    def test_full_suite_of_ordinary_exit_paths_unaffected_by_thesis_columns(self, conn):
        """Un trade TAKE_PROFIT normal, con la memoria desactivada, no toca
        `theses` ni deja thesis_id — sigue siendo NULL como antes de que
        existiera esta funcionalidad."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "b1", "TPCO", d0, "8K_2.02_EARNINGS",
            trade_decision_conservative="LONG", net_conviction=0.8, confidence=90.0, ev_conservative=0.01,
        )
        closes = [100.0, 100.0] + [105.0] * (len(cal) - 2)
        _seed_price_series(conn, "TPCO", cal, closes, opens=closes)

        simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-flag-off-2", thesis_memory_enabled=False)
        with conn.cursor() as cur:
            cur.execute("SELECT exit_reason, thesis_id FROM portfolio_trades WHERE run_batch_tag='test-flag-off-2'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "TAKE_PROFIT"
        assert trade["thesis_id"] is None


class TestReconciliationFullScenario:
    def test_new_event_of_opposite_class_invalidates_thesis_citing_original_alert(self, conn):
        """Escenario del spec: alerta de compra, evento nuevo del mismo
        ticker días después, salida por invalidación, con rationale que
        referencia la alerta original."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 30)
        d0_origin = cal[0]  # origen de la tesis
        d0_bad_news = cal[2]  # evento nuevo, 2 días de negociación después

        origin_event_id = _seed_event_with_analysis(
            conn, "c1", "MEMCO", d0_origin, "8K_1.01_MATERIAL_AGMT",
            trade_decision_conservative="LONG", net_conviction=0.8, confidence=90.0, ev_conservative=0.01,
        )
        bad_news_event_id = _seed_event_with_analysis(
            conn, "c1", "MEMCO", d0_bad_news, "8K_1.03_BANKRUPTCY",
            trade_decision_conservative="NO_TRADE", net_conviction=-0.2, confidence=30.0,
        )
        # Precio esencialmente plano — ni TP(+2%) ni SL(-1.5%) se cruzan antes
        # de que llegue la reconciliación.
        closes = [100.0] * len(cal)
        _seed_price_series(conn, "MEMCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-invalidated", thesis_memory_enabled=True)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-invalidated'")
            trade = cur.fetchone()
            cur.execute("SELECT * FROM theses WHERE run_batch_tag='test-invalidated'")
            thesis = cur.fetchone()
            cur.execute("SELECT * FROM thesis_updates WHERE thesis_id=%s ORDER BY as_of_date", (thesis["thesis_id"],))
            updates = cur.fetchall()

        assert trade["exit_reason"] == "INVALIDATED"
        assert trade["thesis_id"] == thesis["thesis_id"]

        assert thesis["event_id"] == origin_event_id
        assert thesis["status"] == "invalidated"
        assert thesis["close_reason_code"] == "INVALIDATED_EVENT_CLASS"
        assert thesis["created_at_date"].isoformat() in thesis["close_rationale"]  # cita la FECHA de la alerta original (entry_date, no d0)
        assert thesis["rationale"] in thesis["close_rationale"]  # cita la alerta original, literalmente

        assert len(updates) == 1
        assert updates[0]["action"] == "SELL"
        assert updates[0]["blind_judgment_event_id"] == bad_news_event_id
        assert updates[0]["trigger"] == "new_event"

    def test_saturated_by_abnormal_volume_alone_no_llm_override_needed(self, conn):
        """Saturación por el componente de volumen (sin que el precio siquiera
        se mueva) — código puro, sin ninguna llamada de IA.

        La tesis expira a los EVENT_WINDOWS_DAYS[0]=5 días de negociación
        (ver thesis_engine.py / config.py), así que el evento nuevo tiene que
        llegar DENTRO de esa ventana para que la reconciliación llegue a
        evaluarse en vez de que la posición ya se haya cerrado por EXPIRED —
        y el ratio de volumen necesita >=20 días de historia ANTERIOR válida
        (ADV_MIN_TRADING_DAYS). Se resuelve con un calendario largo donde el
        evento ORIGEN se sitúa bien entrado en la historia de precios (40
        días ya sembrados antes), no al principio."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2023, 1, 2), 60)
        origin_idx = 40
        d0_origin = cal[origin_idx]
        d0_new = cal[origin_idx + 2]  # +2 días de negociación -> entrada del nuevo evento bien dentro de los 5 días de horizonte

        _seed_event_with_analysis(
            conn, "d1", "VOLCO", d0_origin, "8K_2.02_EARNINGS",
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG",
            net_conviction=0.8, confidence=90.0, ev_aggressive=0.01,
        )
        _seed_event_with_analysis(
            conn, "d1", "VOLCO", d0_new, "8K_8.01_OTHER",
            trade_decision_aggressive="NO_TRADE", net_conviction=0.1, confidence=20.0,
        )
        closes = [100.0] * len(cal)
        new_entry_idx = origin_idx + 3  # primer día de negociación tras d0_new
        volumes = [100_000] * len(cal)
        volumes[new_entry_idx] = 5_000_000  # 50x la media reciente -> supera THESIS_ABNORMAL_VOLUME_RATIO
        _seed_price_series(conn, "VOLCO", cal, closes, opens=closes, volumes=volumes)

        result = simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-saturated", thesis_memory_enabled=True)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-saturated'")
            trade = cur.fetchone()
            cur.execute("SELECT metrics_snapshot FROM thesis_updates WHERE thesis_id=%s", (trade["thesis_id"],))
            snapshot = cur.fetchone()["metrics_snapshot"]

        assert trade["exit_reason"] == "SATURATED"
        assert snapshot["volume_ratio"] >= config.THESIS_ABNORMAL_VOLUME_RATIO

    def test_objective_fulfillment_wins_over_strongly_contradicting_blind_judgment(self, conn):
        """El conflicto que el spec pide comprobar: condición objetiva de
        salida (FULFILLED) + el juicio ciego del evento nuevo dice justo lo
        contrario con alta confianza -> gana la condición objetiva. Mismo
        ajuste de calendario que el test de saturación por volumen (ver su
        docstring) — el evento nuevo debe llegar dentro de los 5 días de
        horizonte de la tesis."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        origin_idx = 0
        d0_origin = cal[origin_idx]
        d0_new = cal[origin_idx + 2]

        # Análogos históricos pequeños (~1%) -> expected_move_pct bajo, fácil
        # de superar con un movimiento real modesto.
        for i in range(6):
            _seed_car_analogue(conn, f"prior{i}", f"PRIOR{i}", d0_origin - timedelta(days=200 + i), "8K_2.02_EARNINGS", 0.01)

        _seed_event_with_analysis(
            conn, "e1", "CONFCO", d0_origin, "8K_2.02_EARNINGS",
            trade_decision_aggressive="LONG", net_conviction=0.8, confidence=90.0, ev_aggressive=0.01,
        )
        _seed_event_with_analysis(
            conn, "e1", "CONFCO", d0_new, "8K_8.01_OTHER",
            trade_decision_aggressive="NO_TRADE", net_conviction=-0.95, confidence=99.0,  # contradicción fuerte
        )
        entry_idx = origin_idx + 1  # día de entrada (D+1 del origen) — precio de entrada 100
        closes = [100.0] * (entry_idx + 1) + [103.0] * (len(cal) - entry_idx - 1)  # +3% a partir del día siguiente a la entrada
        _seed_price_series(conn, "CONFCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-conflict", thesis_memory_enabled=True)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-conflict'")
            trade = cur.fetchone()

        assert trade["exit_reason"] == "FULFILLED"  # NO invalidated pese al juicio ciego contrario

    def test_reconciliation_uses_blind_judgment_already_persisted_before_thesis_existed(self, conn):
        """Anti-sesgo de confirmación: los números que entran en la
        reconciliación son EXACTAMENTE los que ya estaban en event_analyses
        para el evento nuevo (net_conviction/confidence), sin ninguna
        modificación derivada de la tesis — la tesis del ticker no existe
        siquiera en el momento en que ese event_analyses se sembró."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 30)
        d0_origin = cal[0]
        d0_new = cal[2]

        _seed_event_with_analysis(
            conn, "f1", "BIASCO", d0_origin, "8K_1.01_MATERIAL_AGMT",
            trade_decision_conservative="LONG", net_conviction=0.8, confidence=90.0, ev_conservative=0.01,
        )
        # event_analyses de este evento nuevo se siembra ANTES de que exista
        # ninguna tesis (la tesis solo se crea cuando simulate_portfolio()
        # abre la posición del evento origen, más adelante en esta misma
        # función) — net_conviction/confidence fijos, ajenos a la tesis.
        _seed_event_with_analysis(
            conn, "f1", "BIASCO", d0_new, "8K_1.03_BANKRUPTCY",
            trade_decision_conservative="NO_TRADE", net_conviction=-0.33, confidence=42.0,
        )
        closes = [100.0] * len(cal)
        _seed_price_series(conn, "BIASCO", cal, closes, opens=closes)

        simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-antibias", thesis_memory_enabled=True)

        with conn.cursor() as cur:
            cur.execute("SELECT metrics_snapshot FROM thesis_updates WHERE run_batch_tag='test-antibias'")
            snapshot = cur.fetchone()["metrics_snapshot"]

        assert snapshot["net_conviction"] == pytest.approx(-0.33)
        assert snapshot["confidence_in_conviction"] == pytest.approx(42.0)

    def test_saturation_threshold_is_point_in_time_ignores_future_analogues(self, conn):
        """Los análogos POSTERIORES al día de reconciliación no deben influir
        en el umbral de saturación — mismo principio anti-look-ahead que
        analyze.historical_analogues.get_historical_analogues ya garantiza,
        verificado aquí a través de toda la integración."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio
        from pipeline.backtest.thesis_engine import compute_saturation_threshold_pct

        cal = _business_days(date(2024, 1, 2), 15)
        d0_origin = cal[0]
        d0_new = cal[2]  # dentro de los 5 días de horizonte de la tesis (ver otros tests de este archivo)

        # Análogos ANTES del evento nuevo (los únicos que deberían contar).
        past_cars = [1.0, 2.0, 3.0, 4.0, 5.0]
        for i, car in enumerate(past_cars):
            _seed_car_analogue(conn, f"past{i}", f"PAST{i}", d0_origin - timedelta(days=100 + i), "8K_2.02_EARNINGS", car / 100)
        # Análogo FUTURO respecto al evento nuevo (d0_new) — si se colara,
        # dispararía el umbral hacia arriba de forma drástica.
        _seed_car_analogue(conn, "future0", "FUTURE0", cal[-1] + timedelta(days=5), "8K_2.02_EARNINGS", 0.99)

        _seed_event_with_analysis(
            conn, "g1", "PITCO", d0_origin, "8K_2.02_EARNINGS",
            trade_decision_aggressive="LONG", net_conviction=0.8, confidence=90.0, ev_aggressive=0.01,
        )
        _seed_event_with_analysis(
            conn, "g1", "PITCO", d0_new, "8K_8.01_OTHER",
            trade_decision_aggressive="NO_TRADE", net_conviction=0.05, confidence=10.0,
        )
        closes = [100.0] * len(cal)
        _seed_price_series(conn, "PITCO", cal, closes, opens=closes)

        simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-pit", thesis_memory_enabled=True)

        with conn.cursor() as cur:
            cur.execute("SELECT metrics_snapshot FROM thesis_updates WHERE run_batch_tag='test-pit'")
            snapshot = cur.fetchone()["metrics_snapshot"]

        expected_threshold = compute_saturation_threshold_pct(past_cars)  # SOLO los análogos pasados
        assert snapshot["saturation_threshold_pct"] == pytest.approx(expected_threshold)

    def test_thesis_memory_prevents_independent_second_position_same_ticker(self, conn):
        """Ajuste B acordado: con la memoria activada, un evento nuevo en un
        ticker con tesis abierta NO abre una segunda posición independiente
        — pasa por la reconciliación de la existente."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0_a, d0_b = cal[0], cal[2]  # dentro de los 5 días de horizonte de la tesis (ver otros tests de este archivo)
        _seed_event_with_analysis(
            conn, "h1", "GUARDCO", d0_a, "8K_2.02_EARNINGS",
            trade_decision_conservative="LONG", net_conviction=0.8, confidence=90.0, ev_conservative=0.01,
        )
        # Segundo evento, TAMBIÉN trade_decision LONG para esta versión — sin
        # memoria abriría una 2ª posición independiente (ver el otro test).
        _seed_event_with_analysis(
            conn, "h1", "GUARDCO", d0_b, "8K_2.02_EARNINGS",
            trade_decision_conservative="LONG", net_conviction=0.7, confidence=85.0, ev_conservative=0.01,
        )
        closes = [100.0] * len(cal)
        _seed_price_series(conn, "GUARDCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-no-dup", thesis_memory_enabled=True)
        # Ni el 2º evento abre nada nuevo, ni la reconciliación cierra la
        # tesis (mismo evento class, judgment que confirma, sin condiciones
        # objetivas disparadas) — sigue habiendo una única posición al final,
        # forzada a cerrar por MAX_HOLDING/fin de datos, nunca 2.
        assert result["n_trades"] == 1
