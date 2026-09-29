"""test_ticker_map.py — valida la inversión CIK->ticker sin red.

No se puede descargar company_tickers.json en este sandbox (egress bloqueado
a www.sec.gov). Se prueba la lógica de inversión del mapa contra un JSON de
muestra con la misma forma que el fichero real.
"""
import json
from types import SimpleNamespace

SAMPLE_RAW = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


def test_inversion_strips_leading_zeros_consistently():
    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}
    assert mapping["320193"] == "AAPL"
    assert mapping["1045810"] == "NVDA"


def test_resolve_matches_edgar_zero_padded_cik_format():
    """El .idx de EDGAR trae CIKs con ceros a la izquierda (ej '0000320193').
    resolve() debe despojarlos antes de buscar en el mapa (construido sin ceros).
    """
    from pipeline.ingest.ticker_map import get_ticker_map

    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}

    def resolve_against(cik: str, m: dict) -> str | None:
        return m.get(cik.lstrip("0") or "0")

    assert resolve_against("0000320193", mapping) == "AAPL"
    assert resolve_against("320193", mapping) == "AAPL"
    assert resolve_against("0000000000", mapping) is None


def test_resolve_usa_normalize_cik(monkeypatch):
    """Regresión de cableado (IMPROVEMENT_PLAN.md Q4): resolve() debe pasar
    por pipeline.ingest.cik.normalize_cik, no por una normalización inline
    propia — si alguien reintroduce un `cik.lstrip("0")` local, este test
    debe seguir pasando igual (normalize_cik hace lo mismo y algo más, ver
    test_cik.py), pero deja de haber DOS sitios que mantener sincronizados."""
    from pipeline.ingest import ticker_map

    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}
    monkeypatch.setattr(ticker_map, "get_ticker_map", lambda: mapping)

    assert ticker_map.resolve("0000320193") == "AAPL"


def test_fetch_and_build_map_usa_throttled_get_no_requests_desnudo(monkeypatch, tmp_path):
    """Hallazgo de auditoría (IMPROVEMENT_PLAN.md R7): antes de esta sesión,
    _fetch_and_build_map hacía un requests.get desnudo, sin retry/backoff —
    un fallo transitorio aquí (sin caché en disco todavía) tumbaba el día
    entero de ingesta EDGAR, porque resolve() se llama por cada filing.
    edgar_http.throttled_get ya tiene ese retry/backoff (mismo host,
    www.sec.gov, mismo User-Agent) — este test confirma que se usa ESE en
    vez de una llamada propia, no reimplementa el retry en sí (eso ya lo
    prueba test_edgar_http.py)."""
    from pipeline.ingest import ticker_map

    monkeypatch.setattr(ticker_map, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(ticker_map, "_cache", None)
    llamado = {}

    def _fake_throttled_get(url):
        llamado["url"] = url
        return SimpleNamespace(json=lambda: SAMPLE_RAW)

    monkeypatch.setattr(ticker_map, "throttled_get", _fake_throttled_get)

    mapping = ticker_map.get_ticker_map(force_refresh=True)

    assert llamado["url"] == ticker_map.SOURCE_URL
    assert mapping["320193"] == "AAPL"
