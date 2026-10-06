"""gasto_notifier.py — aviso por Telegram cuando el gasto de IA del día llega
al 80 % del tope diario (Tanda 5).

Sale como mucho una vez por día (tabla avisos_enviados). Si Telegram no está
configurado o el envío falla, no se marca como enviado: lo reintenta la
corrida siguiente. Nunca lanza: un aviso no debe tumbar el análisis.
"""
from __future__ import annotations

import logging
from datetime import date

from pipeline import config
from pipeline.notify import telegram

logger = logging.getLogger(__name__)

UMBRAL_AVISO = 0.80


def avisar_si_gasto_alto(conn, gasto_hoy_usd: float, hoy: date | None = None) -> bool:
    """Manda el aviso si el gasto de hoy alcanza el umbral y no se avisó ya
    hoy. Devuelve True si se envió ahora."""
    tope = config.DAILY_SPEND_CAP_USD
    if not tope or gasto_hoy_usd < UMBRAL_AVISO * tope:
        return False
    clave = f"gasto_ia_80:{(hoy or date.today()).isoformat()}"
    try:
        # Se reserva ANTES de enviar: si después algo falla, no se repite el
        # mensaje en cada corrida. Si el envío falla, se libera la reserva.
        with conn.cursor() as cur:
            cur.execute("INSERT INTO avisos_enviados (clave) VALUES (%s) ON CONFLICT DO NOTHING RETURNING clave", (clave,))
            reservado = cur.fetchone() is not None
        conn.commit()
        if not reservado:
            return False
        texto = (
            f"⚠️ <b>Gasto de IA al {gasto_hoy_usd / tope:.0%} del tope diario</b>\n"
            f"Hoy: ~{gasto_hoy_usd:.2f} $ de {tope:.2f} $ ({config.DAILY_SPEND_CAP_EUR:.2f} €/día). "
            "Al llegar al tope, el análisis se para hasta mañana."
        )
        if not telegram.send_message(texto):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM avisos_enviados WHERE clave = %s", (clave,))
            conn.commit()
            return False
        return True
    except Exception:
        logger.exception("No se pudo enviar el aviso de gasto")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
