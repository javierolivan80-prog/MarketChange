"""signals_notifier.py — avisa por Telegram de las señales TRADE nuevas.

Corre al final de cada pasada del pipeline nocturno
(.github/workflows/nightly_pipeline.yml), después de
event_analysis_pipeline.py. Selecciona las filas de event_analyses con
notified_at IS NULL en las que AL MENOS una versión de estrategia decidió
LONG/SHORT (abstention_engine.py), y manda un mensaje por evento — un
mensaje por evento y no un digest agregado a propósito: así un fallo de red
a mitad del batch dedup se resuelve fila a fila (solo se marca notified_at
en las que sí se enviaron, ver query_pending/mark_notified) en vez de tener
que reenviar o perder el lote entero.

`main()` es idempotente y seguro de re-ejecutar: sin TELEGRAM_BOT_TOKEN
configurado (por ejemplo, en este sandbox de desarrollo), no envía nada, no
marca nada como notificado, y no falla el resto del pipeline (ver
telegram.send_message).
"""
from __future__ import annotations

import logging

from pipeline.notify import telegram

logger = logging.getLogger(__name__)

STRATEGIES = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]

_DIRECTION_EMOJI = {"LONG": "🟢", "SHORT": "🔴"}


def fetch_pending_signals(conn) -> list[dict]:
    """Eventos con trade_decision_* != 'NO_TRADE' en alguna versión y que
    todavía no se han notificado. Una fila por evento (no por versión) — el
    mensaje agrupa las 3 versiones para no mandar 3 avisos del mismo evento.

    Investigado (IMPROVEMENT_PLAN.md M17): _format_message hace float() sobre
    confidence_in_conviction/net_conviction/ev_* sin guarda de NULL, y el
    WHERE de esta query no garantiza explícitamente que esas columnas vengan
    pobladas para toda fila que matchee. Pero SÍ lo garantiza el esquema —
    event_analyses.{net_conviction,confidence_in_conviction,ev_conservative,
    ev_aggressive,ev_balanced} y events.{ticker,event_class,source_url,
    filed_at} son NOT NULL en schema.sql — así que ninguna fila insertada por
    el código actual (_store_event_analysis, upsert_events) puede tener un
    NULL ahí. Confirmado el hallazgo como una falsa alarma para el código
    actual: no se añade una guarda contra un NULL que el propio esquema ya
    hace imposible (ver la disciplina general del proyecto de no validar lo
    que no puede pasar)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ea.event_id, e.ticker, e.event_class, e.source_url, e.filed_at,
                   ea.trade_decision_conservative, ea.trade_decision_aggressive, ea.trade_decision_balanced,
                   ea.confidence_in_conviction, ea.net_conviction,
                   ea.ev_conservative, ea.ev_aggressive, ea.ev_balanced
            FROM event_analyses ea
            JOIN events e ON e.event_id = ea.event_id
            WHERE ea.notified_at IS NULL
              AND (ea.trade_decision_conservative != 'NO_TRADE'
                   OR ea.trade_decision_aggressive != 'NO_TRADE'
                   OR ea.trade_decision_balanced != 'NO_TRADE')
            ORDER BY ea.analyzed_at
            """
        )
        return cur.fetchall()


def _format_message(row: dict) -> str:
    lines = [f"<b>{row['ticker']}</b> — {row['event_class']}"]
    ev_by_version = {
        "CONSERVATIVE": row["ev_conservative"],
        "AGGRESSIVE": row["ev_aggressive"],
        "BALANCED": row["ev_balanced"],
    }
    decision_by_version = {
        "CONSERVATIVE": row["trade_decision_conservative"],
        "AGGRESSIVE": row["trade_decision_aggressive"],
        "BALANCED": row["trade_decision_balanced"],
    }
    for version in STRATEGIES:
        decision = decision_by_version[version]
        if decision == "NO_TRADE":
            continue
        emoji = _DIRECTION_EMOJI.get(decision, "")
        ev_pct = float(ev_by_version[version]) * 100
        lines.append(f"{emoji} {version}: {decision} · EV={ev_pct:+.2f}%")
    lines.append(f"Confianza: {float(row['confidence_in_conviction']):.0f}% · net_conviction={float(row['net_conviction']):+.2f}")
    lines.append(f"<a href=\"{row['source_url']}\">Filing</a>")
    return "\n".join(lines)


def mark_notified(conn, event_ids: list[int]) -> None:
    if not event_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE event_analyses SET notified_at = now() WHERE event_id = ANY(%s)",
            (event_ids,),
        )
    conn.commit()


def notify_pending_signals(conn) -> int:
    """Envía y marca. Devuelve cuántas señales se notificaron con éxito.
    Si Telegram no está configurado, devuelve 0 sin tocar la base de datos
    (telegram.send_message ya registra el motivo)."""
    if not telegram.is_configured():
        logger.info("Telegram no configurado — no se buscan señales pendientes")
        return 0

    pending = fetch_pending_signals(conn)
    sent_ids = []
    for row in pending:
        if telegram.send_message(_format_message(row)):
            sent_ids.append(row["event_id"])
        else:
            # No se marca notified_at: se reintenta en la próxima pasada.
            logger.warning("No se pudo notificar event_id=%s (%s) — se reintentará", row["event_id"], row["ticker"])
    mark_notified(conn, sent_ids)
    logger.info("Señales notificadas: %d/%d pendientes", len(sent_ids), len(pending))
    return len(sent_ids)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    notify_pending_signals(get_connection())
