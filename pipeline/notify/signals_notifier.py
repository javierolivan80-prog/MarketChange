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

import html
import logging

from pipeline import config
from pipeline.notify import telegram

logger = logging.getLogger(__name__)

STRATEGIES = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]

_DIRECTION_EMOJI = {"LONG": "🟢", "SHORT": "🔴"}

# Mismo vocabulario que el panel (app/lib/labels.ts): el aviso y la pantalla
# a la que enlaza tienen que llamar igual a las mismas cosas.
_VERSION_LABEL = {"CONSERVATIVE": "Conservador", "BALANCED": "Equilibrado", "AGGRESSIVE": "Agresivo"}
_EVENT_CLASS_LABEL = {
    "8K_2.02_EARNINGS": "Resultados",
    "8K_1.01_MATERIAL_AGMT": "Acuerdo relevante / M&A",
    "8K_4.02_RESTATEMENT": "Reformulación de cuentas",
    "8K_5.02_MGMT_CHANGE": "Cambio en la dirección",
    "8K_1.03_BANKRUPTCY": "Concurso / quiebra",
    "8K_4.01_AUDITOR_CHANGE": "Cambio de auditor",
    "8K_8.01_OTHER": "Otros hechos relevantes",
    "FDA_APPROVAL": "Aprobación FDA",
    "FDA_CRL": "Rechazo FDA (CRL)",
}

# Tope por campo de texto libre del LLM: el aviso tiene que leerse de un
# vistazo en el móvil; el razonamiento completo está en el panel.
_MAX_REASON_CHARS = 280


def _clip(text: str, limit: int = _MAX_REASON_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


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
    # Filtros de aviso (config.ALERT_*): un evento que no los pasa no se
    # marca como notificado — se queda pendiente, así que si se amplía el
    # filtro, los que vuelvan a entrar dentro de ALERT_MAX_AGE_DAYS se avisan
    # en la siguiente pasada.
    filters = []
    params: list = []
    if config.ALERT_MAX_AGE_DAYS:
        filters.append("e.d0_close_date >= current_date - %s")
        params.append(config.ALERT_MAX_AGE_DAYS)
    if config.ALERT_MIN_CONFIDENCE:
        filters.append("ea.confidence_in_conviction >= %s")
        params.append(config.ALERT_MIN_CONFIDENCE)
    if config.ALERT_TICKERS:
        filters.append("upper(e.ticker) = ANY(%s)")
        params.append(list(config.ALERT_TICKERS))
    if config.ALERT_EVENT_CLASSES:
        filters.append("e.event_class = ANY(%s)")
        params.append(list(config.ALERT_EVENT_CLASSES))
    if config.ALERT_REQUIRE_TECHNICAL:
        # Solo señales cuyo plan técnico pasa las comprobaciones previas
        # (analyze/technical_analysis.py). Sin plan todavía = pendiente.
        filters.append("ta.passes_filters IS TRUE")
    extra = "".join(f"\n              AND {f}" for f in filters)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ea.event_id, e.ticker, e.event_class, e.source_url, e.filed_at,
                   ea.trade_decision_conservative, ea.trade_decision_aggressive, ea.trade_decision_balanced,
                   ea.confidence_in_conviction, ea.net_conviction,
                   ea.ev_conservative, ea.ev_aggressive, ea.ev_balanced,
                   ea.bull_analyst_output, ea.bear_analyst_output, ea.judge_output,
                   ta.entry_price, ta.stop_price, ta.target_price, ta.target2_price, ta.risk_reward,
                   ta.confidence AS tech_confidence, ta.passes_filters, ta.position_size_pct, ta.timeframe_days,
                   ta.details->>'reason_if_rejected' AS tech_reason
            FROM event_analyses ea
            JOIN events e ON e.event_id = ea.event_id
            LEFT JOIN technical_analyses ta ON ta.event_id = ea.event_id
            WHERE ea.notified_at IS NULL
              AND (ea.trade_decision_conservative != 'NO_TRADE'
                   OR ea.trade_decision_aggressive != 'NO_TRADE'
                   OR ea.trade_decision_balanced != 'NO_TRADE'){extra}
            ORDER BY ea.analyzed_at
            """.format(extra=extra),
            params,
        )
        return cur.fetchall()


def _price(value) -> str:
    return f"{float(value):.2f}"


def _plan_lines(row: dict) -> list[str]:
    """Plan técnico (analyze/technical_analysis.py), si ya está calculado."""
    if row.get("tech_confidence") is None or row.get("entry_price") is None:
        return []
    verdict = "pasa los filtros de riesgo" if row.get("passes_filters") else f"no pasa los filtros: {row.get('tech_reason') or 'ver panel'}"
    lines = [f"\n<b>Plan técnico</b> · confianza {int(row['tech_confidence'])}/100 · {html.escape(verdict)}"]
    targets = f"Objetivo {_price(row['target_price'])}" if row.get("target_price") is not None else "Objetivo —"
    if row.get("target2_price") is not None:
        targets += f" (final {_price(row['target2_price'])})"
    lines.append(f"Entrada ~{_price(row['entry_price'])} · Stop {_price(row['stop_price'])} · {targets}")
    extra = []
    if row.get("risk_reward") is not None:
        extra.append(f"Riesgo/beneficio 1:{float(row['risk_reward']):.1f}")
    if row.get("timeframe_days") is not None:
        extra.append(f"~{int(row['timeframe_days'])} sesiones")
    if row.get("position_size_pct") is not None and row.get("passes_filters"):
        extra.append(f"tamaño máx. {float(row['position_size_pct']):g}% del capital")
    if extra:
        lines.append(" · ".join(extra))
    return lines


def _format_message(row: dict) -> str:
    """Aviso de una señal nueva. Todo texto interpolado se escapa con
    html.escape: el mensaje va con parse_mode="HTML" y un '<' o '&' sin
    escapar (frecuente en texto redactado por el LLM, p. ej. "margen <5%" o
    "M&A") hace que la Bot API rechace el mensaje ENTERO — y como el evento
    no se marca notified_at, se reintentaría en cada pasada sin llegar nunca.

    Además de la decisión por versión, el aviso incluye el porqué (la tesis
    del lado que ganó el debate) y qué lo invalidaría (key_uncertainty del
    Judge): sin eso el usuario recibe un ticker y un número y tiene que abrir
    el filing en bruto para saber si le interesa."""
    e = html.escape
    event_label = _EVENT_CLASS_LABEL.get(row["event_class"], row["event_class"])
    lines = [f"<b>{e(row['ticker'])}</b> · {e(event_label)}"]
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
        lines.append(f"{emoji} {_VERSION_LABEL[version]}: {decision} · EV {ev_pct:+.2f}%")
    lines.append(f"Confianza: {float(row['confidence_in_conviction']):.0f}%")

    net = float(row["net_conviction"])
    winning_side = row.get("bull_analyst_output") if net > 0 else row.get("bear_analyst_output")
    thesis = (winning_side or {}).get("thesis" if net > 0 else "counter_thesis")
    if thesis:
        lines.append(f"\n<b>Por qué:</b> {e(_clip(thesis))}")
    uncertainty = (row.get("judge_output") or {}).get("key_uncertainty")
    if uncertainty:
        lines.append(f"<b>Qué lo cambiaría:</b> {e(_clip(uncertainty))}")
    lines.extend(_plan_lines(row))

    links = [f'<a href="{e(row["source_url"], quote=True)}">Filing</a>']
    if config.DASHBOARD_URL:
        url = f"{config.DASHBOARD_URL}/senales/{int(row['event_id'])}"
        links.append(f'<a href="{e(url, quote=True)}">Análisis completo</a>')
    lines.append("\n" + " · ".join(links))
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
