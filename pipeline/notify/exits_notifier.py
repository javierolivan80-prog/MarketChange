"""exits_notifier.py — avisa por Telegram cuando toca CERRAR una posición.

Este proyecto no ejecuta órdenes reales (decisión explícita: quien usa
MarketChange opera a mano en su propio bróker — MyInvestor u otro). Hasta
este módulo, signals_notifier.py avisaba de CUÁNDO ENTRAR, pero nada avisaba
de CUÁNDO SALIR — la única forma de enterarse de que tocaba vender era entrar
al dashboard cada día. Este módulo cierra ese hueco.

No repite ningún cálculo: reutiliza exactamente lo que
paper_trading/simulator.py ya calcula cada noche contra precios reales
(paper_trades.status pasa a CLOSED_TP/CLOSED_SL/CLOSED_TIMEOUT solo cuando la
simulación, con la MISMA disciplina anti-look-ahead que el backtest
histórico, detecta que el take-profit/stop-loss/límite de tiempo se cumplió).
Un aviso de salida es, por tanto, tan fiable como la propia simulación de
paper trading — no una fuente de verdad nueva.

Un mensaje por trade cerrado, no un digest: mismo motivo que
signals_notifier.py (un fallo de red a mitad del envío se resuelve fila a
fila, ver notified_at)."""
from __future__ import annotations

import html
import logging

from pipeline.notify import telegram

logger = logging.getLogger(__name__)

_CLOSED_STATUSES = ("CLOSED_TP", "CLOSED_SL", "CLOSED_TIMEOUT")

_VERSION_LABEL = {"CONSERVATIVE": "conservadora", "BALANCED": "equilibrada", "AGGRESSIVE": "agresiva"}

_EXIT_REASON_LABEL = {
    "TAKE_PROFIT": "Objetivo de beneficio alcanzado",
    "STOP_LOSS": "Stop de pérdidas alcanzado",
    "TIMEOUT": "Límite de tiempo alcanzado sin disparar TP ni SL",
}


def fetch_pending_exits(conn) -> list[dict]:
    """Trades de paper_trades ya cerrados (status CLOSED_*) que todavía no
    se han notificado. Una fila por (evento, versión) — igual que
    paper_trades, así que la misma empresa puede aparecer varias veces si
    varias versiones de estrategia la operaron."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.trade_id, pt.version, pt.direction, pt.entry_date, pt.entry_price,
                   pt.exit_date, pt.exit_price, pt.exit_reason, pt.pnl_pct,
                   e.ticker, e.source_url
            FROM paper_trades pt
            JOIN events e ON e.event_id = pt.event_id
            WHERE pt.status = ANY(%s)
              AND pt.notified_at IS NULL
            ORDER BY pt.exit_date
            """,
            (list(_CLOSED_STATUSES),),
        )
        return cur.fetchall()


def _format_message(row: dict) -> str:
    label = _EXIT_REASON_LABEL.get(row["exit_reason"], row["exit_reason"] or "Cierre")
    pnl = row["pnl_pct"]
    pnl_text = f"{float(pnl):+.2f}%" if pnl is not None else "—"
    lines = [
        # Mismo vocabulario que el aviso de entrada y el panel (estrategia en
        # español, nada de nombres internos); ticker escapado por parse_mode=HTML.
        f"<b>CERRAR {html.escape(str(row['ticker']))}</b> · estrategia {_VERSION_LABEL.get(row['version'], row['version'])} · {row['direction'].capitalize()}",
        label,
        f"Entrada: {row['entry_date']} a {float(row['entry_price']):.2f}",
        f"Salida sugerida: {row['exit_date']} a {float(row['exit_price']):.2f} · "
        f"P&L de referencia: {pnl_text}",
    ]
    if row.get("source_url"):
        lines.append(f'<a href="{row["source_url"]}">Filing original</a>')
    lines.append("Calculado sobre precio de cierre/máx/mín reales — al operar a mano, tu precio de ejecución puede variar.")
    return "\n".join(lines)


def mark_notified(conn, trade_ids: list[int]) -> None:
    if not trade_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE paper_trades SET notified_at = now() WHERE trade_id = ANY(%s)",
            (trade_ids,),
        )
    conn.commit()


def notify_pending_exits(conn) -> int:
    """Envía y marca. Devuelve cuántas salidas se notificaron con éxito.
    Si Telegram no está configurado, devuelve 0 sin tocar la base de datos
    (telegram.send_message ya registra el motivo)."""
    if not telegram.is_configured():
        logger.info("Telegram no configurado — no se buscan salidas pendientes")
        return 0

    pending = fetch_pending_exits(conn)
    sent_ids = []
    for row in pending:
        if telegram.send_message(_format_message(row)):
            sent_ids.append(row["trade_id"])
        else:
            # No se marca notified_at: se reintenta en la próxima pasada.
            logger.warning("No se pudo notificar la salida de trade_id=%s (%s) — se reintentará", row["trade_id"], row["ticker"])
    mark_notified(conn, sent_ids)
    logger.info("Salidas notificadas: %d/%d pendientes", len(sent_ids), len(pending))
    return len(sent_ids)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    notify_pending_exits(get_connection())
