"""test_edgar_scraper.py — backfill_range y su disciplina de aislamiento de fallos.

Hallazgo de auditoría (IMPROVEMENT_PLAN.md R3): backfill_range no hacía
conn.rollback() tras un fallo de un día concreto. Sin eso, un error real de
Postgres a mitad del rango (p. ej. una violación de constraint en
upsert_events) deja la transacción ABORTADA, y todos los días siguientes del
rango fallan en cadena con "current transaction is aborted, commands ignored"
— exactamente el mismo patrón ya corregido en xbrl_fundamentals.py
(ingest_universe_fundamentals, ver su docstring y test_xbrl_fundamentals.py).

Sin red: www.sec.gov está bloqueado en este sandbox (AUDIT_LEAN.md §1.5) —
scrape_day/upsert_* se monkeypatchean, no se prueba el scraping en sí (eso ya
lo cubre test_edgar_parser.py contra un fixture offline).
"""
from datetime import date

import pytest

from pipeline.ingest import edgar_scraper


class _ConexionFalsa:
    """Imita a psycopg en lo único que importa aquí: tras un error, la
    transacción queda ABORTADA y toda sentencia siguiente falla hasta que se
    hace rollback. Sin eso, el test pasaría igual con y sin el arreglo (ver
    test_sin_rollback_el_fallo_se_propagaria más abajo)."""

    def __init__(self):
        self.abortada = False
        self.rollbacks = 0

    def commit(self):
        if self.abortada:
            raise RuntimeError("current transaction is aborted, commands ignored")

    def rollback(self):
        self.rollbacks += 1
        self.abortada = False


def test_un_dia_roto_no_se_lleva_por_delante_al_resto_del_rango(monkeypatch):
    """EL fallo de producción (mismo patrón que xbrl_fundamentals.py): un solo
    día con un fallo de Postgres en upsert_events no debe impedir que los
    días siguientes del rango se procesen."""
    conn = _ConexionFalsa()
    dia_que_falla = date(2024, 1, 3)  # miércoles
    procesados: list[date] = []

    monkeypatch.setattr(edgar_scraper, "scrape_day", lambda day: [f"filing-{day.isoformat()}"])

    import pipeline.db.connection as db

    monkeypatch.setattr(db, "get_connection", lambda: conn)
    monkeypatch.setattr(db, "upsert_universe_entries", lambda c, filings: None)

    def _upsert_events(c, filings, classify_fn, d0_fn):
        day = date.fromisoformat(filings[0].removeprefix("filing-"))
        if day == dia_que_falla:
            conn.abortada = True
            raise RuntimeError('violates foreign key constraint "events_cik_fkey"')
        procesados.append(day)
        return 1

    monkeypatch.setattr(db, "upsert_events", _upsert_events)

    # Rango de una semana hábil completa (2024-01-01 es lunes) para tener
    # días antes Y después del que falla.
    edgar_scraper.backfill_range(date(2024, 1, 1), date(2024, 1, 5))

    assert dia_que_falla not in procesados
    assert procesados == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 4), date(2024, 1, 5)]
    assert conn.rollbacks == 1


def test_sin_rollback_el_fallo_se_propagaria():
    """Comprobación de que el test de arriba no pasa por casualidad: si se
    quita el rollback, la cascada reaparece. Lo que se verifica es que la
    conexión falsa REPRODUCE el comportamiento de Postgres."""
    conn = _ConexionFalsa()
    conn.abortada = True
    with pytest.raises(RuntimeError, match="transaction is aborted"):
        conn.commit()
    conn.rollback()
    conn.commit()  # ya no debe lanzar
