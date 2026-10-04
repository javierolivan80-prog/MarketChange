"""test_ticker_map.py — valida la inversión CIK->ticker sin red.

No se puede descargar company_tickers.json en este sandbox (egress bloqueado
a www.sec.gov). Se prueba la lógica de inversión del mapa contra un JSON de
muestra con la misma forma que el fichero real.
"""
from types import SimpleNamespace

import pytest

from pipeline.ingest import ticker_map

SAMPLE_RAW = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


def test_inversion_strips_leading_zeros_consistently():
    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}
    assert mapping["320193"] == "AAPL"
    assert mapping["1045810"] == "NVDA"


@pytest.fixture
def _mapa_de_muestra(monkeypatch):
    """Sustituye get_ticker_map() por el mapa de muestra, sin tocar la caché
    real en disco/memoria ni hacer red — resolve() en sí no se toca, así que
    esto SÍ ejercita su lógica real de normalización de CIK."""
    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}
    monkeypatch.setattr(ticker_map, "get_ticker_map", lambda force_refresh=False: mapping)
    return mapping


def test_resolve_matches_edgar_zero_padded_cik_format(_mapa_de_muestra):
    """El .idx de EDGAR trae CIKs con ceros a la izquierda (ej '0000320193').
    resolve() debe despojarlos antes de buscar en el mapa (construido sin ceros).

    REGRESIÓN DE CALIDAD DE TEST (IMPROVEMENT_PLAN.md Q7): la versión
    anterior de este test definía y llamaba una función local
    `resolve_against()` que reimplementaba a mano la misma lógica de
    `resolve()` en vez de importar y llamar la función real — podía pasar
    con un `resolve()` real roto, con solo confirmar que la copia local
    (que nadie edita cuando el original cambia) seguía teniendo razón."""
    assert ticker_map.resolve("0000320193") == "AAPL"
    assert ticker_map.resolve("320193") == "AAPL"
    assert ticker_map.resolve("0000000000") is None


def test_resolve_un_cik_que_no_esta_en_el_mapa_devuelve_none(_mapa_de_muestra):
    assert ticker_map.resolve("9999999") is None


def test_fetch_and_build_map_usa_throttled_get_no_requests_desnudo(monkeypatch):
    """Hallazgo de auditoría (IMPROVEMENT_PLAN.md R7): antes de esta sesión,
    _fetch_and_build_map hacía un requests.get desnudo, sin retry/backoff —
    un fallo transitorio aquí tumbaba el día entero de ingesta EDGAR, porque
    resolve() se llama por cada filing. edgar_http.throttled_get ya tiene ese
    retry/backoff (mismo host, www.sec.gov, mismo User-Agent) — este test
    confirma que se usa ESE en vez de una llamada propia, no reimplementa el
    retry en sí (eso ya lo prueba test_edgar_http.py).

    Sin CACHE_PATH que monkeypatchear: la caché en disco se quitó en
    IMPROVEMENT_PLAN.md Q6 (ver docstring del módulo) — solo queda la de
    memoria del proceso, _cache."""
    from pipeline.ingest import ticker_map

    monkeypatch.setattr(ticker_map, "_cache", None)
    llamado = {}

    def _fake_throttled_get(url):
        llamado["url"] = url
        return SimpleNamespace(json=lambda: SAMPLE_RAW)

    monkeypatch.setattr(ticker_map, "throttled_get", _fake_throttled_get)

    mapping = ticker_map.get_ticker_map(force_refresh=True)

    assert llamado["url"] == ticker_map.SOURCE_URL
    assert mapping["320193"] == "AAPL"


# --- Varias acciones por CIK (BUGS_REPORT.md H-15) ---------------------------


def test_con_varios_simbolos_gana_la_accion_ordinaria_no_el_ultimo():
    from pipeline.ingest.ticker_map import build_map

    raw = {
        "0": {"cik_str": 1, "ticker": "ACME", "title": "Acme"},
        "1": {"cik_str": 1, "ticker": "ACMEW", "title": "Acme"},  # warrant, antes ganaba por ser el último
        "2": {"cik_str": 2, "ticker": "BETA-PA", "title": "Beta"},  # preferente antes que la común
        "3": {"cik_str": 2, "ticker": "BETA", "title": "Beta"},
    }
    assert build_map(raw) == {"1": "ACME", "2": "BETA"}


def test_si_ningun_simbolo_es_operable_se_queda_el_primero():
    from pipeline.ingest.ticker_map import build_map

    raw = {
        "0": {"cik_str": 3, "ticker": "GAMMU", "title": "Gamma SPAC"},
        "1": {"cik_str": 3, "ticker": "GAMMW", "title": "Gamma SPAC"},
    }
    assert build_map(raw) == {"3": "GAMMU"}
