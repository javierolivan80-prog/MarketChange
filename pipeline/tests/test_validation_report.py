"""test_validation_report.py — Fase 6. generate_full_validation_report de
extremo a extremo contra Postgres real, escribiendo a un directorio
temporal (nunca a docs/ real, para no ensuciar el repo en cada test run)."""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE paper_trades, paper_trading_reports, portfolio_trades, portfolio_equity_curve, "
            "portfolio_reports, car_results, backtest_runs, event_analyses, event_enrichment, events, "
            "prices, fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _business_days(start, n):
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _seed_full_event(conn, cik, ticker, d0, cal, closes, decision, confidence, ev, event_class, vix_d0):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, cal[0], cal[-1]),
        )
        for d, c in zip(cal, closes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) ON CONFLICT (ticker, trade_date) DO UPDATE SET "
                "open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw",
                (ticker, d, c, c, c * 1.01, c * 0.99),
            )
        cur.execute(
            """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
            RETURNING event_id""",
            (cik, ticker, event_class, f"acc-{cik}", d0, d0, f"hash-{cik}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            """INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (%s, 80, '{}', '{}', '{}', '{}', 0.6, %s, '{}', 25, '{}', %s, %s, %s, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')""",
            (event_id, confidence, ev, ev, ev, decision, decision, decision),
        )
        cur.execute("INSERT INTO event_enrichment (event_id, vix_d0) VALUES (%s, %s)", (event_id, vix_d0))
        cur.execute(
            "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, 20, %s, 100)",
            (event_id, ev),
        )
    conn.commit()
    return event_id


def test_generate_full_validation_report_end_to_end(conn, tmp_path):
    from pipeline.validation.report import generate_full_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    classes = ["8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT", "8K_5.02_OFFICER_CHANGE"]
    for i in range(15):
        d0 = cal[i]
        ticker = f"V{i}"
        closes = [100.0 + i * 0.5 + j * 0.2 for j in range(len(cal))]
        _seed_full_event(
            conn, f"v{i}", ticker, d0, cal, closes,
            decision="LONG" if i % 4 != 0 else "NO_TRADE",
            confidence=60.0 + i, ev=0.01, event_class=classes[i % 3], vix_d0=15.0 + i,
        )

    result = generate_full_validation_report(conn, run_batch_tag="validation-test-1", docs_dir=str(tmp_path))

    assert result["run_batch_tag"] == "validation-test-1"
    assert set(result["decisions"].keys()) == {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    assert result["best_version"] in {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    assert result["best_decision"]["option"] in {"A", "B", "C"}

    report_path = tmp_path / "VALIDATION_REPORT.md"
    assert report_path.exists()
    content = report_path.read_text()
    assert "# VALIDATION_REPORT.md" in content
    assert "PARTE 1 — Event Study" in content
    assert "PARTE 2 — Backtesting" in content
    assert "PARTE 3 — Calibración" in content
    assert "PARTE 4 — Sesgo y limitaciones" in content
    assert "PARTE 5 — Sensibilidad" in content
    assert "PARTE 6 — Decisión de inversión" in content
    assert "PARTE 7 — Next steps" in content
    assert result["best_decision"]["label"] in content

    # IMPROVEMENT_PLAN.md Q3: DYNAMIC se corre y persiste como las otras 3,
    # pero queda fuera de las decisiones de PARTE 6 — la comparación con
    # BALANCED se hace visible en un apéndice aparte, no cambia el veredicto.
    assert "DYNAMIC" not in result["decisions"]
    assert "Apéndice — DYNAMIC vs BALANCED" in content
    apendice = content.split("Apéndice — DYNAMIC vs BALANCED")[1].split("PARTE 7")[0]
    assert "DYNAMIC" in apendice
    assert "BALANCED" in apendice


def test_backtest_table_rec_column_matches_parte_6_decision(conn, tmp_path):
    """Hallazgo de auditoría (IMPROVEMENT_PLAN.md R2): la columna "Rec" de la
    tabla de PARTE 2 usaba antes su propio criterio ad hoc e inline
    (win_rate>55% and sharpe>1.0, sin mirar drawdown/calibración/n_trades),
    pudiendo mostrar "YES" en PARTE 2 y "REDLIGHT" en PARTE 6 para la MISMA
    versión en el MISMO documento. Ahora reutiliza el mismo `decisions[version]`
    de PARTE 6 — este test falla si algún día alguien vuelve a bifurcar la
    lógica: reproduce exactamente la letra A/B/C, para cada versión con datos,
    a partir del propio Markdown generado."""
    from pipeline.validation.report import generate_full_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    classes = ["8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT", "8K_5.02_OFFICER_CHANGE"]
    for i in range(15):
        d0 = cal[i]
        ticker = f"R{i}"
        closes = [100.0 + i * 0.5 + j * 0.2 for j in range(len(cal))]
        _seed_full_event(
            conn, f"r{i}", ticker, d0, cal, closes,
            decision="LONG" if i % 4 != 0 else "NO_TRADE",
            confidence=60.0 + i, ev=0.01, event_class=classes[i % 3], vix_d0=15.0 + i,
        )

    result = generate_full_validation_report(conn, run_batch_tag="validation-rec-match", docs_dir=str(tmp_path))
    content = (tmp_path / "VALIDATION_REPORT.md").read_text()

    parte2 = content.split("## PARTE 2")[1].split("## PARTE 3")[0]
    checked_any = False
    for version, decision in result["decisions"].items():
        row = next((line for line in parte2.splitlines() if line.startswith(f"| {version} |")), None)
        assert row is not None, f"fila de {version} no encontrada en la tabla de PARTE 2"
        rec_cell = row.rstrip("|").rsplit("|", 1)[-1].strip()
        assert rec_cell == decision["option"], (
            f"{version}: PARTE 2 muestra Rec={rec_cell!r} pero PARTE 6 decidió "
            f"option={decision['option']!r} ({decision['label']}) — deben coincidir siempre"
        )
        checked_any = True
    assert checked_any, "ninguna versión tenía datos para comparar — fixture insuficiente"


def test_generate_full_validation_report_oos_uses_separate_filename_and_banner(conn, tmp_path):
    """Misma fixture que el test de arriba (eventos todos en 2024, es decir,
    todos OOS) pero pidiendo sample='oos' explícitamente: debe escribir
    VALIDATION_REPORT_OOS.md (nunca pisar VALIDATION_REPORT.md) y llevar la
    cabecera de aviso visible — punto 3 del plan."""
    from pipeline.validation.report import generate_full_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    classes = ["8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT", "8K_5.02_OFFICER_CHANGE"]
    for i in range(15):
        d0 = cal[i]
        ticker = f"O{i}"
        closes = [100.0 + i * 0.5 + j * 0.2 for j in range(len(cal))]
        _seed_full_event(
            conn, f"o{i}", ticker, d0, cal, closes,
            decision="LONG" if i % 4 != 0 else "NO_TRADE",
            confidence=60.0 + i, ev=0.01, event_class=classes[i % 3], vix_d0=15.0 + i,
        )

    result = generate_full_validation_report(conn, run_batch_tag="validation-oos-1", docs_dir=str(tmp_path), sample="oos")

    oos_path = tmp_path / "VALIDATION_REPORT_OOS.md"
    in_sample_path = tmp_path / "VALIDATION_REPORT.md"
    assert oos_path.exists()
    assert not in_sample_path.exists()  # nunca se escribe el default en una corrida OOS
    assert result["report_path"] == str(oos_path)

    content = oos_path.read_text()
    assert "OUT-OF-SAMPLE" in content
    assert "NO USAR PARA AJUSTAR PARÁMETROS" in content


def test_generate_full_validation_report_in_sample_excludes_2024_events(conn, tmp_path):
    """Misma fixture (todos los eventos en 2024) pero pidiendo sample='in_sample':
    ninguno debe sobrevivir el filtro — el Event Study de PARTE 1 debe salir
    vacío, no solo el backtest de PARTE 2 (ver event_study.py:run_event_study,
    que también acepta `sample` para no filtrar solo la mitad del reporte)."""
    from pipeline.validation.report import generate_full_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    for i in range(5):
        d0 = cal[i]
        ticker = f"I{i}"
        closes = [100.0 + i for _ in cal]
        _seed_full_event(
            conn, f"i{i}", ticker, d0, cal, closes,
            decision="LONG", confidence=70.0, ev=0.01, event_class="8K_2.02_EARNINGS", vix_d0=18.0,
        )

    result = generate_full_validation_report(conn, run_batch_tag="validation-insample-1", docs_dir=str(tmp_path), sample="in_sample")
    for version in ("CONSERVATIVE", "AGGRESSIVE", "BALANCED"):
        assert result["decisions"][version]["reasons"]  # todavía calcula una decisión (n=0)...
    content = (tmp_path / "VALIDATION_REPORT.md").read_text()
    # ...pero la tabla de Event Study (PARTE 1) no debe listar ninguna clase:
    # todos los eventos sembrados son de 2024 (OOS), sample='in_sample' los
    # excluye antes de llegar a car_results.
    assert "8K_2.02_EARNINGS" not in content.split("PARTE 2")[0]

    if result["n_trades_exported"] > 0:
        csv_path = tmp_path / f"trades_validation-test-1.csv"
        assert csv_path.exists()
        csv_content = csv_path.read_text()
        assert "ticker" in csv_content
        assert "pnl_pct" in csv_content


def test_persist_validation_report_reuses_existing_portfolio_report(conn):
    """persist_validation_report (el paso del cron nocturno) no debe
    resimular la cartera si backtest/portfolio_report.py ya dejó un
    portfolio_report para ese run_batch_tag en la misma corrida — solo debe
    LEERLO. Se verifica sembrando un portfolio_reports con un
    bias_report reconocible y comprobando que ese mismo valor aparece en el
    validation_reports resultante, sin volver a calcular nada."""
    from pipeline.backtest.portfolio_report import run_full_backtest
    from pipeline.validation.report import persist_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    classes = ["8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT"]
    for i in range(10):
        d0 = cal[i]
        ticker = f"P{i}"
        closes = [100.0 + i * 0.5 + j * 0.2 for j in range(len(cal))]
        _seed_full_event(
            conn, f"p{i}", ticker, d0, cal, closes,
            decision="LONG" if i % 3 != 0 else "NO_TRADE",
            confidence=55.0 + i, ev=0.01, event_class=classes[i % 2], vix_d0=15.0 + i,
        )

    tag = "validation-persist-test-1"
    portfolio_report = run_full_backtest(conn, run_batch_tag=tag)

    payload = persist_validation_report(conn, tag)

    assert payload["run_batch_tag"] == tag
    assert payload["bias_report"] == portfolio_report["bias_report"]
    assert set(payload["decisions"].keys()) == {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    assert payload["best_version"] in {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}

    with conn.cursor() as cur:
        cur.execute("SELECT report_json FROM validation_reports WHERE run_batch_tag = %s", (tag,))
        row = cur.fetchone()
    assert row is not None
    assert row["report_json"]["best_version"] == payload["best_version"]
    assert "event_study" in row["report_json"]
    assert "sensitivity" in row["report_json"]


def test_json_safe_replaces_non_finite_floats():
    from pipeline.validation.report import _json_safe

    payload = {
        "a": float("inf"),
        "b": float("-inf"),
        "c": float("nan"),
        "d": 1.5,
        "nested": {"x": float("inf"), "y": [1.0, float("nan"), 3]},
        "s": "unchanged",
    }
    safe = _json_safe(payload)
    assert safe["a"] is None
    assert safe["b"] is None
    assert safe["c"] is None
    assert safe["d"] == 1.5
    assert safe["nested"]["x"] is None
    assert safe["nested"]["y"] == [1.0, None, 3]
    assert safe["s"] == "unchanged"

    import json

    json.dumps(safe)  # no debe lanzar — es justo lo que rompía el INSERT real


def test_persist_validation_report_upsert_overwrites(conn):
    """ON CONFLICT DO UPDATE: reejecutar el paso nocturno con el mismo tag
    (ej. un re-disparo manual del workflow) actualiza la fila en vez de
    fallar por duplicate key — mismo patrón que portfolio_reports."""
    from pipeline.validation.report import persist_validation_report

    cal = _business_days(date(2024, 1, 2), 40)
    _seed_full_event(
        conn, "u1", "U1", cal[0], cal, [100.0 + j * 0.1 for j in range(len(cal))],
        decision="LONG", confidence=70.0, ev=0.01, event_class="8K_2.02_EARNINGS", vix_d0=18.0,
    )

    tag = "validation-persist-test-2"
    first = persist_validation_report(conn, tag)
    second = persist_validation_report(conn, tag)

    assert first["run_batch_tag"] == second["run_batch_tag"] == tag
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM validation_reports WHERE run_batch_tag = %s", (tag,))
        assert cur.fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# _safe_get / lecturas defensivas de JSONB — IMPROVEMENT_PLAN.md M18: un
# portfolio_report con un campo faltante (drift de esquema entre cuándo se
# guardó y cuándo se lee) no debe tumbar la generación completa del reporte
# con un KeyError. Puras, sin BD.
# ---------------------------------------------------------------------------


def _portfolio_report_incompleto() -> dict:
    """Un portfolio_report con CONSERVATIVE completo y AGGRESSIVE con varios
    campos faltantes (simula una versión persistida antes de añadir esos
    campos, o un rename de clave) — el caso real que motivó M18."""
    return {
        "versions": {
            "CONSERVATIVE": {
                "trade_metrics": {"win_rate": 0.6, "total_trades": 20},
                "equity_metrics": {"sharpe_ratio": 1.5, "total_return": 0.1, "max_drawdown": -0.05},
                "calibration": {"calibration_score": 0.7},
                "no_lookahead_violations": [],
                "top_10_winners": [{"ticker": "A", "pnl_pct": 5.0, "exit_reason": "TP"}],
                "top_10_losers": [{"ticker": "B", "pnl_pct": -3.0, "exit_reason": "SL"}],
            },
            "AGGRESSIVE": {
                # Sin "equity_metrics", sin "calibration", sin "top_10_losers" —
                # el drift de esquema simulado.
                "trade_metrics": {"win_rate": 0.4, "total_trades": 5},
                "no_lookahead_violations": [],
                "top_10_winners": [{"ticker": "C", "pnl_pct": 2.0, "exit_reason": "TIMEOUT"}],
            },
        }
    }


def test_evaluate_all_versions_decision_no_revienta_con_campos_faltantes():
    from pipeline.validation.report import evaluate_all_versions_decision

    decisions = evaluate_all_versions_decision(_portfolio_report_incompleto())

    assert set(decisions.keys()) == {"CONSERVATIVE", "AGGRESSIVE"}
    # AGGRESSIVE, con datos faltantes, nunca puede salir GREENLIGHT (None no
    # cuenta como "sí pasa el umbral" — ver generate_decision).
    assert decisions["AGGRESSIVE"]["option"] != "A"


def test_backtest_table_no_revienta_con_campos_faltantes():
    from pipeline.validation.report import _backtest_table

    tabla = _backtest_table(_portfolio_report_incompleto())

    assert "CONSERVATIVE" in tabla
    assert "AGGRESSIVE" in tabla
    assert "—" in tabla  # el hueco de AGGRESSIVE se muestra, no rompe la tabla


def test_safe_get_loguea_cuando_falta_la_clave(caplog):
    import logging

    from pipeline.validation.report import _safe_get

    with caplog.at_level(logging.WARNING, logger="pipeline.validation.report"):
        valor = _safe_get({}, "no_existe", "por_defecto", "contexto_de_prueba")

    assert valor == "por_defecto"
    assert "no_existe" in caplog.text
    assert "contexto_de_prueba" in caplog.text


def test_safe_get_no_loguea_cuando_la_clave_esta(caplog):
    import logging

    from pipeline.validation.report import _safe_get

    with caplog.at_level(logging.WARNING, logger="pipeline.validation.report"):
        valor = _safe_get({"x": 1}, "x", 0, "ctx")

    assert valor == 1
    assert caplog.text == ""


def test_backtest_table_acepta_un_subconjunto_de_versiones():
    """IMPROVEMENT_PLAN.md Q3: _backtest_table ahora acepta qué versiones
    mostrar, para poder reutilizarla en el apéndice DYNAMIC vs BALANCED sin
    duplicar la función — por defecto sigue mostrando las 3 de VERSION_ORDER."""
    from pipeline.validation.report import _backtest_table

    fake_metrics = {
        "equity_metrics": {"total_return": 0.05, "sharpe_ratio": 1.2, "max_drawdown": -0.03},
        "trade_metrics": {"win_rate": 0.6, "total_trades": 10},
    }
    portfolio_report = {"versions": {"BALANCED": fake_metrics, "DYNAMIC": fake_metrics, "CONSERVATIVE": fake_metrics}}

    solo_dos = _backtest_table(portfolio_report, versions=("BALANCED", "DYNAMIC"))
    assert "BALANCED" in solo_dos
    assert "DYNAMIC" in solo_dos
    assert "CONSERVATIVE" not in solo_dos

    default = _backtest_table(portfolio_report)
    assert "DYNAMIC" not in default  # sigue sin aparecer si no se pide explícitamente


# ---------------------------------------------------------------------------
# _sensitivity_table — pura, IMPROVEMENT_PLAN.md R13/R14/R15
# ---------------------------------------------------------------------------


def _escenario(total_return: float | None, n_trades: int) -> dict:
    return {"total_return": total_return, "n_trades": n_trades, "win_rate": None}


def _sensitivity_fixture(**overrides) -> dict:
    base_scenarios = {
        version: {
            "baseline": _escenario(0.05, 20),
            "commission_plus_0.1pct": _escenario(0.04, 20),
            "spread_plus_0.2pct": _escenario(0.03, 20),
            "latency_d_plus_2": _escenario(0.02, 15),
            "confidence_minus_20pct": _escenario(0.045, 18),
            "high_vix_regime": _escenario(0.06, 10),
            "low_vix_regime": _escenario(0.04, 10),
            "n_missing_vix": 2,
        }
        for version in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")
    }
    base_scenarios.update(overrides)
    return {"run_batch_tag": "t", "scenarios": base_scenarios}


def test_sensitivity_table_incluye_columna_balanced():
    """IMPROVEMENT_PLAN.md R13: BALANCED puede ser best_version en PARTE 6,
    así que su fila de sensibilidad tiene que verse en la tabla, no solo
    Conservative/Aggressive."""
    from pipeline.validation.report import _sensitivity_table

    tabla = _sensitivity_table(_sensitivity_fixture())

    assert "Balanced Return" in tabla
    assert tabla.count("Baseline") == 1  # una fila, con las 3 versiones en columnas


def test_sensitivity_table_muestra_n_trades_por_escenario():
    """IMPROVEMENT_PLAN.md R14: sin el tamaño de muestra por escenario, un
    return que se desploma en 'latency_d_plus_2' es indistinguible de 'la
    muestra se redujo a la mitad'."""
    from pipeline.validation.report import _sensitivity_table

    tabla = _sensitivity_table(_sensitivity_fixture())

    assert "(n=20)" in tabla  # baseline
    assert "(n=15)" in tabla  # latency_d_plus_2, con menos trades


def test_sensitivity_table_muestra_n_missing_vix():
    """IMPROVEMENT_PLAN.md R15: split_by_vix_regime ya calculaba
    n_missing_vix; antes de esta sesión se descartaba en vez de mostrarse,
    así que 'sin datos VIX' y 'sin efecto de VIX' eran indistinguibles en
    el reporte final."""
    from pipeline.validation.report import _sensitivity_table

    tabla = _sensitivity_table(_sensitivity_fixture())

    assert "Trades sin vix_d0 disponible" in tabla
    assert "Conservative=2" in tabla
