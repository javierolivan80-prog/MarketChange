"""ticker_map.py — resuelve CIK -> ticker usando el fichero oficial de EDGAR.

El daily-index NO trae el ticker, solo CIK y nombre de compañía (ver
edgar_scraper.parse_daily_index). Sin resolver esto, connection.py insertaría
el CIK como si fuera el ticker, lo cual rompería silenciosamente TODO lo que
viene después (yfinance no entiende CIKs). Se resuelve aquí, una vez, contra
el mapa oficial: https://www.sec.gov/files/company_tickers.json

Se cachea en memoria y en disco (JSON) porque el fichero cambia poco y no
tiene sentido volver a descargarlo en cada ejecución nocturna.

Hallazgo de auditoría (IMPROVEMENT_PLAN.md R7): la descarga usaba un
`requests.get` desnudo, sin retry/backoff — un fallo transitorio aquí, si el
caché en disco todavía no existe (primera vez que corre, o tras un
--force-refresh), tumbaba el día entero de ingesta EDGAR, porque
upsert_universe_entries/upsert_events llaman a resolve() por cada filing. Se
usa edgar_http.throttled_get en vez de reimplementar el mismo retry/backoff
aquí: este fichero también descarga de www.sec.gov, así que es EXACTAMENTE
el mismo host, límite de tasa y User-Agent que ya tiene ese módulo — no una
coincidencia superficial que justifique una copia propia.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline import config
from pipeline.ingest.edgar_http import throttled_get

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent / ".cache_company_tickers.json"
SOURCE_URL = f"{config.EDGAR_BASE}/files/company_tickers.json"

_cache: dict[str, str] | None = None


def _load_from_disk() -> dict[str, str] | None:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Cache de tickers en disco corrupta, se re-descarga")
    return None


def _fetch_and_build_map() -> dict[str, str]:
    """Descarga company_tickers.json y lo invierte a {cik_sin_ceros: ticker}.

    Formato real del fichero (estable, usado ampliamente por la comunidad):
      {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {...}, ...}
    """
    resp = throttled_get(SOURCE_URL)
    raw = resp.json()
    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in raw.values()}
    CACHE_PATH.write_text(json.dumps(mapping))
    return mapping


def get_ticker_map(force_refresh: bool = False) -> dict[str, str]:
    global _cache
    if _cache is not None and not force_refresh:
        return _cache
    if not force_refresh:
        disk = _load_from_disk()
        if disk is not None:
            _cache = disk
            return _cache
    _cache = _fetch_and_build_map()
    return _cache


def resolve(cik: str) -> str | None:
    """Devuelve el ticker para un CIK, o None si no está en el mapa (ej. CIKs
    de emisores sin acciones cotizadas — fondos, insiders individuales, etc.,
    que de todas formas no pertenecen al universo invertible)."""
    return get_ticker_map().get(cik.lstrip("0") or "0")
