"""test_notify.py — pipeline/notify/*.

telegram.py se prueba puro (urlopen mockeado, sin red real ni credenciales).
signals_notifier.py y alerts_notifier.py se prueban de dos formas: el
formateo de mensajes es puro, y la query/dedup/marcado en base de datos es
integración contra Postgres real (mismo patrón que test_db_integration.py —
se salta si no hay DATABASE_URL)."""
from __future__ import annotations

import io
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


# ---------------------------------------------------------------------------
# telegram.py — puro, sin red
# ---------------------------------------------------------------------------


def test_send_message_noop_when_not_configured(monkeypatch):
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", None)
    assert telegram.is_configured() is False
    assert telegram.send_message("hola") is False


def test_send_message_posts_expected_payload(monkeypatch):
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    assert telegram.send_message("<b>hola</b>") is True
    assert captured["url"] == "https://api.telegram.org/botTESTTOKEN/sendMessage"
    assert captured["body"]["chat_id"] == "12345"
    assert captured["body"]["text"] == "<b>hola</b>"


def test_send_message_false_on_api_not_ok(monkeypatch):
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": False, "description": "chat not found"}).encode("utf-8")

    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda request, timeout=None: FakeResponse())

    assert telegram.send_message("hola") is False


def test_send_message_truncates_long_text(monkeypatch):
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    telegram.send_message("x" * 5000)
    assert len(captured["body"]["text"]) <= telegram.MAX_MESSAGE_LENGTH


def test_truncate_html_aware_closes_open_tags():
    """IMPROVEMENT_PLAN.md M16: un truncado por caracteres a secas puede
    cortar a mitad de una etiqueta o dejarla sin cerrar — la Bot API rechaza
    el mensaje ENTERO por HTML mal formado, no solo lo trunca."""
    from pipeline.notify.telegram import _truncate_html_aware

    texto = "<b>" + "x" * 50 + "</b>"
    resultado = _truncate_html_aware(texto, limit=20)

    assert resultado.count("<b>") == resultado.count("</b>")
    assert resultado.endswith("… (truncado)")
    # No debe quedar una etiqueta a medias tras el corte (p.ej. "<b" sin ">").
    assert "<b" not in resultado.split("</b>")[0][3:]


def test_truncate_html_aware_no_corta_dentro_de_la_etiqueta():
    """Un límite que caería justo en mitad de <a href="..."> debe recortar
    ANTES de esa etiqueta, no dejarla partida."""
    from pipeline.notify.telegram import _truncate_html_aware

    texto = "hola " + '<a href="https://example.com/muy/largo/de/verdad">enlace</a>' + " fin"
    # Límite justo a mitad de la apertura de la etiqueta <a ...>.
    resultado = _truncate_html_aware(texto, limit=len("hola ") + 10)

    assert "<a" not in resultado.replace("… (truncado)", "")


def test_truncate_html_aware_texto_sin_etiquetas_se_comporta_como_antes():
    from pipeline.notify import telegram

    resultado = telegram._truncate_html_aware("x" * 5000, limit=telegram.MAX_MESSAGE_LENGTH)
    assert len(resultado) <= telegram.MAX_MESSAGE_LENGTH


def test_send_message_reintenta_ante_error_transitorio(monkeypatch):
    """IMPROVEMENT_PLAN.md M15: antes, un solo fallo de red perdía el
    mensaje sin más — ahora se reintenta con el mismo patrón de backoff que
    el resto del proyecto."""
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram.time, "sleep", lambda _: None)

    intentos = {"n": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        intentos["n"] += 1
        if intentos["n"] < 2:
            raise telegram.urllib.error.URLError("conexión cortada")
        return FakeResponse()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    assert telegram.send_message("hola") is True
    assert intentos["n"] == 2


def test_send_message_no_reintenta_un_error_permanente(monkeypatch):
    """Un 400 (p.ej. HTML mal formado, chat_id inválido) no se arregla
    reintentando — mismo principio que edgar_http.PermanentHTTPError."""
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram.time, "sleep", lambda _: None)

    intentos = {"n": 0}

    def fake_urlopen(request, timeout=None):
        intentos["n"] += 1
        raise telegram.urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"description": "bad request"}')
        )

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    assert telegram.send_message("hola") is False
    assert intentos["n"] == 1


def test_send_message_reintenta_un_429(monkeypatch):
    """429 (rate limit) SÍ es transitorio — mismo criterio que edgar_http.py."""
    from pipeline.notify import telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram.time, "sleep", lambda _: None)

    intentos = {"n": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise telegram.urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b'{"description": "flood"}')
            )
        return FakeResponse()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    assert telegram.send_message("hola") is True
    assert intentos["n"] == 2


# ---------------------------------------------------------------------------
# signals_notifier._format_message — puro
# ---------------------------------------------------------------------------


def _signal_row(**overrides):
    row = {
        "event_id": 42,
        "ticker": "ACME",
        "event_class": "8K_2.02_EARNINGS",
        "source_url": "https://example.com/filing",
        "trade_decision_conservative": "NO_TRADE",
        "trade_decision_aggressive": "LONG",
        "trade_decision_balanced": "SHORT",
        "confidence_in_conviction": 72.4,
        "net_conviction": -0.31,
        "ev_conservative": 0.001,
        "ev_aggressive": 0.021,
        "ev_balanced": -0.004,
    }
    row.update(overrides)
    return row


def test_format_message_only_lists_versions_that_traded():
    from pipeline.notify.signals_notifier import _format_message

    text = _format_message(_signal_row())
    assert "ACME" in text
    assert "Resultados" in text  # clase de evento en lenguaje llano, no el código
    assert "Conservador" not in text  # NO_TRADE no se lista
    assert "Agresivo: LONG" in text
    assert "Equilibrado: SHORT" in text
    assert "72%" in text


def test_format_message_includes_winning_thesis_and_key_uncertainty():
    from pipeline.notify.signals_notifier import _format_message

    text = _format_message(
        _signal_row(
            net_conviction=0.4,
            bull_analyst_output={"thesis": "Guía elevada por encima del consenso."},
            bear_analyst_output={"counter_thesis": "No debería aparecer."},
            judge_output={"key_uncertainty": "Margen bruto del próximo trimestre."},
        )
    )
    assert "Guía elevada por encima del consenso." in text
    assert "No debería aparecer." not in text
    assert "Margen bruto del próximo trimestre." in text


def test_format_message_escapes_llm_text_for_telegram_html():
    """Un '<' o '&' sin escapar hace que Telegram rechace el mensaje entero
    (parse_mode=HTML) — y el evento se reintentaría para siempre."""
    from pipeline.notify.signals_notifier import _format_message

    text = _format_message(
        _signal_row(
            ticker="A&B",
            bear_analyst_output={"counter_thesis": "Margen <5% tras la M&A"},
            judge_output={"key_uncertainty": "x > y"},
        )
    )
    assert "<b>A&amp;B</b>" in text
    assert "Margen &lt;5% tras la M&amp;A" in text
    assert "x &gt; y" in text


def test_format_message_links_dashboard_only_when_configured(monkeypatch):
    from pipeline import config
    from pipeline.notify.signals_notifier import _format_message

    monkeypatch.setattr(config, "DASHBOARD_URL", None)
    assert "Análisis completo" not in _format_message(_signal_row())

    monkeypatch.setattr(config, "DASHBOARD_URL", "https://panel.example.com")
    text = _format_message(_signal_row())
    assert '<a href="https://panel.example.com/senales/42">Análisis completo</a>' in text


# ---------------------------------------------------------------------------
# alerts_notifier._notification_id — puro, determinista
# ---------------------------------------------------------------------------


def test_notification_id_is_stable_and_distinguishes_messages():
    from pipeline.notify.alerts_notifier import _notification_id

    alert_a = {"type": "WARNING", "message": "2 pérdidas consecutivas"}
    alert_b = {"type": "WARNING", "message": "3 pérdidas consecutivas"}

    id_a1 = _notification_id("2024-W10", "BALANCED", alert_a)
    id_a2 = _notification_id("2024-W10", "BALANCED", alert_a)
    id_b = _notification_id("2024-W10", "BALANCED", alert_b)

    assert id_a1 == id_a2  # determinista
    assert id_a1 != id_b  # distingue por contenido del mensaje


# ---------------------------------------------------------------------------
# Integración contra Postgres real
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE notifications_sent, paper_trading_reports, event_analyses, events, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _insert_universe_and_event(conn, ticker: str = "ACME", d0: date | None = None, event_class: str = "8K_2.02_EARNINGS") -> int:
    # D0 reciente por defecto: signals_notifier solo avisa de eventos de los
    # últimos config.ALERT_MAX_AGE_DAYS días.
    d0 = d0 or (date.today() - timedelta(days=1))
    # cik derivado del ticker (no hardcodeado): varios tests insertan más de
    # un ticker en la misma conexión y universe.cik es PRIMARY KEY.
    cik = f"CIK{abs(hash(ticker)) % 10_000_000:07d}"
    accession = f"000{abs(hash(ticker)) % 1_000_000:06d}-24-000123"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s, %s, 'ACME WIDGETS CORP', %s, %s)",
            (cik, ticker, date(2024, 1, 1), date(2024, 1, 1)),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, event_class, item_codes, accession_number,
                                 source_url, filed_at, d0_close_date, classification_method,
                                 classification_confidence, raw_text_hash)
            VALUES (%s, %s, 'EDGAR', %s, ARRAY['2.02'], %s,
                    'https://example.com/filing', %s, %s, 'RULE', 1.0, %s)
            RETURNING event_id
            """,
            (cik, ticker, event_class, accession, datetime(d0.year, d0.month, d0.day, 9, 0, tzinfo=timezone.utc), d0, f"hash-{ticker}"),
        )
        event_id = cur.fetchone()["event_id"]
    conn.commit()
    return event_id


def _insert_event_analysis(conn, event_id: int, *, trade_balanced: str = "LONG") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (
                %(event_id)s, 80, '{}', '{}', '{}', '{}', 0.5, 75, '{}', 10, '{}', 0.001, 0.02, 0.01,
                '{}', 'NO_TRADE', %(trade_balanced)s, %(trade_balanced)s, 'claude-haiku-4-5', 'claude-sonnet-4-6'
            )
            """,
            {"event_id": event_id, "trade_balanced": trade_balanced},
        )
    conn.commit()


@pytestmark_db
def test_fetch_pending_signals_only_returns_tradeable_unnotified(conn):
    from pipeline.notify.signals_notifier import fetch_pending_signals

    traded_id = _insert_universe_and_event(conn, ticker="TRADED")
    _insert_event_analysis(conn, traded_id, trade_balanced="LONG")

    no_trade_id = _insert_universe_and_event(conn, ticker="NOTRADE")
    _insert_event_analysis(conn, no_trade_id, trade_balanced="NO_TRADE")

    pending = fetch_pending_signals(conn)
    tickers = {row["ticker"] for row in pending}
    assert tickers == {"TRADED"}


@pytestmark_db
def test_mark_notified_excludes_from_future_pending(conn):
    from pipeline.notify.signals_notifier import fetch_pending_signals, mark_notified

    event_id = _insert_universe_and_event(conn)
    _insert_event_analysis(conn, event_id, trade_balanced="SHORT")

    assert len(fetch_pending_signals(conn)) == 1
    mark_notified(conn, [event_id])
    assert fetch_pending_signals(conn) == []


@pytestmark_db
def test_notify_pending_signals_noop_without_telegram_config(conn, monkeypatch):
    from pipeline.notify import signals_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", None)

    event_id = _insert_universe_and_event(conn)
    _insert_event_analysis(conn, event_id, trade_balanced="LONG")

    assert signals_notifier.notify_pending_signals(conn) == 0
    # Nada se marcó como notificado: sigue pendiente para cuando sí haya config.
    assert len(signals_notifier.fetch_pending_signals(conn)) == 1


@pytestmark_db
def test_notify_pending_signals_sends_and_marks(conn, monkeypatch):
    from pipeline.notify import signals_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram, "send_message", lambda text, parse_mode="HTML": True)

    event_id = _insert_universe_and_event(conn)
    _insert_event_analysis(conn, event_id, trade_balanced="LONG")

    sent = signals_notifier.notify_pending_signals(conn)
    assert sent == 1
    assert signals_notifier.fetch_pending_signals(conn) == []


@pytestmark_db
def test_try_claim_only_true_once(conn):
    from pipeline.notify.alerts_notifier import _try_claim

    assert _try_claim(conn, "same-id") is True
    assert _try_claim(conn, "same-id") is False  # ya reclamado, no se repite


@pytestmark_db
def test_notify_new_alerts_dedupes_across_reruns(conn, monkeypatch):
    from pipeline.notify import alerts_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    sent_messages = []
    monkeypatch.setattr(telegram, "send_message", lambda text, parse_mode="HTML": sent_messages.append(text) or True)

    report = {
        "versions": {
            "BALANCED": {
                "alerts": [{"type": "WARNING", "version": "BALANCED", "message": "2 pérdidas consecutivas en BALANCED"}]
            }
        }
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO paper_trading_reports (run_batch_tag, week_start, week_end, report_json) "
            "VALUES ('2024-W10', %s, %s, %s)",
            (date(2024, 3, 4), date(2024, 3, 8), json.dumps(report)),
        )
    conn.commit()

    first_run = alerts_notifier.notify_new_alerts(conn)
    second_run = alerts_notifier.notify_new_alerts(conn)  # misma noche siguiente, MISMO run_batch_tag/alert

    assert first_run == 1
    assert second_run == 0  # ya se había mandado, no se repite
    assert len(sent_messages) == 1


@pytestmark_db
def test_notify_new_alerts_loguea_las_perdidas(conn, monkeypatch, caplog):
    """IMPROVEMENT_PLAN.md M15: una alert reclamada (dedup) cuyo envío falla
    se pierde para siempre — antes, sin ninguna métrica que lo visibilice
    más allá de un warning suelto por alert. Ahora hay un resumen agregado."""
    import logging

    from pipeline.notify import alerts_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram, "send_message", lambda text, parse_mode="HTML": False)

    report = {
        "versions": {
            "BALANCED": {
                "alerts": [{"type": "WARNING", "version": "BALANCED", "message": "fallo de envío de prueba"}]
            }
        }
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO paper_trading_reports (run_batch_tag, week_start, week_end, report_json) "
            "VALUES ('2024-W11', %s, %s, %s)",
            (date(2024, 3, 11), date(2024, 3, 15), json.dumps(report)),
        )
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="pipeline.notify.alerts_notifier"):
        sent = alerts_notifier.notify_new_alerts(conn)

    assert sent == 0
    assert "1 alert(s)" in caplog.text
    assert "perdidas para siempre" in caplog.text


# ---------------------------------------------------------------------------
# exits_notifier — avisos de CUÁNDO SALIR para quien opera a mano (sin
# ejecución automática de órdenes en este proyecto)
# ---------------------------------------------------------------------------


def test_exits_format_message_incluye_motivo_entrada_y_salida():
    from pipeline.notify.exits_notifier import _format_message

    row = {
        "ticker": "ACME",
        "version": "BALANCED",
        "direction": "LONG",
        "entry_date": date(2024, 3, 15),
        "entry_price": 100.0,
        "exit_date": date(2024, 3, 18),
        "exit_price": 108.5,
        "exit_reason": "TAKE_PROFIT",
        "pnl_pct": 8.2,
        "source_url": "https://example.com/filing",
    }
    text = _format_message(row)
    assert "CERRAR ACME" in text
    assert row["version"] not in text  # nombre interno de la estrategia, no se muestra
    assert "estrategia" in text
    assert "Objetivo de beneficio alcanzado" in text
    assert "100.00" in text
    assert "108.50" in text
    assert "+8.20%" in text
    assert "https://example.com/filing" in text


def test_exits_format_message_sin_pnl_muestra_guion():
    from pipeline.notify.exits_notifier import _format_message

    row = {
        "ticker": "ACME", "version": "AGGRESSIVE", "direction": "SHORT",
        "entry_date": date(2024, 3, 15), "entry_price": 50.0,
        "exit_date": date(2024, 3, 20), "exit_price": 50.0,
        "exit_reason": "TIMEOUT", "pnl_pct": None, "source_url": None,
    }
    text = _format_message(row)
    assert "Límite de tiempo alcanzado" in text
    assert "—" in text


def _insert_paper_trade(
    conn,
    event_id: int,
    *,
    version: str = "BALANCED",
    status: str = "CLOSED_TP",
    exit_reason: str | None = "TAKE_PROFIT",
    notified: bool = False,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_trades (
                run_batch_tag, week_start, week_end, event_id, version, direction,
                entry_date, entry_price, exit_date, exit_price, exit_reason, status,
                pnl_pct, confidence, ev, prediction, notified_at
            ) VALUES (
                '2024-W11', %(ws)s, %(we)s, %(event_id)s, %(version)s, 'LONG',
                %(entry_date)s, 100.0, %(exit_date)s, 105.0, %(exit_reason)s, %(status)s,
                5.0, 70.0, 0.01, 0.3, %(notified_at)s
            )
            RETURNING trade_id
            """,
            {
                "ws": date(2024, 3, 11), "we": date(2024, 3, 15),
                "event_id": event_id, "version": version,
                "entry_date": date(2024, 3, 12),
                "exit_date": date(2024, 3, 14) if status != "OPEN" else None,
                "exit_reason": exit_reason,
                "status": status,
                "notified_at": datetime(2024, 3, 14, tzinfo=timezone.utc) if notified else None,
            },
        )
        return cur.fetchone()["trade_id"]


@pytestmark_db
def test_fetch_pending_exits_solo_devuelve_cerrados_sin_notificar(conn):
    from pipeline.notify.exits_notifier import fetch_pending_exits

    open_id = _insert_universe_and_event(conn, ticker="OPEN")
    _insert_paper_trade(conn, open_id, status="OPEN", exit_reason=None)

    closed_id = _insert_universe_and_event(conn, ticker="CLOSED")
    _insert_paper_trade(conn, closed_id, status="CLOSED_SL", exit_reason="STOP_LOSS")

    already_notified_id = _insert_universe_and_event(conn, ticker="YAAVISADO")
    _insert_paper_trade(conn, already_notified_id, status="CLOSED_TP", notified=True)

    pending = fetch_pending_exits(conn)
    tickers = {row["ticker"] for row in pending}
    assert tickers == {"CLOSED"}


@pytestmark_db
def test_exits_mark_notified_excluye_de_futuros_pendientes(conn):
    from pipeline.notify.exits_notifier import fetch_pending_exits, mark_notified

    event_id = _insert_universe_and_event(conn)
    trade_id = _insert_paper_trade(conn, event_id, status="CLOSED_TP")

    assert len(fetch_pending_exits(conn)) == 1
    mark_notified(conn, [trade_id])
    assert fetch_pending_exits(conn) == []


@pytestmark_db
def test_notify_pending_exits_noop_sin_config_telegram(conn, monkeypatch):
    from pipeline.notify import exits_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", None)

    event_id = _insert_universe_and_event(conn)
    _insert_paper_trade(conn, event_id, status="CLOSED_SL")

    assert exits_notifier.notify_pending_exits(conn) == 0
    assert len(exits_notifier.fetch_pending_exits(conn)) == 1


@pytestmark_db
def test_notify_pending_exits_envia_y_marca(conn, monkeypatch):
    from pipeline.notify import exits_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram, "send_message", lambda text, parse_mode="HTML": True)

    event_id = _insert_universe_and_event(conn)
    _insert_paper_trade(conn, event_id, status="CLOSED_TP")

    sent = exits_notifier.notify_pending_exits(conn)
    assert sent == 1
    assert exits_notifier.fetch_pending_exits(conn) == []


@pytestmark_db
def test_notify_pending_exits_no_marca_si_falla_el_envio(conn, monkeypatch):
    from pipeline.notify import exits_notifier, telegram

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(telegram, "send_message", lambda text, parse_mode="HTML": False)

    event_id = _insert_universe_and_event(conn)
    _insert_paper_trade(conn, event_id, status="CLOSED_TIMEOUT", exit_reason="TIMEOUT")

    sent = exits_notifier.notify_pending_exits(conn)
    assert sent == 0
    # Sigue pendiente para reintentar en la próxima pasada.
    assert len(exits_notifier.fetch_pending_exits(conn)) == 1


# ---------------------------------------------------------------------------
# weekly_digest — resumen semanal
# ---------------------------------------------------------------------------


def test_weekly_digest_categorizes_abstention_reasons_like_the_dashboard():
    from pipeline.notify.weekly_digest import _categorize

    assert _categorize("novelty_score=10 < 20 (evento completamente descontado por el mercado)") == "El mercado ya lo sabía"
    assert _categorize("|ev|=0.10% < umbral balanced") == "Valor esperado insuficiente tras costes"
    assert _categorize("ADV≈$10,000 < $1,000,000 (ilíquido, proxy de volumen)") == "Acción poco líquida"
    assert _categorize("sin datos de volumen suficientes para estimar ADV (proxy de liquidez no disponible)") == "Acción poco líquida"
    assert _categorize(None) == "Otros motivos"


def test_weekly_digest_format_escapes_and_links(monkeypatch):
    from pipeline import config
    from pipeline.notify.weekly_digest import format_digest

    monkeypatch.setattr(config, "DASHBOARD_URL", "https://panel.example.com")
    text = format_digest(
        {
            "version": "BALANCED",
            "days": 7,
            "analyzed": 12,
            "traded": 2,
            "discarded": [("El mercado ya lo sabía", 6), ("Valor esperado insuficiente tras costes", 4)],
            "closed": [("A&B", 3.2), ("ACME", -1.0)],
            "open": 1,
        }
    )
    assert "12 eventos analizados · 2 superaron los filtros" in text
    assert "papel" not in text
    assert "6 · El mercado ya lo sabía" in text
    assert "A&amp;B +3.20%" in text
    assert "2 · 1 con ganancia · media +1.10%" in text
    assert '<a href="https://panel.example.com/historial">' in text


def test_weekly_digest_warns_when_nothing_was_analyzed(monkeypatch):
    from pipeline import config
    from pipeline.notify.weekly_digest import format_digest

    monkeypatch.setattr(config, "DASHBOARD_URL", None)
    text = format_digest({"version": "AGGRESSIVE", "days": 7, "analyzed": 0, "traded": 0, "discarded": [], "closed": [], "open": 0})
    assert "no se ha publicado ningún análisis" in text
    assert "href" not in text


@pytestmark_db
def test_weekly_digest_sends_once_per_week_on_saturday_and_retries_failures(conn, monkeypatch):
    from pipeline.notify import telegram, weekly_digest

    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", "1")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE validation_reports")
    conn.commit()
    sent: list[str] = []
    outcome = {"ok": False}
    monkeypatch.setattr(telegram, "send_message", lambda text: sent.append(text) or outcome["ok"])

    friday = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
    assert weekly_digest.send_weekly_digest(conn, friday) is False
    assert sent == []

    # Falla el envío: no se marca, se reintenta en la siguiente pasada.
    assert weekly_digest.send_weekly_digest(conn, saturday) is False
    assert len(sent) == 1
    outcome["ok"] = True
    assert weekly_digest.send_weekly_digest(conn, saturday) is True
    assert weekly_digest.send_weekly_digest(conn, saturday) is False
    assert len(sent) == 2


@pytestmark_db
def test_weekly_digest_collects_recent_analyses(conn):
    from pipeline.notify.weekly_digest import collect_week

    traded = _insert_universe_and_event(conn, ticker="WTRADE")
    _insert_event_analysis(conn, traded, trade_balanced="LONG")
    skipped = _insert_universe_and_event(conn, ticker="WSKIP")
    _insert_event_analysis(conn, skipped, trade_balanced="NO_TRADE")
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE event_analyses SET abstention_decision = '{"BALANCED": {"reason_if_no_trade": "novelty_score=5 < 20"}}'
               WHERE event_id = %s""",
            (skipped,),
        )
    conn.commit()

    data = collect_week(conn, "BALANCED")
    assert data["analyzed"] == 2
    assert data["traded"] == 1
    assert data["discarded"] == [("El mercado ya lo sabía", 1)]


# ---------------------------------------------------------------------------
# signals_notifier — filtros de aviso (config.ALERT_*)
# ---------------------------------------------------------------------------


@pytestmark_db
def test_fetch_pending_signals_ignora_eventos_antiguos_de_un_backfill(conn, monkeypatch):
    """Sin tope de antigüedad, un backfill histórico mandaba un aviso de
    "entra" por cada evento de hace años que pasara los filtros."""
    from pipeline import config
    from pipeline.notify.signals_notifier import fetch_pending_signals

    monkeypatch.setattr(config, "ALERT_MAX_AGE_DAYS", 7)
    old_id = _insert_universe_and_event(conn, ticker="VIEJO", d0=date.today() - timedelta(days=400))
    _insert_event_analysis(conn, old_id, trade_balanced="LONG")
    new_id = _insert_universe_and_event(conn, ticker="NUEVO")
    _insert_event_analysis(conn, new_id, trade_balanced="LONG")

    assert {r["ticker"] for r in fetch_pending_signals(conn)} == {"NUEVO"}
    monkeypatch.setattr(config, "ALERT_MAX_AGE_DAYS", None)
    assert {r["ticker"] for r in fetch_pending_signals(conn)} == {"NUEVO", "VIEJO"}


@pytestmark_db
def test_fetch_pending_signals_aplica_watchlist(conn, monkeypatch):
    from pipeline import config
    from pipeline.notify.signals_notifier import fetch_pending_signals

    for ticker, cls in [("AAA", "8K_2.02_EARNINGS"), ("BBB", "FDA_CRL"), ("CCC", "8K_2.02_EARNINGS")]:
        eid = _insert_universe_and_event(conn, ticker=ticker, event_class=cls)
        _insert_event_analysis(conn, eid, trade_balanced="LONG")

    monkeypatch.setattr(config, "ALERT_TICKERS", ("AAA", "BBB"))
    assert {r["ticker"] for r in fetch_pending_signals(conn)} == {"AAA", "BBB"}
    monkeypatch.setattr(config, "ALERT_EVENT_CLASSES", ("FDA_CRL",))
    assert {r["ticker"] for r in fetch_pending_signals(conn)} == {"BBB"}
    monkeypatch.setattr(config, "ALERT_TICKERS", ())
    monkeypatch.setattr(config, "ALERT_EVENT_CLASSES", ())
    monkeypatch.setattr(config, "ALERT_MIN_CONFIDENCE", 80.0)  # _insert_event_analysis usa confianza 75
    assert fetch_pending_signals(conn) == []
