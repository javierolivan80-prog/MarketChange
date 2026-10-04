"""edgar_http.py — GET rate-limitado y con reintentos hacia EDGAR, compartido.

Extraído de edgar_scraper.py en la Fase 3 porque filing_text.py necesita
exactamente el mismo comportamiento (User-Agent con contacto real, límite de
10 req/s de la SEC, backoff en 429/fallos transitorios) para descargar el
cuerpo completo de cada filing. Duplicarlo en dos sitios es precisamente el
tipo de cosa que diverge en silencio con el tiempo (ver el mismo razonamiento
en backtest/factor_model.py, extraído en la Fase 2 por el mismo motivo).
"""
from __future__ import annotations

import logging
import time

import requests

from pipeline import config

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": config.EDGAR_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
_RATE_LIMIT_DELAY = 1.0 / config.EDGAR_RATE_LIMIT_PER_SEC
# Backoff: 2s, 4s, 8s, 16s — misma política que el resto del proyecto.
# Hallazgo de auditoría (IMPROVEMENT_PLAN.md Q5): esta lista estaba
# duplicada literal en throttled_get y throttled_get_header — exactamente
# el tipo de cosa que diverge en silencio si alguien cambia una copia y no
# la otra.
_RETRY_DELAYS = [2, 4, 8, 16]

# Una sola sesión HTTP para toda la corrida (BUGS_REPORT.md H-29): con
# requests.get suelto, cada petición abría conexión y TLS nuevos con la SEC.
_SESION = requests.Session()
_SESION.headers.update(HEADERS)


def _get(url: str, **kwargs) -> requests.Response:
    """Único punto de salida hacia EDGAR (los tests lo sustituyen)."""
    return _SESION.get(url, **kwargs)


# Throttle por intervalo entre INICIOS de petición, no un sleep fijo antes de
# cada una (H-29): el sleep fijo se sumaba a la latencia de la propia petición
# y dejaba el ritmo real en ~5 req/s frente a los 8 configurados. Ahora solo
# se espera lo que falta para cumplir el intervalo, así que el tope de la SEC
# (10 req/s) se respeta igual y no se pierde tiempo.
_ultimo_inicio = 0.0
_reloj = time.monotonic  # inyectable en tests sin tocar el módulo time global


def _esperar_turno() -> None:
    global _ultimo_inicio
    falta = _ultimo_inicio + _RATE_LIMIT_DELAY - _reloj()
    if falta > 0:
        time.sleep(falta)
    _ultimo_inicio = _reloj()


class PermanentHTTPError(RuntimeError):
    """4xx que no tiene sentido reintentar (404, 403...).

    Se separa de los fallos transitorios porque reintentar un error permanente
    no solo no arregla nada: lo ESCONDE. Con una URL mal construida, cada
    filing costaba 30 segundos de esperas (2+4+8+16) antes de rendirse, así que
    un fallo que debería saltar a la vista en un segundo convertía la ingesta de
    un día en un proceso de más de media hora que parecía estar trabajando.
    """


def _es_permanente(status: int) -> bool:
    # 429 (rate limit) es 4xx pero SÍ es transitorio: es justo lo que hay que
    # reintentar. 408 (timeout) igual.
    return 400 <= status < 500 and status not in (408, 429)


# Tope al Retry-After que la SEC pueda pedir (IMPROVEMENT_PLAN.md M4) — por
# si un valor disparatado (o un proxy intermedio raro) pidiera esperar
# horas; con esto, como mucho se respeta hasta un minuto de más sobre el
# backoff fijo antes de intentarlo de todas formas.
_MAX_RETRY_AFTER_S = 60


def _retry_after_seconds(resp: requests.Response) -> int | None:
    """Segundos del header Retry-After de un 429, o None si no viene o no es
    un entero (Retry-After también admite una fecha HTTP completa — formato
    poco común en APIs modernas y no soportado aquí; se cae al backoff fijo
    en ese caso, no es un error).

    Antes, un 429 siempre esperaba el backoff fijo (2/4/8/16s) sin mirar si
    la SEC pedía explícitamente un tiempo distinto — ignorar una instrucción
    del servidor sobre cuánto esperar es justo el tipo de comportamiento que
    un 429 existe para corregir."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = int(raw)
    except ValueError:
        return None
    return min(seconds, _MAX_RETRY_AFTER_S) if seconds > 0 else None


def throttled_get(url: str, **kwargs) -> requests.Response:
    """GET con rate limit fijo y reintentos con backoff exponencial.

    La SEC devuelve 429 si se supera el límite; también hay que tolerar caídas
    de red transitorias. Backoff: 2s, 4s, 8s, 16s (misma política que el resto
    del proyecto, por consistencia).
    """
    # Copia propia (no el objeto _RETRY_DELAYS compartido, IMPROVEMENT_PLAN.md
    # Q5): esta lista se MUTA más abajo cuando un 429 trae Retry-After, y
    # mutar la constante del módulo corrompería el backoff de cualquier otra
    # llamada concurrente o posterior que la reutilice.
    espera = [0, *_RETRY_DELAYS]
    last_exc: Exception | None = None
    for attempt, delay in enumerate(espera):
        if delay:
            time.sleep(delay)
        _esperar_turno()
        try:
            resp = _get(url, timeout=30, **kwargs)
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None and attempt + 1 < len(espera):
                    espera[attempt + 1] = retry_after
                    logger.warning(
                        "429 de EDGAR en %s, reintentando (Retry-After=%ds)", url, retry_after
                    )
                else:
                    logger.warning("429 de EDGAR en %s, reintentando", url)
                continue
            if _es_permanente(resp.status_code):
                raise PermanentHTTPError(f"{resp.status_code} en {url} — no se reintenta")
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning("Fallo en %s (intento %d): %s", url, attempt, exc)
    raise RuntimeError(f"No se pudo descargar {url} tras reintentos") from last_exc


# Corte de seguridad para la descarga parcial. La cabecera SGML de un
# submission son 1-3 KB; 256 KB deja margen de sobra para cabeceras raras y
# aun así evita traerse el cuerpo entero.
_HEADER_BYTE_CAP = 256 * 1024
_HEADER_END_MARKER = "</SEC-HEADER>"


def throttled_get_header(url: str) -> str:
    """Igual que throttled_get, pero corta la descarga en cuanto termina la
    cabecera SGML del submission.

    MOTIVO (medido, no supuesto): el .txt de un submission completo incluye
    TODOS los documentos y anexos del filing — habitualmente varios MB, a veces
    decenas. De todo eso, la ingesta solo lee las líneas 'ACCESSION NUMBER:' e
    'ITEM INFORMATION:', que están en los primeros KB. Descargarlo entero para
    leer la cabecera hacía que un solo día de backfill (unos cientos de 8-K)
    tardara más de media hora, casi toda en transferencia tirada a la basura.

    El corte es por contenido (`</SEC-HEADER>`) con tope duro por bytes: si un
    fichero no trae el marcador, se devuelven los primeros 256 KB y el parseo
    sigue como antes en vez de fallar. Nunca devuelve MENOS de lo que un
    parser de cabecera necesita.
    """
    espera = [0, *_RETRY_DELAYS]  # copia propia — ver el comentario en throttled_get
    last_exc: Exception | None = None
    for attempt, delay in enumerate(espera):
        if delay:
            time.sleep(delay)
        _esperar_turno()
        try:
            with _get(url, timeout=30, stream=True) as resp:
                if resp.status_code == 429:
                    retry_after = _retry_after_seconds(resp)
                    if retry_after is not None and attempt + 1 < len(espera):
                        espera[attempt + 1] = retry_after
                        logger.warning(
                            "429 de EDGAR en %s, reintentando (Retry-After=%ds)", url, retry_after
                        )
                    else:
                        logger.warning("429 de EDGAR en %s, reintentando", url)
                    continue
                if _es_permanente(resp.status_code):
                    raise PermanentHTTPError(f"{resp.status_code} en {url} — no se reintenta")
                resp.raise_for_status()
                chunks: list[str] = []
                total = 0
                # Ventana con la cola del trozo anterior: el marcador puede
                # quedar partido entre trozos (y con trozos pequeños, entre
                # más de dos), así que buscarlo solo en el último se lo salta
                # y la descarga seguiría hasta el tope de bytes.
                solapamiento = len(_HEADER_END_MARKER) - 1
                cola = ""
                for chunk in resp.iter_content(chunk_size=8192, decode_unicode=True):
                    if not chunk:
                        continue
                    if isinstance(chunk, bytes):  # decode_unicode no aplica sin charset
                        chunk = chunk.decode("utf-8", errors="replace")
                    chunks.append(chunk)
                    total += len(chunk)
                    if _HEADER_END_MARKER in cola + chunk or total >= _HEADER_BYTE_CAP:
                        break
                    cola = (cola + chunk)[-solapamiento:]
                return "".join(chunks)
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning("Fallo en %s (intento %d): %s", url, attempt, exc)
    raise RuntimeError(f"No se pudo descargar la cabecera de {url} tras reintentos") from last_exc
