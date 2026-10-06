"""test_portfolio_simulator_integration.py — simulate_portfolio() de extremo
a extremo contra Postgres real, con precios sintéticos diseñados para
disparar cada mecanismo de salida al menos una vez (TP, SL, max_holding,
trailing_stop) y verificar la reconciliación de caja/equity.
"""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE portfolio_trades, portfolio_equity_curve, car_results, backtest_runs, "
            "event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
            "RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _seed_price_series(conn, ticker: str, dates: list[date], closes: list[float], opens: list[float] | None = None):
    opens = opens or closes
    with conn.cursor() as cur:
        for d, o, c in zip(dates, opens, closes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) "
                "ON CONFLICT (ticker, trade_date) DO UPDATE SET open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw, "
                "high_raw=EXCLUDED.high_raw, low_raw=EXCLUDED.low_raw",
                (ticker, d, o, c, max(o, c) * 1.005, min(o, c) * 0.995),
            )
    conn.commit()


def _seed_event_with_analysis(
    conn, cik: str, ticker: str, d0: date,
    trade_decision_conservative: str, trade_decision_aggressive: str, trade_decision_balanced: str,
    net_conviction: float, confidence: float,
    ev_conservative: float, ev_aggressive: float, ev_balanced: float,
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
            VALUES (%s,%s,'EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
            RETURNING event_id
            """,
            (cik, ticker, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}"),
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
    conn.commit()
    return event_id


def _seed_gap_sentinel(conn, ticker: str, d: date) -> None:
    """Fila centinela de survivorship_warning con close_raw=NULL — exactamente
    lo que yfinance_backfill.py:_flag_full_gap/_store_with_gap_detection
    inserta para un ticker deslistado/en halt. Es el dato que reproduce el
    bug de _resolve_forced_close cuando cae como ÚLTIMA fila del ticker."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning) "
            "VALUES (%s, %s, NULL, NULL, NULL, TRUE) "
            "ON CONFLICT (ticker, trade_date) DO UPDATE SET survivorship_warning = TRUE, close_raw = NULL",
            (ticker, d),
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


class TestConservativeTakeProfitPath:
    def test_conservative_trade_hits_take_profit_and_credits_cash(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        # d0 en business day 0; entrada en business day 1 (apertura); TP a +2% al día siguiente.
        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        closes = [100.0] + [100.0] + [105.0] * (len(cal) - 2)  # salto a +5% en la 2ª vela tras entrada -> dispara TP (+2%)
        _seed_price_series(conn, "TESTCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-1", starting_capital=100_000.0)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-1'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "TAKE_PROFIT"
        assert trade["direction"] == "LONG"
        assert float(trade["pnl_pct"]) > 0
        assert trade["entry_date"] > d0  # checksum anti-look-ahead: nunca entra en D0

        # Verifica reconciliación de caja: balance final = capital inicial + pnl_abs de todos los trades.
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM portfolio_equity_curve WHERE version='CONSERVATIVE' AND run_batch_tag='test-1' ORDER BY trade_date DESC LIMIT 1")
            final_balance = float(cur.fetchone()["balance"])
        assert final_balance == pytest.approx(100_000.0 + float(trade["pnl_abs"]), rel=1e-6)


class TestConservativeStopLossPath:
    def test_conservative_trade_hits_stop_loss(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.75, confidence=85.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        closes = [100.0, 100.0] + [95.0] * (len(cal) - 2)  # cae -5% -> dispara SL (-1.5%)
        _seed_price_series(conn, "TESTCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-2", starting_capital=100_000.0)
        assert result["n_trades"] == 1
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-2'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "STOP_LOSS"
        assert float(trade["pnl_pct"]) < 0


class TestMaxConcurrentEnforced:
    def test_fourth_conservative_signal_is_dropped_when_three_slots_full(self, conn):
        """max_concurrent=3 para Conservative — una 4ª señal el mismo día no
        debe abrir posición."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 30)
        d0 = cal[0]
        flat_closes = [100.0] * len(cal)  # sin movimiento: nada dispara TP/SL, todo cierra por max_holding
        for i in range(4):
            _seed_event_with_analysis(
                conn, str(i), f"T{i}", d0,
                trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
                net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
            )
            _seed_price_series(conn, f"T{i}", cal, flat_closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-3", starting_capital=100_000.0)
        assert result["n_trades"] == 3  # la 4ª señal se descartó por falta de hueco


class TestBalancedExecutionStyleSplit:
    def test_balanced_trade_uses_conservative_style_sizing_when_high_confidence(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="LONG",
            net_conviction=0.8, confidence=90.0,  # >= 70 y ev_conservative >= 0.002 -> estilo CONSERVATIVE
            ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        flat_closes = [100.0] * len(cal)
        _seed_price_series(conn, "TESTCO", cal, flat_closes)

        simulate_portfolio(conn, "BALANCED", run_batch_tag="test-4", starting_capital=100_000.0)
        with conn.cursor() as cur:
            cur.execute("SELECT execution_style, position_size_pct FROM portfolio_trades WHERE run_batch_tag='test-4'")
            trade = cur.fetchone()
        assert trade["execution_style"] == "CONSERVATIVE"
        assert float(trade["position_size_pct"]) == pytest.approx(1.5)  # tamaño fijo de Balanced-Conservative


class TestDynamicEvWeightedSizing:
    def test_dynamic_reuses_balanced_trade_decision_but_sizes_by_ev(self, conn):
        """DYNAMIC no tiene su propia trade_decision_dynamic (no existe esa
        columna) — reutiliza trade_decision_balanced (mismo evento, mismo
        estilo CONSERVATIVE por la misma clasificación), pero el tamaño debe
        salir de compute_ev_weighted_position_size_pct (ponderado por EV), NO
        del 1.5% fijo que usaría BALANCED para el mismo evento."""
        from pipeline.analyze.ev_engine import position_size_pct
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="LONG",
            net_conviction=0.8, confidence=90.0,  # mismo caso que el test de BALANCED de arriba -> estilo CONSERVATIVE
            ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        flat_closes = [100.0] * len(cal)
        _seed_price_series(conn, "TESTCO", cal, flat_closes)

        result = simulate_portfolio(conn, "DYNAMIC", run_batch_tag="test-dynamic-1", starting_capital=100_000.0)
        assert result["n_trades"] == 1
        with conn.cursor() as cur:
            cur.execute("SELECT execution_style, position_size_pct FROM portfolio_trades WHERE run_batch_tag='test-dynamic-1'")
            trade = cur.fetchone()
        assert trade["execution_style"] == "CONSERVATIVE"  # mismo criterio que BALANCED
        expected_size = position_size_pct(0.01, 90.0, "CONSERVATIVE")
        assert float(trade["position_size_pct"]) == pytest.approx(expected_size)
        assert float(trade["position_size_pct"]) != pytest.approx(1.5)  # NO el fijo de BALANCED

    def test_dynamic_sizes_up_with_higher_ev_same_confidence(self, conn):
        """Dos eventos idénticos salvo el EV: DYNAMIC debe apostar más al de
        mayor EV — justo lo que BALANCED (tamaño fijo) no puede hacer."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "LOWEV", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="LONG",
            net_conviction=0.8, confidence=90.0,
            ev_conservative=0.003, ev_aggressive=0.003, ev_balanced=0.003,
        )
        _seed_event_with_analysis(
            conn, "2", "HIGHEV", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="LONG",
            net_conviction=0.8, confidence=90.0,
            ev_conservative=0.02, ev_aggressive=0.02, ev_balanced=0.02,
        )
        flat_closes = [100.0] * len(cal)
        _seed_price_series(conn, "LOWEV", cal, flat_closes)
        _seed_price_series(conn, "HIGHEV", cal, flat_closes)

        simulate_portfolio(conn, "DYNAMIC", run_batch_tag="test-dynamic-2", starting_capital=100_000.0)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.ticker, pt.position_size_pct FROM portfolio_trades pt "
                "JOIN events e ON e.event_id = pt.event_id "
                "WHERE pt.run_batch_tag='test-dynamic-2' ORDER BY e.ticker"
            )
            rows = {r["ticker"]: float(r["position_size_pct"]) for r in cur.fetchall()}
        assert rows["HIGHEV"] > rows["LOWEV"]


class TestSampleSplitFiltering:
    """fetch_events_for_version + sample — ver pipeline/backtest/sample_split.py.
    Dos eventos idénticos salvo la fecha (uno IN_SAMPLE, uno OOS): confirma
    que el filtro de fecha real contra Postgres coincide con lo que
    test_sample_split.py ya prueba en puro (aritmética de fechas)."""

    def test_sample_none_returns_both_in_sample_and_oos_events(self, conn):
        from pipeline.backtest.portfolio_simulator import fetch_events_for_version

        _seed_event_with_analysis(
            conn, "1", "INSAMPLE", date(2023, 6, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        _seed_event_with_analysis(
            conn, "2", "OOSEVENT", date(2024, 6, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )

        events = fetch_events_for_version(conn, "CONSERVATIVE", sample=None)
        assert {e["ticker"] for e in events} == {"INSAMPLE", "OOSEVENT"}

    def test_sample_in_sample_excludes_2024_event(self, conn):
        from pipeline.backtest.portfolio_simulator import fetch_events_for_version

        _seed_event_with_analysis(
            conn, "1", "INSAMPLE", date(2023, 6, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        _seed_event_with_analysis(
            conn, "2", "OOSEVENT", date(2024, 6, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )

        events = fetch_events_for_version(conn, "CONSERVATIVE", sample="in_sample")
        assert {e["ticker"] for e in events} == {"INSAMPLE"}

    def test_sample_oos_excludes_2023_event(self, conn):
        from pipeline.backtest.portfolio_simulator import fetch_events_for_version

        _seed_event_with_analysis(
            conn, "1", "INSAMPLE", date(2023, 6, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        _seed_event_with_analysis(
            conn, "2", "OOSEVENT", date(2024, 6, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )

        events = fetch_events_for_version(conn, "CONSERVATIVE", sample="oos")
        assert {e["ticker"] for e in events} == {"OOSEVENT"}

    def test_boundary_dates_land_on_the_correct_side(self, conn):
        """31-dic-2023 (IN_SAMPLE_END, inclusive) y 1-ene-2024 (OOS_START,
        inclusive) son el corte exacto que pide el plan — ambos deben caer
        cada uno en su partición, ninguno se pierde ni se duplica."""
        from pipeline.backtest.portfolio_simulator import fetch_events_for_version

        _seed_event_with_analysis(
            conn, "1", "LASTINSAMPLE", date(2023, 12, 31),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        _seed_event_with_analysis(
            conn, "2", "FIRSTOOS", date(2024, 1, 1),
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )

        in_sample_tickers = {e["ticker"] for e in fetch_events_for_version(conn, "CONSERVATIVE", sample="in_sample")}
        oos_tickers = {e["ticker"] for e in fetch_events_for_version(conn, "CONSERVATIVE", sample="oos")}
        assert in_sample_tickers == {"LASTINSAMPLE"}
        assert oos_tickers == {"FIRSTOOS"}


class TestForcedCloseWithDataGap:
    """Bug de auditoría: el cierre forzado al final del panel de precios
    asumía que la última fila SIEMPRE tiene close_raw válido. Falso cuando el
    ticker se deslista/entra en halt a mitad de una posición abierta (fila
    centinela de survivorship_warning, close_raw=NULL) — antes del fix,
    simulate_portfolio() reventaba con TypeError: float() argument must be
    a string or a real number, not 'NoneType'."""

    def test_ticker_delisted_mid_holding_does_not_crash_and_tags_data_gap(self, conn):
        """Precio plano hasta el día 3 (nada dispara TP/SL/trailing), luego
        el ticker desaparece del todo — la última fila que ve
        simulate_portfolio es el centinela NULL. No debe reventar, y el
        trade forzado debe salir marcado DATA_GAP, no MAX_HOLDING."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 5)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "DELISTED", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        # Precios normales para los primeros días (sin cruzar TP +2%/SL -1.5%
        # de Conservative), y el último día del panel es el gap total.
        flat_days = cal[1:4]
        _seed_price_series(conn, "DELISTED", flat_days, [100.5] * len(flat_days))
        _seed_gap_sentinel(conn, "DELISTED", cal[4])

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-datagap-1", starting_capital=100_000.0)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT exit_reason, exit_date, exit_price, pnl_pct FROM portfolio_trades WHERE run_batch_tag='test-datagap-1'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "DATA_GAP"
        assert trade["exit_date"] == flat_days[-1]  # última fecha CON precio válido, no la del centinela
        assert float(trade["exit_price"]) == 100.5

    def test_ticker_delisted_the_day_after_entry_falls_back_to_entry_price(self, conn):
        """Caso extremo: ni un solo día con precio válido tras la entrada —
        cierra en la última fecha disponible (el propio centinela) al precio
        de entrada, retorno plano (menos comisión), nunca inventado."""
        from pipeline.backtest.portfolio_simulator import COMMISSION_BPS_ROUND_TRIP, simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 3)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "GONEFAST", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        entry_day = cal[1]
        _seed_price_series(conn, "GONEFAST", [entry_day], [100.0])
        _seed_gap_sentinel(conn, "GONEFAST", cal[2])

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-datagap-2", starting_capital=100_000.0)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT exit_reason, entry_price, exit_price, pnl_pct FROM portfolio_trades WHERE run_batch_tag='test-datagap-2'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "DATA_GAP"
        assert float(trade["exit_price"]) == float(trade["entry_price"])  # retorno plano
        # Solo los costes: comisión + deslizamiento conservador (sin capitalización en D0, H-32).
        assert float(trade["pnl_pct"]) == pytest.approx(-COMMISSION_BPS_ROUND_TRIP / 100 - 2 * 25.0 / 100)


class TestDrawdownCircuitBreaker:
    """Circuit-breaker de drawdown de cartera (hallazgo de auditoría,
    prioridad máxima). Escenario: una posición Aggressive que se hunde SIN
    disparar su propio stop-loss (el low se mantiene por encima del precio
    de SL, solo el close cae) — MTM no realizado que empuja el drawdown de
    la CARTERA por encima del umbral. Se usa circuit_breaker_pct=0.005 (0.5%)
    en vez del 15% de producción para que un solo movimiento de precio
    modesto (-4%, sin tocar el SL real de -5%) baste para cruzarlo — el
    umbral en sí ya se prueba en puro en test_portfolio_simulator.py."""

    def _seed_main(self, conn, cal, precios_por_indice):
        _seed_event_with_analysis(
            conn, "1", "MAIN", cal[0],
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=100.0,  # confidence=100 -> tamaño máximo Aggressive (20%)
            ev_conservative=0.0, ev_aggressive=0.01, ev_balanced=0.0,
        )
        with conn.cursor() as cur:
            for i, (o, h, l, c) in precios_por_indice.items():
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES ('MAIN', %s, %s, %s, %s, %s, 1.0, 100000, FALSE)",
                    (cal[i], o, c, h, l),
                )
        conn.commit()

    def _seed_signal(self, conn, cik, ticker, d0, cal_from):
        _seed_event_with_analysis(
            conn, cik, ticker, d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=80.0, ev_conservative=0.0, ev_aggressive=0.01, ev_balanced=0.0,
        )
        _seed_price_series(conn, ticker, cal_from, [100.0] * len(cal_from))

    def _trades(self, conn, tag):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.ticker, pt.position_size_pct FROM portfolio_trades pt JOIN events e ON e.event_id = pt.event_id "
                "WHERE pt.run_batch_tag = %s", (tag,),
            )
            return {r["ticker"]: float(r["position_size_pct"]) for r in cur.fetchall()}

    def _curve(self, conn, tag):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, circuit_breaker_active FROM portfolio_equity_curve "
                "WHERE version = 'AGGRESSIVE' AND run_batch_tag = %s ORDER BY trade_date", (tag,),
            )
            return {r["trade_date"]: r["circuit_breaker_active"] for r in cur.fetchall()}

    def test_pausa_y_despues_medio_tamano_sin_volver_a_saltar_al_reanudar(self, conn):
        """BUGS_REPORT.md H-10: al saltar, pausa de N sesiones; después se
        opera a mitad de tamaño y el freno se mide contra el nivel al
        reanudar, así que NO vuelve a saltar en cuanto termina la pausa
        aunque el drawdown frente al máximo siga por encima del umbral."""
        from pipeline.backtest.portfolio_simulator import CIRCUIT_BREAKER_REDUCED_SIZE, simulate_portfolio
        from pipeline.backtest.portfolio_strategies import STRATEGIES, compute_position_size_pct

        cal = _business_days(date(2024, 1, 2), 15)
        # MAIN: entra a 100 en cal[1] y se queda hundida en 96 sin tocar su
        # stop (95): drawdown no realizado del 0,8 % > 0,5 % del test.
        precios = {1: (100.0, 101.0, 99.0, 100.0)}
        for i in range(2, 15):
            precios[i] = (96.0, 97.0, 95.8, 96.0)
        self._seed_main(conn, cal, precios)
        self._seed_signal(conn, "2", "BLOCKED", cal[2], cal[3:])   # entra en cal[3]: en plena pausa
        self._seed_signal(conn, "3", "HALF", cal[5], cal[6:])      # entra en cal[6]: tras la pausa

        result = simulate_portfolio(
            conn, "AGGRESSIVE", run_batch_tag="test-breaker-1", starting_capital=100_000.0,
            circuit_breaker_pct=0.005, circuit_breaker_pause_sessions=3,
        )
        curva = self._curve(conn, "test-breaker-1")
        assert [curva[cal[i]] for i in (2, 3, 4)] == [True, True, True]  # 3 sesiones de pausa
        assert curva[cal[5]] is False and curva[cal[6]] is False          # reanuda, no vuelve a saltar
        assert result["n_days_circuit_breaker_active"] == 3

        operadas = self._trades(conn, "test-breaker-1")
        assert "BLOCKED" not in operadas
        normal = compute_position_size_pct(80.0, STRATEGIES["AGGRESSIVE"])
        assert operadas["HALF"] == pytest.approx(normal * CIRCUIT_BREAKER_REDUCED_SIZE, rel=1e-6)

    def test_un_nuevo_maximo_devuelve_el_tamano_completo(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio
        from pipeline.backtest.portfolio_strategies import STRATEGIES, compute_position_size_pct

        cal = _business_days(date(2024, 1, 2), 15)
        # MAIN se hunde (pausa de 1 sesión) y luego sube a 110: nuevo máximo.
        precios = {1: (100.0, 101.0, 99.0, 100.0), 2: (96.0, 97.0, 95.8, 96.0), 3: (96.0, 97.0, 95.8, 96.0)}
        for i in range(4, 15):
            precios[i] = (110.0, 111.0, 109.0, 110.0)
        self._seed_main(conn, cal, precios)
        self._seed_signal(conn, "3", "FULL", cal[5], cal[6:])

        simulate_portfolio(
            conn, "AGGRESSIVE", run_batch_tag="test-breaker-2", starting_capital=100_000.0,
            circuit_breaker_pct=0.005, circuit_breaker_pause_sessions=1,
        )
        operadas = self._trades(conn, "test-breaker-2")
        normal = compute_position_size_pct(80.0, STRATEGIES["AGGRESSIVE"])
        assert operadas["FULL"] == pytest.approx(normal, rel=1e-6)


class TestEntrySessionEvaluated:
    """BUGS_REPORT.md H-31: un stop tocado en la misma sesión de entrada se
    ejecuta ese día; antes se ignoraba hasta el día siguiente."""

    def test_stop_en_la_sesion_de_entrada(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 8)
        _seed_event_with_analysis(
            conn, "1", "DROP", cal[0],
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        # Entra a 100 en cal[1] y ese mismo día cae a 90 (stop conservador por
        # debajo de 100); al día siguiente vuelve a 100.
        precios = {cal[1]: (100.0, 100.5, 90.0, 91.0)}
        for d in cal[2:]:
            precios[d] = (100.0, 100.5, 99.5, 100.0)
        with conn.cursor() as cur:
            for d, (o, h, l, c) in precios.items():
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES ('DROP', %s, %s, %s, %s, %s, 1.0, 100000, FALSE)",
                    (d, o, c, h, l),
                )
        conn.commit()

        simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-h31", starting_capital=100_000.0)
        with conn.cursor() as cur:
            cur.execute("SELECT entry_date, exit_date, exit_reason, actual_move_pct FROM portfolio_trades WHERE run_batch_tag='test-h31'")
            t = cur.fetchone()
        assert t["exit_reason"] == "STOP_LOSS"
        assert t["exit_date"] == t["entry_date"] == cal[1]
        assert float(t["actual_move_pct"]) < 0


class TestPositionSizeCappedByADV:
    """Tope de posición por %ADV (hallazgo de auditoría). Dos tickers,
    misma confianza (tamaño pedido idéntico: 20% de Aggressive) pero
    volumen muy distinto — el ADV real, calculado desde el propio panel de
    precios del backtest (no desde universe.adv_usd_60d, ver
    compute_trailing_adv_usd), decide si el tamaño se recorta o no."""

    def _seed_ticker_with_volume(self, conn, ticker: str, dates: list[date], close: float, volume: int) -> None:
        with conn.cursor() as cur:
            for d in dates:
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, volume, adj_factor, survivorship_warning) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 1.0, FALSE)",
                    (ticker, d, close, close, close * 1.01, close * 0.99, volume),
                )
        conn.commit()

    def test_illiquid_ticker_gets_reduced_and_flagged(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 40)
        d0 = cal[25]  # entry_date = cal[26] -> 26 días de historial previo, >= ADV_MIN_TRADING_DAYS (20)

        _seed_event_with_analysis(
            conn, "1", "ILLIQUID", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=100.0,  # 20% pedido (máximo Aggressive)
            ev_conservative=0.0, ev_aggressive=0.01, ev_balanced=0.0,
        )
        # close=$50, volumen=1,000 -> ADV ~ $50,000/día. Tope 5% = $2,500,
        # muy por debajo del 20% de $100k ($20,000) que pediría el sizing normal.
        self._seed_ticker_with_volume(conn, "ILLIQUID", cal[: 37], close=50.0, volume=1_000)

        simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-adv-1", starting_capital=100_000.0)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT position_size_dollars, position_size_pct, had_adv_cap_applied "
                "FROM portfolio_trades WHERE run_batch_tag = 'test-adv-1'"
            )
            trade = cur.fetchone()
        assert trade["had_adv_cap_applied"] is True
        assert float(trade["position_size_dollars"]) == pytest.approx(2_500.0, rel=0.05)
        assert float(trade["position_size_pct"]) < 20.0  # muy por debajo del 20% pedido

    def test_liquid_ticker_untouched(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 40)
        d0 = cal[25]

        _seed_event_with_analysis(
            conn, "1", "LIQUID", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=100.0,
            ev_conservative=0.0, ev_aggressive=0.01, ev_balanced=0.0,
        )
        # close=$100, volumen=1,000,000 -> ADV ~ $100M/día. Tope 5% = $5M,
        # muchísimo más que el 20% de $100k ($20,000) pedido -> sin recorte.
        self._seed_ticker_with_volume(conn, "LIQUID", cal[:37], close=100.0, volume=1_000_000)

        simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-adv-2", starting_capital=100_000.0)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT position_size_dollars, position_size_pct, had_adv_cap_applied "
                "FROM portfolio_trades WHERE run_batch_tag = 'test-adv-2'"
            )
            trade = cur.fetchone()
        assert trade["had_adv_cap_applied"] is False
        assert float(trade["position_size_pct"]) == pytest.approx(20.0)  # tamaño máximo normal, sin tocar


class TestAggressiveTrailingStopPath:
    def test_aggressive_trade_partially_closes_via_trailing_stop(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 30)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=60.0, ev_conservative=0.0, ev_aggressive=0.01, ev_balanced=0.0,
        )
        # Entrada en cal[1] a 100. Sube gradualmente hasta +65% para cruzar
        # los tramos de trailing stop de 20/40/60, luego se mantiene plana.
        closes = [100.0, 100.0]
        ramp = [110.0, 122.0, 135.0, 148.0, 162.0, 168.0]
        closes += ramp
        closes += [165.0] * (len(cal) - len(closes))
        _seed_price_series(conn, "TESTCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-6", starting_capital=100_000.0)
        assert result["n_trades"] == 1
        with conn.cursor() as cur:
            cur.execute("SELECT exit_reason, pnl_pct FROM portfolio_trades WHERE run_batch_tag='test-6'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "TRAILING_STOP"
        assert float(trade["pnl_pct"]) > 0  # una subida sostenida debe dar ganancia neta


class TestNoLookaheadChecksum:
    def test_all_trades_satisfy_exit_after_entry_and_entry_after_d0(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 40)
        d0 = cal[5]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        flat_closes = [100.0] * len(cal)
        _seed_price_series(conn, "TESTCO", cal, flat_closes)

        simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-5", starting_capital=100_000.0)
        with conn.cursor() as cur:
            cur.execute("SELECT entry_date, exit_date FROM portfolio_trades WHERE run_batch_tag='test-5'")
            trade = cur.fetchone()
        assert trade["entry_date"] > d0
        assert trade["exit_date"] > trade["entry_date"]


def test_una_operacion_por_empresa_y_dia(conn):
    """BUGS_REPORT.md H-20: dos eventos de la misma empresa el mismo día (un
    8-K con varios Items) abrían dos posiciones sobre el mismo precio. Se
    queda uno, el de mayor |EV| de la versión."""
    from pipeline.backtest.portfolio_simulator import fetch_events_for_version

    d0 = date(2024, 3, 4)
    # Cada versión mira SU EV: A gana en Conservadora; B en Equilibrada (y
    # en Dinámica, que usa la decisión y el EV de Equilibrada).
    a = _seed_event_with_analysis(conn, "1", "AAA", d0, "LONG", "LONG", "LONG", 0.5, 70, 0.04, 0.01, 0.01)
    b = _seed_event_with_analysis(conn, "2", "AAA", d0, "LONG", "LONG", "LONG", 0.8, 80, 0.01, 0.01, 0.03)
    otro = _seed_event_with_analysis(conn, "3", "BBB", d0, "LONG", "LONG", "LONG", 0.5, 70, 0.01, 0.01, 0.01)
    esperado = {"CONSERVATIVE": a, "BALANCED": b, "DYNAMIC": b}
    for version, ganador in esperado.items():
        ids = [r["event_id"] for r in fetch_events_for_version(conn, version)]
        assert sorted(ids) == sorted([ganador, otro]), version
    # Mismo día: primero el de mayor |EV| (orden en que se reparten los huecos).
    ids = [r["event_id"] for r in fetch_events_for_version(conn, "BALANCED")]
    assert ids == [b, otro]


def test_deslizamiento_usa_la_capitalizacion_en_d0_sin_look_ahead(conn):
    """H-32: la capitalización para el deslizamiento es la de D0 (acciones
    del último 10-K PUBLICADO antes de D0 × precio de D0), no la de hoy."""
    from pipeline.backtest.portfolio_simulator import _build_entry_plan, fetch_events_for_version

    d0 = date(2024, 3, 4)
    dias = _business_days(d0, 5)
    _seed_price_series(conn, "BIG", dias, [100.0] * 5)
    _seed_price_series(conn, "GROW", dias, [100.0] * 5)
    _seed_event_with_analysis(conn, "1", "BIG", d0, "LONG", "LONG", "LONG", 0.8, 80, 0.03, 0.03, 0.03)
    _seed_event_with_analysis(conn, "2", "GROW", d0, "LONG", "LONG", "LONG", 0.8, 80, 0.03, 0.03, 0.03)
    with conn.cursor() as cur:
        # BIG: 200 M acciones × 100 $ = 20.000 M$ en D0.
        cur.execute("INSERT INTO fundamentals (cik, fiscal_period_end, filed_at, form, shares_outstanding) "
                    "VALUES ('1', '2023-12-31', '2024-02-15', '10-K', 200000000)")
        # GROW: en D0 solo había 50 M acciones (5.000 M$); el 10-K con 500 M se
        # publicó DESPUÉS de D0 y no puede contar.
        cur.execute("INSERT INTO fundamentals (cik, fiscal_period_end, filed_at, form, shares_outstanding) "
                    "VALUES ('2', '2022-12-31', '2023-02-15', '10-K', 50000000), "
                    "('2', '2023-12-31', '2024-03-20', '10-K', 500000000)")
    conn.commit()

    eventos = fetch_events_for_version(conn, "BALANCED")
    plan = {p["ticker"]: p for p in _build_entry_plan(conn, "BALANCED", eventos, {})}
    assert plan["BIG"]["slippage_bps_por_lado"] == 10.0
    assert plan["GROW"]["slippage_bps_por_lado"] == 25.0
