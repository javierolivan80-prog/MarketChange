"""cik.py — normalización del CIK a la forma canónica del proyecto.

Hallazgo de auditoría (IMPROVEMENT_PLAN.md Q4): esta normalización vivía
duplicada en tres sitios, con dos implementaciones ligeramente distintas —
exactamente el tipo de cosa que diverge en silencio con el tiempo (mismo
razonamiento que llevó a extraer edgar_http.py en su día). `edgar_scraper.py`
y `ticker_map.py` hacían `cik.lstrip("0") or "0"` inline; `xbrl_fundamentals.py`
tenía la versión más completa (`normalizar_cik`, con prefijo "CIK" y
mayúsculas) porque ahí fue donde el bug real de formato se manifestó
primero (ver docstring de `normalize_cik` más abajo). Consolidado aquí, la
única versión completa, para que las tres fuentes (daily-index de EDGAR,
company_tickers.json, companyfacts) queden con la misma disciplina.
"""
from __future__ import annotations


def normalize_cik(cik) -> str:
    """CIK en la forma canónica del proyecto: dígitos sin ceros a la izquierda.

    BUG REAL (2026-09-15, run 34943861450). La tabla universe guarda el CIK
    como viene del daily-index, sin rellenar ('1119190'). La API de
    companyfacts lo devuelve rellenado a 10 dígitos ('0001119190'). Son la
    misma empresa y para Postgres son dos cadenas distintas:

        insert or update on table "fundamentals" violates foreign key
        constraint "fundamentals_cik_fkey"
        DETAIL: Key (cik)=(0001119190) is not present in table "universe".

    Cualquier sitio donde un CIK cruce la frontera entre dos fuentes tiene que
    pasar por aquí. El CIK se usa además como clave ajena contra universe, así
    que una diferencia de formato no da un dato raro: rompe la inserción.

    `or "0"`: un CIK de puros ceros (no ocurre en la práctica — la SEC
    empieza a numerar en 1 — pero más barato cubrirlo que dejar `lstrip`
    devolver una cadena vacía que después falla `isdigit()`).
    """
    texto = str(cik).strip().upper().removeprefix("CIK").lstrip("0") or "0"
    if not texto.isdigit():
        raise ValueError(f"CIK con formato inesperado: {cik!r}")
    return texto
