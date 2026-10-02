"""test_technical_analysis.py — capa técnica y plan de operación por señal."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from pipeline.analyze import technical_analysis as ta

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


def _ohlcv(closes, spread=1.0, volume=1_000_000.0, last_volume=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    vol = np.full(len(closes), volume)
    if last_volume is not None:
        vol[-1] = last_volume
    return pd.DataFrame(
        {"open": closes, "high": closes + spread / 2, "low": closes - spread / 2, "close": closes, "volume": vol},
        index=idx,
    )


def _trend(n=260, start=50.0, step=0.15, seed=1):
    rng = np.random.default_rng(seed)
    return start + np.arange(n) * step + rng.normal(0, 0.4, n).cumsum() * 0.2


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------


def test_rsi_extremos():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ta.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert ta.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_atr_de_rango_constante():
    df = _ohlcv(np.full(60, 100.0), spread=2.0)
    assert ta.atr(df).iloc[-1] == pytest.approx(2.0)


def test_volumen_relativo_excluye_d0():
    df = _ohlcv(_trend(100), last_volume=3_000_000)
    assert ta.compute_indicators(df)["volume_ratio"] == pytest.approx(3.0)


def test_ema200_ausente_con_poco_historico():
    ind = ta.compute_indicators(_ohlcv(_trend(120)))
    assert ind["ema200"] is None
    assert ind["ema50"] is not None


def test_tendencia_alcista_alinea_indicadores_largos_no_cortos():
    df = _ohlcv(_trend(260, step=0.3))
    ind = ta.compute_indicators(df)
    longs = ta.aligned_indicators(ind, "LONG")
    shorts = ta.aligned_indicators(ind, "SHORT")
    assert "Medias móviles" in longs and "OBV" in longs
    assert len(longs) > len(shorts)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _plan_with_levels(monkeypatch, df, direction, offsets_atr, catalyst=True):
    """Plan con niveles controlados, a `offsets_atr` ATR del cierre de D0."""
    ind = ta.compute_indicators(df)
    entry, a = ind["close"], ind["atr"]
    levels = [{"price": entry + o * a, "kind": k} for k, o in offsets_atr]
    monkeypatch.setattr(ta, "key_levels", lambda _df, _ind: levels)
    return ta.build_trade_plan(df, direction, catalyst), entry, a


def test_plan_largo_completo(monkeypatch):
    df = _ohlcv(_trend(260, step=0.3), last_volume=2_000_000)
    plan, entry, a = _plan_with_levels(
        monkeypatch, df, "LONG",
        [("Mínimo 20 sesiones", -1.0), ("EMA 50", -1.1), ("Máximo 20 sesiones", 3.0), ("Pivot R2", 5.0)],
    )
    assert plan.stop == pytest.approx(entry - 1.0 * a - ta.STOP_BUFFER_ATR * a, rel=1e-3)
    assert plan.target == pytest.approx(entry + 3.0 * a, rel=1e-3)
    assert plan.target2 == pytest.approx(entry + 5.0 * a, rel=1e-3)
    assert plan.risk_reward == pytest.approx(3.0 / 1.25, rel=1e-2)
    assert plan.stop_basis == ["EMA 50", "Mínimo 20 sesiones"]  # confluencia de 2 tipos
    assert plan.components["catalyst"] == 40
    assert plan.components["confluence"] == 20
    assert plan.components["volume"] == 10
    assert plan.confidence == sum(plan.components.values())
    assert plan.passes_filters is True
    assert plan.reason_if_rejected is None
    assert 0 < plan.position_size_pct <= ta.MAX_POSITION_PCT
    assert 3 <= plan.timeframe_days <= 20
    assert plan.exit_rules and plan.limitations


def test_plan_rechazado_por_riesgo_beneficio(monkeypatch):
    df = _ohlcv(_trend(260, step=0.3))
    plan, _, _ = _plan_with_levels(monkeypatch, df, "LONG", [("Mínimo 60 sesiones", -2.5), ("Pivot R1", 1.0)])
    assert plan.risk_reward < ta.MIN_RISK_REWARD
    assert plan.passes_filters is False
    assert "riesgo/beneficio" in plan.reason_if_rejected.lower()


def test_plan_rechazado_si_el_catalizador_no_esta_confirmado(monkeypatch):
    df = _ohlcv(_trend(260, step=0.3))
    plan, _, _ = _plan_with_levels(
        monkeypatch, df, "LONG", [("Mínimo 20 sesiones", -1.0), ("Máximo 20 sesiones", 4.0)], catalyst=False
    )
    assert plan.components["catalyst"] == 0
    assert plan.passes_filters is False
    assert "catalizador" in plan.reason_if_rejected.lower()


def test_sin_soporte_el_stop_va_por_atr_y_no_pasa(monkeypatch):
    df = _ohlcv(_trend(260, step=0.3))
    plan, entry, a = _plan_with_levels(monkeypatch, df, "LONG", [("Máximo 20 sesiones", 6.0)])
    assert plan.stop == pytest.approx(entry - ta.FALLBACK_STOP_ATR * a, rel=1e-3)
    assert plan.checks["stop_on_support"] is False
    assert plan.passes_filters is False


def test_plan_corto_es_simetrico(monkeypatch):
    df = _ohlcv(_trend(260, step=-0.3, start=150))
    plan, entry, a = _plan_with_levels(
        monkeypatch, df, "SHORT", [("Máximo 20 sesiones", 1.0), ("Mínimo 20 sesiones", -3.0)]
    )
    assert plan.stop > entry > plan.target
    assert plan.stop == pytest.approx(entry + 1.0 * a + ta.STOP_BUFFER_ATR * a, rel=1e-3)
    assert plan.risk_reward == pytest.approx(3.0 / 1.25, rel=1e-2)


def test_historico_insuficiente():
    plan = ta.build_trade_plan(_ohlcv(_trend(30)), "LONG", True)
    assert plan.passes_filters is False
    assert plan.entry is None
    assert "insuficiente" in plan.reason_if_rejected


def test_volatilidad_alta_reduce_el_tamano(monkeypatch):
    calm = np.full(200, 100.0)
    wild = 100 + np.random.default_rng(3).normal(0, 4, 60)
    df = _ohlcv(np.concatenate([calm, wild]))
    df.iloc[-60:, df.columns.get_loc("high")] = df["close"].iloc[-60:] + 4
    df.iloc[-60:, df.columns.get_loc("low")] = df["close"].iloc[-60:] - 4
    plan, _, _ = _plan_with_levels(monkeypatch, df, "LONG", [("Mínimo 20 sesiones", -1.0), ("Máximo 20 sesiones", 4.0)])
    assert plan.position_size_pct < ta.REDUCED_POSITION_PCT


def test_niveles_reales_incluyen_pivots_fibonacci_y_medias():
    df = _ohlcv(_trend(260, step=0.3))
    kinds = {lv["kind"] for lv in ta.key_levels(df, ta.compute_indicators(df))}
    assert {"Pivot S1", "Pivot R1", "Fibonacci 61.8%", "EMA 50", "EMA 200", "VWAP 20", "Mínimo 20 sesiones"} <= kinds


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE technical_analyses, event_analyses, prices, events, universe RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.close()


def _event(conn, ticker, d0, source="EDGAR", event_class="8K_2.02_EARNINGS", decision="LONG", conviction=0.5, rumor=False):
    import json

    cik = f"C{abs(hash((ticker, d0, source))) % 10_000_000:07d}"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) VALUES (%s,%s,'X',%s,%s) "
            "ON CONFLICT (cik) DO NOTHING",
            (cik, ticker, d0, d0),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes, accession_number, source_url,
                                filed_at, d0_close_date, classification_method, classification_confidence, raw_text_hash)
            VALUES (%s,%s,%s,%s,%s,ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s) RETURNING event_id
            """,
            (cik, ticker, source, source != "EDGAR", event_class, f"acc-{cik}-{d0}", datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc), d0, f"h-{cik}-{d0}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            """
            INSERT INTO event_analyses (event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation, n_historical_analogues,
                ev_calculation, ev_conservative, ev_aggressive, ev_balanced, abstention_decision,
                trade_decision_conservative, trade_decision_aggressive, trade_decision_balanced,
                model_version_bull_bear, model_version_judge)
            VALUES (%s, 70, %s, '{}', '{}', '{}', %s, 70, '{}', 5, '{}', 0.01, 0.02, 0.015, '{}', 'NO_TRADE', %s, %s, 'm1', 'm2')
            """,
            (event_id, json.dumps({"rumor_flag": rumor}), conviction, decision, decision),
        )
    conn.commit()
    return event_id


def _prices(conn, ticker, end: date, n=260, after=0):
    closes = _trend(n + after, step=0.3)
    days = pd.bdate_range(end=end, periods=n).tolist() + pd.bdate_range(start=end + timedelta(days=1), periods=after).tolist()
    with conn.cursor() as cur:
        for d, c in zip(days, closes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, high_raw, low_raw, close_raw, adj_factor, volume) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,1000000)",
                (ticker, d.date(), c, c + 0.5, c - 0.5, c),
            )
    conn.commit()


@pytestmark_db
def test_load_prices_nunca_lee_despues_de_d0(conn):
    d0 = date(2026, 3, 13)
    _prices(conn, "AAA", d0, n=100, after=15)
    df = ta.load_prices(conn, "AAA", d0)
    assert len(df) == 100
    assert df.index.max() <= d0


@pytestmark_db
def test_catalizador_fda_confirmado_solo_con_8k_previo(conn):
    d0 = date(2026, 3, 13)
    fda = _event(conn, "BIO", d0, source="FDA_OPENFDA", event_class="FDA_CRL")
    event = {"ticker": "BIO", "source": "FDA_OPENFDA", "d0_close_date": d0, "novelty_reasoning": {}}
    assert ta.is_catalyst_confirmed(conn, event) is False
    _event(conn, "BIO", d0 + timedelta(days=2), decision="NO_TRADE")  # 8-K POSTERIOR: no cuenta
    assert ta.is_catalyst_confirmed(conn, event) is False
    _event(conn, "BIO", d0 - timedelta(days=1), decision="NO_TRADE")
    assert ta.is_catalyst_confirmed(conn, event) is True
    assert fda


@pytestmark_db
def test_rumor_no_es_catalizador_confirmado(conn):
    event = {"ticker": "X", "source": "EDGAR", "d0_close_date": date(2026, 1, 5), "novelty_reasoning": {"rumor_flag": True}}
    assert ta.is_catalyst_confirmed(conn, event) is False


@pytestmark_db
def test_run_guarda_un_plan_por_senal_operable_e_idempotente(conn):
    d0 = date(2026, 3, 13)
    _prices(conn, "UP", d0)
    traded = _event(conn, "UP", d0)
    _event(conn, "NOPE", d0, decision="NO_TRADE")

    assert ta.run(conn)["analyzed"] == 1
    assert ta.run(conn)["analyzed"] == 0  # ya tiene plan
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM technical_analyses")
        rows = cur.fetchall()
    assert [r["event_id"] for r in rows] == [traded]
    row = rows[0]
    assert row["direction"] == "LONG"
    assert float(row["stop_price"]) < float(row["entry_price"])
    assert row["details"]["limitations"]
    assert 0 <= row["confidence"] <= 100


def test_los_niveles_menores_no_son_objetivo(monkeypatch):
    """Un VWAP o un pivot R1 justo encima no deben fijar el objetivo: entre
    tantos niveles menores casi siempre hay uno pegado al precio."""
    df = _ohlcv(_trend(260, step=0.3))
    ind = ta.compute_indicators(df)
    entry, a = ind["close"], ind["atr"]
    levels = [
        {"price": entry - 1.0 * a, "kind": "Mínimo 20 sesiones", "major": True},
        {"price": entry + 1.2 * a, "kind": "VWAP 20", "major": False},
        {"price": entry + 4.0 * a, "kind": "Máximo 60 sesiones", "major": True},
    ]
    monkeypatch.setattr(ta, "key_levels", lambda _df, _ind: levels)
    plan = ta.build_trade_plan(df, "LONG", True)
    assert plan.target == pytest.approx(entry + 4.0 * a, rel=1e-3)
    assert plan.target_basis == ["Máximo 60 sesiones"]


def test_niveles_reales_marcan_mayores_y_menores():
    df = _ohlcv(_trend(260, step=0.3))
    levels = {lv["kind"]: lv["major"] for lv in ta.key_levels(df, ta.compute_indicators(df))}
    assert levels["Máximo 60 sesiones"] is True and levels["Fibonacci 61.8%"] is True
    assert levels["VWAP 20"] is False and levels["Pivot R1"] is False
