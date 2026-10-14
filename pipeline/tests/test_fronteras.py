"""Tanda 13 «Fronteras limpias»: lo que separa un dato de otro en el tiempo.

- H-37: entre In-Sample y Out-of-Sample hay un embargo. Una operación que
  empieza al final del In-Sample se cerraría con precios del OOS (y su CAR
  también los usaría): esos eventos no entran en ninguna de las dos muestras.
  El In-Sample empieza en BACKTEST_START, como dice la documentación.
- H-17: si EDGAR no da la hora de aceptación, `filed_at` queda a las 00:00 del
  día del índice. Antes D0 era ese mismo día (como si se hubiera publicado
  antes del cierre); ahora se toma la sesión siguiente, lo conservador. Los
  eventos sin hora no entran en las estadísticas (CAR de análogos, prior,
  event study, backtest), y los antiguos con D0 dudoso no van a la IA.
- H-18: los días de cierre anticipado (13:00) un filing de las 14:00 ya es
  posterior al cierre.
"""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# H-18: cierres anticipados
# ---------------------------------------------------------------------------

# Cierres a las 13:00 de la NYSE en el histórico del proyecto (calendarios
# publicados por la NYSE): el día después de Acción de Gracias, el 24 de
# diciembre si cae de lunes a jueves y el 3 de julio si cae de lunes a jueves.
CIERRES_ANTICIPADOS = [
    date(2021, 11, 26),
    date(2022, 11, 25),
    date(2023, 7, 3),
    date(2023, 11, 24),
    date(2024, 7, 3),
    date(2024, 11, 29),
    date(2024, 12, 24),
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 11, 27),
    date(2026, 12, 24),
]
# Días que parecen candidatos pero tienen sesión completa o son festivo.
SIN_CIERRE_ANTICIPADO = [
    date(2021, 7, 2),    # el 4 cae domingo: festivo el lunes 5, el viernes 2 es normal
    date(2021, 12, 24),  # viernes: festivo (Navidad cae sábado)
    date(2022, 7, 1),    # el 4 cae lunes
    date(2022, 12, 23),  # el 24 cae sábado: el viernes 23 es normal
    date(2023, 12, 22),  # el 24 cae domingo
    date(2026, 7, 2),    # el 3 es festivo (el 4 cae sábado); el jueves 2 es normal
    date(2026, 7, 3),
    date(2024, 11, 28),  # Acción de Gracias: festivo, no cierre anticipado
]


@pytest.mark.parametrize("d", CIERRES_ANTICIPADOS)
def test_cierres_anticipados_a_las_13(d):
    from pipeline.ingest.market_calendar import cierre_anticipado, hora_de_cierre

    assert cierre_anticipado(d)
    assert hora_de_cierre(d) == time(13, 0)


@pytest.mark.parametrize("d", SIN_CIERRE_ANTICIPADO)
def test_dias_sin_cierre_anticipado(d):
    from pipeline.ingest.market_calendar import cierre_anticipado

    assert not cierre_anticipado(d)


def test_juneteenth_cierra_la_bolsa_desde_2022():
    from pipeline.ingest.market_calendar import dias_de_negociacion, festivos_del_anio

    assert date(2021, 6, 18) in dias_de_negociacion(date(2021, 6, 18), date(2021, 6, 18))
    assert date(2022, 6, 20) in festivos_del_anio(2022)


def test_un_dia_normal_cierra_a_las_16():
    from pipeline.ingest.market_calendar import hora_de_cierre

    assert hora_de_cierre(date(2024, 3, 14)) == time(16, 0)


def test_filing_tras_un_cierre_anticipado_pasa_a_la_sesion_siguiente():
    from pipeline.ingest.edgar_scraper import compute_d0_close_date

    # Viernes 29-11-2024: la bolsa cerró a las 13:00.
    assert compute_d0_close_date(datetime(2024, 11, 29, 14, 0, tzinfo=ET)) == date(2024, 12, 2)
    assert compute_d0_close_date(datetime(2024, 11, 29, 12, 30, tzinfo=ET)) == date(2024, 11, 29)
    # Martes 24-12-2024 a las 13:05: el 25 es festivo, así que el jueves 26.
    assert compute_d0_close_date(datetime(2024, 12, 24, 13, 5, tzinfo=ET)) == date(2024, 12, 26)


def test_la_hora_se_lee_en_nueva_york_aunque_llegue_en_otra_zona():
    from pipeline.ingest.edgar_scraper import compute_d0_close_date

    # 20:30 UTC = 16:30 en Nueva York (horario de verano): tras el cierre.
    assert compute_d0_close_date(datetime(2024, 6, 12, 20, 30, tzinfo=UTC)) == date(2024, 6, 13)
    # 19:30 UTC = 15:30 en Nueva York: antes del cierre.
    assert compute_d0_close_date(datetime(2024, 6, 12, 19, 30, tzinfo=UTC)) == date(2024, 6, 12)


# ---------------------------------------------------------------------------
# H-17: filing sin hora de aceptación
# ---------------------------------------------------------------------------


def test_sin_hora_de_aceptacion_d0_es_la_sesion_siguiente():
    from pipeline.ingest.edgar_scraper import compute_d0_close_date

    # Respaldo de scrape_day: 00:00 (Nueva York) del día del índice.
    assert compute_d0_close_date(datetime(2024, 3, 14, 0, 0, tzinfo=ET)) == date(2024, 3, 15)
    # Viernes: el lunes.
    assert compute_d0_close_date(datetime(2024, 3, 15, 0, 0, tzinfo=ET)) == date(2024, 3, 18)


def test_hora_desconocida_solo_la_medianoche_exacta_de_nueva_york():
    from pipeline.ingest.hora_publicacion import hora_desconocida

    assert hora_desconocida(datetime(2024, 3, 14, 0, 0, tzinfo=ET))
    # EDGAR acepta filings de 6:00 a 22:00 (Nueva York): cualquier otra hora es real.
    assert not hora_desconocida(datetime(2024, 3, 14, 6, 0, 1, tzinfo=ET))
    # Medianoche UTC son las 19:00/20:00 del día anterior en Nueva York.
    assert not hora_desconocida(datetime(2024, 3, 14, 0, 0, tzinfo=UTC))
    assert not hora_desconocida(None)


def test_la_app_usa_la_misma_condicion_de_hora_desconocida():
    """La ficha de la app enseña solo la fecha cuando no hay hora (H-17):
    su SQL tiene que ser exactamente el del pipeline."""
    from pathlib import Path

    from pipeline.ingest.hora_publicacion import HORA_DESCONOCIDA_SQL

    queries = (Path(__file__).resolve().parents[2] / "app" / "lib" / "queries.ts").read_text()
    assert f'"{HORA_DESCONOCIDA_SQL}"' in queries


def test_la_ia_sabe_que_la_hora_es_desconocida():
    from pipeline.analyze.enrichment import momento_de_publicacion

    texto = momento_de_publicacion(datetime(2024, 3, 14, 0, 0, tzinfo=ET), date(2024, 3, 15))
    assert "hora" in texto and "desconocida" in texto
    assert "00:00" not in texto  # no se presenta como una hora real


def test_la_ia_sabe_del_cierre_anticipado():
    from pipeline.analyze.enrichment import momento_de_publicacion

    texto = momento_de_publicacion(datetime(2024, 11, 29, 14, 0, tzinfo=ET), date(2024, 12, 2))
    assert "después del cierre" in texto and "13:00" in texto


# ---------------------------------------------------------------------------
# H-37: embargo entre In-Sample y OOS
# ---------------------------------------------------------------------------


def test_in_sample_empieza_en_backtest_start():
    from pipeline import config
    from pipeline.backtest.sample_split import SAMPLE_IN_SAMPLE, date_bounds

    inicio, _ = date_bounds(SAMPLE_IN_SAMPLE)
    assert inicio == date.fromisoformat(config.BACKTEST_START)


def test_embargo_cubre_la_operacion_mas_larga():
    """La operación más larga entra en la apertura de D0+1 y puede durar
    holding_period_max_days sesiones: el embargo, en sesiones tras D0, tiene
    que llegar al menos a su última sesión. Y a la ventana del CAR más larga."""
    from pipeline import config
    from pipeline.backtest.portfolio_strategies import STRATEGIES
    from pipeline.backtest.sample_split import EMBARGO_SESIONES

    holding = max(s.holding_period_max_days for s in STRATEGIES.values())
    assert EMBARGO_SESIONES >= 1 + holding
    # Ventanas del CAR en días naturales: 21 sesiones son más de 28 días.
    assert EMBARGO_SESIONES * 7 // 5 >= max(config.EVENT_WINDOWS_DAYS)


def test_ultimo_d0_in_sample_deja_sus_operaciones_dentro_del_in_sample():
    from pipeline.backtest.sample_split import EMBARGO_SESIONES, OOS_START, SAMPLE_IN_SAMPLE, date_bounds
    from pipeline.ingest.market_calendar import dias_de_negociacion

    _, fin = date_bounds(SAMPLE_IN_SAMPLE)
    sesiones = sorted(dias_de_negociacion(fin, OOS_START + timedelta(days=60)))
    assert sesiones[0] == fin  # el último D0 es una sesión
    # Su última sesión posible queda antes del OOS...
    assert sesiones[EMBARGO_SESIONES] < OOS_START
    # ...y es el más tardío que lo cumple: con la sesión siguiente ya no.
    assert sesiones[1 + EMBARGO_SESIONES] >= OOS_START


def test_el_embargo_no_toca_el_oos():
    from pipeline.backtest.sample_split import OOS_START, SAMPLE_OOS, date_bounds

    assert date_bounds(SAMPLE_OOS) == (OOS_START, None)


# ---------------------------------------------------------------------------
# Contra Postgres: quién deja fuera los eventos sin hora
# ---------------------------------------------------------------------------

db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE calidad_precios_revision, car_results, event_analyses, event_enrichment, events, prices, universe "
            "RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _sesiones(inicio: date, n: int) -> list[date]:
    out, d = [], inicio
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _precios(conn, ticker: str, dias: list[date]) -> None:
    with conn.cursor() as cur:
        for d in dias:
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume) "
                "VALUES (%s, %s, 10, 10, 10.1, 9.9, 1.0, 1000000)",
                (ticker, d),
            )
    conn.commit()


def _evento(conn, cik: str, ticker: str, d0: date, filed_at: datetime, *, car: float = 0.05, analizado: bool = True) -> int:
    from pipeline.analyze.event_analysis_pipeline import METODO_SIN_IA

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) VALUES (%s, %s, 'X', %s, %s) "
            "ON CONFLICT (cik) DO NOTHING",
            (cik, ticker, d0, d0),
        )
        cur.execute(
            """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes, accession_number,
                   source_url, filed_at, d0_close_date, classification_method, classification_confidence, raw_text_hash)
               VALUES (%s, %s, 'EDGAR', FALSE, '8K_2.02_EARNINGS', ARRAY['2.02'], %s, 'https://x', %s, %s, 'RULE', 1.0, %s)
               RETURNING event_id""",
            (cik, ticker, f"acc-{cik}-{d0}", filed_at, d0, f"h-{cik}-{d0}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, 20, %s, 200)",
            (event_id, car),
        )
        if analizado:
            cur.execute(
                """INSERT INTO event_analyses (event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                       judge_output, net_conviction, confidence_in_conviction, impact_estimation, n_historical_analogues,
                       ev_calculation, ev_conservative, ev_aggressive, ev_balanced, abstention_decision,
                       trade_decision_conservative, trade_decision_aggressive, trade_decision_balanced,
                       model_version_bull_bear, model_version_judge, decision_sin_ia)
                   VALUES (%s, 80, '{}', '{}', '{}', '{}', 0, 0, '{}', 20, '{}', 0.03, 0.03, 0.03, '{}',
                       'NO_TRADE', 'NO_TRADE', 'NO_TRADE', 'm', 'm', %s)""",
                (event_id, '{"metodo": "%s", "regla_historica": {"net_conviction": 1, "confidence_in_conviction": 100, '
                 '"ev_conservative": 0.03, "ev_balanced": 0.03, "ev_aggressive": 0.03, '
                 '"decisiones": {"BALANCED": {"trade_decision": "LONG"}}}}' % METODO_SIN_IA),
            )
    conn.commit()
    return event_id


@db
def test_eventos_sin_hora_fuera_de_analogos_prior_event_study_y_backtest(conn):
    from pipeline.analyze.historical_analogues import get_class_prior_mean, get_historical_analogues
    from pipeline.backtest.portfolio_report import excluidos_por_hora_desconocida
    from pipeline.backtest.portfolio_simulator import fetch_events_for_version
    from pipeline.validation.event_study import _fetch_car_by_class

    dias = _sesiones(date(2024, 1, 2), 30)
    _precios(conn, "CONHORA", dias)
    _precios(conn, "SINHORA", dias)
    d0 = dias[5]
    con_hora = _evento(conn, "1", "CONHORA", d0, datetime.combine(d0, time(9, 0), ET), car=0.05)
    # Respaldo antiguo: medianoche de Nueva York y D0 = ese mismo día.
    _evento(conn, "2", "SINHORA", d0, datetime.combine(d0, time(0, 0), ET), car=0.40)

    analogos = get_historical_analogues(conn, "8K_2.02_EARNINGS", date(2025, 1, 1), -1, window_days=20)
    assert [float(a["car"]) for a in analogos] == [pytest.approx(0.05)]
    assert get_class_prior_mean(conn, "8K_2.02_EARNINGS", 20, date(2025, 1, 1)) == pytest.approx(5.0)
    assert _fetch_car_by_class(conn, 20)["8K_2.02_EARNINGS"]["car"] == [pytest.approx(0.05)]
    assert [r["event_id"] for r in fetch_events_for_version(conn, "BALANCED")] == [con_hora]
    assert excluidos_por_hora_desconocida(conn, None) == 1


@db
def test_la_cola_de_la_ia_no_coge_un_d0_dudoso_pero_si_uno_ya_corregido(conn):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis

    medianoche = datetime(2026, 9, 10, 0, 0, tzinfo=ET)
    dudoso = _evento(conn, "1", "VIEJO", date(2026, 9, 10), medianoche, analizado=False)
    corregido = _evento(conn, "2", "NUEVO", date(2026, 9, 11), medianoche, analizado=False)
    con_hora = _evento(conn, "3", "NORMAL", date(2026, 9, 10), datetime(2026, 9, 10, 8, 0, tzinfo=ET), analizado=False)

    ids = {r["event_id"] for r in fetch_events_needing_analysis(conn)}
    assert dudoso not in ids
    assert {corregido, con_hora} <= ids


@db
def test_filas_anteriores_a_la_hora_de_aceptacion_cuentan_como_sin_hora(conn):
    """Revisión adversarial de la Tanda 13: hasta el 25-9-2026 (2c8e969)
    scrape_day no leía la hora de aceptación y guardaba la medianoche SIN
    zona; en una base en UTC queda a las 00:00 UTC (19:00/20:00 del día
    anterior en Nueva York), así que «medianoche de Nueva York» no las veía.
    Su D0 es el día del índice. Las de después (y los fixtures de los tests,
    creados hoy) a esa hora son reales."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis
    from pipeline.analyze.historical_analogues import get_historical_analogues

    medianoche_utc = datetime(2024, 3, 14, 0, 0, tzinfo=UTC)
    antiguo = _evento(conn, "1", "ANTIGUO", date(2024, 3, 14), medianoche_utc, car=0.40)
    _evento(conn, "2", "RECIENTE", date(2024, 3, 14), medianoche_utc, car=0.05)
    pendiente = _evento(conn, "3", "COLA", date(2026, 9, 14), datetime(2026, 9, 14, 0, 0, tzinfo=UTC), analizado=False)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE events SET created_at = TIMESTAMPTZ '2026-09-20 06:00+00' WHERE event_id = ANY(%s)",
            ([antiguo, pendiente],),
        )
    conn.commit()

    analogos = get_historical_analogues(conn, "8K_2.02_EARNINGS", date(2025, 1, 1), -1, window_days=20)
    assert [float(a["car"]) for a in analogos] == [pytest.approx(0.05)]
    assert pendiente not in {r["event_id"] for r in fetch_events_needing_analysis(conn)}
