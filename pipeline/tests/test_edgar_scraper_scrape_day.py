"""test_edgar_scraper_scrape_day.py — scrape_day/backfill_range (IMPROVEMENT_PLAN.md M6).

Hasta ahora solo estaban probadas las sub-funciones puras de edgar_scraper.py
(parse_daily_index, classify_event_classes, archive_url...) contra fixtures
offline. La propia lógica de scrape_day — los contadores de descarte
(sin_items/sin_clase/no_descargados), qué construye en cada RawFiling, y el
resumible/tolerante-a-fallos de backfill_range — no tenía ningún test
directo, solo se verificaba indirectamente al pasar los tests de las
sub-funciones que llama.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pipeline.ingest import edgar_scraper

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResp:
    def __init__(self, text: str):
        self.text = text


def _daily_index_text() -> str:
    return (FIXTURES / "sample_daily_index.idx").read_text()


def _header_con_items(*items: str) -> str:
    lineas = "\n".join(f"ITEM INFORMATION:\t\t{it}" for it in items)
    return (
        "<SEC-HEADER>x.hdr.sgml\n"
        "ACCESSION NUMBER:\t\t0001234567-24-000123\n"
        f"{lineas}\n"
        "</SEC-HEADER>\n"
    )


@pytest.fixture(autouse=True)
def _sin_red_real(monkeypatch):
    # scrape_day pide primero el índice diario vía throttled_get.
    monkeypatch.setattr(edgar_scraper, "throttled_get", lambda url, **kw: _FakeResp(_daily_index_text()))


def test_scrape_day_descarta_por_separado_sin_items_sin_clase_y_no_descargados(monkeypatch):
    """De los 3 8-K del fixture (ACME, BETA, EPSILON): ACME falla al
    descargarse, BETA no trae ningún Item reconocible, EPSILON trae un Item
    fuera de las clases con potencia estadística (8.01 relevante en cambio sí
    cuenta) — aquí se usa un item sin mapear en absoluto."""

    def _header(file_name, **kw):
        if "1234567" in file_name:
            raise RuntimeError("500 tras agotar reintentos")
        if "9876543" in file_name:
            return _header_con_items()  # cabecera sin ITEM INFORMATION alguno
        return _header_con_items("9.99")  # item real pero fuera de ITEM_TO_EVENT_CLASS

    monkeypatch.setattr(edgar_scraper, "throttled_get_header", _header)

    filings = edgar_scraper.scrape_day(date(2024, 3, 15))

    assert filings == []


def test_scrape_day_construye_el_raw_filing_con_los_campos_esperados(monkeypatch):
    def _header(file_name, **kw):
        return _header_con_items("2.02")

    monkeypatch.setattr(edgar_scraper, "throttled_get_header", _header)

    filings = edgar_scraper.scrape_day(date(2024, 3, 15))

    assert len(filings) == 3
    acme = next(f for f in filings if "ACME" in f.company_name)
    assert acme.accession_number == "0001234567-24-000123"
    assert acme.item_codes == ["2.02"]
    assert acme.source_url == "https://www.sec.gov/Archives/edgar/data/1234567/0001234567-24-000123.txt"
    assert acme.filed_at.date() == date(2024, 3, 15)  # fallback: sin ACCEPTANCE-DATETIME en la cabecera


def test_scrape_day_usa_la_hora_de_aceptacion_cuando_esta_disponible(monkeypatch):
    def _header(file_name, **kw):
        return (
            "<SEC-HEADER>x.hdr.sgml\n"
            "ACCESSION NUMBER:\t\t0001234567-24-000123\n"
            "ITEM INFORMATION:\t\t2.02\n"
            "<ACCEPTANCE-DATETIME>20240315161234\n"
            "</SEC-HEADER>\n"
        )

    monkeypatch.setattr(edgar_scraper, "throttled_get_header", _header)

    filings = edgar_scraper.scrape_day(date(2024, 3, 15))

    acme = next(f for f in filings if "ACME" in f.company_name)
    assert acme.filed_at.hour == 16
    assert acme.filed_at.minute == 12


def test_scrape_day_sin_ningun_8k_en_el_dia_no_revienta(monkeypatch):
    # Filas reconocibles (para que parse_daily_index no dispare su propia
    # comprobación de formato), pero ninguna es un 8-K.
    sin_8k = (
        "Form Type   Company Name        CIK         Date Filed  File Name\n"
        "---------- ------------------  ----------  ----------  ----------------------------------\n"
        "10-K       DELTA INDUSTRIES     0002223334  2024-03-15  edgar/data/2223334/x.txt\n"
    )
    monkeypatch.setattr(edgar_scraper, "throttled_get", lambda url, **kw: _FakeResp(sin_8k))

    assert edgar_scraper.scrape_day(date(2024, 3, 15)) == []


# --- backfill_range ---------------------------------------------------------


def test_backfill_range_salta_los_fines_de_semana(monkeypatch):
    dias_pedidos = []

    def _fake_scrape_day(day):
        dias_pedidos.append(day)
        return []

    monkeypatch.setattr(edgar_scraper, "scrape_day", _fake_scrape_day)
    monkeypatch.setattr("pipeline.db.connection.get_connection", lambda: object())
    monkeypatch.setattr("pipeline.db.connection.upsert_universe_entries", lambda conn, filings: None)
    monkeypatch.setattr("pipeline.db.connection.upsert_events", lambda *a, **kw: 0)

    # 2024-03-15 es viernes; el rango cubre el fin de semana completo.
    edgar_scraper.backfill_range(date(2024, 3, 15), date(2024, 3, 18))

    assert dias_pedidos == [date(2024, 3, 15), date(2024, 3, 18)]


def test_backfill_range_continua_tras_un_fallo_en_un_dia(monkeypatch):
    """Resumible de verdad: un día que revienta no debe cortar el resto del
    rango, o un solo día problemático bloquearía todo el backfill histórico."""
    dias_procesados = []

    def _fake_scrape_day(day):
        if day == date(2024, 3, 18):
            raise RuntimeError("EDGAR caído ese día")
        dias_procesados.append(day)
        return []

    monkeypatch.setattr(edgar_scraper, "scrape_day", _fake_scrape_day)
    monkeypatch.setattr("pipeline.db.connection.get_connection", lambda: object())
    monkeypatch.setattr("pipeline.db.connection.upsert_universe_entries", lambda conn, filings: None)
    monkeypatch.setattr("pipeline.db.connection.upsert_events", lambda *a, **kw: 0)

    edgar_scraper.backfill_range(date(2024, 3, 15), date(2024, 3, 19))

    # Viernes 15, lunes 18 (falla, no se cuenta) y martes 19 — el fin de
    # semana (16-17) se salta igual que en el otro test.
    assert dias_procesados == [date(2024, 3, 15), date(2024, 3, 19)]


def test_backfill_range_acumula_el_total_de_eventos_insertados(monkeypatch, caplog):
    monkeypatch.setattr(edgar_scraper, "scrape_day", lambda day: ["filing-fake"])
    monkeypatch.setattr("pipeline.db.connection.get_connection", lambda: object())
    monkeypatch.setattr("pipeline.db.connection.upsert_universe_entries", lambda conn, filings: None)
    monkeypatch.setattr("pipeline.db.connection.upsert_events", lambda *a, **kw: 5)

    with caplog.at_level("INFO", logger="pipeline.ingest.edgar_scraper"):
        # Viernes 15 y lunes 18 son los dos únicos días hábiles del rango.
        edgar_scraper.backfill_range(date(2024, 3, 15), date(2024, 3, 18))

    assert "Backfill completo: 10 eventos insertados/actualizados" in caplog.text
