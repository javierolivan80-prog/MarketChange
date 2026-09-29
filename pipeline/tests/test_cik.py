"""test_cik.py — normalización del CIK a la forma canónica del proyecto.

Antes de IMPROVEMENT_PLAN.md Q4, esta lógica (y sus tests) vivían en
xbrl_fundamentals.py, aunque edgar_scraper.py y ticker_map.py duplicaban una
versión más simple de la misma normalización inline. Consolidado en
pipeline/ingest/cik.py — este fichero es ahora la única fuente de tests
sobre la normalización en sí; test_xbrl_fundamentals.py sigue probando que
parse_company_facts la usa correctamente (integración), no el detalle de
qué formas de CIK son equivalentes (eso vive aquí).
"""
import pytest

from pipeline.ingest.cik import normalize_cik


@pytest.mark.parametrize(
    "entrada",
    ["0001119190", "1119190", 1119190, "CIK0001119190", " 0001119190 "],
)
def test_normalize_cik_deja_todas_las_formas_iguales(entrada):
    assert normalize_cik(entrada) == "1119190"


def test_normalize_cik_rechaza_lo_que_no_es_un_cik():
    with pytest.raises(ValueError, match="formato inesperado"):
        normalize_cik("no-soy-un-cik")


def test_normalize_cik_de_puros_ceros_da_cero():
    """Caso límite que las implementaciones duplicadas en edgar_scraper.py y
    ticker_map.py ya cubrían con `or "0"`, pero que la versión original de
    xbrl_fundamentals.py no cubría (habría fallado isdigit() sobre una
    cadena vacía) — consolidar en un solo sitio también consolidó esta
    cobertura en las tres fuentes a la vez."""
    assert normalize_cik("0000000000") == "0"
