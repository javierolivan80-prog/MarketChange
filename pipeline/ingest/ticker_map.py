"""ticker_map.py — resuelve CIK -> ticker usando el fichero oficial de EDGAR.

El daily-index NO trae el ticker, solo CIK y nombre de compañía (ver
edgar_scraper.parse_daily_index). Sin resolver esto, connection.py insertaría
el CIK como si fuera el ticker, lo cual rompería silenciosamente TODO lo que
viene después (yfinance no entiende CIKs). Se resuelve aquí, una vez, contra
el mapa oficial: https://www.sec.gov/files/company_tickers.json

Se cachea SOLO en memoria del proceso (IMPROVEMENT_PLAN.md Q6) — antes
también escribía una copia en disco (`.cache_company_tickers.json`, en
`.gitignore`), pero en el despliegue real (`nightly_pipeline.yml`) cada
corrida es un runner de GitHub Actions efímero con un `actions/checkout`
nuevo: no hay ningún `actions/cache` configurado para esa ruta, así que el
fichero se escribía y se tiraba entero al acabar el job, sin que ninguna
corrida futura llegara a leerlo nunca. La única caché que de verdad se
reutiliza es la de memoria, dentro del mismo proceso — que ya cubre el caso
real que motivó tener caché (`resolve()` se llama una vez por filing durante
la ingesta EDGAR de una misma corrida). Montar `actions/cache` para esto
sería una solución real al problema real, pero ninguna corrida nocturna
necesita re-descargar company_tickers.json más de una vez por noche, así
que el ahorro no justifica la complejidad.
"""
from __future__ import annotations

import logging

import requests

from pipeline import config

logger = logging.getLogger(__name__)

SOURCE_URL = f"{config.EDGAR_BASE}/files/company_tickers.json"

_cache: dict[str, str] | None = None


def _fetch_and_build_map() -> dict[str, str]:
    """Descarga company_tickers.json y lo invierte a {cik_sin_ceros: ticker}.

    Formato real del fichero (estable, usado ampliamente por la comunidad):
      {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {...}, ...}
    """
    resp = requests.get(SOURCE_URL, headers={"User-Agent": config.EDGAR_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    return {str(entry["cik_str"]): entry["ticker"] for entry in raw.values()}


def get_ticker_map(force_refresh: bool = False) -> dict[str, str]:
    global _cache
    if _cache is not None and not force_refresh:
        return _cache
    _cache = _fetch_and_build_map()
    return _cache


def resolve(cik: str) -> str | None:
    """Devuelve el ticker para un CIK, o None si no está en el mapa (ej. CIKs
    de emisores sin acciones cotizadas — fondos, insiders individuales, etc.,
    que de todas formas no pertenecen al universo invertible)."""
    return get_ticker_map().get(cik.lstrip("0") or "0")
