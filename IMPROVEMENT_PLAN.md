# Plan de mejora — auditoría de código real (no de docs)

Fecha del análisis: 2026-09-28. Alcance: código en `pipeline/` y
`.github/workflows/nightly_pipeline.yml`, verificado línea a línea contra el
repo real (no contra `RUNBOOK.md`/`ARCHITECTURE_LEAN.md`). No repite los
puntos de la auditoría anterior ya en plan (split OOS, Benjamini-Hochberg,
bug `close_raw=NULL`, circuit-breaker, tope %ADV, VIX/sector_mood sin usar,
fallback de timestamp EDGAR, FRED, Form 4, 13F, short interest, impacto de
mercado, coste de gaps, memoria de tesis) salvo hallazgos nuevos sobre ellos.

## Resumen ejecutivo

El sistema es más sólido de lo que parece a primera vista: la disciplina
anti-look-ahead está bien enforced donde importa de verdad (entradas D+1,
`validate_no_lookahead`, `sample_split`), y los tests de las piezas puras
(cálculo de EV, calibración, `sample_split`, novelty) son genuinamente
rigurosos, no cosméticos. Pero hay tres problemas que pesan más que el
resto: **(1)** existen **tres sistemas de veredicto "¿invertir capital
real?" independientes y con umbrales distintos** que pueden discrepar en el
mismo reporte sin que nada lo explique; **(2)** el **CAR usado como insumo
de EV/análogos históricos se calcula sobre todo el histórico, sin respetar
el split IN_SAMPLE/OOS** que sí respeta el resto del pipeline; y **(3)** el
control de gasto de la API de Anthropic es solo un tope *por corrida*, sin
techo mensual acumulado ni un pre-filtro que aproveche todo lo que ya se
sabe sin coste (liquidez, deslistado, FDA sin 8-K) antes de invocar al LLM —
con 50 €/mes de presupuesto, esto es lo primero que hay que cerrar antes de
gastar un euro real. Hay también una pieza de código muerto significativa
(`backtester.py`: un motor de backtest completo, con sus propios umbrales,
que nada en producción llama) que conviene borrar antes de que alguien la
confunda con el camino real (`portfolio_simulator.py`).

## Tabla de puntos

| # | Título | Categoría | Evidencia | Impacto | Esfuerzo | Coste API | Dependencias |
|---|---|---|---|---|---|---|---|
| R1 | ~~CAR (insumo de EV/análogos) no respeta el split IN_SAMPLE/OOS~~ — **investigado y descartado (sesión 4, PR #19): NO es un riesgo real.** `compute_car()` es un estadístico point-in-time POR EVENTO (usa solo el `estimation_window` relativo al D0 de ESE evento, sin comparar entre eventos ni agregar nada) — no hay ningún "vistazo prematuro a OOS" posible aquí. La disciplina de partición SÍ se aplica donde corresponde: `event_study.py` (estadísticas agregadas por clase) y `historical_analogues.py` (point-in-time por `d0_close_date < as_of_date`, suficiente sea el evento IN_SAMPLE u OOS). Particionar `populate_car_results.py` habría sido contraproducente: habría resucitado el bug original que ese módulo cierra (`n_analogues=0` permanente para eventos OOS). Documentado en el docstring del módulo + test de regresión | Riesgo (descartado) | `pipeline/backtest/populate_car_results.py` (docstring ampliado) vs `portfolio_simulator.py`/`validation/event_study.py` (sample-aware por una razón distinta: agregan resultados, no point-in-time por evento) | Ninguno (era una falsa alarma) | 1 sesión (investigar) — sin sesión de implementación, no había nada que implementar | Ninguno | Ninguna |
| R2 | ~~Tres~~ **Dos** sistemas de veredicto "invertir o no", uno de ellos (el de PARTE 2) discrepando del oficial (PARTE 6) sin explicación, en el MISMO documento — **✅ resuelta (sesión 3, PR #18)**. Investigado antes de tocar código: `backtest/portfolio_report.py:37-39` (`MAX_DRAWDOWN_CEILING=0.25`, `MIN_TRADES_FOR_ANY_CONCLUSION=20`) NO es un descuido — es una decisión de diseño explícitamente documentada en `RUNBOOK.md` ("responde una pregunta más laxa: ¿esta corrida en concreto se ve bien?"), distinta a propósito de `decision.py:8-16` (GREENLIGHT, drawdown≤15%, n≥300); no se toca. El problema real era solo `validation/report.py:145` (`_backtest_table`, columna "Rec" de PARTE 2): un criterio ad hoc inline (`win_rate>0.55 and sharpe>1.0`, sin drawdown/calibración/n_trades) que podía mostrar "YES" mientras PARTE 6 mostraba "REDLIGHT" para la misma versión. Corregido: `_backtest_table` ahora reutiliza el mismo `decisions[version]` de PARTE 6, con test de regresión (`test_backtest_table_rec_column_matches_parte_6_decision`) que parsea el Markdown real y falla si alguien vuelve a bifurcar la lógica | Alto | 2 sesiones | Ninguno | Bloqueaba criterio "listo para gastar" — ya no |
| R3 | **✅ Hecha (PR #23, sesión 8).** Confirmado contra el código real: `backfill_range` no tenía ningún `rollback()`. Añadido en el mismo sitio y por el mismo motivo que `xbrl_fundamentals.py`; también cuenta días fallidos y reporta el primer error. `test_edgar_scraper.py` nuevo (no existía ningún test de `backfill_range`) | Riesgo | `pipeline/ingest/edgar_scraper.py:448-473` | Alto | 1 sesión | Ninguno | Ninguna |
| R4 | Sin techo de gasto mensual acumulado — solo hay tope por corrida (`ANALYSIS_MAX_EVENTS_PER_RUN`, 500/corrida por defecto) | Riesgo | `pipeline/config.py:79-85`; sin ninguna tabla/consulta que sume gasto acumulado del mes en curso (confirmado por grep de `MONTHLY`/`budget`/`spend` en `pipeline/`) | Alto | 1 sesión | Ninguno | Debe ir antes del piloto de 50 € |
| R5 | ~~Solo `novelty` se pre-filtra antes del LLM~~ — **✅ resuelta (sesión 5, PR #20).** Nueva `abstention_engine.objective_no_trade_reason()` agrupa novelty + las 4 reglas objetivas restantes (survivorship, `beta_available`, FDA CRL sin 8-K, iliquidez) en un solo pre-filtro pre-LLM, reutilizada por `process_chunk()` | Riesgo (resuelto) | `pipeline/analyze/abstention_engine.py` (`objective_no_trade_reason`) + `pipeline/analyze/event_analysis_pipeline.py` (`_process_single_event`, `skip_reason` generalizado) | Alto | 1 sesión | Ahorra API (directo, medible: hasta 5 de 7 reglas ahora gratis) | Ninguna |
| R6 | Sin `timeout-minutes` en ningún job del workflow + polling de la Batch API sin cota máxima de espera | Riesgo | `.github/workflows/nightly_pipeline.yml` (grep completo: cero hits de `timeout-minutes`); `pipeline/analyze/adversarial_analyzer.py:280-285` (`while True: ...; time.sleep(30)`, sin `max_wait`/reintentos) | Alto | 1 sesión | Ninguno (evita quemar horas de CI, no € de Anthropic) | Relacionado con R4 (visibilidad de gasto) |
| R7 | Llamadas HTTP sin retry/backoff en `fama_french.py` y `ticker_map.py`, a diferencia del resto del pipeline (`edgar_http.py`) | Riesgo | `pipeline/ingest/fama_french.py:70-71` (`requests.get` desnudo); `pipeline/ingest/ticker_map.py:45-46`; `ticker_map.resolve()` se invoca desde `upsert_universe_entries`/`upsert_events` para CADA filing, así que un fallo transitorio aquí tumba la ingesta EDGAR del día entero | Medio | 1 sesión | Ninguno | Ninguna |
| R8 | Constantes de `ev_engine.py` calibradas a mano, sin ajuste empírico ni test de sensibilidad sobre ellas mismas | Riesgo (a verificar) | `pipeline/analyze/ev_engine.py:34-46` (`_MAGNITUDE_MULTIPLIER`, `_SIZING_SCALE=5.0`, `_MAX_POSITION_SIZE_PCT`) — el propio docstring dice "calibración manual"; multiplica `confidence_in_conviction/100 * impact_confidence/100`, así que dos números moderados (ej. 60% y 60%) ya reducen el EV a 36% de su valor crudo | Medio | 1 sesión (medir con datos ya existentes, no tocar código todavía) | Ninguno | Depende de que haya backtest con histórico suficiente |
| R9 | `novelty_score` es en la práctica casi solo el componente de drift (50/25/25 es teórico, no real) porque `has_prior_guidance`/`rumor_flag` casi siempre son `None` hoy | Riesgo (a verificar) | `pipeline/analyze/novelty.py:20-23,63-64` (docstring lo admite explícitamente); `guidance_detector.get_prior_filing_texts` limita a `DEFAULT_LIMIT=10` filings (`guidance_detector.py:33,62-85`) sin marcar si el límite recortó la ventana | Medio | 1 sesión (medir qué % de eventos tiene guidance/rumor no-None en producción) | Ninguno | Ninguna |
| R10 | `quality_score.py`: dato ausente tratado como "favorable" en vez de "no computable", al revés del principio que el propio módulo dice seguir | Riesgo | `pipeline/analyze/quality_score.py:141` (`long_term_debt is None → debt=0.0`, sube el score de "solidez"); línea 157 (`capex is None → 0.0`, sube el "quality of earnings"); contradice el principio declarado en líneas 31-35 ("un componente que no se puede calcular se excluye") | Medio | 1 sesión | Ninguno | Ninguna |
| R11 | `fetch_annual_rows(as_of_date=None)` no aplica ningún filtro anti-look-ahead, y nada impide llamarla así por error desde un contexto de backtest | Riesgo | `pipeline/analyze/quality_score.py:395-416`; el propio comentario en `run_quality_screen` (líneas 304-309) reconoce que un test dedicado (`test_no_lookahead_guard.py`) ya cazó una vez esta clase de bug | Alto | 1 sesión | Ninguno | Ninguna |
| R12 | Estadísticos sobre muestras minúsculas se reportan como número, no se ocultan, aunque estén flageados como "insuficientes" | Riesgo | `pipeline/backtest/portfolio_metrics.py:183-190` (`sharpe_per_trade` se calcula con n≥2 y se sigue mostrando aunque `insufficient_sample=True`); `compute_prediction_regression` da R²=1.0 con n=2 sin aviso (`portfolio_metrics.py:284`) | Medio | 1 sesión | Ninguno | Ninguna |
| R13 | BALANCED queda excluido del análisis de sensibilidad (PARTE 5) aunque pueda ser la versión recomendada para capital real | Riesgo | `pipeline/backtest/sensitivity.py:170-197` (comentario líneas 172-174 excluye BALANCED "por legibilidad"); `validation/report.py` PARTE 6 (`overall_verdict`) puede elegir BALANCED como `best_version` sin que su sensibilidad a costes/latencia/VIX se haya probado nunca | Alto | 1 sesión | Ninguno | Depende de R2 (qué versión se recomienda) |
| R14 | `apply_latency_sensitivity` descarta trades sin precio D+2 sin loguear cuántos ni comparar tamaño de muestra entre escenarios | Riesgo | `pipeline/backtest/sensitivity.py:103-104` (`continue` silencioso); `_sensitivity_table` en `validation/report.py:153-177` no muestra `n_trades` por escenario aunque `summarize_scenario` sí lo calcula | Medio | 1 sesión | Ninguno | Ninguna |
| R15 | `split_by_vix_regime` con <4 trades con VIX devuelve listas vacías indistinguibles de "sin efecto VIX" en el reporte final | Riesgo | `pipeline/backtest/sensitivity.py:126-145` (umbral `len(with_vix)>=4`, línea 139); `_sensitivity_table` no muestra `n_missing_vix` | Bajo | 1 sesión (junto con R14) | Ninguno | Ninguna |
| R16 | (a verificar) `ev_engine.compute_ev` calcula el EV con el SIGNO de `net_conviction` (negativo para convicción bajista), pero `abstention_engine.decide_for_strategy` compara ese EV con signo contra un umbral SIEMPRE positivo (`EV_THRESHOLDS[strategy] + EV_ABSTENTION_BUFFER`) antes de decidir la dirección — una convicción SHORT fuerte (`net_conviction` muy negativo) produciría un EV muy negativo, que nunca cruzaría ese umbral positivo, así que la regla 3 podría estar vetando TODOS los SHORT sin importar la convicción. Encontrado incidentalmente en la sesión 6 (A1) al diseñar el "mejor caso" de EV para el corte anticipado — no verificado a fondo (podría haber un `abs()` o una convención de signo en otra parte que lo compense, o ser el comportamiento deliberado si el spec solo contempla EV como "magnitud a favor de la dirección elegida por el Judge") | Riesgo (sin confirmar) | `pipeline/analyze/ev_engine.py:102` (`_raw_point_estimate`, signo de `net_conviction` propagado) vs `pipeline/analyze/abstention_engine.py:179-188` (regla 3 de `decide_for_strategy`, umbral positivo) | Alto si se confirma (bloquearía toda la mitad SHORT de la estrategia) | 1 sesión (investigar primero) | Ninguno hasta confirmar | Ninguna |
| Q1 | `backtester.py` contiene un motor de backtest paralelo completo que nada en producción llama — con sus propios umbrales de estrategia, distintos de los reales | Quitar | `pipeline/backtest/backtester.py`: `run_backtest_for_event` (150-208), `decide_trade` (112-118), `compute_trade_return` (121-134), `summarize_run` (211-242), `BacktestTrade` (137-147), `STRATEGY_THRESHOLDS` (43-48) — cero llamadas fuera de `test_backtester.py`; `compute_car`/`CAREstimate`/`fit_factor_model` SÍ son código vivo (los usa `populate_car_results.py`) y no se tocan | Alto (simplifica y elimina una fuente de confusión sobre "cuál es el backtest real") | 2 sesiones (confirmar cero referencias + migrar/borrar tests que solo cubren lo muerto) | Ninguno | Ninguna — hacer temprano, antes de tocar R2 |
| Q2 | `pipeline/backtest/thesis_engine.REDUCE` (contradicción moderada) es una acción nueva, sin validar con datos reales, que añade un grado de libertad más | Quitar (a verificar) | `pipeline/backtest/thesis_engine.py` — `THESIS_MILD_CONTRADICTION_CONFIDENCE_FLOOR`/`THESIS_REDUCE_FRACTION`; `THESIS_MEMORY_ENABLED=False` en `config.py`, sin datos de backtest todavía que muestren que REDUCE aporta sobre un HOLD/SELL binario | Bajo (autor: yo mismo, self-crítica) | 1 sesión (revisar tras el primer backtest con memoria activada) | Ninguno | Depende de que la memoria de tesis se ejecute al menos una vez en backtest |
| Q3 | DYNAMIC como 4ª versión de cartera: ¿aporta sobre BALANCED con evidencia real, o es un grado de libertad sin validar? | Quitar (a verificar) | `pipeline/backtest/portfolio_strategies.py:166-179` (`compute_ev_weighted_position_size_pct`), `portfolio_simulator.VERSIONS` incluye 4 versiones que se corren, reportan y paper-tradean en paralelo cada semana | Medio | 1 sesión (comparar resultados reales BALANCED vs DYNAMIC en el histórico ya corrido) | Ninguno | Depende de tener ya corridas de ambas |
| Q4 | Duplicación de "normalización de CIK" (quitar leyendo tres cosas distintas y quedarse con una) | Quitar/Simplificar | `pipeline/ingest/edgar_scraper.py:167` (`cik.lstrip("0") or "0"`), `pipeline/ingest/ticker_map.py:70` (idéntico), `pipeline/ingest/xbrl_fundamentals.py:144-147` (`normalizar_cik`, más completo — quita prefijo "CIK" y mayúsculas) | Medio | 1 sesión | Ninguno | Ninguna |
| Q5 | Duplicación de la secuencia de backoff `[2,4,8,16]` en 2 sitios de `edgar_http.py` + una tercera reimplementación distinta en `yfinance_backfill.py` | Quitar/Simplificar | `pipeline/ingest/edgar_http.py:49` y `:93`; `pipeline/ingest/yfinance_backfill.py` (`BACKOFF_BASE_S * 2**attempt`) | Bajo | 1 sesión | Ninguno | Ninguna |
| Q6 | Caché en disco de `ticker_map.py` no aporta nada en el despliegue real (runner de GitHub Actions efímero, sin `actions/cache`, y `.gitignore`) | Quitar/Simplificar | `pipeline/ingest/ticker_map.py:9-10,24,53-63`; `.gitignore:10`; `nightly_pipeline.yml` sin ningún paso `actions/cache` para esa ruta | Bajo | 1 sesión (quitar la capa de disco y quedarse solo con la caché en memoria del proceso, más simple que montar `actions/cache` para esto) | Ninguno | Ninguna |
| Q7 | `test_ticker_map.py`: un test "prueba" una reimplementación copiada a mano, no la función real | Quitar/Simplificar (calidad de test) | `pipeline/tests/test_ticker_map.py:22-35` (`test_resolve_matches_edgar_zero_padded_cik_format` define y llama `resolve_against(...)` local, nunca `ticker_map.resolve`) | Medio (falso sentido de seguridad) | 1 sesión | Ninguno | Ninguna |
| Q8 | `git rev-parse` en `validation/report.py __main__` con `except Exception` amplio que cae a `"unknown"` sin garantía de unicidad del tag | Quitar/Simplificar | `pipeline/validation/report.py:500-504` | Bajo | 1 sesión (usar timestamp/UUID en vez de intentar leer git) | Ninguna |
| M1 | Batch polling sin reintentos ni cota máxima de espera (código, no solo el workflow de R6) | Mejorar | `pipeline/analyze/adversarial_analyzer.py:277-285` (`run_batch_and_collect`) | Alto | 1 sesión | Ninguno | Junto con R6 |
| M2 | Consulta N+1: `get_cached_analysis` se llama una vez por evento en un bucle en vez de una sola consulta por lote | Mejorar | `pipeline/analyze/event_analysis_pipeline.py:195-198` (`for ev in event_rows: cached = get_cached_analysis(...)`) | Bajo | 1 sesión | Ninguno | Ninguna |
| M3 | `THESIS_CONTRADICTION_CONFIDENCE_FLOOR`/`THESIS_MILD_CONTRADICTION_CONFIDENCE_FLOOR` en `config.py` duplican por VALOR (no por referencia) las constantes de `abstention_engine.py`, pese al comentario que dice "reutilizadas" | Mejorar | `pipeline/config.py:138-139` vs `pipeline/analyze/abstention_engine.py:58,68` — nada las importa, son literales independientes | Bajo | 1 sesión | Ninguno | Autocrítica de mi propio PR de memoria de tesis |
| M4 | `edgar_http.py` no respeta `Retry-After` en un 429 — usa siempre el backoff fijo | Mejorar | `pipeline/ingest/edgar_http.py:57-59,101-103` | Bajo | 1 sesión | Ninguno | Junto con Q5 |
| M5 | `PermanentHTTPError` (subclase de `RuntimeError`) se trata igual que un fallo transitorio en 2 sitios — se pierde la distinción que la propia excepción existe para preservar | Mejorar | `pipeline/ingest/edgar_scraper.py:374-379`; `pipeline/ingest/filing_text.py:164-169`; definición en `pipeline/ingest/edgar_http.py:25-33` | Medio | 1 sesión | Ninguno | Ninguna |
| M6 | `scrape_day`/`backfill_range` sin test directo de su propia lógica de conteo/branching (solo sus sub-funciones puras están testeadas) | Mejorar (tests) | `pipeline/ingest/edgar_scraper.py:358-467`; `pipeline/tests/test_edgar_parser.py` no las cubre | Medio | 1 sesión | Ninguno | Depende de R3 (test de regresión para el fix) |
| M7 | Cero cobertura de test en el camino de escritura a BD de `filing_text.py` (reintentos, `MAX_ATTEMPTS`, UPDATE) | Mejorar (tests) | `pipeline/ingest/filing_text.py:162-192`; `pipeline/tests/test_filing_text.py` solo prueba parsing puro | Medio | 1 sesión | Ninguno | Ninguna |
| M8 | Truncado de `filing_text` a `MAX_TEXT_CHARS` es un corte de caracteres sin respetar límites de frase, pese a que el propio código advierte del riesgo | Mejorar | `pipeline/ingest/filing_text.py:26-28,122-123` | Bajo | 1 sesión | Bajo (mejora la calidad del texto que se paga por analizar) | Ninguna |
| M9 | `fetch_ff3_daily`/`store_factors` sin test — justo la capa donde ya ocurrió un incidente real de 2h de colgado (INSERT de 25k filas) | Mejorar (tests) | `pipeline/ingest/fama_french.py:70-76,118-141`; `pipeline/tests/test_fama_french.py` solo cubre parseo de CSV | Medio | 1 sesión | Ninguno | Junto con R7 |
| M10 | `_download_one_with_retry`/`_flag_full_gap`/`_store_with_gap_detection` (yfinance) sin ningún test — es la etapa que el propio código llama "la más frágil del pipeline" | Mejorar (tests) | `pipeline/ingest/yfinance_backfill.py:121-182,322-390`; `test_yfinance_backfill.py` solo cubre los adaptadores puros | Alto | 2 sesiones | Ninguno | Ninguna |
| M11 | Docstring de `yfinance_backfill.py` afirma una caché HTTP en disco (`requests-cache`) que no existe en el código | Mejorar | `pipeline/ingest/yfinance_backfill.py:20` vs imports reales (sin `requests_cache` en todo el repo) | Bajo | 1 sesión (corregir el docstring, no el código) | Ninguno | Ninguna |
| M12 | `universe_maintenance.py` sin test dedicado que verifique el límite exacto de `MAX_PRICE_STALENESS_DAYS` | Mejorar (tests) | `pipeline/ingest/universe_maintenance.py:70-72`; `config.py:61`; `test_analysis_queue.py:113` solo prueba el caso normal | Medio | 1 sesión | Ninguno | Ninguna |
| M13 | `refresh_universe_metrics` recalcula el universo completo en las 3 pasadas diarias, no solo en la nocturna, sin justificación de tamaño registrada | Mejorar | `pipeline/ingest/universe_maintenance.py:94-122`; invocado sin `if:` restrictivo en `nightly_pipeline.yml:250` | Bajo | 1 sesión | Ninguno | Ninguna |
| M14 | El estilo de ejecución "AGGRESSIVE" de paper trading es un TP único (primer tramo del trailing), no el trailing-stop escalonado real del backtest | Mejorar | `pipeline/paper_trading/simulator.py:142-149` vs `portfolio_strategies.generate_trailing_stop_tiers()` | Medio (invalida la comparación paper-vs-backtest para AGGRESSIVE) | 1-2 sesiones | Ninguno | Ninguna |
| M15 | Telegram: sin retry/backoff en fallos transitorios, y una alerta ya "reclamada" (dedup) que falla al enviarse se pierde para siempre sin métrica que lo visibilice | Mejorar | `pipeline/notify/telegram.py:60-77`; `pipeline/notify/alerts_notifier.py:73-81` | Medio | 1 sesión | Ninguno | Junto con R6/A4 |
| M16 | Truncado de mensajes de Telegram por caracteres, sin awareness de HTML — puede cortar a mitad de una etiqueta y que la API rechace el mensaje entero | Mejorar | `pipeline/notify/telegram.py:43-46` | Bajo | 1 sesión | Ninguno | Ninguna |
| M17 | `fetch_pending_signals` hace `float(row[...])` sin guarda de NULL sobre columnas que el propio WHERE no garantiza pobladas juntas | Mejorar | `pipeline/notify/signals_notifier.py:31-51,73` | Bajo | 1 sesión | Ninguno | Ninguna |
| M18 | Indexado directo (`v["clave"]`, no `.get`) sobre JSONB leído de Postgres en varias funciones de reporte — un `KeyError` tumba la generación completa ante cualquier drift de esquema | Mejorar | `pipeline/validation/report.py:47-69` (`evaluate_all_versions_decision`), `:138-150`, `:201-215` | Medio | 1-2 sesiones | Ninguno | Ninguna |
| M19 | Duplicación de valor (no solo de concepto) del umbral "divergencia de win-rate" con dos números distintos | Mejorar | `pipeline/backtest/portfolio_validation.py:42` (`15.0` pp) vs `pipeline/paper_trading/report.py:25` (`30.0` pp) — la relajación para muestras semanales pequeñas está documentada, pero como constante desacoplada | Bajo | 1 sesión | Ninguno | Relacionado con R2 |
| M20 | Explosión de `annual_return` para curvas de equity muy cortas (sin piso mínimo de historia) | Mejorar | `pipeline/backtest/portfolio_metrics.py:110-113` | Bajo | 1 sesión | Ninguno | Ninguna |
| A1 | **✅ Hecha (PR #21, sesión 6).** Corte anticipado por "techo de EV alcanzable": la Etapa 6 (impact estimation, sin coste) se calcula en `process_chunk()` para los eventos que ya sobrevivieron las 5 condiciones objetivas de R5, antes de invocar Bull/Bear/Judge, y se descartan los que ni con el mejor caso posible (`net_conviction=+1.0`, `confidence=100`) superarían el umbral+buffer de EV de ninguna versión — nueva función `abstention_engine.ev_ceiling_no_trade_reason()` | Añadir | `pipeline/analyze/event_analysis_pipeline.py`, `pipeline/analyze/abstention_engine.py` | Alto | 1 sesión | Ahorra API (directo, medible) | Ninguna |
| A2 | **✅ Hecha (PR #22, sesión 7).** Tope de gasto DIARIO acumulado: `config.DAILY_SPEND_CAP_EUR` (50 €/mes ÷ 30 ≈ 1,67 €/día) convertido a `DAILY_SPEND_CAP_USD` con un tipo de cambio fijo deliberadamente conservador (`EUR_USD_RATE_CONSERVATIVE`, sin API de forex). Investigado primero: NO hacía falta una tabla nueva — `event_analyses` ya es el libro de cuentas (`spend_today_usd()` cuenta las filas de hoy con `from_cache=FALSE` y `model_version_bull_bear` real). `remaining_daily_budget_events()` calcula cuánto más cabe hoy; el bloque `__main__` usa el mínimo entre eso y `ANALYSIS_MAX_EVENTS_PER_RUN` | Añadir | `pipeline/config.py`, `pipeline/analyze/event_analysis_pipeline.py` (no se creó ninguna tabla — ver razón arriba) | Alto | 1 sesión | Ninguno (control de gasto, no gasto en sí) | Ninguna |
| A3 | Alerta de "el pipeline se rompió" distinta de las alertas de negocio (Telegram u otro canal) cuando un step crítico del cron falla | Añadir | Hoy solo hay notificación de señales/alertas de paper trading (`nightly_pipeline.yml:343-358`); nada avisa si `test`, "Ingesta EDGAR" o "Ingesta Fama-French" fallan | Alto | 1 sesión | Ninguno | Junto con R6 |
| A4 | `timeout-minutes` explícito por job/step en `nightly_pipeline.yml` | Añadir | Ninguno existe hoy (confirmado por grep completo del archivo) | Medio | 1 sesión | Ninguno | Junto con R6/A3 |
| A5 | Cap explícito de volumen/tiempo para el backfill de EDGAR vía `workflow_dispatch` (hoy `--start`/`--end` no tiene techo) | Añadir | `pipeline/ingest/edgar_scraper.py:448-467`; sin equivalente al `ANALYSIS_MAX_EVENTS_PER_RUN` de la Etapa de análisis | Bajo | 1 sesión | Ninguno | Ninguna |
| A6 | Test de integración real conectando `guidance_detector.compute_novelty_signals` → `compute_novelty`, no solo cada extremo por separado | Añadir (test) | `pipeline/analyze/novelty.py` + `guidance_detector.py`; hoy solo hay tests con `NoveltyInputs` construidos a mano | Bajo | 1 sesión | Ninguno | Ninguna |
| A7 | Confirmación/dry-run antes de un `--forzar` de `yfinance_backfill.py` (re-descarga completa de miles de tickers) | Añadir | `pipeline/ingest/yfinance_backfill.py:255-262,401-405` — sin cap ni conteo previo | Bajo | 1 sesión | Ninguno | Ninguna |

## Plan ordenado sesión a sesión

Cada fila = una sesión de 1-2h. El orden respeta: primero lo que evita que
el backtest mienta o que se queme saldo, luego lo que simplifica, luego lo
que añade señal. Los ítems marcados "a verificar" son sesiones de *medir*,
no de *tocar código*, salvo que la medición confirme el problema.

| Sesión | Punto(s) | Qué se hace |
|---|---|---|
| 1 | Q1 (parte 1/2) | **✅ Hecha.** Confirmado por grep exhaustivo: cero callers en producción de `run_backtest_for_event`/`decide_trade`/`compute_trade_return`/`summarize_run`/`BacktestTrade`/`STRATEGY_THRESHOLDS` (solo se llaman entre sí dentro de `backtester.py` y desde sus propios tests). `compute_car`/`CAREstimate`/`fit_factor_model` confirmados como código VIVO (usados por `populate_car_results.py`; `fit_factor_model` también de forma independiente por `enrichment.py`). Hallazgo adicional: el comentario de `portfolio_simulator.py:183` afirma que `gain_pct` "reutiliza" `compute_trade_return` — es falso, es una reimplementación independiente; corregir en la sesión 2. Decisión: borrar los 6 símbolos muertos + los tests que solo los cubren (`test_backtester.py`, aprox. líneas 35-186), conservando los tests de `compute_car` (líneas 189+) |
| 2 | Q1 (parte 2/2) | **✅ Hecha.** Borrado el código muerto de `backtester.py` (los 6 símbolos de la sesión 1) y los tests que solo lo cubrían en `test_backtester.py`; conservados `compute_car`/`CAREstimate`/`fit_factor_model` y sus tests (T2, réplica de efecto conocido + ventana insuficiente). Corregido el comentario falso de `portfolio_simulator.py:183`. Suite completa verificada en verde tras el borrado |
| 3 | R2 | **✅ Hecha (PR #18).** Investigado primero: `portfolio_report.py` vs `decision.py` es una divergencia INTENCIONAL y ya documentada en `RUNBOOK.md`, no se toca. El descuido real era solo `validation/report.py:145` — corregido para reutilizar el `decisions[version]` de PARTE 6 en vez de un criterio ad hoc propio; test de regresión añadido. Suite completa: 639 passed |
| 4 | R1 | **✅ Hecha (PR #19).** Investigado a fondo: no particionar `populate_car_results.py` es CORRECTO, no un descuido — `compute_car()` es point-in-time por evento, sin ninguna agregación cross-evento que pudiera filtrar información de OOS. Documentado en el docstring del módulo + test de regresión que fija el comportamiento. No se implementó ninguna partición porque habría sido incorrecta. Suite completa: 639 passed |
| 5 | R5 | **✅ Hecha (PR #20).** Pre-filtro pre-LLM extendido a las 4 reglas 100% objetivas (survivorship, FDA CRL sin 8-K, iliquidez, `beta_available`) + 12 tests nuevos. Suite completa: 650 passed. A1 (corte por techo de EV) NO se tocó en esta sesión — se hace entera en la sesión 6, no solo su segunda mitad, ya que R5 se resolvió sin necesidad de tocar el orden de las Etapas |
| 6 | A1 | **✅ Hecha (PR #21).** La Etapa 6 (impact estimation) se calcula en `process_chunk()` para los eventos que sobreviven las 5 condiciones objetivas de R5, antes del bloque LLM; nueva `abstention_engine.ev_ceiling_no_trade_reason()` descarta el evento si ni el mejor caso (`net_conviction=+1.0`, `confidence=100`) supera el umbral+buffer de EV de ninguna versión. Fixtures de `test_event_analysis_pipeline.py`/`test_analysis_queue.py` que no sembraban `car_results` quedaban con magnitud/confianza en 0 (correcto: con 0 análogos el EV real tras el Judge también sería 0) — se les añadió `_seed_car_results_for_class` para poder seguir ejercitando el camino de Bull/Bear/Judge donde el test lo pide. Hallazgo incidental sin verificar, añadido como R16: el EV con signo de `net_conviction` comparado contra un umbral siempre positivo podría estar vetando todos los SHORT (ver sesión 27). Suite completa: 655 passed |
| 7 | A2 | **✅ Hecha (PR #22).** Tope de gasto DIARIO acumulado (no mensual: más simple de aplicar día a día). Sin tabla nueva — `event_analyses` ya registra qué se gastó de verdad; `spend_today_usd()`/`remaining_daily_budget_events()` lo leen de ahí. 5 tests nuevos. Suite completa (sobre el tip sin sesiones 5/6, independiente de esas): 643 passed |
| 8 | R3 | **✅ Hecha (PR #23).** `conn.rollback()` añadido en `backfill_range` + test de regresión nuevo (mismo patrón que `xbrl_fundamentals.py`). Suite completa (sobre el tip sin sesiones 5/6/7, independiente de esas): 640 passed |
| 9 | R6 + A3 + A4 | `timeout-minutes` en el workflow + alerta de fallo de infraestructura distinta de la de negocio + cota máxima en el polling de la Batch API (M1) |
| 10 | R7 | Retry/backoff en `fama_french.py` y `ticker_map.py` |
| 11 | R11 | Cerrar el hueco de `fetch_annual_rows(as_of_date=None)` sin filtro anti-look-ahead |
| 12 | R10 | `quality_score.py`: tratar `long_term_debt`/`capex` ausentes como "no computable", no como cero |
| 13 | R13 + R14 + R15 | Incluir BALANCED en sensibilidad, loguear muestra descartada en latencia D+2, marcar explícitamente "sin datos VIX" vs "sin efecto VIX" |
| 14 | R12 + R9 (medir) | Ocultar (no solo flagear) estadísticos sobre n insuficiente; medir qué % de eventos reales tiene guidance/rumor no-None |
| 15 | R8 (medir) | Medir con el histórico ya corrido si las constantes de `ev_engine.py` producen EV razonables, sin tocar código todavía |
| 16 | Q4 + Q5 | Consolidar normalización de CIK y secuencia de backoff en un solo sitio cada una |
| 17 | Q6 + Q7 | Quitar la caché en disco inútil de `ticker_map.py`; quitar el intento de `git rev-parse` en `validation/report.py` |
| 18 | Q3 (medir) | Comparar resultados reales DYNAMIC vs BALANCED — decidir si DYNAMIC se queda, se pausa o se retira |
| 19 | M14 | Alinear (o documentar la divergencia de) el estilo AGGRESSIVE de paper trading con el trailing-stop real |
| 20 | M15 + M16 + M17 | Robustez de Telegram: retry en fallos transitorios, truncado HTML-aware, guarda de NULL en `signals_notifier` |
| 21 | M18 | Sustituir indexado directo por `.get()` con logging en los puntos de lectura de JSONB entre etapas |
| 22 | M4 + M5 | `Retry-After` en `edgar_http.py`; distinguir `PermanentHTTPError` de fallo transitorio en `edgar_scraper.py`/`filing_text.py` |
| 23 | M6 + M7 | Tests de `scrape_day`/`backfill_range` y del camino de escritura de `filing_text.py` |
| 24 | M9 + M10 (parte 1/2) | Tests de `store_factors`/`fetch_ff3_daily` y de `_download_one_with_retry` |
| 25 | M10 (parte 2/2) | Tests de `_flag_full_gap`/`_store_with_gap_detection` (yfinance) |
| 26 | Q2 (a verificar, tras piloto) | Revisar si `REDUCE` de la memoria de tesis aportó algo sobre HOLD/SELL binario, una vez haya datos |
| 27 | R16 (a verificar) | Investigar si el EV con signo de `net_conviction` realmente veta todos los SHORT contra el umbral positivo de `decide_for_strategy` — trazar un caso real de convicción SHORT fuerte de punta a punta (Judge → `compute_ev` → `decide_for_strategy`) antes de tocar nada. Si se confirma, es "Alto" y debería subir de prioridad, no quedarse al final de la cola |
| 28+ | M2, M3, M8, M11, M12, M13, M19, M20, A5, A6, A7 | Backlog de mejoras puntuales de bajo esfuerzo/bajo impacto, una por sesión, sin orden estricto |

## Criterio de "listo para gastar API" (antes del piloto de 50 €)

No lanzar gasto real de la API de Anthropic hasta que estén hechas, como
mínimo:

1. **R2** — un único criterio de veredicto, o al menos que los tres
   sistemas compartan las mismas constantes (para no invertir basándose en
   un "YES" que el propio sistema contradice en otra parte del mismo
   reporte).
2. **R1** — resuelto, o al menos el reporte avisa explícitamente si el CAR
   usado no respeta el split OOS.
3. **R5 + A1** — el pre-filtro completo (reglas objetivas + techo de EV)
   implementado: es la forma más directa de que el presupuesto de 50 €/mes
   rinda más análisis reales.
4. **A2** — tope de gasto mensual acumulado, probado (no solo el tope por
   corrida).
5. **R3** — corregido: que el pipeline no se caiga en producción de forma
   silenciosa y en cascada durante el piloto.
6. **R6 + A3 + A4** — timeouts y alerta de infraestructura: para enterarte
   si algo se rompe durante el piloto sin tener que vigilar la pestaña de
   Actions.
7. **Q1** hecho (código muerto fuera) — para que cualquier revisión de
   umbrales en el piloto se haga sobre el ÚNICO backtest real, sin el
   riesgo de mirar por error los umbrales de `backtester.py`.
8. Memoria de tesis: sigue con `THESIS_MEMORY_ENABLED=False` durante el
   piloto (ya decidido) — no bloquea, pero no se activa hasta tener datos
   del propio piloto sin memoria primero.
9. Una sesión de revisión manual: mirar a mano 20-30 señales generadas en
   modo barato/backfill antes de activar gasto real, como última
   comprobación de sentido común que ningún test automatizado sustituye.

## Nota sobre lo que ya está bien hecho (para no repetirlo en la tabla)

- Disciplina anti-look-ahead donde más importa: `validate_no_lookahead`
  contra Postgres real, `sample_split.py` con test de partición completa,
  entradas D+1 con `assert` explícito en `backtester.py` y en
  `portfolio_simulator.py`.
- Tests de `decision.py`, `sample_split.py`, `portfolio_metrics.py`
  (funciones puras) y `novelty.py`: rigurosos, con casos borde reales, no
  solo camino feliz.
- El patrón de aislar fallos por-entidad con `rollback()` explícito SÍ
  existe en el codebase (`xbrl_fundamentals.py`, `quality_score.py`) — el
  problema no es que falte el patrón, es que no se aplicó de forma
  consistente en todos los bucles que lo necesitan (ver R3).
- Cero `except:` desnudos, cero argumentos por defecto mutables, en los ~20
  archivos revisados a fondo para este análisis.
