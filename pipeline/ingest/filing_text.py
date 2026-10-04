"""filing_text.py — extracción de texto de filings (Fase 3).

Cierra el gap señalado dos veces en RUNBOOK.md (Fase 1 y Fase 2):
edgar_scraper.py descarga el .txt completo del submission SOLO para leer la
cabecera SGML (Items del 8-K) — nunca guarda ni parsea el CUERPO del
documento. Sin eso, adversarial_analyzer.py razona sobre un placeholder y
novelty.py no puede calcular has_prior_guidance/rumor_flag.

POR QUÉ HAY QUE BUSCAR EXHIBITS, NO SOLO EL DOCUMENTO PRIMARIO: para un 8-K
de Item 2.02 (earnings — la clase con más potencia estadística de todo el
proyecto, AUDIT_LEAN.md §2.2.3), el cuerpo del propio 8-K suele ser una
frase de trámite ("see attached press release") — la información real está
en el exhibit adjunto, casi siempre con TYPE 'EX-99.1' o similar. Extraer
solo el documento primario dejaría a Bull/Bear razonando sobre "la empresa
publicó un comunicado" sin decir qué decía el comunicado.

FORMATO DE EDGAR (submission .txt completo): un wrapper SGML con uno o más
bloques <DOCUMENT>...</DOCUMENT>, cada uno con <TYPE>, <SEQUENCE>, <FILENAME>
y el contenido en <TEXT>...</TEXT> (casi siempre HTML). Es el formato
"full submission text file" documentado y estable de EDGAR desde hace años.

ADVERTENCIA DE VALIDACIÓN — sin verificar en vivo (egress bloqueado a
www.sec.gov, AUDIT_LEAN.md §1.5). El parser se prueba contra un fixture
offline con esta forma (test_filing_text.py), pero no se ha podido descargar
ni parsear un filing real desde este sandbox. Antes del backfill completo:
correr --single-event contra 2-3 filings conocidos y leer el texto extraído
a mano — comprobar que no queden restos de HTML/CSS. El corte a
MAX_TEXT_CHARS respeta el último fin de frase cuando lo encuentra cerca del
límite (ver _truncate_at_sentence_boundary), pero sigue pudiendo cortar a
mitad de frase en el caso límite sin puntuación cercana — revisar también
eso a mano.
"""
from __future__ import annotations

import hashlib
import logging
import re

from bs4 import BeautifulSoup

from pipeline.ingest.edgar_http import PermanentHTTPError, throttled_get

logger = logging.getLogger(__name__)

# Tipos de documento que cuentan como "el exhibit con la noticia real" para
# earnings. No exhaustivo — EX-99, EX-99.1, EX-99.2... cubren el patrón más
# común, pero una empresa puede nombrar su exhibit de otra forma.
_PRESS_RELEASE_EXHIBIT_PREFIX = "EX-99"

MAX_ATTEMPTS = 3  # intentos fallidos antes de dejar un evento sin texto para siempre
MAX_TEXT_CHARS = 8000  # tope de longitud guardada — controla coste de prompt en Bull/Bear/Judge
# Solo se extrae texto de eventos recientes: la IA analiza lo más nuevo
# primero y novelty.py mira como mucho 180 días atrás. Sin este tope, la
# pasada nocturna (más recientes primero) acabaría recorriendo hacia atrás
# los ~220k eventos del histórico a 1000/noche, ~3 MB comprimidos por noche,
# y llenaría una base de datos de plan gratuito en pocos meses.
MAX_EVENT_AGE_DAYS = 200

# Bajo este umbral (fracción de MAX_TEXT_CHARS), el corte por frase se
# descarta y se cae al corte duro de siempre (IMPROVEMENT_PLAN.md M8): un
# filing sin '.'/'!'/'?' cerca del límite (tablas, listas numéricas...) no
# debe perder más de la mitad del presupuesto de texto solo por buscar un
# punto que no está ahí.
_SENTENCE_BOUNDARY_MIN_FRACTION = 0.5
_SENTENCE_ENDINGS = (".", "!", "?")


def parse_submission_documents(raw_text: str) -> list[dict]:
    """Divide el submission completo en sus bloques <DOCUMENT>, cada uno con
    type/sequence/filename/text. No asume que TYPE/SEQUENCE/FILENAME
    aparezcan en un orden fijo dentro del bloque — los busca por regex,
    tolerando que falte alguno (se guarda como None en vez de fallar)."""
    blocks = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", raw_text, re.DOTALL | re.IGNORECASE)
    documents = []
    for block in blocks:
        doc_type = _extract_tag(block, "TYPE")
        sequence = _extract_tag(block, "SEQUENCE")
        filename = _extract_tag(block, "FILENAME")
        text_match = re.search(r"<TEXT>(.*?)</TEXT>", block, re.DOTALL | re.IGNORECASE)
        text = text_match.group(1) if text_match else ""
        documents.append(
            {
                "type": doc_type,
                "sequence": int(sequence) if sequence and sequence.isdigit() else None,
                "filename": filename,
                "raw_text": text,
            }
        )
    return documents


def _extract_tag(block: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.+)", block, re.IGNORECASE)
    return match.group(1).strip() if match else None


def strip_html_to_text(raw: str) -> str:
    """HTML (o texto plano) -> texto legible, sin tags/scripts/estilos,
    espacios colapsados. Usa BeautifulSoup en vez de regex ad-hoc: un
    stripper de tags por regex se rompe con HTML anidado, entidades, y
    bloques <script>/<style> que hay que descartar por completo, no solo
    "destaggear" — para el texto que le llega a un LLM, la robustez importa
    más que evitar una dependencia."""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _truncate_at_sentence_boundary(text: str, limit: int) -> str:
    """Corta text a lo sumo en limit caracteres, en el último punto/!/? antes
    del límite en vez de a mitad de frase (advertencia del docstring del
    módulo: "comprobar... que no se haya cortado a mitad de una frase").

    Si no hay ningún fin de frase razonablemente cerca del límite (por debajo
    de _SENTENCE_BOUNDARY_MIN_FRACTION de limit — p. ej. una tabla larga sin
    puntuación), se cae al corte duro de siempre: preferir una frase entera es
    una mejora de calidad, no algo por lo que tirar la mitad del presupuesto
    de texto que se paga por analizar."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    boundary = max(window.rfind(c) for c in _SENTENCE_ENDINGS)
    if boundary >= limit * _SENTENCE_BOUNDARY_MIN_FRACTION:
        return window[: boundary + 1]
    return window


def extract_best_text(documents: list[dict], prefer_exhibit: bool = True) -> dict:
    """Elige qué documento(s) usar como texto del evento.

    Estrategia (ver docstring del módulo): si prefer_exhibit=True (eventos de
    earnings) y existe un exhibit tipo EX-99*, se antepone su texto al del
    documento primario — porque suele ser donde está la noticia real. Si no
    hay exhibit, o prefer_exhibit=False, se usa solo el documento primario
    (el de sequence=1, o el primero de la lista si no hay sequence).

    Devuelve {"text": str, "includes_exhibit": bool, "length_chars": int},
    truncado a MAX_TEXT_CHARS — no por ahorrar espacio en BD, sino porque
    cada carácter de más es coste de tokens en CADA llamada de Bull/Bear/Judge
    (ARCHITECTURE_LEAN.md §6). El corte respeta el último fin de frase antes
    del límite en vez de partir a mitad de una (ver _truncate_at_sentence_boundary
    — IMPROVEMENT_PLAN.md M8).
    """
    if not documents:
        return {"text": "", "includes_exhibit": False, "length_chars": 0}

    primary = min(documents, key=lambda d: (d["sequence"] is None, d["sequence"] or 0))
    primary_text = strip_html_to_text(primary["raw_text"])

    exhibit_text = ""
    includes_exhibit = False
    if prefer_exhibit:
        exhibit = next((d for d in documents if d["type"] and d["type"].upper().startswith(_PRESS_RELEASE_EXHIBIT_PREFIX)), None)
        if exhibit:
            exhibit_text = strip_html_to_text(exhibit["raw_text"])
            includes_exhibit = True

    combined = f"{exhibit_text}\n\n{primary_text}" if includes_exhibit else primary_text
    combined = _truncate_at_sentence_boundary(combined.strip(), MAX_TEXT_CHARS)

    return {"text": combined, "includes_exhibit": includes_exhibit, "length_chars": len(combined)}


def fetch_filing_text(source_url: str, event_class: str) -> dict:
    """Descarga el submission completo y devuelve el texto elegido.
    prefer_exhibit se activa para clases de earnings — ver extract_best_text."""
    resp = throttled_get(source_url)
    documents = parse_submission_documents(resp.text)
    prefer_exhibit = event_class == "8K_2.02_EARNINGS"
    return extract_best_text(documents, prefer_exhibit=prefer_exhibit)


def populate_missing_filing_text(conn, limit: int = 200) -> int:
    """Backfill: rellena events.filing_text para eventos de EDGAR que aún no
    lo tienen. Los eventos FDA (source != 'EDGAR') se saltan explícitamente —
    no tienen un submission .txt de EDGAR que descargar, y el scraper de FDA
    sigue sin construirse (RUNBOOK.md), así que su filing_text queda NULL
    hasta que exista esa fuente."""
    # Más recientes primero: son los que la IA y el paper trading necesitan
    # YA; el histórico se completa en las pasadas siguientes.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.event_id, e.source_url, e.event_class FROM events e "
            "LEFT JOIN universe u ON u.cik = e.cik "
            "WHERE e.source = 'EDGAR' AND e.filing_text IS NULL AND e.filing_text_attempts < %s "
            "AND e.d0_close_date >= current_date - %s "
            # Fuera del universo invertible la IA no analiza: no hace falta texto
            # (las empresas aún sin evaluar, sin capitalización, sí entran).
            "AND (u.in_investable_universe OR u.market_cap_last_usd IS NULL) "
            "ORDER BY e.d0_close_date DESC, e.event_id DESC LIMIT %s",
            (MAX_ATTEMPTS, MAX_EVENT_AGE_DAYS, limit),
        )
        pending = cur.fetchall()

    def _fallo(event_id: int, *, definitivo: bool = False) -> None:
        # definitivo=True (404/403/400 — PermanentHTTPError) salta directo a
        # MAX_ATTEMPTS: un source_url roto no se arregla reintentando, así que
        # gastar 2 pasadas nocturnas más en el mismo evento no cambia el
        # resultado, solo lo retrasa (IMPROVEMENT_PLAN.md M5).
        with conn.cursor() as cur:
            if definitivo:
                cur.execute(
                    "UPDATE events SET filing_text_attempts = GREATEST(filing_text_attempts + 1, %s) "
                    "WHERE event_id = %s",
                    (MAX_ATTEMPTS, event_id),
                )
            else:
                cur.execute(
                    "UPDATE events SET filing_text_attempts = filing_text_attempts + 1 WHERE event_id = %s",
                    (event_id,),
                )
        conn.commit()

    count = 0
    for row in pending:
        try:
            result = fetch_filing_text(row["source_url"], row["event_class"])
        except PermanentHTTPError as exc:
            logger.error(
                "Evento %d: error permanente descargando el filing (%s) — no se reintenta",
                row["event_id"], exc,
            )
            _fallo(row["event_id"], definitivo=True)
            continue
        except RuntimeError:
            logger.exception("No se pudo descargar el filing del evento %d — se omite", row["event_id"])
            _fallo(row["event_id"])
            continue
        if not result["text"]:
            logger.warning("Evento %d: texto extraído vacío", row["event_id"])
            _fallo(row["event_id"])
            continue
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE events SET
                    filing_text = %(text)s,
                    filing_text_length_chars = %(length)s,
                    filing_text_includes_exhibit = %(includes_exhibit)s,
                    filing_text_extracted_at = now()
                WHERE event_id = %(event_id)s
                """,
                {
                    "text": result["text"],
                    "length": result["length_chars"],
                    "includes_exhibit": result["includes_exhibit"],
                    "event_id": row["event_id"],
                },
            )
        conn.commit()
        count += 1
    return count


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Extracción de texto de filings de EDGAR")
    parser.add_argument("--single-event", type=int, default=None, help="Verificación manual de UN event_id antes del backfill completo")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    from pipeline.db.connection import get_connection

    conn = get_connection()

    if args.single_event:
        with conn.cursor() as cur:
            cur.execute("SELECT source_url, event_class FROM events WHERE event_id = %s", (args.single_event,))
            row = cur.fetchone()
        if not row:
            print(f"Evento {args.single_event} no encontrado")
        else:
            result = fetch_filing_text(row["source_url"], row["event_class"])
            print(f"includes_exhibit={result['includes_exhibit']}, length={result['length_chars']}")
            print("---")
            print(result["text"][:2000])
    else:
        n = populate_missing_filing_text(conn, args.limit)
        print(f"{n} filings con texto extraído")
