"""test_notify.py — pipeline/notify/*.

telegram.py se prueba puro (urlopen mockeado, sin red real ni credenciales).
signals_notifier.py y alerts_notifier.py se prueban de dos formas: el
formateo de mensajes es puro, y la query/dedup/marcado en base de datos es
integración contra Postgres real (mismo patrón que test_db_integration.py —
se salta si no hay DATABASE_URL)."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

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


# ---------------------------------------------------------------------------
# signals_notifier._format_message — puro
# ---------------------------------------------------------------------------


def test_format_message_only_lists_versions_that_traded():
    from pipeline.notify.signals_notifier import _format_message

    row = {
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
    text = _format_message(row)
    assert "ACME" in text
    assert "CONSERVATIVE" not in text  # NO_TRADE no se lista
    assert "AGGRESSIVE: LONG" in text
    assert "BALANCED: SHORT" in text
    assert "72%" in text


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


def _insert_universe_and_event(conn, ticker: str = "ACME") -> int:
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
            VALUES (%s, %s, 'EDGAR', '8K_2.02_EARNINGS', ARRAY['2.02'], %s,
                    'https://example.com/filing', %s, %s, 'RULE', 1.0, %s)
            RETURNING event_id
            """,
            (cik, ticker, accession, datetime(2024, 3, 15, 9, 0, tzinfo=timezone.utc), date(2024, 3, 15), f"hash-{ticker}"),
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
