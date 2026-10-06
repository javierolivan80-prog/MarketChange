"""weekly_digest.py — resumen semanal por Telegram.

El único motivo para volver al sistema era el aviso de cada señal nueva; una
semana sin señales (lo normal en un sistema que se abstiene la mayor parte
del tiempo) era una semana de silencio, indistinguible de un pipeline roto.
El resumen cierra ese hueco una vez por semana: cuántos eventos se
analizaron, cuántos superaron los filtros, por qué se descartó el resto, y
cómo han ido las señales cerradas, medidas con precios reales — con enlace al
historial del panel si DASHBOARD_URL está definida.

Se envía en la pasada nocturna del sábado (UTC), que es la primera tras el
cierre del viernes. Deduplicación por semana ISO en notifications_sent: el
aviso se registra DESPUÉS de enviarse con éxito (a diferencia de
alerts_notifier, que reclama antes), así que un fallo de red no pierde el
resumen — se reintenta en la siguiente pasada. El workflow no permite dos
corridas simultáneas (concurrency: nightly-pipeline), así que no hay carrera
entre comprobar y registrar.

Mismo vocabulario que el panel (app/lib/labels.ts y getAbstentionSummary en
app/lib/queries.ts): los motivos se agrupan por el prefijo fijo que escribe
abstention_engine.decide_for_strategy.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from pipeline import config
from pipeline.backtest.sample_split import NO_ES_OOS_SQL
from pipeline.notify import telegram

logger = logging.getLogger(__name__)

SEND_WEEKDAY = 5  # sábado (datetime.weekday(): lunes=0)

_VERSION_COLUMN = {
    "CONSERVATIVE": "trade_decision_conservative",
    "BALANCED": "trade_decision_balanced",
    "AGGRESSIVE": "trade_decision_aggressive",
}
_VERSION_LABEL = {"CONSERVATIVE": "conservadora", "BALANCED": "equilibrada", "AGGRESSIVE": "agresiva"}

# (prefijo/patrón LIKE, etiqueta) — en el mismo orden que el CASE del panel.
_REASON_CATEGORIES = [
    ("novelty_score=%", "El mercado ya lo sabía"),
    ("confidence_in_conviction=%", "El debate no fue concluyente"),
    ("|ev|=%", "Valor esperado insuficiente tras costes"),
    ("%deslistado%", "Riesgo de exclusión de bolsa"),
    ("datos contradictorios%", "Datos contradictorios"),
    ("CRL de FDA%", "FDA aún no confirmado por la empresa"),
    ("%ilíquido%", "Acción poco líquida"),
    ("%liquidez%", "Acción poco líquida"),
]


def _recommended_version(conn) -> str:
    with conn.cursor() as cur:
        # El último in-sample: un OOS lanzado a mano no cuenta (H-07).
        cur.execute(f"SELECT report_json->>'best_version' AS v FROM validation_reports WHERE {NO_ES_OOS_SQL} ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    version = row["v"] if row else None
    return version if version in _VERSION_COLUMN else "BALANCED"


def _categorize(reason: str | None) -> str:
    if reason:
        for pattern, label in _REASON_CATEGORIES:
            core = pattern.strip("%")
            if pattern.startswith("%") and core in reason:
                return label
            if not pattern.startswith("%") and reason.startswith(core):
                return label
    return "Otros motivos"


def collect_week(conn, version: str, days: int = 7) -> dict:
    """Datos del resumen para los últimos `days` días. Puro SQL de lectura."""
    column = _VERSION_COLUMN[version]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ea.{column} AS decision, ea.abstention_decision->%s->>'reason_if_no_trade' AS reason
            FROM event_analyses ea
            WHERE ea.analyzed_at > now() - make_interval(days => %s)
            """,
            (version, days),
        )
        analyses = cur.fetchall()
        cur.execute(
            """
            SELECT DISTINCT ON (p.event_id) p.event_id, e.ticker, p.status, p.pnl_pct, p.exit_date
            FROM paper_trades p
            JOIN events e ON e.event_id = p.event_id
            WHERE p.version = %s
            ORDER BY p.event_id, p.updated_at DESC
            """,
            (version,),
        )
        trades = cur.fetchall()

    discarded: dict[str, int] = {}
    for row in analyses:
        if row["decision"] == "NO_TRADE":
            label = _categorize(row["reason"])
            discarded[label] = discarded.get(label, 0) + 1

    now = datetime.now(timezone.utc).date()
    closed_this_week = [
        t for t in trades
        if t["status"] != "OPEN" and t["pnl_pct"] is not None and t["exit_date"] is not None and (now - t["exit_date"]).days <= days
    ]
    return {
        "version": version,
        "days": days,
        "analyzed": len(analyses),
        "traded": sum(1 for r in analyses if r["decision"] != "NO_TRADE"),
        "discarded": sorted(discarded.items(), key=lambda kv: (-kv[1], kv[0])),
        "closed": [(t["ticker"], float(t["pnl_pct"])) for t in closed_this_week],
        "open": sum(1 for t in trades if t["status"] == "OPEN"),
    }


def format_digest(data: dict) -> str:
    e = html.escape
    lines = [f"<b>Resumen semanal</b> · estrategia {_VERSION_LABEL[data['version']]}"]
    lines.append(f"{data['analyzed']} eventos analizados · {data['traded']} superaron los filtros")
    if data["discarded"]:
        lines.append("\n<b>Descartados</b>")
        for label, n in data["discarded"][:4]:
            lines.append(f"{n} · {e(label)}")
    closed = data["closed"]
    if closed:
        wins = sum(1 for _, pnl in closed if pnl > 0)
        avg = sum(pnl for _, pnl in closed) / len(closed)
        lines.append(f"\n<b>Cerradas esta semana</b>: {len(closed)} · {wins} con ganancia · media {avg:+.2f}%")
        for ticker, pnl in sorted(closed, key=lambda t: -t[1])[:5]:
            lines.append(f"{e(str(ticker))} {pnl:+.2f}%")
    else:
        lines.append("\nNinguna posición cerrada esta semana.")
    lines.append(f"Posiciones abiertas: {data['open']}")
    if data["analyzed"] == 0:
        lines.append("\nEsta semana no se ha publicado ningún análisis: las actualizaciones se reanudarán en breve.")
    if config.DASHBOARD_URL:
        lines.append(f'\n<a href="{e(config.DASHBOARD_URL + "/historial", quote=True)}">Ver historial completo</a>')
    return "\n".join(lines)


def _week_id(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"weekly_digest:{year}-W{week:02d}"


def _already_sent(conn, notification_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM notifications_sent WHERE notification_id = %s", (notification_id,))
        return cur.fetchone() is not None


def _mark_sent(conn, notification_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notifications_sent (notification_id) VALUES (%s) ON CONFLICT (notification_id) DO NOTHING",
            (notification_id,),
        )
    conn.commit()


def send_weekly_digest(conn, now: datetime | None = None, *, force: bool = False) -> bool:
    """Envía el resumen si toca (sábado, y no enviado ya esta semana ISO).
    `force=True` ignora el día de la semana (no la deduplicación)."""
    if not telegram.is_configured():
        logger.info("Telegram no configurado — no se envía el resumen semanal")
        return False
    now = now or datetime.now(timezone.utc)
    if not force and now.weekday() != SEND_WEEKDAY:
        logger.info("Resumen semanal: hoy no toca (solo sábados UTC)")
        return False
    notification_id = _week_id(now)
    if _already_sent(conn, notification_id):
        logger.info("Resumen semanal %s ya enviado", notification_id)
        return False

    text = format_digest(collect_week(conn, _recommended_version(conn)))
    if not telegram.send_message(text):
        logger.warning("No se pudo enviar el resumen semanal %s — se reintentará en la próxima pasada", notification_id)
        return False
    _mark_sent(conn, notification_id)
    logger.info("Resumen semanal %s enviado", notification_id)
    return True


if __name__ == "__main__":
    import argparse

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="Enviar aunque hoy no sea sábado (respeta la deduplicación semanal)")
    args = parser.parse_args()
    send_weekly_digest(get_connection(), force=args.force)
