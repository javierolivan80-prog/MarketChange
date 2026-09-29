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
import re
import time
import urllib.error
import urllib.request

from pipeline import config

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096  # límite duro de la Bot API por mensaje

# Hallazgo de auditoría (IMPROVEMENT_PLAN.md M15): send_message hacía UN
# único intento — cualquier caída de red transitoria, o un 429 real de
# Telegram (rate limit), perdía el mensaje sin más. Mismo backoff que el
# resto del proyecto (edgar_http.py, fama_french.py), pero más corto (2
# intentos, no 5): un aviso de Telegram no justifica retrasar el resto del
# batch nocturno 30s si el problema no es de verdad transitorio.
_RETRY_DELAYS = [2, 4]


def _es_permanente(status: int) -> bool:
    # Mismo criterio que edgar_http._es_permanente: 429 (rate limit) y los
    # 5xx (problema del lado de Telegram) sí merece la pena reintentarlos.
    return 400 <= status < 500 and status != 429


# Etiquetas HTML que Telegram soporta en parse_mode="HTML" y que este
# proyecto usa de verdad (signals_notifier.py, alerts_notifier.py): <b> y
# <a href="...">. El truncado de abajo es agnóstico a CUÁLES sean — cierra
# cualquier etiqueta que quede abierta al cortar, sea la que sea.
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")


def _truncate_html_aware(text: str, limit: int) -> str:
    """Trunca a `limit` caracteres sin cortar a mitad de una etiqueta HTML ni
    dejar una sin cerrar (IMPROVEMENT_PLAN.md M16) — antes, un truncado por
    caracteres a secas podía partir un `<a href="...">` o dejar un `<b>` sin
    su `</b>`, y la Bot API de Telegram rechaza el mensaje ENTERO por HTML
    mal formado (parse_mode="HTML") en vez de solo el mensaje resultar
    truncado. El aviso cortado es la respuesta correcta a "no cabe"; el
    mensaje entero perdido no lo es."""
    if len(text) <= limit:
        return text
    suffix = "\n… (truncado)"
    cut = text[: limit - len(suffix)]

    # No cortar a mitad de una etiqueta: si el último '<' del corte no tiene
    # su '>' correspondiente DENTRO del propio corte, recortar hasta antes
    # de ese '<' — mismo principio que un tope de bytes con marcador
    # (edgar_http.throttled_get_header).
    last_lt, last_gt = cut.rfind("<"), cut.rfind(">")
    if last_lt > last_gt:
        cut = cut[:last_lt]

    # Cerrar cualquier etiqueta que haya quedado abierta dentro de `cut` —
    # pila simple (abre -> push, cierra -> pop si coincide), no un parser
    # HTML completo: basta para las etiquetas planas que este proyecto usa.
    stack: list[str] = []
    for match in _TAG_RE.finditer(cut):
        is_closing, tag = match.group(1), match.group(2).lower()
        if is_closing:
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)
    closing_tags = "".join(f"</{tag}>" for tag in reversed(stack))
    return cut + closing_tags + suffix


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send_message(text: str, *, parse_mode: str = "HTML") -> bool:
    """Manda un mensaje al chat configurado. Devuelve True si la API de
    Telegram respondió ok=True, False en cualquier otro caso (sin configurar,
    error de red persistente, error permanente de la API) — nunca lanza,
    porque un fallo de notificación no debe tumbar el resto del batch
    nocturno.

    Reintenta (IMPROVEMENT_PLAN.md M15) ante fallos transitorios — caída de
    red o 429/5xx de Telegram — con el mismo backoff corto que el resto del
    proyecto. Un 4xx permanente (token/chat_id inválido, HTML mal formado)
    no se reintenta: no se arregla solo en el siguiente intento."""
    if not is_configured():
        logger.info("Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID) — se omite el envío")
        return False

    text = _truncate_html_aware(text, MAX_MESSAGE_LENGTH)

    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
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
            # El cuerpo del error de Telegram trae el motivo (chat_id
            # inválido, bot bloqueado por el usuario, token revocado, 429 de
            # rate limit, etc.) — se registra para que quede en los logs de
            # GitHub Actions.
            detail = e.read().decode("utf-8", errors="replace")
            if _es_permanente(e.code):
                logger.warning("Telegram HTTPError %s (permanente, no se reintenta): %s", e.code, detail)
                return False
            logger.warning("Telegram HTTPError %s (intento %d): %s", e.code, attempt, detail)
        except urllib.error.URLError as e:
            logger.warning("Telegram URLError (intento %d): %s", attempt, e.reason)

    logger.warning("No se pudo enviar el mensaje a Telegram tras reintentos")
    return False
