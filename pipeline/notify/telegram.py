"""telegram.py — cliente mínimo de la Bot API de Telegram.

Sin dependencias nuevas a propósito (mismo principio que whisper.py del
skill /watch, y el resto del pipeline: `urllib.request` de la stdlib, nada
de instalar `python-telegram-bot` para mandar un POST). Un bot de Telegram
es gratis y no exige verificación — se crea hablándole a @BotFather, ver
RUNBOOK.md para el paso a paso.

Uso: define TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (ver config.py) y llama a
`send_message(texto)`. Si cualquiera de las dos variables falta, `send_message`
no falla el pipeline — registra un aviso y devuelve False, para que un
entorno sin Telegram configurado (por ejemplo, este mismo sandbox de
desarrollo) siga pudiendo correr el resto del pipeline sin tropezar aquí.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from pipeline import config

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096  # límite duro de la Bot API por mensaje


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send_message(text: str, *, parse_mode: str = "HTML") -> bool:
    """Manda un mensaje al chat configurado. Devuelve True si la API de
    Telegram respondió ok=True, False en cualquier otro caso (sin configurar,
    error de red, error de la API) — nunca lanza, porque un fallo de
    notificación no debe tumbar el resto del batch nocturno."""
    if not is_configured():
        logger.info("Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID) — se omite el envío")
        return False

    if len(text) > MAX_MESSAGE_LENGTH:
        # Trunca en vez de dejar que la API lo rechace entero — un aviso
        # cortado es más útil que ningún aviso.
        text = text[: MAX_MESSAGE_LENGTH - 20] + "\n… (truncado)"

    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
            if not body.get("ok"):
                logger.warning("Telegram respondió ok=False: %s", body)
                return False
            return True
    except urllib.error.HTTPError as e:
        # El cuerpo del error de Telegram trae el motivo (chat_id inválido,
        # bot bloqueado por el usuario, token revocado, etc.) — se registra
        # para que quede en los logs de GitHub Actions, no se reintenta:
        # un token/chat_id malo no se arregla solo en la siguiente pasada.
        detail = e.read().decode("utf-8", errors="replace")
        logger.warning("Telegram HTTPError %s: %s", e.code, detail)
        return False
    except urllib.error.URLError as e:
        logger.warning("Telegram URLError: %s", e.reason)
        return False
