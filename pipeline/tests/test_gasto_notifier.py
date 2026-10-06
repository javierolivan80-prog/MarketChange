"""Aviso por Telegram al 80 % del tope diario de gasto de IA (Tanda 5)."""
import os
from datetime import date

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE avisos_enviados")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def enviados(monkeypatch):
    from pipeline import config
    from pipeline.notify import telegram

    monkeypatch.setattr(config, "DAILY_SPEND_CAP_USD", 10.0)
    mensajes: list[str] = []
    resultado = {"ok": True}

    def enviar(texto, **_):
        mensajes.append(texto)
        return resultado["ok"]

    monkeypatch.setattr(telegram, "send_message", enviar)
    return mensajes, resultado


def test_por_debajo_del_80_no_avisa(conn, enviados):
    from pipeline.notify.gasto_notifier import avisar_si_gasto_alto

    assert avisar_si_gasto_alto(conn, 7.99, hoy=date(2026, 10, 6)) is False
    assert enviados[0] == []


def test_al_80_avisa_una_sola_vez_al_dia(conn, enviados):
    from pipeline.notify.gasto_notifier import avisar_si_gasto_alto

    hoy = date(2026, 10, 6)
    assert avisar_si_gasto_alto(conn, 8.0, hoy=hoy) is True
    assert avisar_si_gasto_alto(conn, 9.5, hoy=hoy) is False
    assert len(enviados[0]) == 1 and "80%" in enviados[0][0]
    assert avisar_si_gasto_alto(conn, 8.5, hoy=date(2026, 10, 7)) is True  # otro día


def test_si_el_envio_falla_se_reintenta_en_la_siguiente_corrida(conn, enviados):
    from pipeline.notify.gasto_notifier import avisar_si_gasto_alto

    mensajes, resultado = enviados
    resultado["ok"] = False
    hoy = date(2026, 10, 6)
    assert avisar_si_gasto_alto(conn, 9.0, hoy=hoy) is False
    resultado["ok"] = True
    assert avisar_si_gasto_alto(conn, 9.0, hoy=hoy) is True
    assert len(mensajes) == 2


def test_nunca_lanza_aunque_falle_la_base(enviados):
    from pipeline.db.connection import get_connection
    from pipeline.notify.gasto_notifier import avisar_si_gasto_alto

    cerrada = get_connection()
    cerrada.close()
    assert avisar_si_gasto_alto(cerrada, 9.0, hoy=date(2026, 10, 6)) is False
    assert enviados[0] == []
