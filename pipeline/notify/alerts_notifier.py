"""alerts_notifier.py — avisa por Telegram de las alerts del último reporte
de paper trading (pipeline/paper_trading/analysis.py:compute_alerts).

A diferencia de signals_notifier.py, aquí NO basta con "ya se notificó esta
fila": paper_trading_reports.report_json se RECALCULA cada noche sobre la
MISMA semana (run_batch_tag es semanal, no diario — ver
paper_trading/report.py), así que la misma alert ("2 pérdidas consecutivas
en BALANCED") reaparecería idéntica noche tras noche mientras la semana
siga abierta. La deduplicación es por CONTENIDO de la alert (run_batch_tag +
version + type + message), vía la tabla notifications_sent y un INSERT ...
ON CONFLICT DO NOTHING RETURNING como compare-and-set atómico: si la fila ya
existía, no se devuelve nada y esa alert concreta no se reenvía.
"""
from __future__ import annotations

import hashlib
import logging

from pipeline.notify import telegram

logger = logging.getLogger(__name__)

_EMOJI_BY_TYPE = {"WARNING": "⚠️", "CONGRATULATE": "✅"}


def fetch_latest_paper_trading_report(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT run_batch_tag, report_json FROM paper_trading_reports ORDER BY created_at DESC LIMIT 1"
        )
        return cur.fetchone()


def _notification_id(run_batch_tag: str, version: str, alert: dict) -> str:
    # Hash corto del message para no depender de un id explícito por alert
    # (compute_alerts no genera uno) sin que la clave se vuelva ilegible.
    digest = hashlib.sha256(alert["message"].encode("utf-8")).hexdigest()[:12]
    return f"paper_trading:{run_batch_tag}:{version}:{alert['type']}:{digest}"


def _try_claim(conn, notification_id: str) -> bool:
    """True si esta alert no se había mandado antes (y queda reservada)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notifications_sent (notification_id) VALUES (%s) "
            "ON CONFLICT (notification_id) DO NOTHING RETURNING notification_id",
            (notification_id,),
        )
        claimed = cur.fetchone() is not None
    conn.commit()
    return claimed


def notify_new_alerts(conn) -> int:
    if not telegram.is_configured():
        logger.info("Telegram no configurado — no se buscan alerts de paper trading")
        return 0

    report_row = fetch_latest_paper_trading_report(conn)
    if report_row is None:
        return 0

    run_batch_tag = report_row["run_batch_tag"]
    report = report_row["report_json"]
    sent = 0
    lost = []  # IMPROVEMENT_PLAN.md M15: visibilidad de lo que se reclama y no llega a enviarse
    for version, v_report in report.get("versions", {}).items():
        for alert in v_report.get("alerts", []):
            notification_id = _notification_id(run_batch_tag, version, alert)
            if not _try_claim(conn, notification_id):
                continue  # ya se mandó esta misma alert en una pasada anterior
            emoji = _EMOJI_BY_TYPE.get(alert["type"], "")
            text = f"{emoji} <b>{version}</b> ({run_batch_tag})\n{alert['message']}"
            if telegram.send_message(text):
                sent += 1
            else:
                # El claim ya quedó registrado en notifications_sent aunque el
                # envío falle: es preferible perder un aviso puntual (poco
                # frecuente, se ve igualmente en el dashboard) a arriesgarse a
                # reenviar la misma alert cada noche por un fallo de red
                # recurrente en un chat_id roto. send_message ya reintenta
                # los fallos transitorios (ver telegram.py) — si sigue
                # fallando aquí es porque de verdad se agotaron los
                # reintentos o el error era permanente.
                lost.append(notification_id)
                logger.warning("No se pudo enviar alert %s (reclamada, no se reintentará)", notification_id)
    if lost:
        # Resumen agregado, no solo el warning por alert de arriba — mismo
        # patrón que xbrl_fundamentals.ingest_universe_fundamentals: un
        # recuento al final es lo que de verdad hace visible "cuántas se
        # están perdiendo", no una línea de log suelta por evento que hay
        # que contar a mano en la salida de GitHub Actions.
        logger.warning(
            "%d alert(s) de paper trading reclamadas pero NO enviadas (perdidas para siempre, no se reintentan): %s",
            len(lost), ", ".join(lost),
        )
    logger.info("Alerts de paper trading notificadas: %d enviadas, %d perdidas", sent, len(lost))
    return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    notify_new_alerts(get_connection())
