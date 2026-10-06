"""test_event_analysis_pipeline.py — el orquestador completo, Etapas 1-8.

Tres frentes: check_fda_crl_without_8k (real Postgres), compute_day3_stats
(real Postgres, verificando las 3 ramas de recomendación), y un test de
integración de extremo a extremo de process_chunk() contra Postgres real con
un cliente Anthropic SIMULADO (no hay ANTHROPIC_API_KEY en este sandbox) que
responde con JSON válido según el esquema exacto de cada etapa — esto prueba
que TODO el cableado (enrichment -> novelty -> Bull/Bear/Judge -> impact ->
EV -> abstention -> event_analyses) funciona junto, incluida la caché de 24h
saltándose una segunda llamada al LLM para el mismo (ticker, event_class).
"""
import json
import os
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pipeline.tests.fake_batch_api import validar_requests_como_la_api

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture(autouse=True)
def ia_sin_fecha_de_corte(monkeypatch):
    """Los datos de prueba son de 2021, anteriores al corte real de los
    modelos (H-06). Para probar el camino CON IA, el corte se adelanta; los
    tests del camino sin IA lo vuelven a poner con `corte_real`."""
    from pipeline import config

    monkeypatch.setattr(config, "AI_VALIDATION_START", date(2000, 1, 3))


@pytest.fixture
def corte_real(monkeypatch):
    from pipeline import config

    monkeypatch.setattr(config, "AI_VALIDATION_START", config.primer_d0_validable_ia(config.ANALYZER_MODEL, config.JUDGE_MODEL))


class _ScriptedBatchesClient:
    """Responde con JSON válido para cualquier custom_id de bull/bear/judge,
    inspeccionando la request real en vez de una lista fija — así sirve para
    cualquier conjunto de eventos que le pase process_chunk()."""

    def __init__(self):
        self.call_count = 0

    def create(self, requests):
        validar_requests_como_la_api(requests)
        self.call_count += 1
        self._last_requests = requests
        return SimpleNamespace(id=f"batch_{self.call_count}", processing_status="ended")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        out = []
        for req in self._last_requests:
            custom_id = req["custom_id"]
            if custom_id.endswith("_bull"):
                payload = {"thesis": "bull thesis", "upside_drivers": ["d1"], "addressable_market": "big TAM", "comparable_events": "similar to X", "catalysts_forward": ["c1"]}
            elif custom_id.endswith("_bear"):
                payload = {"counter_thesis": "bear thesis", "downside_risks": ["r1"], "valuation_concern": "priced in", "historical_precedent": "failed at Y", "negative_catalysts": ["n1"]}
            elif custom_id.endswith("_judge"):
                payload = {"net_conviction": 0.6, "confidence_in_conviction": 80, "key_uncertainty": "u", "overriding_concern": "c"}
            else:
                continue
            content_block = SimpleNamespace(type="text", text=json.dumps(payload))
            message = SimpleNamespace(content=[content_block])
            result = SimpleNamespace(type="succeeded", message=message)
            out.append(SimpleNamespace(custom_id=custom_id, result=result))
        return out


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
            "fama_french_factors, universe, ai_batches RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()

@pytest.fixture
def sin_techo_de_ev(monkeypatch):
    """Para los tests que prueban la MECÁNICA de llamada a la IA (batch,
    caché, reconexión...) sin sembrar análogos: el techo de EV
    (BUGS_REPORT.md H-13) los descartaría antes, porque sin análogos el EV
    máximo es 0. El techo tiene sus propios tests."""
    monkeypatch.setattr("pipeline.analyze.event_analysis_pipeline.ev_ceiling_no_trade_reason", lambda *a, **k: None)



def _seed_market_data(conn, tickers_and_bases, n_days=320):
    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    with conn.cursor() as cur:
        for ticker, base in tickers_and_bases:
            for i, d in enumerate(dates):
                price = base * (1 + 0.0002 * i + rng.normal(0, 0.01))
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES (%s,%s,%s,%s,%s,1.0,100000,FALSE) ON CONFLICT (ticker, trade_date) DO NOTHING",
                    # high/low a +-0.1%: la cola de la IA exige que la barra de
                    # D0 tenga high/low (BUGS_REPORT.md H-39). El rango diario ya
                    # no decide la liquidez (H-08); la decide el ADV.
                    (ticker, d.date(), price, price * 1.001, price * 0.999),
                )
        for d in dates:
            cur.execute(
                "INSERT INTO fama_french_factors (trade_date, mkt_rf, smb, hml, rf) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (trade_date) DO NOTHING",
                (d.date(), float(rng.normal(0.0003, 0.008)), float(rng.normal(0.0001, 0.004)), float(rng.normal(-0.0001, 0.004)), 0.00005),
            )
    conn.commit()
    return dates


def _seed_event(conn, cik: str, ticker: str, d0: date, event_class: str = "8K_2.02_EARNINGS", sic_code: str = "2836", filing_text: str | None = None) -> int:
    # source debe reflejar de dónde vendría el evento de verdad: un FDA_CRL no
    # es un filing de EDGAR (se encontró este bug de fixture al ejecutar el
    # test de check_fda_crl_without_8k — con source hardcodeado a 'EDGAR', el
    # propio evento FDA_CRL se emparejaba consigo mismo como si fuera "el 8-K
    # correspondiente").
    source = "FDA_OPENFDA" if event_class.startswith("FDA_") else "EDGAR"
    is_satellite = event_class.startswith("FDA_")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, sic_code, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'Test Co',%s,%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, sic_code, d0, d0),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash, filing_text)
            VALUES (%s,%s,%s,%s,%s,ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s,%s)
            RETURNING event_id
            """,
            (cik, ticker, source, is_satellite, event_class, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}", filing_text),
        )
        event_id = cur.fetchone()["event_id"]
    conn.commit()
    return event_id


# ---------------------------------------------------------------------------
# check_fda_crl_without_8k
# ---------------------------------------------------------------------------


def test_fda_crl_without_8k_returns_false_for_non_crl_classes(conn):
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    assert check_fda_crl_without_8k(conn, "1", "8K_2.02_EARNINGS", date(2024, 1, 1)) is False


def test_fda_crl_without_8k_true_when_no_matching_8k_nearby(conn):
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    _seed_event(conn, "1", "BIOX", date(2024, 1, 1), event_class="FDA_CRL")
    assert check_fda_crl_without_8k(conn, "1", "FDA_CRL", date(2024, 1, 1)) is True


def test_fda_crl_without_8k_false_when_8k_already_filed_before_d0(conn):
    """Un 8-K anterior a D0 sí está disponible al decidir: la empresa ya lo ha
    comunicado, así que la regla 6 no veta."""
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    _seed_event(conn, "1", "BIOX", date(2024, 1, 10), event_class="FDA_CRL")
    _seed_event(conn, "1", "BIOX", date(2024, 1, 8), event_class="8K_8.01_OTHER")  # comunicado 2 días antes
    assert check_fda_crl_without_8k(conn, "1", "FDA_CRL", date(2024, 1, 10)) is False


def test_fda_crl_without_8k_ignores_8k_filed_after_d0(conn):
    """Regresión anti-look-ahead: un 8-K posterior a D0 no existe todavía en el
    momento de la decisión. Antes se buscaba en una ventana de ±10 días, así
    que este caso devolvía False (operar) usando información del futuro."""
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    _seed_event(conn, "1", "BIOX", date(2024, 1, 1), event_class="FDA_CRL")
    _seed_event(conn, "1", "BIOX", date(2024, 1, 3), event_class="8K_8.01_OTHER")  # aún no ocurrido en D0
    assert check_fda_crl_without_8k(conn, "1", "FDA_CRL", date(2024, 1, 1)) is True


# ---------------------------------------------------------------------------
# spend_today_usd / remaining_daily_budget_events — IMPROVEMENT_PLAN.md A2
# ---------------------------------------------------------------------------


def test_spend_today_usd_counts_only_real_llm_calls(conn):
    """Solo cuenta como gasto una fila con from_cache=FALSE y
    model_version_bull_bear distinto de 'SKIPPED_OBJECTIVE_NO_TRADE' — las
    otras dos no pagaron ninguna llamada real a la Batch API."""
    from pipeline.analyze.event_analysis_pipeline import spend_today_usd
    from pipeline import config

    eid_real = _seed_event(conn, "1", "REAL", date(2024, 1, 1))
    _seed_minimal_event_analysis(conn, eid_real, "NO_TRADE", "NO_TRADE", "NO_TRADE")

    eid_cache = _seed_event(conn, "2", "CACHED", date(2024, 1, 1))
    _seed_minimal_event_analysis(conn, eid_cache, "NO_TRADE", "NO_TRADE", "NO_TRADE")
    eid_skip = _seed_event(conn, "3", "SKIP", date(2024, 1, 1))
    _seed_minimal_event_analysis(conn, eid_skip, "NO_TRADE", "NO_TRADE", "NO_TRADE")
    with conn.cursor() as cur:
        cur.execute("UPDATE event_analyses SET from_cache = TRUE WHERE event_id = %s", (eid_cache,))
        cur.execute("UPDATE event_analyses SET model_version_bull_bear = 'SKIPPED_OBJECTIVE_NO_TRADE' WHERE event_id = %s", (eid_skip,))
    conn.commit()

    assert spend_today_usd(conn) == pytest.approx(config.ANALYSIS_EST_COST_PER_EVENT_USD)


def test_spend_today_usd_ignores_rows_from_other_days(conn):
    from pipeline.analyze.event_analysis_pipeline import spend_today_usd

    eid = _seed_event(conn, "1", "AYER", date(2024, 1, 1))
    _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "NO_TRADE", "NO_TRADE")
    with conn.cursor() as cur:
        cur.execute("UPDATE event_analyses SET analyzed_at = now() - interval '1 day' WHERE event_id = %s", (eid,))
    conn.commit()

    assert spend_today_usd(conn) == 0.0


def test_remaining_daily_budget_events_counts_down_from_the_cap(conn, monkeypatch):
    from pipeline.analyze import event_analysis_pipeline as eap
    from pipeline import config

    monkeypatch.setattr(config, "DAILY_SPEND_CAP_USD", config.ANALYSIS_EST_COST_PER_EVENT_USD * 3)

    assert eap.remaining_daily_budget_events(conn) == 3

    eid = _seed_event(conn, "1", "GASTADO", date(2024, 1, 1))
    _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "NO_TRADE", "NO_TRADE")

    assert eap.remaining_daily_budget_events(conn) == 2


def test_remaining_daily_budget_events_zero_when_cap_already_spent(conn, monkeypatch):
    from pipeline.analyze import event_analysis_pipeline as eap
    from pipeline import config

    monkeypatch.setattr(config, "DAILY_SPEND_CAP_USD", config.ANALYSIS_EST_COST_PER_EVENT_USD)
    eid = _seed_event(conn, "1", "GASTADO", date(2024, 1, 1))
    _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "NO_TRADE", "NO_TRADE")

    assert eap.remaining_daily_budget_events(conn) == 0


def test_remaining_daily_budget_events_none_when_cap_disabled(conn, monkeypatch):
    from pipeline.analyze import event_analysis_pipeline as eap
    from pipeline import config

    monkeypatch.setattr(config, "DAILY_SPEND_CAP_USD", 0.0)
    assert eap.remaining_daily_budget_events(conn) is None


# ---------------------------------------------------------------------------
# compute_day3_stats
# ---------------------------------------------------------------------------


def _seed_minimal_event_analysis(conn, event_id: int, trade_conservative: str, trade_aggressive: str, trade_balanced: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (%s, 60, '{}', '{}', '{}', '{}', 0.5, 70, '{}', 5, '{}', 0.01, 0.02, 0.015, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')
            """,
            (event_id, trade_conservative, trade_aggressive, trade_balanced),
        )
    conn.commit()


def test_day3_stats_flags_too_permissive_above_70pct(conn):
    from pipeline.analyze.event_analysis_pipeline import compute_day3_stats

    for i in range(10):
        eid = _seed_event(conn, str(i), f"T{i}", date(2022, 1, 1 + i))
        # 8 de 10 -> TRADE en Aggressive (80%, por encima de 70%)
        _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "LONG" if i < 8 else "NO_TRADE", "NO_TRADE")

    stats = compute_day3_stats(conn)
    assert stats["pct_trade_aggressive"] == pytest.approx(80.0)
    assert "demasiado permisivo" in stats["recommendation_aggressive"]


def test_day3_stats_flags_too_restrictive_below_10pct(conn):
    from pipeline.analyze.event_analysis_pipeline import compute_day3_stats

    for i in range(10):
        eid = _seed_event(conn, str(i), f"T{i}", date(2022, 1, 1 + i))
        _seed_minimal_event_analysis(conn, eid, "LONG" if i == 0 else "NO_TRADE", "NO_TRADE", "NO_TRADE")

    stats = compute_day3_stats(conn)
    assert stats["pct_trade_conservative"] == pytest.approx(10.0)
    # Límite exacto de 10%: no cae en "demasiado restrictivo" (regla es '< 10'), cae en rango esperado
    assert "rango esperado" in stats["recommendation_conservative"]


def test_day3_stats_in_expected_range(conn):
    from pipeline.analyze.event_analysis_pipeline import compute_day3_stats

    for i in range(10):
        eid = _seed_event(conn, str(i), f"T{i}", date(2022, 1, 1 + i))
        _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "LONG" if i < 3 else "NO_TRADE", "NO_TRADE")  # 30%

    stats = compute_day3_stats(conn)
    assert stats["pct_trade_aggressive"] == pytest.approx(30.0)
    assert "rango esperado" in stats["recommendation_aggressive"]


# ---------------------------------------------------------------------------
# process_chunk — integración de extremo a extremo con LLM simulado
# ---------------------------------------------------------------------------


class _ClientQueMataLaConexion:
    """Imita lo que le pasó a producción (run 34964242549): mientras
    run_batch_and_collect espera a la Batch API, la conexión a Postgres muere
    de verdad (Neon corta las conexiones ociosas; la espera real duró unos 20
    minutos). El primer retrieve() mata la conexión Y devuelve "ended" en la
    misma llamada, así el test no necesita dormir de verdad."""

    def __init__(self, conexion_a_matar):
        self._conn = conexion_a_matar
        self._matada = False
        self._ultima_tanda = None

    def create(self, requests):
        validar_requests_como_la_api(requests)
        self._ultima_tanda = requests
        return SimpleNamespace(id="batch_mortal", processing_status="in_progress")

    def retrieve(self, batch_id):
        if not self._matada:
            self._conn.close()
            self._matada = True
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        out = []
        for req in self._ultima_tanda:
            custom_id = req["custom_id"]
            if custom_id.endswith("_bull"):
                payload = {"thesis": "bull thesis", "upside_drivers": ["d1"], "addressable_market": "big TAM", "comparable_events": "similar to X", "catalysts_forward": ["c1"]}
            elif custom_id.endswith("_bear"):
                payload = {"counter_thesis": "bear thesis", "downside_risks": ["r1"], "valuation_concern": "priced in", "historical_precedent": "failed at Y", "negative_catalysts": ["n1"]}
            elif custom_id.endswith("_judge"):
                payload = {"net_conviction": 0.6, "confidence_in_conviction": 80, "key_uncertainty": "u", "overriding_concern": "c"}
            else:
                continue
            content_block = SimpleNamespace(type="text", text=json.dumps(payload))
            message = SimpleNamespace(content=[content_block])
            result = SimpleNamespace(type="succeeded", message=message)
            out.append(SimpleNamespace(custom_id=custom_id, result=result))
        return out


def test_process_chunk_reconecta_si_la_conexion_muere_durante_la_espera_del_batch(conn, sin_techo_de_ev):
    """EL bug real: 20 minutos esperando la Batch API dejaban la conexión
    ociosa hasta que Postgres la cortaba. El síntoma no salía en el batch —
    salía en el primer INSERT de después, con "the connection is lost", y el
    propio rollback de recuperación reventaba igual, tirando el chunk entero
    pese a que la IA ya había respondido. process_chunk tiene que detectarlo
    y reconectar ANTES de escribir, no después de que el primer INSERT
    reviente."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ClientQueMataLaConexion(conn)))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn_nueva = process_chunk(conn, client, events)

    assert conn_nueva is not conn  # se reconectó, no siguió con la muerta
    assert conn.closed  # la vieja, la que mató el fake client, sigue cerrada

    with conn_nueva.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        rows = cur.fetchall()
    assert len(rows) == 1  # el análisis se guardó pese a la reconexión
    conn_nueva.close()




def test_process_chunk_end_to_end_writes_full_event_analyses_row(conn, sin_techo_de_ev):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        rows = cur.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["from_cache"] is False
    assert row["model_version_bull_bear"] == "claude-haiku-4-5"
    assert row["model_version_judge"] == "claude-sonnet-4-6"
    assert row["trade_decision_conservative"] in ("LONG", "SHORT", "NO_TRADE")
    assert float(row["net_conviction"]) == pytest.approx(0.6)
    assert json.loads(row["bull_analyst_output"])["thesis"] == "bull thesis" if isinstance(row["bull_analyst_output"], str) else row["bull_analyst_output"]["thesis"] == "bull thesis"


def test_process_chunk_second_event_same_ticker_class_uses_cache_not_llm(conn, sin_techo_de_ev):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted_client.call_count == 2  # una llamada para bull/bear, otra para judge

    # Segundo evento: mismo ticker, misma clase, al día hábil siguiente (mismo
    # episodio, p. ej. una corrección) -> debe reusar Bull/Bear/Judge de caché
    # y NO generar nuevas llamadas al cliente.
    _seed_event(conn, "1", "TESTCO", dates[281].date())
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

    assert scripted_client.call_count == 2  # sin llamadas nuevas: se sirvió de caché

    with conn.cursor() as cur:
        cur.execute("SELECT from_cache, net_conviction FROM event_analyses ORDER BY event_id")
        rows = cur.fetchall()
    assert rows[0]["from_cache"] is False
    assert rows[1]["from_cache"] is True
    assert float(rows[1]["net_conviction"]) == pytest.approx(float(rows[0]["net_conviction"]))


def test_process_chunk_otro_trimestre_del_mismo_ticker_no_usa_cache(conn, sin_techo_de_ev):
    """Dos semanas después ya es otro filing: reutilizar el veredicto del
    anterior daría a todos los trimestres de una empresa la misma opinión."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())
    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    _seed_event(conn, "1", "TESTCO", dates[290].date())
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted_client.call_count == 4


def test_process_chunk_skips_llm_for_low_novelty_event_but_still_forces_no_trade(conn):
    """Optimización de coste (pedida explícitamente para gastar menos en la
    API de Anthropic): abstention_engine.NOVELTY_FLOOR hace NO_TRADE en las 3
    estrategias para cualquier evento con novelty_score por debajo del
    umbral, SIN mirar lo que diga Bull/Bear/Judge — es la PRIMERA de las 7
    condiciones que evalúa decide_for_strategy (ver su docstring). Pagar el
    debate de IA en ese caso no cambia ni una sola decisión, así que
    process_chunk debe descartarlo ANTES de construir el batch, no después.

    Se fuerza aquí un pre_event_drift_pct enorme (el precio de TESTCO ya subió
    un 30% justo antes del evento, así que el mercado lo tenía completamente
    descontado) — eso hunde novelty.score muy por debajo de NOVELTY_FLOOR=20
    (ver DRIFT_SATURATION_PCT=8.0 en novelty.py). Se verifica que el cliente
    de IA scripted nunca recibe una sola request Y que el veredicto final es
    idéntico al que habría dado el mismo camino con Bull/Bear/Judge reales."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "TESTCO", d0)

    # dates[280] (d0) es 2022-01-31 (lunes): D-5 calendario cae en 2022-01-26
    # (miércoles, índice 277 — un business day en sí mismo, no un fin de
    # semana) y D-1 calendario cae en el domingo 2022-01-30, cuyo
    # nearest_at_or_before es el viernes 2022-01-28 (índice 279). Por eso el
    # corte va en 278: todo lo anterior (incluido 277 = D-5) se deja en 50,
    # y desde 278 (que cubre 279 = D-1) se sube a 65 — verificado con un
    # cálculo directo de fetch_and_compute_enrichment antes de escribir esto,
    # no a ojo (un desajuste de un índice aquí deja el drift en 0%, no en 30%).
    with conn.cursor() as cur:
        for i in range(260, 278):
            cur.execute("UPDATE prices SET close_raw=50.0, high_raw=50.5, low_raw=49.5 WHERE ticker='TESTCO' AND trade_date=%s", (dates[i].date(),))
        for i in range(278, 281):
            cur.execute("UPDATE prices SET close_raw=65.0, high_raw=65.5, low_raw=64.5 WHERE ticker='TESTCO' AND trade_date=%s", (dates[i].date(),))
    conn.commit()

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)

    assert scripted_client.call_count == 0  # cero llamadas a la Batch API: cero tokens gastados

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        row = cur.fetchone()
    assert float(row["novelty_score"]) < 20
    assert row["model_version_bull_bear"] == "SKIPPED_OBJECTIVE_NO_TRADE"
    assert row["model_version_judge"] == "SKIPPED_OBJECTIVE_NO_TRADE"
    assert row["trade_decision_conservative"] == "NO_TRADE"
    assert row["trade_decision_aggressive"] == "NO_TRADE"
    assert row["trade_decision_balanced"] == "NO_TRADE"
    bull_output = row["bull_analyst_output"] if isinstance(row["bull_analyst_output"], dict) else json.loads(row["bull_analyst_output"])
    assert bull_output["skipped_no_llm_needed"] is True
    assert "novelty_score" in bull_output["reason"]  # la razón concreta es la de novelty, no otra de las 5


def test_process_chunk_skips_llm_for_survivorship_warning_event(conn):
    """Extiende el pre-filtro objetivo pre-LLM (hallazgo de auditoría R5,
    IMPROVEMENT_PLAN.md) más allá de novelty: un ticker con WARNING de
    posible deslistado en la ventana [D-5,D0] (regla 4 de
    abstention_engine.decide_for_strategy) es NO_TRADE garantizado sin mirar
    lo que diga el Judge — no hace falta pagar el debate de IA."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "TESTCO", d0)

    with conn.cursor() as cur:
        cur.execute("UPDATE prices SET survivorship_warning=TRUE WHERE ticker='TESTCO' AND trade_date=%s", (d0,))
    conn.commit()

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)
    assert scripted_client.call_count == 0  # cero llamadas a la Batch API: cero tokens gastados

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        row = cur.fetchone()
    assert row["model_version_bull_bear"] == "SKIPPED_OBJECTIVE_NO_TRADE"
    assert row["trade_decision_conservative"] == "NO_TRADE"
    assert row["trade_decision_aggressive"] == "NO_TRADE"
    assert row["trade_decision_balanced"] == "NO_TRADE"
    bull_output = row["bull_analyst_output"] if isinstance(row["bull_analyst_output"], dict) else json.loads(row["bull_analyst_output"])
    assert bull_output["skipped_no_llm_needed"] is True
    assert "deslistado" in bull_output["reason"]


def test_process_chunk_skips_llm_for_fda_crl_without_8k(conn):
    """Ídem, para la regla 6 (CRL de FDA sin 8-K correspondiente): es
    objetiva (solo consulta `events`), así que también se puede comprobar
    antes de invocar Bull/Bear/Judge."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("PHARMACO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "PHARMACO", d0, event_class="FDA_CRL")  # sin ningún 8-K de EDGAR cerca

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)
    assert scripted_client.call_count == 0

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        row = cur.fetchone()
    assert row["model_version_bull_bear"] == "SKIPPED_OBJECTIVE_NO_TRADE"
    assert row["trade_decision_conservative"] == "NO_TRADE"
    bull_output = row["bull_analyst_output"] if isinstance(row["bull_analyst_output"], dict) else json.loads(row["bull_analyst_output"])
    assert bull_output["skipped_no_llm_needed"] is True
    assert "CRL" in bull_output["reason"]


def test_process_chunk_skips_llm_for_illiquid_low_adv_event(conn):
    """Ídem, para el segundo componente del proxy de liquidez (regla 7,
    ADV de los 60 días de negociación ANTERIORES a D0 por debajo de
    config.MIN_ADV_USD) — el primer componente (spread) ya lo cubre
    indirectamente el resto de tests de este fichero, que dependen de que
    _seed_market_data mantenga un spread estrecho."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("MICROCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "MICROCO", d0)

    # Los 60 días de negociación ANTERIORES a D0 (dates[220..279]) con volumen
    # mínimo -> ADV muy por debajo de config.MIN_ADV_USD.
    with conn.cursor() as cur:
        for i in range(220, 280):
            cur.execute("UPDATE prices SET volume=10 WHERE ticker='MICROCO' AND trade_date=%s", (dates[i].date(),))
    conn.commit()

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)
    assert scripted_client.call_count == 0

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        row = cur.fetchone()
    assert row["model_version_bull_bear"] == "SKIPPED_OBJECTIVE_NO_TRADE"
    assert row["trade_decision_conservative"] == "NO_TRADE"
    bull_output = row["bull_analyst_output"] if isinstance(row["bull_analyst_output"], dict) else json.loads(row["bull_analyst_output"])
    assert bull_output["skipped_no_llm_needed"] is True
    assert "ADV" in bull_output["reason"]


def test_process_chunk_high_novelty_event_still_calls_llm_as_before(conn, sin_techo_de_ev):
    """Contraprueba de la anterior: un evento con novelty normal (el drift
    plano que ya seedeaba _seed_market_data, sin el salto de precio forzado)
    tiene que seguir llamando a Bull/Bear/Judge exactamente igual que antes
    del pre-filtro — el ahorro es solo para los eventos que iban a ser
    NO_TRADE de todas formas, nunca para los demás."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

    assert scripted_client.call_count == 2  # bull/bear + judge, como siempre
    with conn.cursor() as cur:
        cur.execute("SELECT model_version_bull_bear FROM event_analyses")
        row = cur.fetchone()
    assert row["model_version_bull_bear"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Fase 3 — filing_text real y guidance/rumor fluyendo hasta event_analyses
# ---------------------------------------------------------------------------


def test_process_chunk_bull_bear_prompt_contains_real_filing_text(conn):
    """Antes de la Fase 3, filing_excerpt era SIEMPRE el placeholder.
    Verifica que cuando events.filing_text tiene contenido real, ese texto
    (no el fallback) es lo que construye process_chunk() para el prompt de
    Bull/Bear — inspeccionando la construcción de EventContext tal como la
    hace process_chunk, sin necesidad de mockear el batch completo."""
    from pipeline.analyze.adversarial_analyzer import build_bull_bear_batch
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    real_text = "Acme Widgets Corp reported record quarterly revenue of $412 million, up 18% year-over-year."
    _seed_event(conn, "1", "TESTCO", dates[280].date(), filing_text=real_text)

    from pipeline.analyze.adversarial_analyzer import EventContext

    ev = fetch_events_needing_analysis(conn)[0]
    ctx = EventContext(event_id=ev["event_id"], ticker=ev["ticker"], event_class=ev["event_class"], company_name="Test Co", filing_excerpt=ev["filing_text"])
    requests_ = build_bull_bear_batch([ctx])
    prompt = requests_[0]["params"]["messages"][0]["content"]
    assert real_text in prompt


def test_process_chunk_without_filing_text_falls_back_gracefully(conn):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis

    _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    dates = pd.date_range("2021-01-04", periods=320, freq="B")
    _seed_event(conn, "1", "TESTCO", dates[280].date())  # sin filing_text

    ev = fetch_events_needing_analysis(conn)[0]
    assert ev["filing_text"] is None  # confirma el escenario que se está probando


def test_process_chunk_novelty_reasoning_reflects_prior_guidance_detection(conn):
    """Un filing PREVIO con lenguaje de guidance debe hacer que
    novelty_reasoning de ESTE evento registre has_prior_guidance=True — la
    señal completa de la Fase 3, de principio a fin."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    # Evento previo (hace 15 días de calendario) con lenguaje de guidance explícito.
    prior_date = dates[280].date() - pd.Timedelta(days=15)
    _seed_event(conn, "1", "TESTCO", prior_date, filing_text="We are raising our full-year outlook to $2B.")
    # Evento actual, sin texto propio todavía (el guidance viene del PREVIO).
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

    with conn.cursor() as cur:
        cur.execute("SELECT novelty_reasoning FROM event_analyses ea JOIN events e ON e.event_id=ea.event_id WHERE e.d0_close_date = %s", (dates[280].date(),))
        row = cur.fetchone()
    reasoning = row["novelty_reasoning"] if isinstance(row["novelty_reasoning"], dict) else json.loads(row["novelty_reasoning"])
    assert reasoning["has_prior_guidance"] is True


def test_judge_fuera_de_rango_no_se_guarda(conn, sin_techo_de_ev):
    """P0-2: sin minimum/maximum en el esquema, el rango se valida en Python.
    Un Judge con net_conviction=3 no debe llegar a event_analyses (ni recortado
    a 1: sería la convicción máxima inventada)."""
    from pipeline.analyze import event_analysis_pipeline as eap

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    class _JudgeDesbocado(_ScriptedBatchesClient):
        def results(self, batch_id):
            out = super().results(batch_id)
            for r in out:
                if r.custom_id.endswith("_judge"):
                    r.result.message.content[0].text = json.dumps(
                        {"net_conviction": 3, "confidence_in_conviction": 80, "key_uncertainty": "u", "overriding_concern": "c"}
                    )
            return out

    client = SimpleNamespace(messages=SimpleNamespace(batches=_JudgeDesbocado()))
    conn = eap.process_chunk(conn, client, eap.fetch_events_needing_analysis(conn))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM event_analyses")
        assert cur.fetchone()["n"] == 0


def test_spend_today_usd_cuenta_los_batches_enviados_aunque_no_se_guarde_nada(conn):
    """BUGS_REPORT.md H-24: un Bull/Bear pagado cuyo Judge falla no deja fila
    en event_analyses, pero sí se pagó. El libro ai_batches lo cuenta."""
    from pipeline import config
    from pipeline.analyze.event_analysis_pipeline import registrar_batch, spend_today_usd

    assert spend_today_usd(conn) == 0
    registrar_batch(conn, "bull_bear")("batch_bb", 10)
    registrar_batch(conn, "judge")("batch_j", 3)
    registrar_batch(conn, "judge")("batch_j", 3)  # repetido: no se cuenta dos veces

    esperado = 10 * config.EST_COST_BULL_BEAR_REQUEST_USD + 3 * config.EST_COST_JUDGE_REQUEST_USD
    assert spend_today_usd(conn) == pytest.approx(esperado)


def test_el_desglose_por_request_cuadra_con_el_coste_por_evento():
    from pipeline import config

    assert 2 * config.EST_COST_BULL_BEAR_REQUEST_USD + config.EST_COST_JUDGE_REQUEST_USD == pytest.approx(
        config.ANALYSIS_EST_COST_PER_EVENT_USD
    )


def test_registrar_batch_apunta_aunque_la_conexion_haya_muerto_en_la_espera(conn):
    """El batch del Judge se apunta DESPUÉS de esperar al de Bull/Bear (hasta
    ~20 min), y Neon cierra las conexiones ociosas. Con la conexión muerta el
    apunte fallaba en silencio y el gasto del Judge (~60 % del coste) no
    contaba para el tope diario. Debe apuntarse con una conexión nueva."""
    from pipeline.analyze.event_analysis_pipeline import registrar_batch
    from pipeline.db.connection import get_connection

    muerta = get_connection()
    muerta.close()
    registrar_batch(muerta, "judge")("batch_tras_espera", 7)

    with conn.cursor() as cur:
        cur.execute("SELECT kind, n_requests FROM ai_batches WHERE batch_id = 'batch_tras_espera'")
        fila = cur.fetchone()
    assert fila is not None and fila["kind"] == "judge" and fila["n_requests"] == 7


def test_spend_today_usd_usa_el_coste_real_cuando_el_batch_termino(conn):
    """Con el coste real apuntado, el tope diario usa ese y no la estimación;
    el batch aún sin cerrar sigue contando con la estimación."""
    from pipeline import config
    from pipeline.analyze.event_analysis_pipeline import cerrar_batch, registrar_batch, spend_today_usd

    registrar_batch(conn, "bull_bear")("batch_real", 100)
    cerrar_batch(conn, "bull_bear")(
        "batch_real", {"model": "claude-haiku-4-5", "input_tokens": 300_000, "output_tokens": 40_000, "n_responses": 100}
    )
    registrar_batch(conn, "judge")("batch_en_curso", 10)

    real = (300_000 * 1.0 + 40_000 * 5.0) / 1e6 * 0.5
    assert spend_today_usd(conn) == pytest.approx(real + 10 * config.EST_COST_JUDGE_REQUEST_USD, abs=1e-4)
    with conn.cursor() as cur:
        cur.execute("SELECT input_tokens, output_tokens, model, cost_usd, finished_at FROM ai_batches WHERE batch_id = 'batch_real'")
        fila = cur.fetchone()
    assert fila["input_tokens"] == 300_000 and fila["output_tokens"] == 40_000
    assert float(fila["cost_usd"]) == pytest.approx(real) and fila["finished_at"] is not None


def test_cerrar_batch_sin_precio_deja_la_estimacion(conn):
    from pipeline import config
    from pipeline.analyze.event_analysis_pipeline import cerrar_batch, registrar_batch, spend_today_usd

    registrar_batch(conn, "judge")("batch_raro", 5)
    cerrar_batch(conn, "judge")("batch_raro", {"model": "claude-futuro-9", "input_tokens": 1, "output_tokens": 1, "n_responses": 5})
    assert spend_today_usd(conn) == pytest.approx(5 * config.EST_COST_JUDGE_REQUEST_USD)


def test_cerrar_batch_apunta_aunque_la_conexion_haya_muerto(conn):
    from pipeline.analyze.event_analysis_pipeline import cerrar_batch, registrar_batch
    from pipeline.db.connection import get_connection

    registrar_batch(conn, "judge")("batch_x", 2)
    muerta = get_connection()
    muerta.close()
    cerrar_batch(muerta, "judge")("batch_x", {"model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 10, "n_responses": 2})
    with conn.cursor() as cur:
        cur.execute("SELECT cost_usd FROM ai_batches WHERE batch_id = 'batch_x'")
        assert cur.fetchone()["cost_usd"] is not None


# ---------------------------------------------------------------------------
# Un solo análisis por filing (BUGS_REPORT.md H-20)
# ---------------------------------------------------------------------------


def test_un_8k_con_varios_items_paga_un_solo_debate(conn, sin_techo_de_ev):
    """2.02 + 5.02 en el mismo 8-K son dos eventos pero un filing: se envía
    un debate (2 Bull/Bear + 1 Judge), el prompt nombra los dos Items y los
    dos eventos quedan analizados con el mismo resultado."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "TESTCO", d0, event_class="8K_2.02_EARNINGS")
    _seed_event(conn, "1", "TESTCO", d0, event_class="8K_5.02_EXEC_CHANGE")  # mismo accession (acc-1-d0)

    scripted = _ScriptedBatchesClient()
    enviados = []
    original_create = scripted.create

    def _create(requests):
        enviados.append(list(requests))
        return original_create(requests)

    scripted.create = _create
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

    assert [len(r) for r in enviados] == [2, 1]
    prompt = enviados[0][0]["params"]["messages"][0]["content"]
    assert "8K_2.02_EARNINGS + 8K_5.02_EXEC_CHANGE" in str(prompt)
    with conn.cursor() as cur:
        cur.execute("SELECT net_conviction, model_version_judge FROM event_analyses ORDER BY event_id")
        rows = cur.fetchall()
    assert len(rows) == 2
    assert all(float(r["net_conviction"]) == pytest.approx(0.6) for r in rows)
    assert all(r["model_version_judge"] == "claude-sonnet-4-6" for r in rows)


def test_filings_distintos_del_mismo_dia_no_se_agrupan():
    from pipeline.analyze.event_analysis_pipeline import agrupar_por_filing

    d0 = date(2024, 5, 1)
    base = {"source": "EDGAR", "ticker": "X", "d0_close_date": d0}
    eventos = [
        {**base, "event_id": 1, "accession_number": "a1"},
        {**base, "event_id": 2, "accession_number": "a2"},
        {**base, "event_id": 3, "accession_number": "a1"},
        {**base, "event_id": 4, "accession_number": None, "source": "FDA_OPENFDA"},
        {**base, "event_id": 5, "accession_number": None, "source": "FDA_OPENFDA"},
    ]
    grupos = [[e["event_id"] for e in g] for g in agrupar_por_filing(eventos)]
    assert grupos == [[1, 3], [2], [4], [5]]


def test_un_item_del_mismo_filing_analizado_antes_sirve_de_cache(conn, sin_techo_de_ev):
    """Si un Item del filing ya se analizó (en otra corrida o en otro chunk),
    el resto del mismo filing lo reutiliza aunque sea de otra clase."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "TESTCO", d0, event_class="8K_2.02_EARNINGS")
    scripted = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted.call_count == 2

    _seed_event(conn, "1", "TESTCO", d0, event_class="8K_5.02_EXEC_CHANGE")
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted.call_count == 2
    with conn.cursor() as cur:
        cur.execute("SELECT from_cache FROM event_analyses ORDER BY event_id")
        assert [r["from_cache"] for r in cur.fetchall()] == [False, True]


# ---------------------------------------------------------------------------
# Grupo de control: la decisión SIN IA (auditoría, Tanda 1)
# ---------------------------------------------------------------------------


def _entradas(**cambios):
    from pipeline.analyze.abstention_engine import AbstentionInputs

    base = dict(
        novelty_score=80.0, confidence_in_conviction=90.0, net_conviction=0.9,
        ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": 0.05, "AGGRESSIVE": 0.05},
        had_survivorship_warning=False, beta_available=True, adv_usd_60d=1e9, is_fda_crl_without_8k=False,
    )
    base.update(cambios)
    return AbstentionInputs(**base)


def _impacto(direccion, magnitud, confianza, n=60):
    from pipeline.analyze.historical_analogues import ImpactEstimate

    return ImpactEstimate(50.0, 20.0, 5.0, direccion, magnitud, 10.0, confianza, n)


def test_sin_ia_solo_cambia_el_origen_de_net_conviction():
    """El control usa la dirección de los análogos y TODO lo demás de la
    decisión con IA, incluida la confianza del Judge (sin contar dos veces
    la de los análogos)."""
    from pipeline.analyze.ev_engine import compute_ev
    from pipeline.analyze.event_analysis_pipeline import decision_sin_ia

    # La IA dice LONG (0,9) con confianza 90; los análogos dicen bajada.
    control = decision_sin_ia(_entradas(), _impacto(-1.0, -6.0, 70.0), None)
    assert control["net_conviction"] == -1.0
    assert control["confidence_in_conviction"] == 90.0  # la del Judge
    esperado = compute_ev(-1.0, 90.0, -6.0, 70.0)
    assert control["ev_balanced"] == pytest.approx(esperado.ev_balanced)
    assert control["decisiones"]["BALANCED"]["trade_decision"] == "SHORT"
    assert control["metodo"] == "analogos_signo_v3" and control["n_analogues"] == 60


@pytest.mark.parametrize("direccion", [1.0, -1.0, 0.0])
@pytest.mark.parametrize("confianza", [20.0, 55.0, 95.0])
@pytest.mark.parametrize("cambios", [{}, {"adv_usd_60d": 1.0}, {"novelty_score": 5.0}, {"beta_available": False}])
def test_con_la_misma_net_conviction_ia_y_control_dan_el_mismo_ev_y_decision(direccion, confianza, cambios):
    """La garantía del grupo de control: con la misma net_conviction de
    entrada, la versión con IA y el control producen el MISMO EV y la MISMA
    decisión en las 3 estrategias (misma fórmula, umbral y abstención)."""
    from pipeline.analyze.abstention_engine import as_json
    from pipeline.analyze.event_analysis_pipeline import decision_sin_ia, evaluar_decision

    impacto = _impacto(direccion, 4.0 * (direccion or 1.0), 80.0)
    entradas = _entradas(net_conviction=direccion, confidence_in_conviction=confianza, **cambios)
    ev_ia, decisiones_ia = evaluar_decision(entradas, impacto, None)
    control = decision_sin_ia(entradas, impacto, None)
    assert control["ev_conservative"] == ev_ia.ev_conservative
    assert control["ev_balanced"] == ev_ia.ev_balanced
    assert control["ev_aggressive"] == ev_ia.ev_aggressive
    assert control["decisiones"] == as_json(decisiones_ia)


def test_sin_ia_aplica_la_misma_abstencion():
    from pipeline.analyze.event_analysis_pipeline import decision_sin_ia

    # Judge inseguro: la regla de confianza (< 40) descarta también el control.
    control = decision_sin_ia(_entradas(confidence_in_conviction=20.0), _impacto(1.0, 8.0, 95.0), None)
    assert all(d["trade_decision"] == "NO_TRADE" for d in control["decisiones"].values())
    # Iliquidez: misma regla objetiva aunque los análogos sean buenos.
    control = decision_sin_ia(_entradas(adv_usd_60d=1.0), _impacto(1.0, 8.0, 95.0), None)
    assert all(d["trade_decision"] == "NO_TRADE" for d in control["decisiones"].values())


def test_sin_ia_un_evento_descartado_antes_de_la_ia_es_no_trade_por_el_mismo_motivo():
    from pipeline.analyze.event_analysis_pipeline import decision_sin_ia

    control = decision_sin_ia(_entradas(), _impacto(1.0, 8.0, 95.0), "novelty baja")
    assert {d["reason_if_no_trade"] for d in control["decisiones"].values()} == {"novelty baja"}


@pytest.mark.parametrize("net_ia", [0.9, -0.9, 0.0])
@pytest.mark.parametrize("confianza_ia", [10.0, 55.0, 95.0])
def test_la_regla_historica_no_usa_nada_de_la_ia(net_ia, confianza_ia):
    """H-06: la regla del backtest histórico no depende ni de la dirección ni
    de la confianza de la IA. Cambiar las dos no cambia nada: el EV es el de
    signo de los análogos con confianza 100 (factor neutro)."""
    from pipeline.analyze.ev_engine import compute_ev
    from pipeline.analyze.event_analysis_pipeline import regla_historica

    impacto = _impacto(-1.0, -6.0, 70.0)
    regla = regla_historica(_entradas(net_conviction=net_ia, confidence_in_conviction=confianza_ia), impacto, None)
    referencia = regla_historica(_entradas(), impacto, None)
    assert regla == referencia
    assert regla["net_conviction"] == -1.0 and regla["confidence_in_conviction"] == 100.0
    esperado = compute_ev(-1.0, 100.0, -6.0, 70.0)
    assert regla["ev_balanced"] == pytest.approx(esperado.ev_balanced)
    assert regla["decisiones"]["BALANCED"]["trade_decision"] == "SHORT"


def test_la_regla_historica_pasa_por_la_misma_evaluacion_y_abstencion():
    from pipeline.analyze.abstention_engine import as_json
    from pipeline.analyze.event_analysis_pipeline import evaluar_decision, regla_historica

    impacto = _impacto(1.0, 8.0, 95.0)
    entradas = _entradas(adv_usd_60d=1.0)
    ev, decisiones = evaluar_decision(_entradas(adv_usd_60d=1.0, net_conviction=1.0, confidence_in_conviction=100.0), impacto, None)
    regla = regla_historica(entradas, impacto, None)
    assert regla["decisiones"] == as_json(decisiones)
    assert all(d["trade_decision"] == "NO_TRADE" for d in regla["decisiones"].values())
    assert regla_historica(_entradas(), impacto, "novelty baja")["decisiones"]["BALANCED"]["reason_if_no_trade"] == "novelty baja"


def test_el_control_lleva_dentro_la_regla_historica():
    from pipeline.analyze.event_analysis_pipeline import decision_sin_ia, regla_historica

    impacto = _impacto(1.0, 8.0, 95.0)
    assert decision_sin_ia(_entradas(), impacto, None)["regla_historica"] == regla_historica(_entradas(), impacto, None)


def test_el_analisis_guarda_la_decision_sin_ia_junto_a_la_real(conn, sin_techo_de_ev):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())
    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    with conn.cursor() as cur:
        cur.execute("SELECT net_conviction, decision_sin_ia FROM event_analyses")
        fila = cur.fetchone()
    control = fila["decision_sin_ia"]
    assert control is not None and control["metodo"] == "analogos_signo_v3"
    assert set(control["decisiones"]) == {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}
    # Sin análogos sembrados el control no tiene dirección: nunca opera.
    assert control["net_conviction"] == 0.0
    assert all(d["trade_decision"] == "NO_TRADE" for d in control["decisiones"].values())


def _sembrar_analogos(conn, dates, n=30, car=0.05):
    """n eventos pasados de la misma clase con su CAR, para que los análogos
    den una dirección y una confianza no triviales."""
    ids = []
    for i in range(n):
        eid = _seed_event(conn, f"9{i:03d}", f"AN{i}", dates[100 + i].date())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO car_results (event_id, window_days, car, abnormal_volume_ratio, n_estimation_days) "
                "VALUES (%s, 20, %s, 1.2, 200)",
                (eid, car + 0.001 * (i % 3)),
            )
        ids.append(eid)
    conn.commit()
    return ids


def test_el_control_guardado_sigue_a_los_analogos(conn, sin_techo_de_ev):
    """Con 30 análogos positivos el control sin IA va en su dirección, y se
    guarda junto a la decisión de la IA (que en el cliente simulado dice 0,6)."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    analogos = _sembrar_analogos(conn, dates)
    objetivo = _seed_event(conn, "1", "TESTCO", dates[280].date())
    with conn.cursor() as cur:  # solo se analiza el evento objetivo
        cur.execute("UPDATE universe SET in_investable_universe = FALSE WHERE ticker LIKE 'AN%%'")
    conn.commit()
    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    eventos = [e for e in fetch_events_needing_analysis(conn) if e["event_id"] == objetivo]
    conn = process_chunk(conn, client, eventos)
    with conn.cursor() as cur:
        cur.execute("SELECT net_conviction, decision_sin_ia FROM event_analyses WHERE event_id = %s", (objetivo,))
        fila = cur.fetchone()
    control = fila["decision_sin_ia"]
    assert float(fila["net_conviction"]) == pytest.approx(0.6)
    assert control["net_conviction"] == 1.0
    assert control["n_analogues"] == len(analogos)
    assert control["confidence_in_conviction"] == pytest.approx(80.0)  # la del Judge simulado


def test_backfill_calcula_el_control_de_los_analisis_antiguos(conn, sin_techo_de_ev):
    from pipeline.analyze.event_analysis_pipeline import (
        backfill_decision_sin_ia,
        fetch_events_needing_analysis,
        process_chunk,
    )

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _sembrar_analogos(conn, dates)
    objetivo = _seed_event(conn, "1", "TESTCO", dates[280].date())
    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    eventos = [e for e in fetch_events_needing_analysis(conn) if e["event_id"] == objetivo]
    conn = process_chunk(conn, client, eventos)
    with conn.cursor() as cur:  # como si se hubiera calculado con la regla anterior
        cur.execute("""UPDATE event_analyses SET decision_sin_ia = '{"metodo": "analogos_v1"}'""")
    conn.commit()

    assert backfill_decision_sin_ia(conn) == 1
    assert backfill_decision_sin_ia(conn) == 0  # idempotente
    with conn.cursor() as cur:
        cur.execute("SELECT decision_sin_ia FROM event_analyses WHERE event_id = %s", (objetivo,))
        control = cur.fetchone()["decision_sin_ia"]
    assert control["recalculado"] is True and control["net_conviction"] == 1.0
    assert control["metodo"] == "analogos_signo_v3" and control["confidence_in_conviction"] == pytest.approx(80.0)
    assert control["regla_historica"]["confidence_in_conviction"] == 100.0
    assert control["regla_historica"]["net_conviction"] == 1.0


def test_rellenar_regla_sin_ia_no_se_atasca_con_un_analisis_que_falla(conn, sin_techo_de_ev, monkeypatch):
    """H-06 (revisión): el relleno corre por tandas hasta acabar, sin la IA;
    un análisis que falla no se reintenta en la misma corrida ni tapona la
    cola, y queda contado como pendiente en la cobertura."""
    from pipeline.analyze import event_analysis_pipeline as eap

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _sembrar_analogos(conn, dates)
    a = _seed_event(conn, "1", "TESTCO", dates[280].date())
    b = _seed_event(conn, "1", "TESTCO", dates[281].date())
    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    eventos = [e for e in eap.fetch_events_needing_analysis(conn) if e["event_id"] in (a, b)]
    conn = eap.process_chunk(conn, client, eventos)
    with conn.cursor() as cur:
        cur.execute("UPDATE event_analyses SET decision_sin_ia = NULL")
        cur.execute("SELECT count(*) AS n FROM event_analyses")
        total = cur.fetchone()["n"]
    conn.commit()

    original = eap.estimate_impact_for_event

    def falla_con_b(c, event_class, d0, event_id, window_days=20):
        if event_id == b:
            raise RuntimeError("dato roto")
        return original(c, event_class, d0, event_id, window_days=window_days)

    monkeypatch.setattr(eap, "estimate_impact_for_event", falla_con_b)
    resultado = eap.rellenar_regla_sin_ia(conn, tanda=1)
    assert resultado["fallidos"] == 1
    assert resultado["pendientes"] == 1 and resultado["con_regla"] == total - 1
    assert eap.cobertura_regla_sin_ia(conn)["pendientes"] == 1


# ---------------------------------------------------------------------------
# Eventos anteriores a la fecha de corte de los modelos (H-06, Tanda 3b):
# no van a la IA; se guardan con la regla sin IA, gratis.
# ---------------------------------------------------------------------------


def _con_capitalizacion(conn):
    """La cola filtra por capitalización actual (como la de la IA)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE universe SET market_cap_last_usd = 1e12, in_investable_universe = TRUE")
    conn.commit()


def test_un_evento_anterior_al_corte_no_va_a_la_ia_y_guarda_la_regla(conn, corte_real, sin_techo_de_ev):
    from pipeline.analyze.adversarial_analyzer import MODELO_ANTES_DEL_CORTE
    from pipeline.analyze.event_analysis_pipeline import (
        MOTIVO_ANTES_DEL_CORTE,
        analizar_antes_del_corte,
        fetch_events_needing_analysis,
        run_pipeline,
    )

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    analogos = _sembrar_analogos(conn, dates)
    objetivo = _seed_event(conn, "1", "TESTCO", dates[280].date())
    _con_capitalizacion(conn)
    with conn.cursor() as cur:  # solo se analiza el evento objetivo
        cur.execute("UPDATE universe SET in_investable_universe = FALSE WHERE ticker LIKE 'AN%%'")
    conn.commit()

    # La cola de la IA no lo ve: no se llama a la IA aunque haya cliente.
    scripted = _ScriptedBatchesClient()
    procesados, conn = run_pipeline(conn, SimpleNamespace(messages=SimpleNamespace(batches=scripted)), min_market_cap=0, require_d0_bar=True)
    assert procesados == 0 and scripted.call_count == 0

    # Sin cliente ni clave: se guarda con la regla sin IA.
    assert analizar_antes_del_corte(conn, min_market_cap=0) == 1
    assert analizar_antes_del_corte(conn, min_market_cap=0) == 0  # ya no está en la cola
    assert [e for e in fetch_events_needing_analysis(conn) if e["event_id"] == objetivo] == []
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses WHERE event_id = %s", (objetivo,))
        fila = cur.fetchone()
    assert fila["model_version_bull_bear"] == MODELO_ANTES_DEL_CORTE == fila["model_version_judge"]
    assert {fila["trade_decision_conservative"], fila["trade_decision_balanced"], fila["trade_decision_aggressive"]} == {"NO_TRADE"}
    assert fila["abstention_decision"]["BALANCED"]["reason_if_no_trade"] == MOTIVO_ANTES_DEL_CORTE
    regla = fila["decision_sin_ia"]["regla_historica"]
    # La regla decide con los análogos, no con el motivo del corte.
    assert regla["net_conviction"] == 1.0 and regla["confidence_in_conviction"] == 100.0
    assert regla["decisiones"]["BALANCED"]["reason_if_no_trade"] != MOTIVO_ANTES_DEL_CORTE
    assert fila["decision_sin_ia"]["n_analogues"] == len(analogos)


def test_el_relleno_de_una_fila_anterior_al_corte_no_la_descarta_por_el_corte(conn, corte_real, sin_techo_de_ev):
    from pipeline.analyze.event_analysis_pipeline import (
        MOTIVO_ANTES_DEL_CORTE,
        analizar_antes_del_corte,
        backfill_decision_sin_ia,
    )

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _sembrar_analogos(conn, dates)
    objetivo = _seed_event(conn, "1", "TESTCO", dates[280].date())
    _con_capitalizacion(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE universe SET in_investable_universe = FALSE WHERE ticker LIKE 'AN%%'")
    conn.commit()
    analizar_antes_del_corte(conn, min_market_cap=0)
    with conn.cursor() as cur:
        cur.execute("SELECT decision_sin_ia FROM event_analyses WHERE event_id = %s", (objetivo,))
        antes = cur.fetchone()["decision_sin_ia"]["regla_historica"]
        cur.execute("""UPDATE event_analyses SET decision_sin_ia = '{"metodo": "viejo"}' WHERE event_id = %s""", (objetivo,))
    conn.commit()
    assert backfill_decision_sin_ia(conn) >= 1
    with conn.cursor() as cur:
        cur.execute("SELECT decision_sin_ia FROM event_analyses WHERE event_id = %s", (objetivo,))
        despues = cur.fetchone()["decision_sin_ia"]["regla_historica"]
    assert despues["decisiones"]["BALANCED"]["reason_if_no_trade"] != MOTIVO_ANTES_DEL_CORTE
    assert despues["decisiones"] == antes["decisiones"]


def test_una_fila_anterior_al_corte_no_sirve_de_cache_para_la_ia(conn, monkeypatch, sin_techo_de_ev):
    """Mismo ticker y clase, un día después y ya tras el corte: la IA analiza
    de verdad, no copia la fila sin IA del día anterior."""
    from pipeline import config
    from pipeline.analyze.event_analysis_pipeline import analizar_antes_del_corte, run_pipeline

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    a = _seed_event(conn, "1", "TESTCO", dates[280].date())
    b = _seed_event(conn, "1", "TESTCO", dates[281].date())
    _con_capitalizacion(conn)
    monkeypatch.setattr(config, "AI_VALIDATION_START", dates[281].date())
    assert analizar_antes_del_corte(conn, min_market_cap=0) == 1
    scripted = _ScriptedBatchesClient()
    _, conn = run_pipeline(conn, SimpleNamespace(messages=SimpleNamespace(batches=scripted)), min_market_cap=0, require_d0_bar=True)
    assert scripted.call_count == 2  # Bull/Bear y Judge
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, from_cache, model_version_judge FROM event_analyses ORDER BY event_id")
        filas = {r["event_id"]: r for r in cur.fetchall()}
    assert filas[b]["from_cache"] is False and filas[b]["model_version_judge"] == config.JUDGE_MODEL
    assert a in filas


def test_el_gasto_estimado_no_cuenta_las_filas_sin_ia(conn, corte_real, sin_techo_de_ev):
    from pipeline.analyze.event_analysis_pipeline import analizar_antes_del_corte, spend_today_usd

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())
    _con_capitalizacion(conn)
    assert analizar_antes_del_corte(conn, min_market_cap=0) == 1
    assert spend_today_usd(conn) == 0


def test_sin_corte_conocido_no_se_guarda_nada_como_anterior_al_corte(conn, monkeypatch):
    """Sin corte no se sabe qué es anterior: guardarlo como tal lo dejaría
    fuera de la IA para siempre."""
    from pipeline import config
    from pipeline.analyze.event_analysis_pipeline import analizar_antes_del_corte

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())
    _con_capitalizacion(conn)
    monkeypatch.setattr(config, "AI_VALIDATION_START", None)
    assert analizar_antes_del_corte(conn, min_market_cap=0) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM event_analyses")
        assert cur.fetchone()["n"] == 0


def test_si_el_corte_se_mueve_atras_las_filas_sin_ia_vuelven_a_la_cola(conn, monkeypatch, sin_techo_de_ev):
    from pipeline import config
    from pipeline.analyze.event_analysis_pipeline import (
        analizar_antes_del_corte,
        fetch_events_needing_analysis,
        requeue_obsolete_skips,
    )

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    viejo = _seed_event(conn, "1", "TESTCO", dates[200].date())
    reciente = _seed_event(conn, "1", "TESTCO", dates[280].date())
    _con_capitalizacion(conn)
    monkeypatch.setattr(config, "AI_VALIDATION_START", dates[300].date())
    assert analizar_antes_del_corte(conn, min_market_cap=0) == 2
    assert requeue_obsolete_skips(conn) == 0  # los dos siguen siendo anteriores
    monkeypatch.setattr(config, "AI_VALIDATION_START", dates[250].date())
    assert requeue_obsolete_skips(conn) == 1
    pendientes = {e["event_id"] for e in fetch_events_needing_analysis(conn)}
    assert reciente in pendientes and viejo not in pendientes


def test_el_analisis_guarda_el_enrichment_con_el_vix(conn, sin_techo_de_ev):
    """H-23: event_enrichment no se escribía nunca y el filtro VIX del plan
    técnico leía una tabla vacía. Ahora se guarda con cada análisis, y el
    relleno lo añade a los antiguos."""
    from pipeline.analyze.event_analysis_pipeline import backfill_decision_sin_ia, fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    objetivo = _seed_event(conn, "1", "TESTCO", dates[280].date())
    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    conn = process_chunk(conn, client, [e for e in fetch_events_needing_analysis(conn) if e["event_id"] == objetivo])
    with conn.cursor() as cur:
        cur.execute("SELECT vix_d0, price_d0, adv_usd_60d FROM event_enrichment WHERE event_id = %s", (objetivo,))
        fila = cur.fetchone()
    assert fila is not None and fila["vix_d0"] is not None and fila["price_d0"] is not None

    with conn.cursor() as cur:  # como un análisis de antes del arreglo
        cur.execute("DELETE FROM event_enrichment")
    conn.commit()
    assert backfill_decision_sin_ia(conn) == 1
    assert backfill_decision_sin_ia(conn) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM event_enrichment WHERE vix_d0 IS NOT NULL")
        assert cur.fetchone()["n"] == 1
