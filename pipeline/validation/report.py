"""report.py — Fase 6: ensambla PARTES 1-7 del spec en un único
/docs/VALIDATION_REPORT.md, más un CSV de todos los trades (spec:
"CSV exportable: todos los trades para análisis external").

No recalcula ninguna fórmula — lee lo que ya calculan portfolio_report.py,
event_study.py, sensitivity.py, y decision.py, y los ensambla en Markdown.
Mismo principio de todo el proyecto: una sola fuente de verdad por número.
"""
from __future__ import annotations

import csv
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path

from pipeline.backtest.portfolio_report import run_full_backtest
from pipeline.backtest.sample_split import (
    OOS_WARNING,
    SAMPLE_IN_SAMPLE,
    SAMPLE_OOS,
    git_sha_corto,
    registrar_oos,
    tag_suffix,
)
from pipeline.backtest.sensitivity import run_sensitivity_analysis
from pipeline.validation.decision import generate_decision
from pipeline.validation.event_study import run_event_study

logger = logging.getLogger(__name__)


def _json_safe(obj):
    """Reemplaza inf/-inf/NaN por None recursivamente. JSON no tiene token
    para "infinito" — Postgres rechaza el INSERT completo si CUALQUIER
    número del payload es no-finito (se encontró así, con
    event_study.py:compute_event_study_for_class produciendo t_statistic=
    Infinity en una clase de varianza cero — ya corregido en la fuente).
    Se mantiene esta red aquí, en el único punto de persistencia de
    validation_reports, como defensa en profundidad: si mañana sensitivity.py
    u otra métrica divide por cero en un caso no previsto, el paso nocturno
    no debe reventar por un JSON inválido — debe guardar None en vez de
    fabricar un número, mismo principio que el resto del proyecto."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj

VERSION_ORDER = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")
_OPTION_RANK = {"A": 0, "B": 1, "C": 2}  # A es mejor -> rank más bajo


def _safe_get(d: dict, key: str, default, context: str):
    """d.get(key, default), pero logueando un warning si la clave falta.

    Hallazgo de auditoría (IMPROVEMENT_PLAN.md M18): varias funciones de
    este módulo indexaban directamente (`v["clave"]`) sobre un
    portfolio_report que en el camino `--persist-only` (persist_validation_report,
    reutilizado del backtest de la MISMA corrida) puede venir de vuelta de
    Postgres, no de memoria — un JSONB leído tal cual. Cualquier drift de
    esquema entre cuándo se guardó y cuándo se lee (un campo renombrado, una
    versión persistida antes de añadir una métrica nueva) tumbaba la
    generación COMPLETA del reporte con un KeyError, incluidas las partes
    que no dependían del campo que faltaba.

    Degradar en silencio a `default` sin decir nada sería cambiar un fallo
    ruidoso por uno mudo — peor, no mejor: nadie se enteraría de que el dato
    que faltaba faltaba. De ahí el warning: el reporte se sigue generando
    (con `default` donde toque, casi siempre None, que _fmt_pct/_fmt_num/
    generate_decision ya tratan como "no lo sabemos", nunca como "sí" o
    cero), pero queda constancia en el log de qué faltó y dónde."""
    if key not in d:
        logger.warning("Falta '%s' en %s — se usa %r (posible drift de esquema)", key, context, default)
    return d.get(key, default)


def evaluate_all_versions_decision(portfolio_report: dict) -> dict[str, dict]:
    """generate_decision() por versión — usa la calibración de la Fase 3
    (1-|predicho-real|/|predicho|, la misma que se muestra en el resto del
    dashboard/PDF) y el `stable` de compute_temporal_stability_report como
    proxy de "walk-forward pasó" (partición temporal en dos mitades — no es
    un walk-forward con reentrenamiento real porque este proyecto no
    reentrena nada, ver RUNBOOK.md)."""
    decisions = {}
    for version in VERSION_ORDER:
        v = portfolio_report["versions"].get(version)
        if not v:
            continue
        ctx = f"portfolio_report.versions.{version}"
        trade_metrics = _safe_get(v, "trade_metrics", {}, ctx)
        equity_metrics = _safe_get(v, "equity_metrics", {}, ctx)
        calibration = _safe_get(v, "calibration", {}, ctx)
        stability = v.get("temporal_stability")
        decisions[version] = generate_decision(
            win_rate=_safe_get(trade_metrics, "win_rate", None, f"{ctx}.trade_metrics"),
            sharpe=_safe_get(equity_metrics, "sharpe_ratio", None, f"{ctx}.equity_metrics"),
            calibration_score=_safe_get(calibration, "calibration_score", None, f"{ctx}.calibration"),
            max_drawdown=_safe_get(equity_metrics, "max_drawdown", None, f"{ctx}.equity_metrics"),
            n_trades=_safe_get(trade_metrics, "total_trades", 0, f"{ctx}.trade_metrics"),
            walk_forward_passed=stability["stable"] if stability else None,
            no_lookahead_violations=_safe_get(v, "no_lookahead_violations", [], ctx),
        )
    return decisions


def overall_verdict(decisions: dict[str, dict]) -> tuple[str, dict]:
    """La mejor opción entre las 3 versiones (A > B > C); empate se
    resuelve a favor de CONSERVATIVE > BALANCED > AGGRESSIVE — mismo sesgo
    de "proteger capital cuando hay empate" que ya usa
    classify_balanced_execution_style en portfolio_strategies.py."""
    best_version = min(decisions, key=lambda v: (_OPTION_RANK[decisions[v]["option"]], VERSION_ORDER.index(v)))
    return best_version, decisions[best_version]


def export_trades_csv(conn, run_batch_tag: str, out_path: str) -> int:
    """Todos los trades de las 3 versiones para esta corrida, en un solo
    CSV — spec: "CSV exportable: todos los trades para análisis external"."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.*, e.ticker, e.event_class
            FROM portfolio_trades pt
            JOIN events e ON e.event_id = pt.event_id
            WHERE pt.run_batch_tag = %s
            ORDER BY pt.version, pt.entry_date
            """,
            (run_batch_tag,),
        )
        rows = cur.fetchall()

    if not rows:
        return 0

    fieldnames = [k for k in rows[0].keys() if k != "trade_id"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in fieldnames})
    return len(rows)


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _fmt_num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _event_study_table(event_study: dict[str, dict]) -> str:
    # p-value BH incluida junto al crudo (hallazgo de auditoría — corrección
    # por contrastes múltiples, ver pipeline/validation/event_study.py):
    # varias event_class se testean a la vez, así que un p-value crudo
    # "significativo" puede no serlo tras corregir por el número de clases.
    lines = [
        "| Event Type | n | Median Return | Media winsorizada | σ | MDE | p-value (agrupado por mes) | p-value (BH) | p simple | p BMP, submuestra (n) | Conclusion |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for event_class, stats in sorted(event_study.items()):
        median = f"{stats['median_return_pct']:+.2f}%" if stats["median_return_pct"] is not None else "—"
        sigma = f"{stats['sigma_pct']:.1f}%" if stats["sigma_pct"] is not None else "—"
        mde = f"{stats['mde_pct']:.0f} bps" if stats["mde_pct"] is not None else "—"
        p = f"{stats['p_value']:.4f}" if stats["p_value"] is not None else "—"
        p_bh = f"{stats['p_value_bh_adjusted']:.4f}" if stats.get("p_value_bh_adjusted") is not None else "—"
        significant_bh = stats.get("significant_bh")
        mark = "✓" if significant_bh else ("✗" if significant_bh is False else "?")
        # Al lado del contraste principal (agrupado por fecha, BUGS_REPORT.md
        # H-19), el t-test simple de antes y el BMP, para ver cuánto cambia.
        p_simple = f"{stats['p_value_simple']:.4f}" if stats.get("p_value_simple") is not None else "—"
        mean_w = f"{stats['mean_winsorized_pct']:+.2f}%" if stats.get("mean_winsorized_pct") is not None else "—"
        p_bmp = f"{stats['p_value_bmp']:.4f} ({stats.get('n_bmp', 0)})" if stats.get("p_value_bmp") is not None else f"— ({stats.get('n_bmp', 0)})"
        lines.append(
            f"| {event_class} | {stats['n']} | {median} | {mean_w} | {sigma} | {mde} | {p} | {p_bh} | {p_simple} | {p_bmp} | {mark} {stats['conclusion']} |"
        )
    return "\n".join(lines)


def _backtest_table(portfolio_report: dict, decisions: dict[str, dict] | None = None, versions: tuple[str, ...] = VERSION_ORDER) -> str:
    """La columna "Rec" reutiliza el MISMO veredicto de PARTE 6
    (`decisions`, ya calculado por `evaluate_all_versions_decision` con los
    umbrales GREENLIGHT/YELLOWLIGHT/REDLIGHT de `decision.py`) — hallazgo de
    auditoría (IMPROVEMENT_PLAN.md R2): antes calculaba su propio criterio
    ad hoc, inline y más laxo (`win_rate>0.55 and sharpe>1.0`, sin mirar
    drawdown, calibración, n_trades ni violaciones anti-look-ahead), lo que
    podía mostrar "YES" aquí y "REDLIGHT" en PARTE 6 para la MISMA versión en
    el MISMO documento, sin explicación. Una sola fuente de verdad por
    veredicto, no dos criterios distintos en el mismo reporte.

    decisions=None (usado por el apéndice DYNAMIC vs BALANCED,
    IMPROVEMENT_PLAN.md Q3): DYNAMIC no pasa por evaluate_all_versions_decision
    (esa etapa solo cubre CONSERVATIVE/BALANCED/AGGRESSIVE), así que no hay
    veredicto real que reutilizar ahí — "Rec" muestra "—" en vez de fingir uno."""
    lines = ["| Versión | Total Return | Sharpe | Max DD | Win Rate | N | Rec |", "|---|---|---|---|---|---|---|"]
    for version in versions:
        v = portfolio_report["versions"].get(version)
        if not v:
            continue
        ctx = f"portfolio_report.versions.{version}"
        em = _safe_get(v, "equity_metrics", {}, ctx)
        tm = _safe_get(v, "trade_metrics", {}, ctx)
        decision = decisions.get(version) if decisions else None
        rec = decision["option"] if decision else "—"
        lines.append(
            f"| {version} | {_fmt_pct(em.get('total_return'))} | {_fmt_num(em.get('sharpe_ratio'))} | "
            f"{_fmt_pct(em.get('max_drawdown'))} | {_fmt_pct(tm.get('win_rate'))} | {tm.get('total_trades', 0)} | {rec} |"
        )
    return "\n".join(lines)


def _sensitivity_table(sensitivity: dict) -> str:
    # R13 (IMPROVEMENT_PLAN.md): BALANCED entra en la tabla — puede ser
    # best_version en PARTE 6, así que su sensibilidad tiene que verse igual
    # que las otras dos, no solo la de Conservative/Aggressive.
    lines = [
        "| Scenario | Conservative Return (n) | Balanced Return (n) | Aggressive Return (n) | Impact |",
        "|---|---|---|---|---|",
    ]
    scenarios = sensitivity["scenarios"]
    scenario_keys = ["baseline", "commission_plus_0.1pct", "spread_plus_0.2pct", "latency_d_plus_2", "confidence_minus_20pct", "high_vix_regime", "low_vix_regime"]
    scenario_labels = {
        "baseline": "Baseline",
        "commission_plus_0.1pct": "Comisiones +0.1%/trade",
        "spread_plus_0.2pct": "Spread +0.2%",
        "latency_d_plus_2": "Entrada D+2 en vez de D+1 (latencia)",
        "confidence_minus_20pct": "Modelo reduce confidence 20%",
        "high_vix_regime": "Régimen alto-VIX (proxy de 'VIX sube', ver sensitivity.py)",
        "low_vix_regime": "Régimen bajo-VIX",
    }
    cons = scenarios.get("CONSERVATIVE", {})
    bal = scenarios.get("BALANCED", {})
    aggr = scenarios.get("AGGRESSIVE", {})
    baseline_cons = cons.get("baseline", {}).get("total_return")

    def _cell(scenario: dict) -> str:
        # (n) por escenario (IMPROVEMENT_PLAN.md R14): sin esto, un return
        # que se desploma en un escenario podía ser "el escenario importa" o
        # simplemente "la muestra se redujo a la mitad" — indistinguibles.
        return f"{_fmt_pct(scenario.get('total_return'))} (n={scenario.get('n_trades', 0)})"

    for key in scenario_keys:
        c, b, a = cons.get(key, {}), bal.get(key, {}), aggr.get(key, {})
        c_ret = c.get("total_return")
        impact = "—"
        if key != "baseline" and c_ret is not None and baseline_cons is not None:
            impact = f"{(c_ret - baseline_cons) * 100:+.2f}pp vs baseline (Conservative)"
        lines.append(f"| {scenario_labels[key]} | {_cell(c)} | {_cell(b)} | {_cell(a)} | {impact} |")

    # n_missing_vix (IMPROVEMENT_PLAN.md R15): metadato POR VERSIÓN, no por
    # escenario (split_by_vix_regime se corre una vez por versión) — una
    # línea aparte en vez de una columna más, para no repetir el mismo
    # número en las 2 filas de régimen VIX de cada versión.
    n_missing = {v: scenarios.get(v, {}).get("n_missing_vix") for v in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")}
    if any(n is not None for n in n_missing.values()):
        lines.append("")
        lines.append(
            "*Trades sin vix_d0 disponible (excluidos del split alto/bajo-VIX): "
            + ", ".join(f"{v.capitalize()}={n if n is not None else '—'}" for v, n in n_missing.items())
            + "*"
        )
    return "\n".join(lines)


def render_validation_report_markdown(
    event_study: dict, portfolio_report: dict, sensitivity: dict, decisions: dict, best_version: str, best_decision: dict
) -> str:
    bias = portfolio_report["bias_report"]
    generated_at = datetime.now().isoformat(timespec="seconds")
    # sample viene del propio portfolio_report (run_full_backtest ya lo
    # persiste ahí) — una sola fuente de verdad, en vez de un parámetro
    # nuevo que pudiera desincronizarse de con qué datos se calculó todo lo
    # demás de este documento.
    oos_banner = (
        f"\n> ## ⚠️ {OOS_WARNING}\n>\n"
        "> Este documento se generó sobre la partición OUT-OF-SAMPLE "
        "(`config.OOS_START` en adelante). Por diseño (`ARCHITECTURE_LEAN.md` "
        "T6), esta evaluación se mira **una sola vez**: si después de leer "
        "este resultado se ajusta cualquier umbral, banda de sizing o regla "
        "de abstención y se vuelve a correr, el holdout queda invalidado — "
        "ese ajuste ya no sería \"a ciegas\" respecto a estos datos.\n"
        if portfolio_report.get("sample") == SAMPLE_OOS
        else ""
    )

    top_trades = []
    for version in VERSION_ORDER:
        v = portfolio_report["versions"].get(version)
        if not v:
            continue
        ctx = f"portfolio_report.versions.{version}"
        winners = _safe_get(v, "top_10_winners", [], ctx)
        losers = _safe_get(v, "top_10_losers", [], ctx)
        for t in winners[:10] + losers[:10]:
            top_trades.append({**t, "version": version})
    top_trades_sorted = sorted(top_trades, key=lambda t: t.get("pnl_pct", 0.0), reverse=True)
    top_20 = top_trades_sorted[:10] + top_trades_sorted[-10:]

    appendix_rows = "\n".join(
        f"| {t['version']} | {t.get('ticker', '—')} | {(t.get('event_class') or '').replace('8K_', '')} | "
        f"{t.get('exit_reason', '—')} | {t.get('pnl_pct', 0.0):+.2f}% |"
        for t in top_20
    )

    lookahead_summary = "\n".join(
        f"   - **{v}**: {'✓ sin violaciones' if not decisions[v]['reasons'] or 'violación' not in decisions[v]['reasons'][0] else '✗ ' + decisions[v]['reasons'][0]}"
        for v in decisions
    )

    return f"""# VALIDATION_REPORT.md
{oos_banner}
_Generado automáticamente por `pipeline/validation/report.py` el {generated_at}._

**Advertencia de honestidad, léela antes que el resto del documento**: los
números de este reporte salen de datos SINTÉTICOS generados en este sandbox
de desarrollo (ver `RUNBOOK.md` — ningún scraper de EDGAR/FDA/yfinance real
se ha ejecutado nunca desde aquí, por bloqueo de red de la organización).
El código que produjo cada número está probado con 269 tests contra
Postgres real, así que el MECANISMO es de fiar; el VEREDICTO concreto de
este documento (GREENLIGHT/YELLOWLIGHT/REDLIGHT) NO debe tomarse como una
recomendación real de inversión hasta que se regenere este mismo reporte
con datos reales (`RUNBOOK.md` §1-§3.11 explica cómo).

## Executive Summary

Este documento responde 4 preguntas, en este orden, porque cada una
presupone que la anterior ya se contestó que sí (`AUDIT_LEAN.md` §2.2):
1. ¿El evento en sí mueve el precio de forma no aleatoria? (Event Study)
2. Si es así, ¿una estrategia de trading sobre él hubiera ganado dinero?
   (Backtest)
3. ¿El modelo sabe cuándo confiar en sí mismo? (Calibración)
4. ¿El resultado es frágil a supuestos razonables? (Sensibilidad)

**Veredicto**: **{best_decision['label']}** (versión recomendada: {best_version}).
{best_decision['recommendation']}

## PARTE 1 — Event Study (validez del concepto)

Sobre `car_results` (ventana de 20 días), no sobre los trades del backtest —
es la pregunta "¿existe un edge?", con n en la escala de todos los eventos
analizados, no solo los operados (`AUDIT_LEAN.md` §2.2.3).

{_event_study_table(event_study)}

MDE = 2.8·σ/√n (80% potencia, α=0.05 bilateral) — misma fórmula que
`AUDIT_LEAN.md` §2.2.3. Una clase con p-value >= 0.05 no está descartada:
puede tener un efecto real más pequeño que el MDE actual, no cero.

## PARTE 2 — Backtesting (viabilidad operativa)

{_backtest_table(portfolio_report, decisions)}

Rec = la misma decisión de PARTE 6 (A=GREENLIGHT, B=YELLOWLIGHT, C=REDLIGHT) — un único criterio, no un umbral aparte para esta tabla.

Reporte de sesgos (sobre todo el universo, no por versión): {bias.get('n_delisted', '—')}/{bias.get('n_total_tickers', '—')} tickers deslistados ({_fmt_num(bias.get('survivorship_bias_pct'), 1)}% posible sesgo de supervivencia) · {bias.get('n_price_gaps', '—')}/{bias.get('n_price_rows', '—')} filas de precio con gap ({_fmt_num(bias.get('data_gap_pct'), 1)}%).

## PARTE 3 — Calibración (confiabilidad del modelo)

Ver el reporte completo en `/calibration` del dashboard (curva de
calibración, Brier score, ECE por versión) — aquí, el resumen que pide el
spec:

| Versión | Calibration score (Fase 3) | Meets target (>0.6) |
|---|---|---|
{chr(10).join(f"| {v} | {_fmt_num(portfolio_report['versions'][v]['calibration']['calibration_score'])} | {'✓' if portfolio_report['versions'][v]['calibration']['meets_target'] else '✗'} |" for v in VERSION_ORDER if v in portfolio_report['versions'])}

## PARTE 4 — Sesgo y limitaciones

1. **Survivorship bias**: ver PARTE 2 arriba — deslistados SÍ se capturan
   (`universe.is_delisted_flag`), no se excluyen del universo simulado.
2. **Data quality**: gaps de precio flageados, no interpolados (ver
   `yfinance_backfill.py` — es información, no un bug).
3. **Look-ahead checks**:
{lookahead_summary}
4. **Walk-forward / estabilidad temporal**: partición en dos mitades
   cronológicas (`portfolio_validation.compute_temporal_stability_report`)
   — no es un walk-forward con reentrenamiento real (este proyecto no
   reentrena ningún modelo; Bull/Bear/Judge son prompts fijos, no pesos
   entrenados sobre datos propios).
5. **Regime dependency**: ver la tabla de sensibilidad (PARTE 5) — split
   alto/bajo VIX en la entrada, la varianza REAL observada en los datos
   disponibles (no una resimulación estocástica bajo un choque
   hipotético — este proyecto no tiene un modelo de precios, ver
   `sensitivity.py`).

## PARTE 5 — Sensibilidad

{_sensitivity_table(sensitivity)}

## PARTE 6 — Decisión de inversión

{chr(10).join(f"- **{v}**: {decisions[v]['label']} — {'; '.join(decisions[v]['reasons'])}" for v in decisions)}

**Veredicto global: {best_decision['label']}** (la mejor de las 3 versiones evaluadas, empates a favor de Conservative).

## Apéndice — DYNAMIC vs BALANCED (IMPROVEMENT_PLAN.md Q3, comparación experimental)

DYNAMIC no es una de las 3 versiones del spec (ver `portfolio_strategies.py`,
nota 5 de su docstring): reutiliza el criterio de SI operar de BALANCED y
solo cambia el sizing (ponderado por EV×confianza en vez del interpolado
por confianza fijo). Se corre y persiste en cada backtest igual que las
otras 3, pero queda fuera de PARTE 2/3/6 a propósito — no es candidata al
veredicto de inversión hasta que se demuestre que aporta algo sobre
BALANCED con evidencia real, no solo con la lógica de diseño. Esta tabla
existe para que esa comparación sea visible sin tener que ir a buscar el
JSON crudo — no cambia el veredicto de arriba ni las 3 versiones que sí lo
determinan.

{_backtest_table(portfolio_report, versions=("BALANCED", "DYNAMIC"))}

## PARTE 7 — Next steps

Si el veredicto es GREENLIGHT o YELLOWLIGHT:
1. Dato a comprar primero: Tiingo (tickers deslistados, ~$15/mes) — cierra
   el sesgo de supervivencia real (`AUDIT_LEAN.md` §3).
2. Versión a activar en vivo: {best_version} (la que superó el veredicto).
3. Monitoreo primeros 3 meses: revisar `/week` (paper trading) cada semana,
   `/calibration` cada mes — un cambio de signo en la expectancy o una
   divergencia de calibración >15pp son las señales de alerta ya
   implementadas (`portfolio_validation.py`,
   `paper_trading/analysis.py:compute_alerts`).
4. Escalar tamaño de posición: solo tras 3 meses de paper trading sin
   alerts de "posible overfitting" (2+ pérdidas consecutivas).
5. Shut-down trigger: cualquier violación anti-look-ahead detectada en una
   corrida real (no debería pasar nunca — es un bug, no una señal de
   mercado), o calibración cayendo por debajo de 0.3 durante 2 meses
   seguidos.

Si el veredicto es REDLIGHT: no proceder — revisar qué versión/clase de
evento específica falló (PARTE 1-2 arriba) antes de repetir este análisis.

## Appendix: Top 10 mejor / peor predichos (todas las versiones)

| Versión | Ticker | Clase | Salida | PnL % |
|---|---|---|---|---|
{appendix_rows}

---
_Datos completos de todos los trades: ver el CSV exportado junto a este
documento (`pipeline/validation/report.py:export_trades_csv`)._
"""


def _fetch_portfolio_report(conn, run_batch_tag: str) -> dict | None:
    """Lee un portfolio_report ya calculado y guardado por
    backtest/portfolio_report.py (el paso nocturno anterior en el mismo
    run_batch_tag) en vez de resimular la cartera completa otra vez —
    simulate_portfolio() es acumulativo y recalcula TODO el histórico, así
    que llamarlo dos veces en la misma corrida nocturna sería el doble de
    coste por nada nuevo."""
    with conn.cursor() as cur:
        cur.execute("SELECT report_json FROM portfolio_reports WHERE run_batch_tag = %s", (run_batch_tag,))
        row = cur.fetchone()
    return row["report_json"] if row else None


def persist_validation_report(conn, run_batch_tag: str, sample: str | None = None) -> dict:
    """Guarda event_study + sensitivity + decisión en validation_reports
    (JSONB), leyendo el portfolio_report ya persistido por el paso de
    backtest de la misma corrida en vez de recalcularlo. Pensado para el
    cron nocturno (ver nightly_pipeline.yml) — no escribe ningún fichero,
    solo Postgres, que es lo único que sobrevive a un runner efímero (ver
    cabecera de la tabla validation_reports en schema.sql).

    `sample`: None (default) / 'in_sample' / 'oos' — ver sample_split.py. Se
    usa SOLO en la rama de defensa (cuando no hay portfolio_report todavía
    y hay que calcularlo aquí) y para run_event_study: si el portfolio_report
    de este run_batch_tag ya existe, su propio campo "sample" manda (fue
    calculado con el que se le pasó a portfolio_report.py en su momento);
    este parámetro es para cuando este mismo módulo es quien dispara ese
    primer cálculo."""
    portfolio_report = _fetch_portfolio_report(conn, run_batch_tag)
    if portfolio_report is None:
        # Defensa: si por lo que sea no hay portfolio_report para este tag
        # (ej. se llama fuera del flujo nocturno normal), lo calcula aquí —
        # más caro, pero nunca deja el paso en un estado roto.
        portfolio_report = run_full_backtest(conn, run_batch_tag=run_batch_tag, sample=sample)

    effective_sample = portfolio_report.get("sample")
    event_study = run_event_study(conn, window_days=20, sample=effective_sample)
    sensitivity = run_sensitivity_analysis(conn, run_batch_tag=run_batch_tag)
    decisions = evaluate_all_versions_decision(portfolio_report)
    best_version, best_decision = overall_verdict(decisions)

    payload = {
        "run_batch_tag": run_batch_tag,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample": effective_sample,
        "event_study": event_study,
        "sensitivity": sensitivity,
        "decisions": decisions,
        "best_version": best_version,
        "best_decision": best_decision,
        "bias_report": portfolio_report["bias_report"],
    }
    if effective_sample == SAMPLE_OOS:
        payload["oos_warning"] = OOS_WARNING

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO validation_reports (run_batch_tag, report_json)
            VALUES (%(tag)s, %(report)s)
            ON CONFLICT (run_batch_tag) DO UPDATE SET report_json = EXCLUDED.report_json, created_at = now()
            """,
            {"tag": run_batch_tag, "report": json.dumps(_json_safe(payload))},
        )
    conn.commit()
    return payload


def generate_full_validation_report(conn, run_batch_tag: str | None = None, docs_dir: str = "docs", sample: str | None = None) -> dict:
    """Punto de entrada único — corre el backtest (si hace falta), el event
    study, la sensibilidad, calcula las 3 decisiones, escribe
    docs/VALIDATION_REPORT.md (o VALIDATION_REPORT_OOS.md si sample='oos' —
    nunca pisa el in-sample) y el CSV de trades junto a él.

    `sample`: None (default) / 'in_sample' / 'oos' — ver sample_split.py.
    Solo se autogenera el sufijo -OOS en run_batch_tag cuando este mismo
    caller lo construye (run_batch_tag=None); si el caller pasa un tag
    explícito, se respeta tal cual (mismo criterio que el resto del
    proyecto: el caller que nombra el tag es responsable de su forma)."""
    if run_batch_tag is None:
        run_batch_tag = f"validation-{date.today().isoformat()}{tag_suffix(sample)}"
    portfolio_report = run_full_backtest(conn, run_batch_tag=run_batch_tag, sample=sample)
    event_study = run_event_study(conn, window_days=20, sample=sample)
    sensitivity = run_sensitivity_analysis(conn, run_batch_tag=run_batch_tag)
    decisions = evaluate_all_versions_decision(portfolio_report)
    best_version, best_decision = overall_verdict(decisions)

    markdown = render_validation_report_markdown(event_study, portfolio_report, sensitivity, decisions, best_version, best_decision)

    docs_path = Path(docs_dir)
    docs_path.mkdir(parents=True, exist_ok=True)
    report_filename = "VALIDATION_REPORT_OOS.md" if sample == SAMPLE_OOS else "VALIDATION_REPORT.md"
    report_path = docs_path / report_filename
    report_path.write_text(markdown)

    csv_path = docs_path / f"trades_{run_batch_tag}.csv"
    n_rows = export_trades_csv(conn, run_batch_tag, str(csv_path))

    return {
        "run_batch_tag": run_batch_tag,
        "report_path": str(report_path),
        "csv_path": str(csv_path) if n_rows > 0 else None,
        "n_trades_exported": n_rows,
        "best_version": best_version,
        "best_decision": best_decision,
        "decisions": decisions,
    }


if __name__ == "__main__":
    import argparse
    import logging

    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--persist-only",
        action="store_true",
        help=(
            "Para el cron nocturno: solo guarda validation_reports (Postgres), sin "
            "recalcular el backtest ni escribir docs/VALIDATION_REPORT.md — reutiliza "
            "el run_batch_tag y el portfolio_report que el paso de backtest de la misma "
            "corrida ya dejó en la BD (ver persist_validation_report)."
        ),
    )
    parser.add_argument(
        "--oos",
        action="store_true",
        help=(
            "SOLO a mano, con el workflow oos_manual.yml — nunca en el cron "
            "nocturno, que mira solo el In-Sample (BUGS_REPORT.md H-07). Ver el "
            "mismo flag en backtest/portfolio_report.py. Con --persist-only, "
            "tiene que coincidir con el --oos (o su ausencia) de la corrida de "
            "portfolio_report.py de este mismo run_batch_tag, o el tag no "
            "coincidirá y se recalculará todo. Exige --motivo."
        ),
    )
    parser.add_argument("--motivo", help="por qué se mira el OOS (obligatorio con --oos)")
    args = parser.parse_args()
    if args.oos and not (args.motivo or "").strip():
        parser.error("--oos exige --motivo")
    sample = SAMPLE_OOS if args.oos else SAMPLE_IN_SAMPLE

    conn = get_connection()
    if sample == SAMPLE_OOS:
        # El mismo tag con el que se guarda cada camino (persist / informe).
        tag_oos = (
            f"{date.today().isoformat()}-{git_sha_corto()}{tag_suffix(sample)}"
            if args.persist_only
            else f"validation-{date.today().isoformat()}{tag_suffix(sample)}"
        )
        vistas = registrar_oos(conn, tag_oos, "validacion", args.motivo)
        print(f"OOS mirado {vistas} vez/veces antes de esta (ver oos_runs)")

    if args.persist_only:
        # Mismo esquema de tag (incluido el sufijo -OOS) que
        # backtest/portfolio_report.py:__main__ — tiene que coincidir
        # exactamente para encontrar el portfolio_report de esta misma
        # corrida en vez de calcular uno nuevo. git_sha_corto() (ver
        # sample_split.py, IMPROVEMENT_PLAN.md Q8) es la MISMA función que
        # llama portfolio_report.py, no una copia independiente.
        tag = f"{date.today().isoformat()}-{git_sha_corto()}{tag_suffix(sample)}"
        payload = persist_validation_report(conn, tag, sample=sample)
        if payload.get("sample") == SAMPLE_OOS:
            print(f"\n{'=' * 70}\n⚠️  {OOS_WARNING}\n{'=' * 70}\n")
        print(f"validation_reports actualizado para {tag}")
        print(f"Veredicto: {payload['best_decision']['label']} ({payload['best_version']})")
    else:
        result = generate_full_validation_report(conn, sample=sample)
        if sample == SAMPLE_OOS:
            print(f"\n{'=' * 70}\n⚠️  {OOS_WARNING}\n{'=' * 70}\n")
        print(f"Reporte escrito en {result['report_path']}")
        print(f"CSV con {result['n_trades_exported']} trades en {result['csv_path']}")
        print(f"Veredicto: {result['best_decision']['label']} ({result['best_version']})")
