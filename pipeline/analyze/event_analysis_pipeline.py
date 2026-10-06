"""event_analysis_pipeline.py — orquestador de la Fase 2, Etapas 1-8.

Conecta los módulos ya escritos (cada uno probado por separado con datos
sintéticos y, donde aplica, contra Postgres real):
  Etapa 1  enrichment.py
  Etapa 2  novelty.py
  Etapas 3-5  adversarial_analyzer.py (Bull/Bear/Judge, con caché de 24h)
  Etapa 6  historical_analogues.py
  Etapa 7  ev_engine.py
  Etapa 8  abstention_engine.py

Precisión importante sobre la caché de 24h (releer el spec: "Cachea
Bull/Bear/Judge"): la caché cubre SOLO las Etapas 3-5 (el veredicto
cualitativo del LLM), NUNCA las Etapas 1/2/6/7/8. Esas cuatro dependen de la
fecha y el precio exactos de CADA evento — reutilizar el EV o la decisión de
abstención de un evento distinto (aunque sea el mismo ticker y la misma
clase) sería aplicar el resultado de "hace 3 horas, con otro precio, con
otros análogos disponibles hasta esa fecha" a un evento que tiene su propio
D0 distinto. Eso rompería la disciplina anti-look-ahead del resto del
proyecto sin que ninguna prueba lo detectara fácilmente. Por eso
process_chunk() extrae del cache_hit SOLO bull_analyst_output/
bear_analyst_output/judge_output/net_conviction/confidence_in_conviction, y
recalcula todo lo demás para el evento actual.

PROCESAMIENTO EN LOTES DE 50 (pedido por el spec): no es un límite de la
Batch API (que admite hasta 100k requests por batch) — es un tamaño de chunk
de orquestación para poder loguear progreso y no perder todo un backfill de
50k eventos si algo falla a mitad de camino.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import date, timedelta

from pipeline import config
from pipeline.analyze.abstention_engine import (
    STRATEGIES,
    AbstentionDecision,
    AbstentionInputs,
    as_json as abstention_as_json,
    decide_all_strategies,
    ev_ceiling_no_trade_reason,
    objective_no_trade_reason,
)
from pipeline.analyze.adversarial_analyzer import (
    MODELO_ANTES_DEL_CORTE,
    MODELOS_SIN_IA,
    SKIPPED_MODEL_VERSION,
    EventContext,
    build_bull_bear_batch,
    build_judge_batch,
    custom_id_de,
    get_cached_analyses_batch,
    run_batch_and_collect,
    validar_salida_judge,
)
from pipeline.analyze.enrichment import fetch_and_compute_enrichment, guardar_enrichment
from pipeline.analyze.ev_engine import compute_ev
from pipeline.analyze.guidance_detector import compute_novelty_signals
from pipeline.analyze.historical_analogues import estimate_impact_for_event
from pipeline.analyze.novelty import NoveltyInputs, compute_novelty

_FALLBACK_FILING_EXCERPT = "(sin texto de filing extraído todavía — ver ingest/filing_text.py)"

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50
FDA_CRL_8K_WINDOW_DAYS = 10  # ventana de tolerancia para buscar un 8-K correspondiente


# Barra de precio de D0 con high/low: sin ella, enrichment no puede calcular
# la liquidez y el evento se descartaba por "sin datos" ANTES de la IA. Ese
# descarte se guardaba en event_analyses y el evento salía de la cola para
# siempre (BUGS_REPORT.md H-39: 126 de 200 análisis en producción). Pasaba
# porque el análisis corre en las 3 pasadas del día y los precios solo se
# bajan en la nocturna: un 8-K de hoy (o con D0 = mañana, si llegó tras el
# cierre) se analizaba antes de existir su barra de D0. Además la decisión es
# "al cierre de D0": analizar antes de ese cierre no tiene sentido.
_D0_BAR_EXISTS = (
    "EXISTS (SELECT 1 FROM prices p WHERE p.ticker = e.ticker AND p.trade_date = e.d0_close_date "
    "AND p.high_raw IS NOT NULL AND p.low_raw IS NOT NULL)"
)


def _queue_filters(
    min_market_cap: float | None, require_text: bool, exclude_ids, require_d0_bar: bool = False,
    d0_desde: date | None = None, d0_antes_de: date | None = None,
) -> tuple[str, dict]:
    where = ["ea.event_id IS NULL"]
    params: dict = {}
    # Fecha de corte de los modelos (BUGS_REPORT.md H-06): la cola de la IA
    # solo lleva eventos con D0 desde config.AI_VALIDATION_START; los
    # anteriores van por analizar_antes_del_corte, sin IA.
    if d0_desde is not None:
        where.append("e.d0_close_date >= %(d0_desde)s")
        params["d0_desde"] = d0_desde
    if d0_antes_de is not None:
        where.append("e.d0_close_date < %(d0_antes_de)s")
        params["d0_antes_de"] = d0_antes_de
    if min_market_cap is not None:
        # Solo el universo invertible, y a partir de cierto tamaño: es la
        # palanca para gastar la IA en empresas grandes primero.
        where.append("u.in_investable_universe AND u.market_cap_last_usd >= %(min_cap)s")
        params["min_cap"] = min_market_cap
    if require_text:
        # Un evento de EDGAR sin texto extraído todavía NO se manda a la IA:
        # Bull/Bear/Judge sobre el placeholder es pagar por ruido. Se analiza
        # en la pasada siguiente, cuando filing_text.py lo haya descargado.
        where.append("(e.source <> 'EDGAR' OR e.filing_text IS NOT NULL)")
    if require_d0_bar:
        where.append(_D0_BAR_EXISTS)
    if exclude_ids:
        where.append("NOT (e.event_id = ANY(%(exclude)s))")
        params["exclude"] = list(exclude_ids)
    return " AND ".join(where), params


def fetch_events_needing_analysis(
    conn,
    limit: int = CHUNK_SIZE,
    *,
    min_market_cap: float | None = None,
    require_text: bool = False,
    require_d0_bar: bool = False,
    exclude_ids=(),
    d0_desde: date | None = None,
    d0_antes_de: date | None = None,
) -> list[dict]:
    """Siguiente tanda de la cola. Sin filtros (los defaults) devuelve todo lo
    pendiente en orden de event_id; run_pipeline() la llama con los filtros
    de config: más recientes primero y, a igualdad de fecha, las empresas más
    grandes primero."""
    where, params = _queue_filters(min_market_cap, require_text, exclude_ids, require_d0_bar, d0_desde, d0_antes_de)
    order = (
        "e.d0_close_date DESC, u.market_cap_last_usd DESC NULLS LAST, e.event_id"
        if min_market_cap is not None
        else "e.event_id"
    )
    params["limit"] = limit
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT e.event_id, e.cik, e.ticker, e.event_class, e.source, e.accession_number, e.d0_close_date,
                   e.filing_text, u.company_name, u.sic_code
            FROM events e
            JOIN universe u ON u.cik = e.cik
            LEFT JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE {where}
            ORDER BY {order}
            LIMIT %(limit)s
            """,
            params,
        )
        return cur.fetchall()


def analysis_queue_summary(conn, min_market_cap: float | None = None) -> dict:
    """Cuántos eventos esperan a la IA y cuánto costaría analizarlos — para que
    el log de cada corrida diga qué hay listo aunque la clave no tenga saldo."""
    min_market_cap = config.ANALYSIS_MIN_MARKET_CAP_USD if min_market_cap is None else min_market_cap
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS pendientes,
                   count(*) FILTER (WHERE u.in_investable_universe AND u.market_cap_last_usd >= %(min_cap)s) AS en_objetivo,
                   count(*) FILTER (WHERE u.in_investable_universe AND u.market_cap_last_usd >= %(min_cap)s
                                    AND (e.source <> 'EDGAR' OR e.filing_text IS NOT NULL)
                                    AND """ + _D0_BAR_EXISTS + """
                                    AND %(inicio_ia)s::date IS NOT NULL AND e.d0_close_date >= %(inicio_ia)s) AS listos,
                   count(*) FILTER (WHERE %(inicio_ia)s::date IS NULL OR e.d0_close_date < %(inicio_ia)s) AS antes_del_corte,
                   count(DISTINCT e.cik) FILTER (WHERE u.in_investable_universe AND u.market_cap_last_usd >= %(min_cap)s) AS empresas
            FROM events e
            JOIN universe u ON u.cik = e.cik
            LEFT JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE ea.event_id IS NULL
            """,
            {"min_cap": min_market_cap, "inicio_ia": config.AI_VALIDATION_START},
        )
        row = dict(cur.fetchone())
    row["min_market_cap_usd"] = min_market_cap
    row["coste_estimado_listos_usd"] = round(row["listos"] * config.ANALYSIS_EST_COST_PER_EVENT_USD, 2)
    return row


def spend_today_usd(conn) -> float:
    """Gasto ya incurrido hoy en Bull/Bear/Judge (IMPROVEMENT_PLAN.md A2).

    La fuente es el libro ai_batches (H-24): cada batch ENVIADO hoy, con su
    coste REAL (cost_usd, de los tokens que devolvió la API) cuando ya
    terminó, o con la estimación por request mientras no se sepa (batch en
    curso, corrida que murió esperando, modelo sin precio en config). Un
    batch pagado cuenta aunque su resultado no llegara a guardarse.

    Solo si hoy no hay NINGUNA línea en el libro (p. ej. apuntarlas falló) se
    cae a contar los eventos guardados en event_analyses por la estimación de
    0,011 $/evento: nunca se da por gastado 0 algo que sí se pagó.

    'Hoy' es CURRENT_DATE en la zona horaria de Postgres (UTC en Neon/
    Supabase): con 3 corridas al día, un desfase de unas horas en el corte del
    día no cambia si queda presupuesto o no."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n,
                   coalesce(sum(coalesce(cost_usd, n_requests * CASE kind WHEN 'judge' THEN %s ELSE %s END)), 0) AS usd
            FROM ai_batches
            WHERE submitted_at::date = CURRENT_DATE
            """,
            (config.EST_COST_JUDGE_REQUEST_USD, config.EST_COST_BULL_BEAR_REQUEST_USD),
        )
        libro = cur.fetchone()
        if libro["n"]:
            return round(float(libro["usd"]), 4)
        cur.execute(
            """
            SELECT count(*) AS n
            FROM event_analyses
            WHERE analyzed_at::date = CURRENT_DATE
              AND from_cache = FALSE
              AND model_version_bull_bear <> ALL(%s)
            """,
            (MODELOS_SIN_IA,),
        )
        n = cur.fetchone()["n"]
    return round(n * config.ANALYSIS_EST_COST_PER_EVENT_USD, 4)


def _escribir_en_libro(conn, sql: str, params: tuple, batch_id: str) -> None:
    """Escribe una línea en ai_batches. Si apuntarla falla, se avisa y se
    sigue: perder una línea del libro no debe tirar un batch ya pagado.

    Los batches se apuntan tras esperas largas a la Batch API (hasta ~20 min)
    y Neon cierra las conexiones ociosas: con `conn` muerta el apunte fallaba
    y ese gasto no contaba para el tope diario. Si `conn` no responde, se
    apunta con una conexión propia y efímera; `conn` no se toca (la
    reconexión del chunk es cosa de process_chunk)."""

    def _escribir(c) -> None:
        with c.cursor() as cur:
            cur.execute(sql, params)
        c.commit()

    try:
        if _conexion_viva(conn):
            _escribir(conn)
            return
        from pipeline.db.connection import get_connection

        propia = get_connection()
        try:
            _escribir(propia)
        finally:
            propia.close()
    except Exception:
        logger.warning("No se pudo apuntar el batch %s en ai_batches", batch_id, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass  # conexión muerta: process_chunk ya reconecta más abajo


def registrar_batch(conn, kind: str, enviados: list | None = None):
    """Callback on_submitted de run_batch_and_collect: apunta el batch en
    ai_batches nada más crearlo (H-24), antes de esperar, para que cuente
    aunque luego algo falle. `enviados`: si se pasa, recoge los batch_id de
    esta corrida (la prueba de humo informa solo de los suyos)."""

    def _registrar(batch_id: str, n_requests: int) -> None:
        if enviados is not None:
            enviados.append(batch_id)
        _escribir_en_libro(
            conn,
            "INSERT INTO ai_batches (batch_id, kind, n_requests) VALUES (%s, %s, %s) ON CONFLICT (batch_id) DO NOTHING",
            (batch_id, kind, n_requests),
            batch_id,
        )

    return _registrar


def cerrar_batch(conn, kind: str):
    """Callback on_finished de run_batch_and_collect: apunta lo que el batch
    COSTÓ de verdad, con los tokens que devuelve la API (usage de cada
    respuesta) y los precios de config.MODEL_PRICES_USD_PER_MTOK. Así el
    tope diario deja de depender de la estimación de 0,011 $/evento, que
    puede quedarse corta (filings largos) o larga (respuestas breves).

    Si el modelo no tiene precio en config, cost_usd se deja vacío y
    spend_today_usd usa la estimación por request para ese batch: nunca se
    cuenta como gratis algo que no se sabe cuánto costó."""

    def _cerrar(batch_id: str, uso: dict) -> None:
        # Sin ninguna respuesta con usage no se sabe lo que costó: vacío (se
        # cuenta con la estimación), nunca 0 $.
        coste = config.batch_cost_usd(uso) if uso.get("n_responses") else None
        if coste is None:
            logger.warning("Batch %s: sin precio para el modelo %r; se cuenta con la estimación", batch_id, uso.get("model"))
        else:
            n = uso.get("n_responses") or 0
            logger.info(
                "Batch %s (%s): %d respuestas, %d tokens de entrada, %d de salida = %.4f $ reales (%.5f $/respuesta)",
                batch_id, kind, n, uso["input_tokens"], uso["output_tokens"], coste, coste / n if n else 0.0,
            )
        _escribir_en_libro(
            conn,
            """
            UPDATE ai_batches
            SET input_tokens = %s, output_tokens = %s, model = %s, cost_usd = %s, finished_at = now(),
                n_errores = %s, n_cortadas = %s, n_json_invalido = %s
            WHERE batch_id = %s
            """,
            (uso["input_tokens"], uso["output_tokens"], uso.get("model"), coste,
             uso.get("n_errores"), uso.get("n_cortadas"), uso.get("n_json_invalido"), batch_id),
            batch_id,
        )

    return _cerrar


def remaining_daily_budget_events(conn) -> int | None:
    """Cuántos eventos MÁS caben hoy dentro de config.DAILY_SPEND_CAP_USD,
    dado lo que spend_today_usd() dice que ya se ha gastado. None si el tope
    diario está desactivado (DAILY_SPEND_CAP_USD <= 0 — configuración
    explícita para desactivarlo, no el caso por defecto)."""
    if config.DAILY_SPEND_CAP_USD <= 0:
        return None
    remaining_usd = config.DAILY_SPEND_CAP_USD - spend_today_usd(conn)
    if remaining_usd <= 0:
        return 0
    return int(remaining_usd / config.ANALYSIS_EST_COST_PER_EVENT_USD)


def check_fda_crl_without_8k(conn, cik: str, event_class: str, d0_close_date: date) -> bool:
    """Regla 6 de abstention_engine: una CRL de FDA sin 8-K correspondiente
    todavía no está comunicada oficialmente por la empresa. Solo aplica a
    eventos FDA_CRL — cualquier otra clase devuelve False sin consultar la BD.

    La ventana mira hacia ATRÁS y hasta D0 inclusive, nunca más allá. Antes se
    extendía FDA_CRL_8K_WINDOW_DAYS también hacia delante, lo que respondía a
    una pregunta distinta de la que plantea la regla: "¿acabará la empresa
    comunicando esto?" en vez de "¿lo ha comunicado ya?". Con la ventana
    futura, el sistema dejaba de abstenerse justo en los casos en que un 8-K
    posterior confirmaba el evento — es decir, usaba el futuro para decidir
    operar en el presente. Acotarla a D0 devuelve la regla a su intención y es
    además el lado conservador: ante la duda, abstenerse.
    """
    if event_class != "FDA_CRL":
        return False
    window_start = d0_close_date - timedelta(days=FDA_CRL_8K_WINDOW_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM events
            WHERE cik = %(cik)s AND source = 'EDGAR'
              AND d0_close_date BETWEEN %(start)s AND %(as_of)s
            LIMIT 1
            """,
            {"cik": cik, "start": window_start, "as_of": d0_close_date},
        )
        return cur.fetchone() is None


def _conexion_viva(conn) -> bool:
    """Comprueba con una consulta real si la conexión sigue usable.

    No basta con mirar conn.closed: psycopg no se entera de que el otro
    extremo cortó hasta que se intenta usar la conexión. Un SELECT 1 fuerza
    esa comprobación sin efectos secundarios.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def agrupar_por_filing(eventos: list[dict]) -> list[list[dict]]:
    """Agrupa los eventos que vienen del mismo filing (misma fuente y mismo
    accession_number), conservando el orden; el primero de cada grupo es el
    que se envía a la IA. Sin accession (p. ej. FDA) cada evento va solo.
    Por prudencia también se exige el mismo ticker y D0: un mismo accession
    con datos distintos sería un error de ingesta, no un filing compartido."""
    grupos: dict[tuple, list[dict]] = {}
    for ev in eventos:
        accession = ev.get("accession_number")
        clave = (
            (ev.get("source"), accession, ev["ticker"], ev["d0_close_date"]) if accession else ("solo", ev["event_id"])
        )
        grupos.setdefault(clave, []).append(ev)
    return list(grupos.values())


def repartir_resultados_del_filing(grupos: list[list[dict]], bull_bear_results: dict, judge_results: dict) -> None:
    """Copia el Bull/Bear/Judge del representante de cada grupo a los demás
    eventos del mismo filing, con sus propios custom_id."""
    for grupo in grupos:
        rep_id = grupo[0]["event_id"]
        for ev in grupo[1:]:
            for lado in ("bull", "bear"):
                if custom_id_de(rep_id, lado) in bull_bear_results:
                    bull_bear_results[custom_id_de(ev["event_id"], lado)] = bull_bear_results[custom_id_de(rep_id, lado)]
            if custom_id_de(rep_id, "judge") in judge_results:
                judge_results[custom_id_de(ev["event_id"], "judge")] = judge_results[custom_id_de(rep_id, "judge")]


def process_chunk(conn, client, event_rows: list[dict], batches_enviados: list | None = None):
    """event_rows: filas de fetch_events_needing_analysis().

    Devuelve la conexión a usar de aquí en adelante — la misma que se pasó,
    salvo que haya hecho falta reconectar (ver más abajo). El caller tiene
    que quedarse con lo que devuelve esta función, no seguir usando la
    conexión original a ciegas.
    """
    # Eventos anteriores al corte de los modelos (H-06): nunca van a la IA
    # (ni de caché), solo se calcula la regla sin IA. Sin corte conocido
    # (AI_VALIDATION_START None) no se sabe dónde está la frontera: ni
    # run_pipeline ni analizar_antes_del_corte llaman entonces a esta función.
    inicio_ia = config.AI_VALIDATION_START
    antes_del_corte = {
        ev["event_id"] for ev in event_rows if inicio_ia is not None and ev["d0_close_date"] < inicio_ia
    }
    cache_hits = get_cached_analyses_batch(conn, [ev for ev in event_rows if ev["event_id"] not in antes_del_corte])
    needs_llm = [ev for ev in event_rows if ev["event_id"] not in cache_hits]

    # Pre-filtro de condiciones objetivas (Etapa 2 + Etapa 8, adelantadas —
    # hallazgo de auditoría IMPROVEMENT_PLAN.md R5, extiende el pre-filtro
    # que antes solo cubría novelty): de las 7 reglas de
    # abstention_engine.decide_for_strategy, 5 (novelty, survivorship
    # warning, modelo de factores sin ajustar, CRL de FDA sin 8-K, e
    # iliquidez) NO dependen en absoluto de lo que diga Bull/Bear/Judge — ver
    # abstention_engine.objective_no_trade_reason(). Si CUALQUIERA dispara,
    # el resultado final es NO_TRADE en las 3 estrategias SIN IMPORTAR qué
    # responda el debate de IA — pagar ese debate no cambia ni una sola
    # decisión, solo gasta tokens. Todos estos datos se calculan aquí
    # (enrichment + guidance/rumor + el cruce EDGAR/FDA, todo solo Postgres,
    # sin coste) ANTES de construir el batch, para no invocar Bull/Bear/Judge
    # en los eventos que van a descartarse igual.
    precomputed_by_id: dict[int, tuple] = {}
    llm_candidates: list[dict] = []
    skipped_ids_with_reason: dict[int, str] = {}
    # Motivo objetivo (o None) de los eventos anteriores al corte: decide la
    # regla sin IA; la decisión de la IA es NO_TRADE por MOTIVO_ANTES_DEL_CORTE.
    skip_regla_antes_del_corte: dict[int, str | None] = {}
    # SPY, ETFs sectoriales, ^VIX y Fama-French: una sola lectura por chunk
    # en vez de una por evento (BUGS_REPORT.md H-27).
    series_comunes: dict = {}
    for ev in needs_llm:
        enrichment = fetch_and_compute_enrichment(conn, ev, series_comunes)
        has_guidance, rumor_flag = compute_novelty_signals(conn, ev["ticker"], ev["d0_close_date"])
        novelty = compute_novelty(
            NoveltyInputs(
                pre_event_drift_pct=enrichment.pre_event_drift_pct or 0.0,
                has_prior_guidance=has_guidance,
                rumor_flag=rumor_flag,
            )
        )
        is_fda_crl_without_8k = check_fda_crl_without_8k(conn, ev["cik"], ev["event_class"], ev["d0_close_date"])
        precomputed_by_id[ev["event_id"]] = (enrichment, novelty, is_fda_crl_without_8k)
        skip_reason = objective_no_trade_reason(
            novelty_score=novelty.score,
            had_survivorship_warning=enrichment.had_survivorship_warning,
            beta_available=enrichment.beta_vs_spy is not None,
            adv_usd_60d=enrichment.adv_usd_60d,
            is_fda_crl_without_8k=is_fda_crl_without_8k,
        )
        if skip_reason is None:
            # Techo de EV (BUGS_REPORT.md H-13): la Etapa 6 no cuesta nada y
            # basta para saber si el evento podría operar en el mejor caso.
            impact = estimate_impact_for_event(conn, ev["event_class"], ev["d0_close_date"], ev["event_id"], window_days=20)
            skip_reason = ev_ceiling_no_trade_reason(impact.expected_magnitude_pct, impact.confidence)
        if ev["event_id"] in antes_del_corte:
            skip_regla_antes_del_corte[ev["event_id"]] = skip_reason
        elif skip_reason is not None:
            skipped_ids_with_reason[ev["event_id"]] = skip_reason
        else:
            llm_candidates.append(ev)

    logger.info(
        "Chunk de %d eventos: %d en caché, %d requieren LLM, %d descartados por condición objetiva "
        "(NO_TRADE garantizado igualmente, se ahorra el debate de IA), %d anteriores al corte (solo regla sin IA)",
        len(event_rows), len(cache_hits), len(llm_candidates), len(skipped_ids_with_reason), len(antes_del_corte),
    )

    bull_bear_results: dict[str, dict] = {}
    judge_results: dict[str, dict] = {}
    bb_batch_id = None
    judge_batch_id = None

    if llm_candidates:
        # H-20: un 8-K con varios Items (2.02 + 5.02...) da un evento por Item,
        # pero es UN filing: mismo texto, misma empresa, mismo D0. Se paga un
        # solo debate por filing; el prompt nombra todos sus Items y el
        # resultado se reparte entre los eventos del grupo (ver abajo).
        grupos = agrupar_por_filing(llm_candidates)
        contexts = [
            EventContext(
                event_id=rep["event_id"],
                ticker=rep["ticker"],
                event_class=" + ".join(sorted({ev["event_class"] for ev in grupo})),
                company_name=rep["company_name"],
                # Fase 3: texto real del filing cuando existe (ingest/filing_text.py
                # ya lo extrajo); si no, degrada al placeholder — un evento sin
                # texto todavía no debe bloquear el análisis, solo empobrecerlo.
                filing_excerpt=rep["filing_text"] or _FALLBACK_FILING_EXCERPT,
            )
            for rep, grupo in ((g[0], g) for g in grupos)
        ]
        if len(contexts) < len(llm_candidates):
            logger.info(
                "%d eventos a la IA comparten filing: se envían %d debates en vez de %d",
                len(llm_candidates), len(contexts), len(llm_candidates),
            )
        bull_bear_results, bb_batch_id = run_batch_and_collect(
            client, build_bull_bear_batch(contexts), on_submitted=registrar_batch(conn, "bull_bear", batches_enviados),
            on_finished=cerrar_batch(conn, "bull_bear"),
        )
        judge_results, judge_batch_id = run_batch_and_collect(
            client, build_judge_batch(contexts, bull_bear_results), on_submitted=registrar_batch(conn, "judge", batches_enviados),
            on_finished=cerrar_batch(conn, "judge"),
        )
        repartir_resultados_del_filing(grupos, bull_bear_results, judge_results)

        # BUG REAL (2026-09-15, run 34964242549): cada run_batch_and_collect
        # espera a la Batch API con un `while ... time.sleep(30)` que puede
        # durar minutos — este chunk en concreto tardó los DOS batches juntos
        # unos 20 minutos. Durante toda esa espera `conn` no hace NADA, y
        # Postgres (Neon, gestionado, agresivo cerrando conexiones ociosas)
        # la corta por su cuenta. El síntoma no fue en el batch: fue en el
        # primer INSERT de la vuelta de escritura, con
        # "psycopg.OperationalError: the connection is lost" — y encima el
        # `except` que debía capturarlo y seguir con el siguiente evento
        # (ver más abajo) tampoco podía hacer el rollback, porque la
        # conexión rota no admite ni eso: se llevaba por delante el chunk
        # entero, con el resto de eventos ya analizados por la IA y sin
        # forma de guardarlos.
        #
        # Se reconecta aquí, justo después de la espera larga y antes de
        # escribir nada, en vez de esperar a que un INSERT falle: así el
        # trabajo caro (la llamada a la IA, ya hecha) no se tira.
        if not _conexion_viva(conn):
            logger.warning("La conexión a la base de datos se perdió durante la espera del batch — reconectando")
            from pipeline.db.connection import get_connection

            conn = get_connection()

    for ev in event_rows:
        try:
            event_id = ev["event_id"]
            if event_id in antes_del_corte:
                _process_single_event(
                    conn, ev, None, {}, {}, None, None,
                    precomputed=precomputed_by_id.get(event_id),
                    skip_reason=MOTIVO_ANTES_DEL_CORTE,
                    series_comunes=series_comunes,
                    skip_reason_regla=skip_regla_antes_del_corte.get(event_id),
                    modelo_omitido=MODELO_ANTES_DEL_CORTE,
                )
                continue
            _process_single_event(
                conn, ev, cache_hits.get(event_id), bull_bear_results, judge_results, bb_batch_id, judge_batch_id,
                precomputed=precomputed_by_id.get(event_id),
                skip_reason=skipped_ids_with_reason.get(event_id),
                series_comunes=series_comunes,
            )
        except Exception:
            logger.exception("Fallo analizando evento %d — se continúa con el siguiente", ev["event_id"])
            # Sin rollback, "se continúa con el siguiente" es mentira cuando el
            # fallo viene de Postgres: la transacción queda abortada y TODOS
            # los eventos siguientes fallan con "current transaction is
            # aborted". Pasó exactamente así en la ingesta de fundamentales
            # (run 34943861450): un error real y 134 copias de su consecuencia.
            try:
                conn.rollback()
            except Exception:
                # La conexión puede haber muerto DESPUÉS de la comprobación de
                # arriba (a mitad de este bucle, no solo durante la espera del
                # batch) — un rollback sobre una conexión rota vuelve a
                # lanzar, y eso es justo lo que tumbó el run 34964242549:
                # el rollback de recuperación reventaba sin capturar y se
                # llevaba por delante los eventos que quedaban por escribir.
                logger.warning("La conexión también murió al hacer rollback — reconectando para el resto del chunk")
                from pipeline.db.connection import get_connection

                conn = get_connection()

    return conn


def _process_single_event(
    conn, ev: dict, cache_hit: dict | None, bull_bear_results: dict, judge_results: dict, bb_batch_id: str | None, judge_batch_id: str | None,
    precomputed: tuple | None = None, skip_reason: str | None = None,
    series_comunes: dict | None = None,
    skip_reason_regla: str | None = ...,
    modelo_omitido: str = SKIPPED_MODEL_VERSION,
) -> None:
    """skip_reason: motivo de NO_TRADE de la decisión con IA cuando no se
    llamó a la IA. skip_reason_regla: el de la regla sin IA y el control
    (por defecto el mismo; distinto para los eventos anteriores al corte,
    donde la IA no opera pero la regla sí decide). modelo_omitido: lo que se
    guarda como modelo cuando no se llamó a la IA."""
    event_id = ev["event_id"]
    if skip_reason_regla is ...:
        skip_reason_regla = skip_reason

    # --- Etapa 1: enrichment (SIEMPRE fresco — ver docstring del módulo) ---
    # --- Etapa 2: novelty (SIEMPRE fresco) ---
    # Si process_chunk ya las calculó para decidir el pre-filtro de
    # condiciones objetivas (ver su docstring), se reutilizan aquí en vez de
    # repetir las mismas consultas a Postgres — no cambia el resultado, solo
    # evita el trabajo duplicado. Los cache_hits no pasan por ese pre-filtro
    # (no lo necesitan, no van a llamar al LLM), así que para ellos se
    # calculan aquí igual que siempre.
    if precomputed is not None:
        enrichment, novelty, is_fda_crl_without_8k = precomputed
    else:
        enrichment = fetch_and_compute_enrichment(conn, ev, series_comunes)
        # Fase 3: has_prior_guidance/rumor_flag ya no son siempre None — se
        # calculan sobre filing_text de eventos previos del mismo ticker (ver
        # guidance_detector.py). Si esos filings aún no tienen texto extraído,
        # compute_novelty_signals devuelve None y compute_novelty renormaliza
        # pesos igual que antes (comportamiento sin cambios en ese caso).
        has_guidance, rumor_flag = compute_novelty_signals(conn, ev["ticker"], ev["d0_close_date"])
        novelty = compute_novelty(
            NoveltyInputs(
                pre_event_drift_pct=enrichment.pre_event_drift_pct or 0.0,
                has_prior_guidance=has_guidance,
                rumor_flag=rumor_flag,
            )
        )
        is_fda_crl_without_8k = check_fda_crl_without_8k(conn, ev["cik"], ev["event_class"], ev["d0_close_date"])

    # --- Etapas 3-5: Bull/Bear/Judge (de caché, de LLM, o descartado por una condición objetiva) ---
    from_cache = cache_hit is not None
    if from_cache:
        bull_output = cache_hit["bull_analyst_output"]
        bear_output = cache_hit["bear_analyst_output"]
        judge_output = cache_hit["judge_output"]
        net_conviction = float(cache_hit["net_conviction"])
        confidence_in_conviction = float(cache_hit["confidence_in_conviction"])
        model_bull_bear = cache_hit["model_version_bull_bear"]
        model_judge = cache_hit["model_version_judge"]
        batch_bb, batch_judge = None, None
    elif skip_reason is not None:
        # No se invocó Bull/Bear/Judge para este evento: una de las 5
        # condiciones objetivas de abstention_engine.objective_no_trade_reason
        # ya dispara NO_TRADE en las 3 estrategias sin mirar
        # net_conviction/confidence en absoluto. Un net_conviction=0/
        # confidence=0 aquí no cambia el veredicto final, solo dice
        # explícitamente "no se gastó IA en este evento" en vez de simular
        # una opinión que nunca se le pidió al modelo.
        placeholder_reason = f"{skip_reason} — NO_TRADE garantizado, no se invoca el debate de IA"
        bull_output = {"skipped_no_llm_needed": True, "reason": placeholder_reason}
        bear_output = {"skipped_no_llm_needed": True, "reason": placeholder_reason}
        judge_output = {"skipped_no_llm_needed": True, "reason": placeholder_reason}
        net_conviction = 0.0
        confidence_in_conviction = 0.0
        model_bull_bear = modelo_omitido
        model_judge = modelo_omitido
        batch_bb, batch_judge = None, None
    else:
        bull_output = bull_bear_results.get(custom_id_de(event_id, "bull"))
        bear_output = bull_bear_results.get(custom_id_de(event_id, "bear"))
        judge_id = custom_id_de(event_id, "judge")
        judge_output = validar_salida_judge(judge_results.get(judge_id), judge_id)
        if not (bull_output and bear_output and judge_output):
            logger.warning("Evento %d sin Bull/Bear/Judge completo tras el batch — se omite", event_id)
            return
        net_conviction = float(judge_output["net_conviction"])
        confidence_in_conviction = float(judge_output["confidence_in_conviction"])
        model_bull_bear = config.ANALYZER_MODEL
        model_judge = config.JUDGE_MODEL
        batch_bb, batch_judge = bb_batch_id, judge_batch_id

    # --- Etapa 6: impact estimation (SIEMPRE fresco — depende de as_of_date) ---
    impact = estimate_impact_for_event(conn, ev["event_class"], ev["d0_close_date"], event_id, window_days=20)

    # --- Etapas 7-8: EV y abstención (is_fda_crl_without_8k ya calculado arriba) ---
    # La MISMA función decide con IA y sin IA (grupo de control): la única
    # diferencia entre ambas es de dónde sale net_conviction.
    abstention_inputs = AbstentionInputs(
        novelty_score=novelty.score,
        confidence_in_conviction=confidence_in_conviction,
        net_conviction=net_conviction,
        ev_by_strategy={},
        had_survivorship_warning=enrichment.had_survivorship_warning,
        beta_available=enrichment.beta_vs_spy is not None,
        adv_usd_60d=enrichment.adv_usd_60d,
        is_fda_crl_without_8k=is_fda_crl_without_8k,
    )
    ev_result, decisions = evaluar_decision(abstention_inputs, impact, skip_reason)
    control = decision_sin_ia(abstention_inputs, impact, skip_reason_regla)

    # Etapa 1 guardada (H-23): la leen el plan técnico (VIX) y la sensibilidad.
    guardar_enrichment(conn, event_id, enrichment)
    _store_event_analysis(
        conn, event_id, novelty, bull_output, bear_output, judge_output,
        net_conviction, confidence_in_conviction, impact, ev_result, decisions,
        model_bull_bear, model_judge, batch_bb, batch_judge, from_cache, control,
    )


def evaluar_decision(entradas: AbstentionInputs, impact, skip_reason: str | None):
    """Etapas 7-8: EV y abstención de las 3 estrategias a partir de
    `entradas` (net_conviction, confidence_in_conviction y el resto de
    insumos) y de la Etapa 6. La usan tanto la decisión con IA como el grupo
    de control, para que ninguna fórmula, umbral o regla pueda divergir entre
    las dos. Devuelve (EVResult, {estrategia: AbstentionDecision})."""
    ev_result = compute_ev(
        entradas.net_conviction, entradas.confidence_in_conviction, impact.expected_magnitude_pct, impact.confidence
    )
    entradas = replace(
        entradas,
        ev_by_strategy={"CONSERVATIVE": ev_result.ev_conservative, "BALANCED": ev_result.ev_balanced, "AGGRESSIVE": ev_result.ev_aggressive},
    )
    if skip_reason is not None:
        # El evento se descartó ANTES de la IA por una regla objetiva. Sin
        # esto, decide_all_strategies evaluaba la regla 2 (confidence=0 < 40)
        # antes que la objetiva y guardaba "no sabemos qué pasa" como motivo
        # en vez del real (BUGS_REPORT.md H-38: 188 de 200 en producción).
        decisions = {
            strategy: AbstentionDecision("NO_TRADE", skip_reason, entradas.confidence_in_conviction)
            for strategy in STRATEGIES
        }
    else:
        decisions = decide_all_strategies(entradas)
    return ev_result, decisions


# Eventos anteriores al corte de los modelos (H-06): no se manda a la IA, se
# guarda una fila con la decisión de la IA en NO_TRADE por este motivo y la
# regla sin IA calculada, para el backtest histórico (el modelo guardado es
# adversarial_analyzer.MODELO_ANTES_DEL_CORTE).
MOTIVO_ANTES_DEL_CORTE = "anterior a la fecha de corte de los modelos: la IA no se usa (solo la regla sin IA)"

METODO_SIN_IA = "analogos_signo_v3"
# Confianza de la regla histórica (ver regla_historica): fija, sin la IA.
CONFIANZA_REGLA_HISTORICA = 100.0


def decision_sin_ia(con_ia: AbstentionInputs, impact, skip_reason: str | None) -> dict:
    """Grupo de control: la decisión que se habría tomado SIN la dirección
    de la IA.

    Pasa por evaluar_decision, exactamente igual que la decisión real; lo
    ÚNICO que cambia es net_conviction, que sale de los análogos en lugar del
    debate: +1 / -1 según el signo del CAR esperado, o 0 si está a menos de
    0,1 % de cero (ImpactEstimate.expected_direction). Todo lo demás, incluida
    confidence_in_conviction, es lo mismo que recibió la decisión con IA
    (decisión del usuario, auditoría 2026-10-05): así la comparación mide si
    la DIRECCIÓN de la IA aporta, sin ventaja de construcción para ninguna.

    `metodo` identifica la regla; los ingredientes se guardan en bruto para
    poder recalcular con otra regla sin volver a analizar."""
    net = float(impact.expected_direction)
    ev, decisiones = evaluar_decision(replace(con_ia, net_conviction=net), impact, skip_reason)
    return {
        "metodo": METODO_SIN_IA,
        "net_conviction": net,
        "confidence_in_conviction": float(con_ia.confidence_in_conviction),
        "expected_magnitude_pct": round(float(impact.expected_magnitude_pct), 4),
        "impact_confidence": float(impact.confidence),
        "n_analogues": impact.n_analogues,
        "ev_conservative": ev.ev_conservative,
        "ev_balanced": ev.ev_balanced,
        "ev_aggressive": ev.ev_aggressive,
        "decisiones": abstention_as_json(decisiones),
        "regla_historica": regla_historica(con_ia, impact, skip_reason),
    }


def regla_historica(con_ia: AbstentionInputs, impact, skip_reason: str | None) -> dict:
    """La regla que mide el backtest histórico (BUGS_REPORT.md H-06): sin
    NADA de la IA. Antes del corte de entrenamiento de los modelos, la IA pudo
    haber leído qué pasó después del evento, y eso vale tanto para su
    dirección como para su confianza; así que aquí net_conviction es el signo
    de los análogos (como en el control) y confidence_in_conviction es 100,
    un factor neutro: el EV queda signo × magnitud × confianza de los
    análogos, sin contar dos veces la de los análogos (decisión del usuario,
    auditoría 2026-10-06). Pasa por evaluar_decision, como las otras dos."""
    net = float(impact.expected_direction)
    entradas = replace(con_ia, net_conviction=net, confidence_in_conviction=CONFIANZA_REGLA_HISTORICA)
    ev, decisiones = evaluar_decision(entradas, impact, skip_reason)
    return {
        "net_conviction": net,
        "confidence_in_conviction": CONFIANZA_REGLA_HISTORICA,
        "ev_conservative": ev.ev_conservative,
        "ev_balanced": ev.ev_balanced,
        "ev_aggressive": ev.ev_aggressive,
        "decisiones": abstention_as_json(decisiones),
    }


def backfill_decision_sin_ia(conn, limit: int = 1000, fallidos: set | None = None) -> int:
    """Calcula el grupo de control de los análisis guardados antes de que
    existiera, o con una regla anterior (`metodo` distinto del actual), sin
    llamar a la IA: la decisión sin IA
    solo usa Postgres (análogos, enrichment, novelty ya guardada).

    Diferencia con el control calculado en el momento: los análogos se
    vuelven a leer hoy con as_of = D0. Sigue sin haber look-ahead (solo
    eventos cuya ventana terminó antes de D0), pero puede haber más CAR de
    entonces calculados después. Se marca con "recalculado": true.

    `fallidos`: event_id que ya fallaron en esta corrida; no se vuelven a
    pedir y se añaden los que fallen ahora (ver rellenar_regla_sin_ia)."""
    excluir = list(fallidos or ())
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.cik, e.ticker, e.event_class, e.d0_close_date, u.sic_code,
                   ea.novelty_score, ea.net_conviction, ea.confidence_in_conviction,
                   ea.model_version_bull_bear, ea.abstention_decision,
                   ea.decision_sin_ia->>'metodo' AS metodo
            FROM event_analyses ea
            JOIN events e ON e.event_id = ea.event_id
            JOIN universe u ON u.cik = e.cik
            WHERE (ea.decision_sin_ia IS NULL OR ea.decision_sin_ia->>'metodo' IS DISTINCT FROM %s
                   OR NOT EXISTS (SELECT 1 FROM event_enrichment ee WHERE ee.event_id = ea.event_id))
              AND NOT (ea.event_id = ANY(%s))
            ORDER BY e.event_id
            LIMIT %s
            """,
            (METODO_SIN_IA, excluir, limit),
        )
        filas = cur.fetchall()
    series_comunes: dict = {}
    hechos = 0
    for f in filas:
        try:
            skip_reason = None
            if f["model_version_bull_bear"] == SKIPPED_MODEL_VERSION:
                skip_reason = ((f["abstention_decision"] or {}).get("BALANCED") or {}).get("reason_if_no_trade") or "descartado antes de la IA"
            enrichment = fetch_and_compute_enrichment(conn, f, series_comunes)
            if f["metodo"] == METODO_SIN_IA:
                # Solo le faltaba el enrichment (H-23): el control calculado
                # en su momento no se toca.
                guardar_enrichment(conn, f["event_id"], enrichment)
                conn.commit()
                hechos += 1
                continue
            impact = estimate_impact_for_event(conn, f["event_class"], f["d0_close_date"], f["event_id"], window_days=20)
            fda_sin_8k = check_fda_crl_without_8k(conn, f["cik"], f["event_class"], f["d0_close_date"])
            if f["model_version_bull_bear"] == MODELO_ANTES_DEL_CORTE:
                # La decisión guardada es «antes del corte»; la regla se decide
                # con las condiciones objetivas, como en process_chunk.
                skip_reason = objective_no_trade_reason(
                    novelty_score=float(f["novelty_score"]),
                    had_survivorship_warning=enrichment.had_survivorship_warning,
                    beta_available=enrichment.beta_vs_spy is not None,
                    adv_usd_60d=enrichment.adv_usd_60d,
                    is_fda_crl_without_8k=fda_sin_8k,
                ) or ev_ceiling_no_trade_reason(impact.expected_magnitude_pct, impact.confidence)
            con_ia = AbstentionInputs(
                novelty_score=float(f["novelty_score"]),
                confidence_in_conviction=float(f["confidence_in_conviction"]),
                net_conviction=float(f["net_conviction"]),
                ev_by_strategy={},
                had_survivorship_warning=enrichment.had_survivorship_warning,
                beta_available=enrichment.beta_vs_spy is not None,
                adv_usd_60d=enrichment.adv_usd_60d,
                is_fda_crl_without_8k=fda_sin_8k,
            )
            control = decision_sin_ia(con_ia, impact, skip_reason)
            control["recalculado"] = True
            guardar_enrichment(conn, f["event_id"], enrichment)  # H-23: los análisis antiguos no lo tenían
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE event_analyses SET decision_sin_ia = %s WHERE event_id = %s",
                    (json.dumps(control), f["event_id"]),
                )
            conn.commit()
            hechos += 1
        except Exception:
            logger.exception("No se pudo calcular el control sin IA del evento %d", f["event_id"])
            conn.rollback()
            if fallidos is not None:
                fallidos.add(f["event_id"])
    if hechos:
        logger.info("Grupo de control calculado para %d análisis antiguos", hechos)
    return hechos


def cobertura_regla_sin_ia(conn) -> dict:
    """Cuántos análisis tienen ya la regla del método actual. El backtest
    histórico solo ve los que la tienen (H-06): si faltan, es parcial."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS analisis, "
            "count(*) FILTER (WHERE decision_sin_ia->>'metodo' = %s) AS con_regla FROM event_analyses",
            (METODO_SIN_IA,),
        )
        fila = cur.fetchone()
    return {"analisis": fila["analisis"], "con_regla": fila["con_regla"], "pendientes": fila["analisis"] - fila["con_regla"]}


def rellenar_regla_sin_ia(conn, max_segundos: float = 600, tanda: int = 500) -> dict:
    """Rellena el control y la regla sin IA de todos los análisis pendientes,
    por tandas, hasta acabar o agotar `max_segundos`. No llama a la IA, así
    que no depende de ANTHROPIC_API_KEY: lo lanza el paso de backtest antes
    de simular. Un análisis que falla no se reintenta en esta corrida (no
    tapona la cola); queda pendiente y se cuenta en la cobertura."""
    inicio = time.monotonic()
    fallidos: set = set()
    hechos = 0
    while time.monotonic() - inicio < max_segundos:
        antes = len(fallidos)
        n = backfill_decision_sin_ia(conn, limit=tanda, fallidos=fallidos)
        hechos += n
        if n == 0 and len(fallidos) == antes:
            break
    cobertura = cobertura_regla_sin_ia(conn)
    if cobertura["pendientes"]:
        logger.warning(
            "Regla sin IA: %d de %d análisis siguen sin ella (%d fallaron en esta corrida); el backtest histórico será parcial",
            cobertura["pendientes"], cobertura["analisis"], len(fallidos),
        )
    return {**cobertura, "rellenados": hechos, "fallidos": len(fallidos)}


def _store_event_analysis(conn, event_id, novelty, bull_output, bear_output, judge_output,
                           net_conviction, confidence_in_conviction, impact, ev_result, decisions,
                           model_bull_bear, model_judge, batch_bb, batch_judge, from_cache,
                           control: dict | None = None) -> None:
    import json as _json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge,
                batch_id_bull_bear, batch_id_judge, from_cache, decision_sin_ia
            ) VALUES (
                %(event_id)s, %(novelty_score)s, %(novelty_reasoning)s, %(bull)s, %(bear)s,
                %(judge)s, %(net_conviction)s, %(confidence)s, %(impact)s,
                %(n_analogues)s, %(ev_calc)s, %(ev_cons)s, %(ev_aggr)s, %(ev_bal)s,
                %(abstention)s, %(td_cons)s, %(td_aggr)s, %(td_bal)s, %(model_bb)s, %(model_j)s,
                %(batch_bb)s, %(batch_j)s, %(from_cache)s, %(sin_ia)s
            )
            ON CONFLICT (event_id) DO UPDATE SET
                novelty_score = EXCLUDED.novelty_score, novelty_reasoning = EXCLUDED.novelty_reasoning,
                bull_analyst_output = EXCLUDED.bull_analyst_output, bear_analyst_output = EXCLUDED.bear_analyst_output,
                judge_output = EXCLUDED.judge_output, net_conviction = EXCLUDED.net_conviction,
                confidence_in_conviction = EXCLUDED.confidence_in_conviction, impact_estimation = EXCLUDED.impact_estimation,
                n_historical_analogues = EXCLUDED.n_historical_analogues, ev_calculation = EXCLUDED.ev_calculation,
                ev_conservative = EXCLUDED.ev_conservative, ev_aggressive = EXCLUDED.ev_aggressive,
                ev_balanced = EXCLUDED.ev_balanced, abstention_decision = EXCLUDED.abstention_decision,
                trade_decision_conservative = EXCLUDED.trade_decision_conservative,
                trade_decision_aggressive = EXCLUDED.trade_decision_aggressive,
                trade_decision_balanced = EXCLUDED.trade_decision_balanced,
                decision_sin_ia = EXCLUDED.decision_sin_ia, analyzed_at = now()
            """,
            {
                "event_id": event_id,
                "novelty_score": novelty.score,
                "novelty_reasoning": _json.dumps(novelty.as_json()),
                "bull": _json.dumps(bull_output),
                "bear": _json.dumps(bear_output),
                "judge": _json.dumps(judge_output),
                "net_conviction": net_conviction,
                "confidence": confidence_in_conviction,
                "impact": _json.dumps(impact.as_json()),
                "n_analogues": impact.n_analogues,
                "ev_calc": _json.dumps(ev_result.as_json()),
                "ev_cons": ev_result.ev_conservative,
                "ev_aggr": ev_result.ev_aggressive,
                "ev_bal": ev_result.ev_balanced,
                "abstention": _json.dumps(abstention_as_json(decisions)),
                "td_cons": decisions["CONSERVATIVE"].trade_decision,
                "td_aggr": decisions["AGGRESSIVE"].trade_decision,
                "td_bal": decisions["BALANCED"].trade_decision,
                "model_bb": model_bull_bear,
                "model_j": model_judge,
                "batch_bb": batch_bb,
                "batch_j": batch_judge,
                "from_cache": from_cache,
                "sin_ia": _json.dumps(control) if control is not None else None,
            },
        )
    conn.commit()


def run_pipeline(
    conn,
    client,
    max_chunks: int | None = None,
    *,
    max_events: int | None = None,
    min_market_cap: float | None = None,
    require_text: bool = False,
    require_d0_bar: bool = False,
) -> tuple[int, object]:
    """Bucle principal: procesa hasta que no queden eventos pendientes, o
    hasta el tope de eventos (tope de gasto).

    Devuelve (nº total de eventos procesados, la conexión vigente). Esta
    última puede NO ser la que se pasó como argumento: process_chunk
    reconecta si la espera de la Batch API deja la conexión muerta (ver su
    docstring), y ese cambio tiene que propagarse — el caller no puede
    seguir usando la conexión original a ciegas después de llamar a esto.

    Cada evento se intenta UNA vez por corrida. Antes, un evento cuyo
    Bull/Bear/Judge volvía incompleto (request errored/expired, JSON inválido)
    no se guardaba, así que la consulta siguiente lo devolvía otra vez, y
    otra: un bucle infinito que además re-pagaba el batch en cada vuelta
    (pasó de verdad: el run 34970097017 mandó el mismo chunk 9 veces hasta
    agotar el saldo). Los fallidos se reintentan en la corrida siguiente."""
    total = 0
    chunks_done = 0
    attempted: set[int] = set()
    # Solo eventos posteriores al corte de los modelos (H-06): los anteriores
    # no se mandan a la IA (ver analizar_antes_del_corte).
    inicio_ia = config.AI_VALIDATION_START
    if inicio_ia is None:
        logger.warning("Algún modelo configurado no tiene fecha de corte (config.MODEL_TRAINING_CUTOFF): no se llama a la IA")
        return 0, conn
    while max_chunks is None or chunks_done < max_chunks:
        limit = CHUNK_SIZE if max_events is None else min(CHUNK_SIZE, max_events - total)
        if limit <= 0:
            logger.info("Tope de %d eventos por corrida alcanzado — el resto queda en cola", max_events)
            break
        batch = fetch_events_needing_analysis(
            conn, limit, min_market_cap=min_market_cap, require_text=require_text,
            require_d0_bar=require_d0_bar, exclude_ids=attempted, d0_desde=inicio_ia,
        )
        if not batch:
            break
        attempted.update(ev["event_id"] for ev in batch)
        conn = process_chunk(conn, client, batch)
        total += len(batch)
        chunks_done += 1
    return total, conn


def analizar_antes_del_corte(conn, max_segundos: float = 300, min_market_cap: float | None = None) -> int:
    """Eventos con D0 anterior al corte de los modelos (H-06; decisión del
    usuario, 2026-10-06): no se mandan a la IA, que pudo haber leído qué pasó
    y no se puede validar con ellos. Se guarda su fila de análisis con la
    regla sin IA, gratis, para que entren en el backtest histórico.

    Misma cola que la IA (universo invertible desde ANALYSIS_MIN_MARKET_CAP_USD
    y barra de D0), sin exigir el texto del filing, que solo usa la IA. No
    depende de ANTHROPIC_API_KEY: lo lanza el paso de backtest, por tandas
    hasta acabar o agotar `max_segundos`. Devuelve cuántos guardó."""
    min_market_cap = config.ANALYSIS_MIN_MARKET_CAP_USD if min_market_cap is None else min_market_cap
    inicio_ia = config.AI_VALIDATION_START
    if inicio_ia is None:
        # Sin corte conocido no se sabe qué eventos son anteriores: guardarlos
        # como «antes del corte» los dejaría fuera de la IA para siempre.
        logger.warning("Algún modelo configurado no tiene fecha de corte: no se guarda ningún evento como anterior al corte")
        return 0
    inicio = time.monotonic()
    intentados: set[int] = set()
    while time.monotonic() - inicio < max_segundos:
        tanda = fetch_events_needing_analysis(
            conn, CHUNK_SIZE, min_market_cap=min_market_cap, require_d0_bar=True,
            exclude_ids=intentados, d0_antes_de=inicio_ia,
        )
        if not tanda:
            break
        intentados.update(ev["event_id"] for ev in tanda)
        conn = process_chunk(conn, None, tanda)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM event_analyses WHERE model_version_bull_bear = %s AND event_id = ANY(%s)",
            (MODELO_ANTES_DEL_CORTE, list(intentados)),
        )
        guardados = cur.fetchone()["n"]
    if intentados:
        logger.info("Anteriores al corte: %d de %d eventos guardados con la regla sin IA", guardados, len(intentados))
    return guardados


# Motivos de descarte pre-IA que ya no se producen o ya no son válidos, y
# cuyos eventos deben volver a la cola. Prefijos del texto guardado en
# bull_analyst_output->>'reason' (ver _process_single_event, rama skip_reason):
#  - "sin datos de high/low": el evento se analizó antes de existir su barra
#    de D0 (BUGS_REPORT.md H-39). La cola ya exige esa barra.
#  - "proxy de spread": el rango diario high-low de D0 se usaba como spread
#    con un techo del 0,5 %, que no pasaba prácticamente ningún evento real
#    (H-08). La liquidez se mide ahora solo por ADV.
# Estas filas no costaron nada (no se llamó a la IA): borrarlas solo hace que
# el evento se vuelva a evaluar con las reglas actuales.
OBSOLETE_SKIP_REASON_PREFIXES = ("sin datos de high/low", "proxy de spread")


def requeue_obsolete_skips(conn) -> int:
    """Borra de event_analyses los descartes pre-IA con un motivo de
    OBSOLETE_SKIP_REASON_PREFIXES para que vuelvan a la cola. Idempotente:
    tras la primera pasada no queda ninguno (la cola ya no los produce).

    También las filas «anteriores al corte» (H-06) cuyo D0 ya no es anterior
    al corte vigente, si este se movió hacia atrás: sin esto no volverían
    nunca a la IA. Son filas sin IA y gratis de recalcular."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM event_analyses
            WHERE model_version_bull_bear = 'SKIPPED_OBJECTIVE_NO_TRADE'
              AND bull_analyst_output->>'reason' LIKE ANY(%s)
            """,
            ([f"{p}%" for p in OBSOLETE_SKIP_REASON_PREFIXES],),
        )
        n = cur.rowcount
        if config.AI_VALIDATION_START is not None:
            cur.execute(
                """
                DELETE FROM event_analyses ea USING events e
                WHERE e.event_id = ea.event_id AND ea.model_version_bull_bear = %s
                  AND e.d0_close_date >= %s
                """,
                (MODELO_ANTES_DEL_CORTE, config.AI_VALIDATION_START),
            )
            n += cur.rowcount
    conn.commit()
    return n


def _is_billing_error(exc: Exception) -> bool:
    """400 de la API por saldo insuficiente ("Your credit balance is too low").
    No es un bug del pipeline: es un estado de la cuenta."""
    import anthropic

    return isinstance(exc, anthropic.BadRequestError) and "credit balance" in str(exc).lower()


def compute_day3_stats(conn) -> dict:
    """Estadísticas de validación del día 3 (pedidas por el spec): % TRADE
    por versión de estrategia y promedios de novelty/EV/confidence.

    NO auto-ajusta los umbrales aunque el % esté fuera del rango 30-40%
    esperado — solo lo reporta con una recomendación textual. Auto-ajustar
    umbrales contra la salida de la MISMA corrida que se está evaluando es
    exactamente el tipo de sobreajuste que AUDIT_LEAN.md prohíbe (§9, T6:
    los umbrales son una decisión que se toma UNA VEZ, antes de mirar el
    resultado fuera de muestra, no una que se recalibra mirando el propio
    resultado)."""
    stats: dict = {}
    # Sin las filas anteriores al corte, que no pasaron por la IA (H-06).
    solo_cola = f"model_version_bull_bear <> '{MODELO_ANTES_DEL_CORTE}'"
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM event_analyses WHERE {solo_cola}")
        total = cur.fetchone()["n"]
        stats["total_analyzed"] = total

        cur.execute(f"SELECT avg(novelty_score) AS avg_novelty, avg(confidence_in_conviction) AS avg_confidence, avg(ev_balanced) AS avg_ev_balanced FROM event_analyses WHERE {solo_cola}")
        row = cur.fetchone()
        stats["avg_novelty_score"] = float(row["avg_novelty"]) if row["avg_novelty"] is not None else None
        stats["avg_confidence_in_conviction"] = float(row["avg_confidence"]) if row["avg_confidence"] is not None else None
        stats["avg_ev_balanced"] = float(row["avg_ev_balanced"]) if row["avg_ev_balanced"] is not None else None

        for strategy, column in [("CONSERVATIVE", "trade_decision_conservative"), ("AGGRESSIVE", "trade_decision_aggressive"), ("BALANCED", "trade_decision_balanced")]:
            cur.execute(f"SELECT count(*) AS n FROM event_analyses WHERE {column} != 'NO_TRADE' AND {solo_cola}")
            trade_count = cur.fetchone()["n"]
            pct = (trade_count / total * 100) if total else 0.0
            stats[f"pct_trade_{strategy.lower()}"] = pct
            if total > 0:
                if pct > 70:
                    stats[f"recommendation_{strategy.lower()}"] = f"% TRADE={pct:.1f}% > 70% — demasiado permisivo, considera subir el umbral de {strategy} en ev_engine.EV_THRESHOLDS (no auto-ajustado, ver docstring)"
                elif pct < 10:
                    stats[f"recommendation_{strategy.lower()}"] = f"% TRADE={pct:.1f}% < 10% — demasiado restrictivo, considera bajar el umbral de {strategy} (no auto-ajustado)"
                else:
                    stats[f"recommendation_{strategy.lower()}"] = f"% TRADE={pct:.1f}% dentro del rango esperado (10-70%, objetivo 30-40%)"

    return stats


# Cerrojo de Postgres para que la pasada de la IA y la prueba de humo no
# corran a la vez (Tanda 6): sin él, las dos podían coger los mismos eventos
# de la cola y pagarlos dos veces. Se libera solo al cerrar la conexión.
CERROJO_IA = 7_241_006


def tomar_cerrojo_ia(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (CERROJO_IA,))
        ok = bool(cur.fetchone()["ok"])
    conn.commit()
    return ok


def es_prueba_de_humo(argv: list[str]) -> bool:
    """`--smoke N` o `--smoke=N`. Cualquier otro argumento hace fallar el
    CLI: la pasada normal no acepta argumentos, y uno mal escrito no debe
    acabar lanzando la pasada completa (con su gasto)."""
    argumentos = argv[1:]
    if any(a == "--smoke" or a.startswith("--smoke=") for a in argumentos):
        return True
    if argumentos:
        raise SystemExit(f"Argumentos desconocidos: {' '.join(argumentos)} (¿querías --smoke N?)")
    return False


if __name__ == "__main__":
    import json
    import sys

    import anthropic

    # Prueba de humo con N eventos (Tanda 6): ver pipeline/analyze/smoke.py.
    if es_prueba_de_humo(sys.argv):
        import argparse

        _parser = argparse.ArgumentParser()
        _parser.add_argument("--smoke", type=int, required=True, help="eventos a analizar (máximo 20)")
        sys.argv = [sys.argv[0], "--n", str(_parser.parse_args().smoke)]
        import runpy

        runpy.run_module("pipeline.analyze.smoke", run_name="__main__")
        raise SystemExit(0)

    logging.basicConfig(level=logging.INFO)
    from pipeline import config
    from pipeline.db.connection import get_connection
    from pipeline.notify.gasto_notifier import avisar_si_gasto_alto

    # Comprobación por delante, antes de tocar la base de datos. Sin esto, la
    # falta de la clave sale como un TypeError desde las tripas del SDK
    # ("Could not resolve authentication method...") veinte líneas de traza más
    # abajo, que no dice qué hay que hacer ni quién tiene que hacerlo.
    if not config.ANTHROPIC_API_KEY:
        raise SystemExit(
            "Falta ANTHROPIC_API_KEY. Este paso es el único del pipeline que la\n"
            "necesita: es el que construye el debate Bull/Bear/Juez de cada evento.\n"
            "Se configura como secreto del repositorio en GitHub:\n"
            "  Settings > Secrets and variables > Actions > New repository secret\n"
            "  Nombre: ANTHROPIC_API_KEY\n"
            "Sin ella no se generan señales nuevas, pero el resto del pipeline\n"
            "(precios, CAR, backtest, validación, largo plazo) sigue funcionando."
        )

    conn = get_connection()
    requeued = requeue_obsolete_skips(conn)
    if requeued:
        print(f"Vueltos a la cola: {requeued} eventos descartados antes de la IA por un motivo que ya no aplica")
    backfill_decision_sin_ia(conn)
    cola = analysis_queue_summary(conn)
    print(
        f"Cola de la IA: {cola['pendientes']} eventos sin analizar; {cola['en_objetivo']} de "
        f"{cola['empresas']} empresas >= {cola['min_market_cap_usd'] / 1e6:,.0f} M$; "
        f"{cola['listos']} con texto y precio de D0, listos (~{cola['coste_estimado_listos_usd']} $). "
        f"Tope por corrida: {config.ANALYSIS_MAX_EVENTS_PER_RUN or 'sin tope'}."
    )

    # Tope de GASTO DIARIO ACUMULADO (IMPROVEMENT_PLAN.md A2) — distinto del
    # tope por corrida de arriba: ese no sabe cuánto han gastado YA las otras
    # corridas de hoy (nightly_pipeline.yml programa 3 al día). gasto_hoy_usd
    # cuenta lo ya incurrido; presupuesto_eventos es cuánto MÁS cabe hoy. El
    # tope efectivo de esta corrida es el más bajo de los dos.
    gasto_hoy_usd = spend_today_usd(conn)
    # Aviso por Telegram al 80 % del tope (Tanda 5), una vez al día.
    avisar_si_gasto_alto(conn, gasto_hoy_usd)
    presupuesto_eventos = remaining_daily_budget_events(conn)
    print(
        f"Gasto de hoy: ~{gasto_hoy_usd} $ de {config.DAILY_SPEND_CAP_USD:.2f} $ "
        f"({config.DAILY_SPEND_CAP_EUR:.2f} €/día). "
        f"Presupuesto restante hoy: {'sin tope' if presupuesto_eventos is None else f'{presupuesto_eventos} eventos'}."
    )
    if presupuesto_eventos == 0:
        print(
            "::warning title=Presupuesto diario agotado::Las corridas anteriores de hoy ya "
            f"gastaron ~{gasto_hoy_usd} $ de los {config.DAILY_SPEND_CAP_USD:.2f} $ del tope diario. "
            f"{cola['listos']} eventos siguen en cola; se retoma sin hacer nada más en cuanto haya "
            "presupuesto (mañana, o si se sube DAILY_SPEND_CAP_EUR)."
        )
        raise SystemExit(0)
    max_events = config.ANALYSIS_MAX_EVENTS_PER_RUN
    if presupuesto_eventos is not None:
        max_events = presupuesto_eventos if max_events is None else min(max_events, presupuesto_eventos)

    # api_key EXPLÍCITO: ver la nota en adversarial_analyzer.py. Sin esto, el
    # chequeo de arriba puede pasar (la variable existe) y aun así reventar
    # más abajo si el secreto trae un salto de línea, porque la librería sin
    # argumentos lee la variable de entorno tal cual, no la ya limpiada.
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    if not tomar_cerrojo_ia(conn):
        print(
            "::warning title=Análisis de la IA ocupado::Hay una prueba de humo u otra pasada analizando "
            "ahora mismo; esta pasada no analiza para no pagar dos veces los mismos eventos."
        )
        raise SystemExit(0)
    try:
        processed, conn = run_pipeline(
            conn,
            client,
            max_events=max_events,
            min_market_cap=config.ANALYSIS_MIN_MARKET_CAP_USD,
            require_text=True,
            require_d0_bar=True,
        )
    except Exception as exc:
        if not _is_billing_error(exc):
            raise
        # Sin saldo: la cola queda intacta (nada se escribe sin respuesta de
        # la IA) y se retoma sola la primera noche que la cuenta tenga fondos.
        # Anotación ::warning:: para que se vea en el resumen del run de
        # GitHub, pero sin tumbar el paso: no es un fallo del código.
        print(
            "::warning title=API de Anthropic sin saldo::La clave es válida pero la cuenta no tiene "
            f"crédito. {cola['listos']} eventos siguen en cola, listos para analizarse "
            f"(~{cola['coste_estimado_listos_usd']} $). Añade fondos en console.anthropic.com > Billing; "
            "la siguiente corrida los procesará sin hacer nada más."
        )
        raise SystemExit(0)
    print(f"Procesados {processed} eventos")
    avisar_si_gasto_alto(conn, spend_today_usd(conn))  # con lo gastado en esta corrida

    stats = compute_day3_stats(conn)
    print(json.dumps(stats, indent=2))
