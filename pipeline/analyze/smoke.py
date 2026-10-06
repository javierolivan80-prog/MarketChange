"""smoke.py — prueba de humo de la IA real con N eventos (Tanda 6).

Analiza con Bull/Bear/Judge de verdad (gasta saldo de la API) solo N eventos
de la cola de la IA (los mismos filtros que la pasada normal: posteriores al
corte de los modelos, con texto y barra de D0, empresas grandes primero) y
devuelve un informe: qué se analizó, qué falló, tokens y coste REAL de cada
batch (libro ai_batches), coste por evento, y decisiones con IA frente al
control sin IA. El informe se guarda en smoke_runs y sale en el log.

Lo lanza el usuario, a mano (workflow smoke.yml o en local), nunca el
nightly. Respeta el tope diario de gasto. Los análisis que salen son reales
y se quedan guardados como los de cualquier pasada.

    python -m pipeline.analyze.smoke --n 5
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter

from pipeline import config
from pipeline.analyze.adversarial_analyzer import MODELOS_SIN_IA
from pipeline.analyze.event_analysis_pipeline import (
    fetch_events_needing_analysis,
    process_chunk,
    remaining_daily_budget_events,
    tomar_cerrojo_ia,
)

logger = logging.getLogger(__name__)

SMOKE_MAX = 20  # una prueba de humo, no una pasada


def ejecutar_smoke(conn, client, n: int) -> dict:
    n = max(1, min(int(n), SMOKE_MAX))
    informe: dict = {"pedidos": n, "estimacion_usd_por_evento": config.ANALYSIS_EST_COST_PER_EVENT_USD}
    if not tomar_cerrojo_ia(conn):
        informe["motivo_sin_ejecutar"] = "la pasada de la IA (u otra prueba) está analizando ahora mismo"
        return _guardar(conn, informe)
    if config.AI_VALIDATION_START is None:
        informe["motivo_sin_ejecutar"] = "algún modelo configurado no tiene fecha de corte (config.MODEL_TRAINING_CUTOFF)"
        return _guardar(conn, informe)
    presupuesto = remaining_daily_budget_events(conn)
    if presupuesto == 0:
        informe["motivo_sin_ejecutar"] = "el tope diario de gasto ya está agotado"
        return _guardar(conn, informe)
    if presupuesto is not None:
        n = min(n, presupuesto)
    eventos = fetch_events_needing_analysis(
        conn, n, min_market_cap=config.ANALYSIS_MIN_MARKET_CAP_USD, require_text=True,
        require_d0_bar=True, d0_desde=config.AI_VALIDATION_START,
    )
    informe["en_cola"] = len(eventos)
    informe["coste_estimado_usd"] = round(len(eventos) * config.ANALYSIS_EST_COST_PER_EVENT_USD, 4)
    if not eventos:
        informe["motivo_sin_ejecutar"] = "no hay eventos listos en la cola de la IA"
        return _guardar(conn, informe)

    t0 = time.monotonic()
    batch_ids: list[str] = []  # solo los batches de esta prueba (no los de otra corrida)
    error: Exception | None = None
    try:
        conn = process_chunk(conn, client, eventos, batches_enviados=batch_ids)
    except Exception as exc:  # noqa: BLE001 — el informe parcial se guarda y el error se relanza
        error = exc
        informe["error"] = f"{type(exc).__name__}: {exc}"
        if conn.closed:
            from pipeline.db.connection import get_connection

            conn = get_connection()
        else:
            conn.rollback()
    informe["duracion_s"] = round(time.monotonic() - t0, 1)

    ids = [ev["event_id"] for ev in eventos]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, model_version_bull_bear AS modelo, from_cache,
                   trade_decision_balanced AS con_ia,
                   decision_sin_ia->'decisiones'->'BALANCED'->>'trade_decision' AS sin_ia
            FROM event_analyses WHERE event_id = ANY(%s)
            """,
            (ids,),
        )
        filas = cur.fetchall()
        cur.execute(
            """
            SELECT batch_id, kind, model, n_requests, input_tokens, output_tokens, cost_usd,
                   n_errores, n_cortadas, n_json_invalido
            FROM ai_batches WHERE batch_id = ANY(%s) ORDER BY submitted_at
            """,
            (batch_ids,),
        )
        batches = [
            {k: (float(v) if k == "cost_usd" and v is not None else v) for k, v in r.items()}
            for r in cur.fetchall()
        ]

    con_ia = [f for f in filas if f["modelo"] not in MODELOS_SIN_IA and not f["from_cache"]]
    informe.update({
        "analizados_con_ia": len(con_ia),
        "de_cache": sum(1 for f in filas if f["from_cache"]),
        "descartados_sin_llamar_a_la_ia": sum(1 for f in filas if f["modelo"] in MODELOS_SIN_IA),
        # En cola pero sin fila: Bull/Bear/Judge incompleto o Judge inválido.
        "sin_guardar": len(ids) - len(filas),
        "batches": batches,
        "requests_fallidas": {
            k: sum(b[k] or 0 for b in batches) for k in ("n_errores", "n_cortadas", "n_json_invalido")
        },
        "decisiones_balanced": {
            "con_ia": dict(Counter(f["con_ia"] for f in filas)),
            "sin_ia": dict(Counter(f["sin_ia"] for f in filas)),
            "coinciden": sum(1 for f in filas if f["con_ia"] == f["sin_ia"]),
        },
    })
    costes = [b["cost_usd"] for b in batches]
    if len(batches) < len(batch_ids):
        informe["coste_real_usd"] = None  # un batch no se pudo apuntar en el libro
    elif all(c is not None for c in costes):
        informe["coste_real_usd"] = round(sum(costes), 4)  # 0 si todo fue de caché o descartado
        if con_ia:
            # Incluye lo pagado por los «sin guardar»: es lo que cuesta de verdad cada evento útil.
            informe["coste_real_por_evento_usd"] = round(sum(costes) / len(con_ia), 5)
    else:
        informe["coste_real_usd"] = None  # algún batch sin precio, sin usage o sin cerrar: no se inventa
    if error is not None:
        logger.error("La prueba de humo falló a mitad: %s (informe parcial guardado)", informe["error"])
    return _guardar(conn, informe)


def _guardar(conn, informe: dict) -> dict:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO smoke_runs (informe) VALUES (%s) RETURNING smoke_run_id", (json.dumps(informe, default=str),))
        informe["smoke_run_id"] = cur.fetchone()["smoke_run_id"]
    conn.commit()
    return informe


def informe_markdown(informe: dict) -> str:
    """Resumen legible para el log y el resumen del run de GitHub."""
    if informe.get("motivo_sin_ejecutar"):
        return f"## Prueba de humo: no se ejecutó\n\n{informe['motivo_sin_ejecutar']}.\n"
    lineas = [
        f"## Prueba de humo de la IA ({informe['en_cola']} de {informe['pedidos']} eventos pedidos)",
        "",
        f"- Analizados con IA: **{informe['analizados_con_ia']}** · de caché: {informe['de_cache']} · "
        f"descartados sin llamar a la IA: {informe['descartados_sin_llamar_a_la_ia']} · "
        f"sin guardar (IA incompleta o inválida): **{informe['sin_guardar']}**",
        f"- Requests fallidas: errores {informe['requests_fallidas']['n_errores']}, "
        f"cortadas por max_tokens {informe['requests_fallidas']['n_cortadas']}, "
        f"JSON ilegible {informe['requests_fallidas']['n_json_invalido']}",
        f"- Coste real: **{informe['coste_real_usd'] if informe['coste_real_usd'] is not None else 'sin dato'} $** "
        f"(estimado antes: {informe['coste_estimado_usd']} $)"
        + (f" · por evento con IA: {informe['coste_real_por_evento_usd']} $" if "coste_real_por_evento_usd" in informe else ""),
        f"- Duración: {informe['duracion_s']} s",
        *([f"- **Falló a mitad:** {informe['error']} (informe parcial)"] if informe.get("error") else []),
        f"- Decisiones Equilibrada con IA: {informe['decisiones_balanced']['con_ia']} · sin IA: "
        f"{informe['decisiones_balanced']['sin_ia']} · coinciden: {informe['decisiones_balanced']['coinciden']}",
        "",
        "| Batch | Modelo | Requests | Tokens entrada | Tokens salida | Coste $ |",
        "|---|---|---|---|---|---|",
    ]
    for b in informe["batches"]:
        lineas.append(
            f"| {b['kind']} | {b['model']} | {b['n_requests']} | {b['input_tokens']} | {b['output_tokens']} | {b['cost_usd']} |"
        )
    return "\n".join(lineas) + "\n"


if __name__ == "__main__":
    import argparse
    import os

    import anthropic

    from pipeline.db.connection import get_connection

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=5, help=f"eventos a analizar (máximo {SMOKE_MAX})")
    args = parser.parse_args()
    if not config.ANTHROPIC_API_KEY:
        raise SystemExit("Falta ANTHROPIC_API_KEY.")
    resultado = ejecutar_smoke(get_connection(), anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY), args.n)
    texto = informe_markdown(resultado)
    print(texto)
    print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen:
        with open(resumen, "a", encoding="utf-8") as f:
            f.write(texto)
    if resultado.get("error"):
        raise SystemExit(1)
