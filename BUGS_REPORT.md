# BUGS_REPORT — Auditoría de MarketChange (pipeline Python)

Fecha: 2026-10-04 · Rama analizada: `claude/great-allen-nv7mdd` (= `claude/audit-free-system-j5kue7` + diagnóstico) · Sin cambios de código.

## 1. Resumen ejecutivo

**Estado:**
- La suite está sana: **822 tests pasan, cobertura del 89 %**.
- La validez estadística del backtest/OOS **no es fiable hoy**.

**Los 3 problemas más graves:**
1. **Look-ahead en los análogos históricos** (H-01/H-02). Un análogo con D0 = ayer aporta un CAR que se mide con precios *posteriores* a la decisión. Además, el CAR de los eventos recientes se guarda **truncado y nunca se recalcula**.
2. **Serie de precios corrupta por splits** (H-03/H-04). `close_raw` sale de la columna `Close` de yfinance, que ya viene ajustada por splits. Como las descargas son incrementales, cualquier split posterior a la primera descarga crea un salto falso.
3. **Sesgos de muestra que invalidan el OOS** (H-05/H-06/H-07):
   - el universo sale del mapa CIK→ticker *actual* (supervivencia);
   - el LLM conoce el desenlace de eventos anteriores a su fecha de corte;
   - el nightly corre `--full-range`, así que el OOS se mira cada noche.

**Verificado con datos de producción (sección 2.1):** los 200 análisis guardados son NO_TRADE y **ninguno llegó a la IA**.
- 126 se quedaron sin barra de D0 al analizarse (H-39) y quedan fuera de la cola para siempre.
- 58 cayeron en el proxy de spread (H-08, confirmado).

**Estado (2026-10-04):**
- **Arreglados:**
  - H-39, H-38 y H-08 (opción a: liquidez solo por ADV): PR #66, fusionado.
  - H-01, H-02, H-12 y H-13: PR #67, fusionado.
  - H-27, H-29 y H-30 (eficiencia), PR #73: series comunes del enrichment leídas una vez por chunk; sesión HTTP única y throttle por intervalo hacia EDGAR; un 404 del índice diario cuenta como día sin índice, no como fallo (sin usar el calendario bursátil, que perdería los filings de Viernes Santo).
  - H-03 (splits entre descargas), PR #72: cada descarga incremental vuelve a pedir el último día guardado y, si su cierre cambió, se rebaja la serie entera; los tickers ya partidos se reparan por tandas en el nightly. H-04 (filtros absolutos con precios reexpresados por contrasplits) sigue abierto.
  - PR #74: H-09 (stop con gap: SL, TP y tramos del trailing se ejecutan a la apertura si la barra ya abre más allá), H-11 (volatilidad base en (D0-90, D0-30]; las ratios antiguas se anulan sin borrar el CAR) y H-15 (con varios símbolos por CIK gana la acción ordinaria). H-14 (universo point-in-time) queda pendiente: solo afecta al análisis histórico.
  - H-24, H-25 y H-26 (gasto de IA), PR #75: libro ai_batches escrito al enviar cada batch (el tope diario cuenta lo enviado, no solo lo guardado); respuestas cortadas por max_tokens identificadas y tope de salida a 2048; fuera el cache_control sin efecto.
  - H-34 y H-36 (higiene): constraints.txt con las versiones exactas que pasan la suite, usado en la CI y en el nightly; ruff en la CI con reglas fijadas (ruff.toml) y los 30 avisos corregidos. De paso: un test de análogos que no comprobaba cuál entraba y un test de contrato pipeline→app para los descartes previos a la IA.
  - Preparación para el gasto real de la IA (2026-10-05): el libro ai_batches guarda los tokens y el coste REAL de cada batch (usage de la API) y el tope diario lo usa; H-20 en su parte de coste (un solo debate por filing; la caché reconoce otro Item del mismo filing). La parte estadística de H-20 (desduplicar por (cik, D0) en el event study) sigue abierta. Además, watchdog.yml relanza la pasada programada que GitHub se salte.
  - Tanda 1 «Medir bien» (2026-10-05): grupo de control (event_analyses.decision_sin_ia: misma decisión con la dirección y la confianza de los análogos en lugar de las del Judge); H-20 completo (una observación u operación por empresa y D0 en event study, análogos, prior, backtest y paper trading); H-19 (contraste principal sobre CAR winsorizado con errores agrupados por fecha de D0; t-test simple y BMP al lado; car_results guarda resid_std y n_event_days).
- **Pendientes de aprobación:** el resto.
- **H-16** (ventana en días naturales frente a sesiones) se deja aparte a propósito. Cambiar la definición obliga a recalcular todos los CAR, y los de empresas no operadas ya no tienen precios guardados (ops_prune): habría que volver a descargarlos por tandas.

## 2. Tabla de hallazgos

Severidad: **CRÍTICA** = afecta a la validez del backtest/OOS o puede perder dinero/datos.
- **SOSPECHA** = no verificado contra la base de datos real; se indica cómo verificarlo.
- **PREGUNTA** = requiere decisión de producto; no se asume nada.

### Detalle de los hallazgos críticos y altos

**H-01 · CRÍTICA · Look-ahead en análogos y prior**
- **Dónde:** `pipeline/analyze/historical_analogues.py:141` y `:180`.
- **Problema:** filtra `e.d0_close_date < as_of_date`, pero el CAR del análogo cubre `(D0, D0+window]` (`backtester.py:79`). Un análogo con D0 = as_of−1 incluye retornos de los 19 días *posteriores* a la decisión. El prior de shrinkage tiene el mismo defecto.
- **Impacto:** en cualquier simulación histórica (backtest/OOS), la magnitud esperada (Etapa 6), el EV y la decisión usan información futura. Pesa más en los primeros eventos de cada clase.
- **Fix:** filtrar por "desenlace conocido": `e.d0_close_date + window_days (+1 margen) < as_of_date`. Mejor aún, guardar `outcome_known_date` en `car_results` y filtrar por ella.
- **Esfuerzo:** S.

**H-02 · CRÍTICA · CAR parcial guardado como definitivo**
- **Dónde:** `pipeline/backtest/backtester.py:79-81`, `populate_car_results.py:93-103`.
- **Problema:** `compute_car` acepta una ventana de evento incompleta. Solo devuelve `None` si está vacía: 1 día de datos se guarda como "CAR a 20 días". `_pending_page` excluye para siempre los eventos que ya tienen fila de 20 días, así que el CAR truncado de un evento de hace 3 días **nunca se recalcula**. Pasa también con deslistados o huecos.
- **Impacto:** los análogos, el event study y la saturación de tesis mezclan CAR de 1-2 días con CAR de 20. Hay un sesgo sistemático hacia 0 y la varianza está mal estimada.
- **Fix:** exigir una ventana completa: `event_end <= último precio disponible` y un nº mínimo de sesiones (p. ej. ≥ 80 % de las esperadas). Si no se cumple, devolver `None` y dejar el evento pendiente. Además, una migración que borre los CAR con `n` sesiones insuficientes.
- **Esfuerzo:** M.

**H-03 · CRÍTICA · `close_raw` no es crudo: splits mal tratados**
- **Dónde:** `pipeline/ingest/yfinance_backfill.py:175`, `:419-421`, `:280`.
- **Problema:** con `auto_adjust=False`, la columna `Close` de yfinance **ya viene ajustada por splits** (solo `Adj Close` añade dividendos). Por tanto `adj_factor` solo recoge dividendos. La descarga es incremental (`desde = max(start, ultimo + 1 día)`): las filas antiguas se guardaron en la base pre-split y las nuevas llegan en la base post-split.
- **Impacto:** saltos falsos de −50 %/+900 % en la serie tras cualquier split o contrasplit. Afectan al CAR, a la beta, al backtest (stops falsos), al plan técnico y al paper trading. Es corrupción silenciosa y acumulativa.
- **Fix:**
  - (a) detectar los splits (`yf.Ticker().splits` o `actions=True`) y re-descargar la serie completa del ticker cuando aparezca uno nuevo;
  - (b) renombrar el campo a lo que es, o guardar también el `split_factor`;
  - (c) un test con un split sintético.
- **Verificar:** `SELECT ticker, trade_date, close_raw/lag(close_raw) OVER w - 1 AS r FROM prices WINDOW w AS (PARTITION BY ticker ORDER BY trade_date)` y filtrar `abs(r) > 0.45` cerca de `captured_at` distintos.
- **Esfuerzo:** M.

**H-04 · ALTA · Precios históricos inflados por contrasplits**
- **Dónde:** misma causa que H-03. Se usan en `enrichment.py:117-120` (`price_d0` ajustado) y en el filtro de precio/ADV.
- **Problema:** Yahoo reexpresa el pasado con los contrasplits futuros. Una acción a 0,50 $ en 2022 con un contrasplit 1:50 en 2024 aparece a 25 $ en 2022.
- **Impacto:** los filtros "precio > 5 $" y ADV usan información de acciones corporativas futuras (look-ahead más supervivencia). Es muy frecuente en microcaps biotech, justo el sector FDA.
- **Fix:** guardar el precio realmente negociado (`Close` / factor de split acumulado posterior) y usarlo en los filtros absolutos.
- **Esfuerzo:** M.

**H-05 · CRÍTICA · Sesgo de supervivencia en el universo**
- **Dónde:** `pipeline/ingest/ticker_map.py:55`, `pipeline/db/connection.py:44,99`.
- **Problema:** el ticker sale de `company_tickers.json` **actual**. Las empresas deslistadas, adquiridas o quebradas desde entonces no están en el fichero, `resolve()` devuelve `None` y sus eventos **ni se ingieren**. Encima, yfinance no tiene precios de deslistados.
- **Impacto:** el histórico 2019-2026 solo contiene supervivientes. El event study y el backtest sobreestiman los retornos (sobre todo LONG, bancarrotas y biotech).
- **Fix:** mapa CIK→ticker histórico, guardando los eventos aunque no haya ticker (`ticker NULL` + `cik`); fuente de precios con deslistados (Tiingo/Stooq/CRSP); como mínimo, cuantificar y documentar el sesgo.
- **Esfuerzo:** L.

**H-06 · CRÍTICA · Look-ahead por memoria del LLM**
- **Dónde:** `pipeline/analyze/adversarial_analyzer.py:226-233`; los modelos están en `config.py`.
- **Problema:** el prompt incluye la empresa, el ticker y el texto del filing. Haiku 4.5 y Sonnet 4.6 se entrenaron con datos posteriores a 2021-2024, es decir, conocen el desenlace. `OOS_START = 2024-01-01` es anterior a su fecha de corte.
- **Impacto:** cualquier backtest/OOS sobre eventos anteriores a la fecha de corte del modelo **no es válido** para medir la capacidad del Judge.
- **Fix:**
  - validar solo con eventos posteriores a la fecha de corte (paper trading/forward), o anonimizar nombre, ticker y fechas, que mitiga pero no resuelve;
  - documentar la fecha de corte por modelo y bloquear `--oos` antes de ella.
- **PREGUNTA:** ¿aceptas que la única validación válida del Judge sea forward?
- **Esfuerzo:** S (documentar/bloquear) a L (anonimizar).

**H-07 · CRÍTICA · El OOS se mira cada noche**
- **Dónde:** `.github/workflows/nightly_pipeline.yml:551` y `:570`; `app/lib/queries.ts:786-789`.
- **Problema:** el nightly corre `portfolio_report --full-range` y `validation.report --full-range`. La app enseña esos informes y **elige `best_version` a partir de ellos** (OOS incluido).
- **Impacto:** "fuera de muestra, una sola vez" no se cumple. Cualquier ajuste hecho mirando el dashboard está contaminado y la versión recomendada se selecciona sobre el OOS.
- **Fix:** el nightly en in-sample; el OOS solo manual, con tag y fecha registrados.
- **PREGUNTA:** ¿qué debe mostrar la app?
- **Esfuerzo:** S.

**H-08 · ALTA · El proxy de spread descarta casi todo**
- **Dónde:** `pipeline/analyze/abstention_engine.py:60` y `:115`.
- **Problema:** se usa el rango diario `(high−low)/close` de D0 como "spread" y se rechaza si supera 0,5 %. El rango diario de una mega-cap normal es de 1-3 %, y más en un día de evento.
- **Impacto:** casi cualquier evento acaba en NO_TRADE por "ilíquido". El Judge no se invoca (pre-filtro), así que no hay señales. Podría explicar "0 análisis técnicos".
- **Fix:** usar un estimador de spread real (Corwin-Schultz o Abdi-Ranaldo), o quitar el componente (a) y dejar el ADV.
- **CONFIRMADO** (sección 2.1): de los 184 eventos que llegaron al control de liquidez, **pasaron 0**. Los 58 con barra de D0 tenían rangos del 1,43 % (p10), 2,77 % (mediana) y 9,76 % (p90), todos por encima del techo del 0,5 %.
- **PREGUNTA:** el umbral.
- **Esfuerzo:** S.

**H-09 · ALTA · Stop-loss sin gap**
- **Dónde:** `pipeline/backtest/portfolio_simulator.py:250-252`.
- **Problema:** el SL se ejecuta siempre al precio del stop, aunque la barra abra por debajo (gap). En event-driven, los gaps son la norma.
- **Impacto:** el P&L del backtest sale **optimista**: las pérdidas por gap se recortan al nivel del stop.
- **Fix:** guardar `open` en `step_position_forward` y llenar a `min(open, stop)` en LONG y `max(open, stop)` en SHORT. Simétrico para el TP.
- **Esfuerzo:** S.

**H-10 · ALTA · El circuit-breaker es un estado absorbente**
- **Dónde:** `portfolio_simulator.py:109`, `:948-953`.
- **Problema:** si salta con DD > 15 % y luego se cierran todas las posiciones, la equity queda constante por debajo del 85 % del pico. Nunca se reactiva y no se abre ningún trade más hasta el final del backtest.
- **Impacto:** a partir del primer DD > 15 %, el backtest muere en silencio. El nº de trades y el Sharpe dependen de la fecha de ese DD.
- **Fix:** regla de reseteo (enfriamiento de N días o nuevo pico relativo).
- **PREGUNTA:** ¿kill-switch permanente o pausa?
- **Esfuerzo:** S.

**H-11 · ALTA · Volatilidad base calculada con datos futuros**
- **Dónde:** `pipeline/backtest/backtester.py:89`.
- **Problema:** `baseline_vol = merged["ret"].rolling(60).std().iloc[-1]` es la volatilidad de los **últimos 60 días de toda la serie** (puede ser hoy), no la previa a D0. Además es volatilidad de retornos, aunque se llame `abnormal_volume_ratio`.
- **Impacto:** `volatility_increase` (Etapa 6) y la saturación de tesis (`THESIS_ABNORMAL_VOLUME_RATIO`) dependen del futuro y miden otra cosa.
- **Fix:** usar la ventana `[D-90, D-30]` y renombrar, o calcular la ratio de volumen real.
- **Esfuerzo:** S.

**H-12 · ALTA · La caché puede reutilizar un evento descartado**
- **Dónde:** `pipeline/analyze/adversarial_analyzer.py:453`.
- **Problema:** la caché no excluye las filas `SKIPPED_OBJECTIVE_NO_TRADE`. Un evento nuevo del mismo ticker, clase y episodio hereda `net_conviction=0` y `confidence=0` sin preguntar a la IA.
- **Impacto:** NO_TRADE silencioso aunque el evento nuevo sí pase los filtros.
- **Fix:** añadir `AND ea.model_version_bull_bear <> 'SKIPPED_OBJECTIVE_NO_TRADE'` (y lo mismo en `get_cached_analysis`).
- **Esfuerzo:** S.

**H-13 · ALTA · Falta el pre-filtro por techo de EV**
- **Dónde:** `pipeline/analyze/event_analysis_pipeline.py:251-300`.
- **Problema:** IMPROVEMENT_PLAN.md (A1, "✅ Hecha, PR #21") afirma que existe `ev_ceiling_no_trade_reason()`. **No existe en el código ni en el historial git** (`git log -S` vacío).
- **Impacto:** se paga Bull/Bear/Judge en eventos que no pueden superar el umbral ni con convicción 1 y confianza 100. Con `EV_aggr = 1.8·|mag|·conf_imp`, hace falta `|mag|·conf_imp ≳ 0,72 %`. Ver la sección 3.
- **Fix:** calcular la Etapa 6 antes del batch y descartar si `max_s mult_s·|mag|/100·conf/100 < umbral_s + buffer`.
- **Esfuerzo:** S.

**H-14 · ALTA · Selección de eventos con la capitalización de hoy**
- **Dónde:** `event_analysis_pipeline.py:66`.
- **Problema:** la cola de la IA filtra por `u.in_investable_universe AND u.market_cap_last_usd`, que son valores **de hoy**.
- **Impacto:** los eventos históricos analizados se eligen por supervivencia y crecimiento futuros: sesgo de selección si se usan para el backtest. En producción (eventos recientes) es aceptable.
- **Fix:** universo point-in-time (capitalización en D0 desde XBRL `shares × price`) para cualquier análisis histórico.
- **Esfuerzo:** M.

**H-15 · ALTA · Varias acciones por CIK: gana la última**
- **Dónde:** `pipeline/ingest/ticker_map.py:55`.
- **Problema:** `{cik: ticker for entry in raw.values()}` se queda con la **última** fila de cada CIK. Si un CIK tiene común, preferentes y warrants, el ticker resultante puede ser el warrant o la preferente.
- **Impacto:** eventos atribuidos a un símbolo no operable. Desde PR #64/#65 se descartan al ingerir **y se borran** en `ops_prune`, así que se pierden eventos de empresas reales.
- **Fix:** preferir el ticker común (el primero del fichero, o el que no tenga sufijo).
- **SOSPECHA**, verificar contando CIKs con más de un ticker en `company_tickers.json` y cuántos acaban en `-`, `.`, `W`, `U`.
- **Esfuerzo:** S.

**H-39 · CRÍTICA · Eventos analizados antes de tener su barra de D0 → NO_TRADE permanente**
- **Dónde:**
  - `nightly_pipeline.yml`: el paso "Análisis de eventos" no tiene `if:` y corre en las 3 pasadas diarias;
  - el paso "Actualizar precios del universo" (línea 389) solo corre en la nocturna;
  - `event_analysis_pipeline.py:79` (`fetch_events_needing_analysis`) no exige que exista precio en D0.
- **Problema:**
  - las pasadas de 14:30 y 21:30 UTC analizan eventos de hoy, o con D0 = mañana (filings tras el cierre), sin la barra de D0;
  - `high_low_range_pct = None` produce "sin datos de high/low", descartado antes de la IA;
  - se guarda una fila en `event_analyses`, así que el evento **sale de la cola para siempre** (la cola es `ea.event_id IS NULL`).
- **Impacto:**
  - **126 de 200 análisis (63 %)** están en este caso, verificado en producción;
  - eventos potencialmente operables se pierden para siempre sin aviso;
  - analizar antes del cierre de D0 contradice además la regla de decisión "al cierre de D0".
- **Fix:**
  - (a) en la cola, exigir `EXISTS (SELECT 1 FROM prices WHERE ticker = e.ticker AND trade_date = e.d0_close_date AND high_raw IS NOT NULL)`;
  - (b) no persistir los descartes por "sin datos" (dejarlos pendientes) o marcarlos como reintentables;
  - (c) migración para borrar esas 126 filas y que se re-analicen.
- **Esfuerzo:** S.

**H-38 · MEDIA · Motivo engañoso en los eventos descartados antes de la IA**
- **Dónde:** `event_analysis_pipeline.py` (rama `skip_reason`: `net_conviction=0`, `confidence=0`) y `abstention_engine.decide_for_strategy`, donde la regla 2 (confianza) va antes que las objetivas.
- **Problema:** a un evento descartado por una regla objetiva se le guarda `confidence_in_conviction=0`. Al re-evaluar las 3 versiones, la regla "confianza < 40" salta antes que la regla que de verdad lo descartó, y ese es el `reason_if_no_trade` que se guarda y enseña la app.
- **Impacto:** 188 de 200 eventos muestran "no sabemos qué pasa (confianza 0 < 40)" cuando el motivo real era falta de datos o liquidez. Despista al usuario y a cualquier diagnóstico.
- **Fix:** cuando hay `skip_reason`, guardar ese motivo en las 3 decisiones sin pasar por `decide_all_strategies`.
- **Esfuerzo:** S.

### 2.1 Verificación con datos de producción (diagnóstico de solo lectura, run 86, 2026-10-04)

| Qué | Resultado |
|---|---|
| Análisis guardados | 200 (del 29/9 al 2/10), **0 operables** en las 3 versiones |
| Cómo se decidieron | **200/200 `SKIPPED_OBJECTIVE_NO_TRADE`**: ninguna llamada a Bull/Bear/Judge (0 € de API gastados) |
| Motivo real del descarte | 126 sin barra de D0 (H-39) · 58 proxy de spread (H-08) · 12 novelty < 20 · 4 sin beta |
| Rango high-low de D0 de los 58 descartados por spread | p10 1,43 % · mediana 2,77 % · p90 9,76 % (techo: 0,5 %) |
| Motivo que muestra la app | 188 "confianza 0 < 40" (H-38) · 12 novelty |
| H-13 (techo de EV) | No verificable todavía: ningún evento ha llegado a la Etapa 6 con la IA |

### Hallazgos medios y bajos

| ID | Categoría | Severidad | Archivo:línea | Descripción | Impacto | Fix propuesto | Esfuerzo |
|---|---|---|---|---|---|---|---|
| H-16 | A/B | MEDIA | `backtester.py:79`, `config.py` (`EVENT_WINDOWS_DAYS`) | La "ventana de 5/20 días" está en **días naturales** (`Timedelta(days=window_days)`); la documentación habla de sesiones (≈3 y ≈14 sesiones reales). | Horizonte del CAR mal etiquetado y desalineado con el holding del simulador (sesiones). | Contar N sesiones de negociación. | S |
| H-17 | A | MEDIA | `edgar_scraper.py:417` | Si falta `<ACCEPTANCE-DATETIME>`, `filed_at` queda a las 00:00 del día del índice, así que se usa D0 = ese día aunque se publicara tras el cierre. | El CAR incluye el salto del anuncio (sobreestimado). **SOSPECHA**: verificar con `SELECT count(*) FROM events WHERE filed_at::time = '00:00'`. | Marcar `filed_at_estimated` y excluirlos del event study, o usar D0+1. | S |
| H-18 | A | BAJA | `edgar_scraper.py:327` | Las sesiones de media jornada (cierre a las 13:00: 3 de julio, día después de Acción de Gracias, Nochebuena) no se tratan. | Un filing de las 14:00 de ese día obtiene D0 = ese día (el mercado ya había cerrado). | Añadir cierres anticipados a `market_calendar`. | S |
| H-19 | B | MEDIA | `validation/event_study.py:104` | t-test simple sobre CAR crudos: sin agrupamiento por fecha (los earnings se concentran en calendario), sin estandarizar (BMP), sin winsorizar (microcaps con ±300 %). | Errores estándar subestimados y significancia inflada. | Test BMP/Kolari-Pynnönen, errores agrupados por fecha y winsorización al 1 %. | M |
| H-20 | B | MEDIA | `edgar_scraper.py` (`classify_event_classes`) | Un mismo 8-K con varios Items (2.02 + 5.02) genera varios eventos; emisores frecuentes (TNXP: 414 eventos) tienen ventanas solapadas. | Observaciones dependientes en el event study; coste de LLM duplicado para el mismo filing. | Desduplicar por (cik, D0) en estadística; un solo análisis por accession. | M |
| H-21 | A | MEDIA | `ev_engine.py:48-50` vs su docstring | `EV_THRESHOLDS`: CONSERVATIVE 0,2 % < AGGRESSIVE 0,8 %, al revés de lo que dice el docstring ("Conservative pide un umbral más alto"). | Lógica de versiones contraria a la documentada. **PREGUNTA**: ¿cuál es la intención? | Según la respuesta. | S |
| H-22 | A | BAJA | `ev_engine.py:125-128` | `_threshold_check` usa el EV con signo (`ev > threshold`) mientras la abstención usa `abs(ev)`: en SHORT la app muestra "NO_TRADE" en `threshold_*` aunque la decisión sea SHORT. | Estados contradictorios visibles en la ficha. | Usar `abs(ev)` y el mismo buffer. | S |
| H-23 | A | MEDIA | `technical_analysis.py:704`; ningún `INSERT INTO event_enrichment` | `event_enrichment` **nunca se escribe** (0 filas en producción); el plan técnico lee `vix_d0` de ahí. | El filtro de régimen VIX del plan técnico está siempre desactivado sin avisar. | Persistir el enrichment en `_store_event_analysis`, o pasar el VIX directamente. | S |
| H-24 | C | MEDIA | `event_analysis_pipeline.py:168`, `:430` | `spend_today_usd` solo cuenta los eventos guardados; Bull/Bear pagados cuyo Judge falla (o cuyo guardado falla) no cuentan y se re-pagan al día siguiente. | El tope diario infraestima el gasto real. | Contar por batch enviado (registrar `batch_id` y nº de requests). | S |
| H-25 | C | MEDIA | `adversarial_analyzer.py:367-381` | No se mira `stop_reason`: una respuesta cortada por `max_tokens=1024` se convierte en "JSON inválido" y el evento se re-paga la noche siguiente. | Gasto repetido y pérdida silenciosa. | Registrar `stop_reason == "max_tokens"` y ajustar `max_tokens`. | S |
| H-26 | C | BAJA | `adversarial_analyzer.py:245`, `:277` | `cache_control` puesto sobre system prompts de ~150 tokens, por debajo del mínimo cacheable del modelo: es un no-op. | Sin ahorro real (falsa sensación de caché). | Quitarlo, o cachear un prefijo compartido que supere el mínimo. | S |
| H-27 | D | MEDIA | `enrichment.py:268-271` | Por cada evento se recarga el histórico completo de SPY, ETF sectorial, ^VIX y Fama-French (5 consultas grandes por evento, hasta 500 eventos por corrida). | Minutos de CPU y red evitables en cada corrida. | Memoizar por corrida. | S |
| H-28 | D | MEDIA | `populate_car_results.py:152-154`; `backtester.py` | INSERT fila a fila y un `join` + `rolling` por evento y ventana: 39 min para 61k filas en la corrida 81. | Cuello de botella del histórico por tandas. | Join una vez por ticker, `executemany` y cálculo vectorizado. | M |
| H-29 | D | MEDIA | `edgar_http.py:93` | `requests.get` sin `Session` (TLS nuevo en cada petición) y throttle por `sleep` fijo más latencia: unos 5 req/s reales frente a 8 permitidos (≈65 min por trimestre). | Nightly e histórico ~35 % más lentos. | `requests.Session` con throttle por token bucket. | S |
| H-30 | B | BAJA | `edgar_scraper.py:475` | El backfill solo salta fines de semana: los festivos piden un índice inexistente, que da error y cuenta como "día fallido". | Ruido en los logs; esconde fallos reales. | Usar `market_calendar.dias_de_negociacion`. | S |
| H-31 | A | MEDIA | `portfolio_simulator.py` (bucle principal) | La posición no se evalúa el día de entrada (entra a la apertura y el SL/TP de esa sesión se ignora). | Sesgo en el path (en ambos sentidos). | Evaluar el resto de la sesión de entrada (high/low de D+1). | S |
| H-32 | A | MEDIA | `portfolio_simulator.py:71` | Solo hay 10 pb de comisión round-trip; el slippage (`SLIPPAGE_BPS_SWEEP`) solo se aplica en `sensitivity.py`, no en el backtest principal ni en la app. | El P&L mostrado es optimista. **PREGUNTA**: ¿qué slippage por defecto? | Aplicar un slippage base en `open_position`/salidas. | S |
| H-33 | E | MEDIA | `config.py:73` vs `schema.sql:33` | `MIN_MARKET_CAP_USD = 50M`, pero el schema documenta "cap > 300M"; el umbral de ADV y otros (`SPREAD_CEILING`, `DRAWDOWN`, `MAX_POSITION_PCT_OF_ADV`) están dispersos por módulos. | Configuración incoherente y difícil de auditar. | Centralizar en `config.py` con una única fuente. | S |
| H-34 | E | MEDIA | `pipeline/requirements.txt` | Solo cotas inferiores (`yfinance>=0.2.40`, `anthropic>=1.5`) y sin lockfile: cada corrida puede usar versiones nuevas (yfinance cambia su formato a menudo). | Roturas no reproducibles. | `pip-compile` / lockfile y Dependabot. | S |
| H-35 | E | BAJA | `IMPROVEMENT_PLAN.md` | Documenta como hechas tareas que no están en el código (A1). | Falsa confianza al priorizar. | Revisar el plan contra el código. | S |
| H-36 | E | BAJA | `filing_text.py:35` | `import hashlib` sin usar (y otros 27 avisos de `ruff`); CI no ejecuta lint. | Código muerto. | Añadir `ruff` al CI. | S |
| H-37 | B | BAJA | `sample_split.py` (`date_bounds`) | Los eventos in-sample cerca del 2023-12-31 tienen CAR que llega al OOS, e in-sample no tiene límite inferior (incluye 2019-2020, antes de `BACKTEST_START`). | Pequeña fuga en la frontera y muestra distinta de la documentada. | Embargo de 30 días en la frontera; inicio = `BACKTEST_START`. | S |

**Verificado y sin hallazgo:**
- **Secretos:** no aparecen en los logs; el token de Telegram no se registra.
- **Prompts:** la inyección en el texto del filing está mitigada.
- **D0:** respeta fines de semana y festivos.
- **Cola de la IA:** el bucle de re-pago está corregido.
- **Abstención en SHORT:** ya usa `abs(ev)` (R16 resuelto).

## 3. Ahorro estimado de API

**Coste actual estimado por evento** (Batch API, −50 %):

| Paso | Tokens de entrada | Tokens de salida | Coste |
|---|---|---|---|
| Bull + Bear (Haiku 4.5, 0,5 / 2,5 $ por M) | ~2 × 2,3k | ~2 × 0,5k | ≈ 0,0048 $ |
| Judge (Sonnet 4.6, 1,5 / 7,5 $ por M) | ~3,2k | ~0,3k | ≈ 0,0070 $ (**~59 %**) |
| **Total** | | | **≈ 0,0118 $/evento** |

Coincide con `ANALYSIS_EST_COST_PER_EVENT_USD = 0,011`. Con 50 €/mes salen unos 4.400 eventos al mes.

| Cambio | Ahorro estimado | Riesgo |
|---|---|---|
| **H-13** pre-filtro por techo de EV | Entre un 30 % y casi el **100 %** de las llamadas, según la magnitud media de cada clase. Con `|mag|·conf_imp < 0,72 %` ningún evento puede operar: hoy se pagaría IA sin ninguna posibilidad de TRADE. | Nulo (la decisión no cambia). **Verificar** con la distribución de `expected_magnitude × confidence` por clase. |
| **H-12** caché sin filas descartadas | 0 € (es de corrección, no de coste). | — |
| Judge con extracto recortado (resumen Bull/Bear + 2.000 caracteres del filing en vez de 8.000) | −1,5k tokens de entrada en el Judge, ≈ −0,0023 $/evento (**−19 %**). | Bajo. |
| Judge en Sonnet 5.5 (2 / 10 $; batch 1 / 5) | Judge −33 %, ≈ **−20 %** del total. | **PREGUNTA**: ¿calidad equivalente? |
| Judge en Haiku 4.5 | Judge −67 %, ≈ **−40 %** del total. | **PREGUNTA**: peor arbitraje. |
| Un solo análisis por accession (H-20) | −5 % a −15 % (filings con varios Items relevantes). | Bajo. |
| No re-pagar respuestas cortadas o Judges fallidos (H-24/H-25) | −2 % a −5 %. | Nulo. |

## 4. Tests que faltan (prioridad)

1. **Análogos sin look-ahead:** un análogo con D0 = as_of−1 **no** debe entrar con `window_days=20` (H-01).
2. **CAR incompleto:** con menos sesiones que la ventana debe devolver `None` y quedar pendiente; un evento reciente se recalcula cuando se cierra su ventana (H-02).
3. **Split en mitad de una descarga incremental:** la serie no debe tener saltos de −50 % (H-03).
4. **SL con gap:** si `open < stop` en LONG, se llena a `open` (H-09).
5. **Circuit-breaker:** se recupera según la regla elegida y no bloquea para siempre (H-10).
6. **Caché:** ignora las filas `SKIPPED_OBJECTIVE_NO_TRADE` (H-12).
7. **Techo de EV:** un evento sin posibilidad de superar el umbral no llama al LLM (H-13).
8. **`ticker_map`:** con varias filas por CIK elige la acción común (H-15).
9. **Enrichment:** `vix_d0` llega al plan técnico (H-23).
10. **Volatilidad base:** `abnormal_volume_ratio` no usa datos posteriores a D0 (H-11).
11. **`filed_at` sin hora de aceptación:** debe quedar marcado como estimado (H-17).
12. **Cobertura baja:** `universe_maintenance` (57 %), `yfinance_backfill` (60 %), `ops_history_rounds` (62 %), `portfolio_report` (78 %), `adversarial_analyzer` (81 %): rutas de error y lotes.
13. **Workflow:** el nightly no corre `--full-range` sobre OOS (H-07; test de configuración).
14. **Cola de la IA:** un evento sin barra de D0 no entra, y un descarte por "sin datos" no se persiste (H-39).
15. **Descartados antes de la IA:** `reason_if_no_trade` es el motivo objetivo real, no "confianza 0" (H-38).

## 5. Plan de ataque (PRs pequeños y atómicos)

Cada PR lleva su test en rojo primero y la suite al 100 %.

0. **PR-0 · Cola de la IA solo con barra de D0 (H-39 + H-38)** y re-analizar las 126 filas afectadas. Es lo que hoy impide cualquier señal en producción.
1. **PR-1 · Análogos point-in-time (H-01 + test 1).** El de más impacto en el backtest con el menor cambio.
2. **PR-2 · CAR completo o nada (H-02 + H-16 + test 2)** más una migración que borre los CAR truncados y recalcule.
3. **PR-3 · Caché correcta y techo de EV (H-12 + H-13 + tests 6-7).** Ahorro de API inmediato.
4. **PR-4 · OOS protegido (H-07, H-37).** Tras responder la PREGUNTA sobre qué muestra la app.
5. **PR-5 · Splits (H-03 + H-04 + test 3).** Detección de splits y re-descarga completa del ticker; después, recalcular CAR.
6. **PR-6 · Simulador realista (H-09, H-31, H-10, H-32).** Tras las PREGUNTAS sobre el circuit-breaker y el slippage.
7. **PR-7 · Proxy de liquidez (H-08).** Tras la PREGUNTA del umbral; verificar antes con la consulta indicada.
8. **PR-8 · `ticker_map` y enrichment (H-15, H-23, H-11).**
9. **PR-9 · Robustez del gasto de IA (H-24, H-25, H-26, H-20).**
10. **PR-10 · Rendimiento (H-27, H-28, H-29, H-30).**
11. **PR-11 · Higiene (H-33, H-34, H-35, H-36, H-22, H-18, H-17).**
12. **Fuera de PR (decisión):**
    - **H-05 supervivencia:** requiere una fuente de datos con deslistados.
    - **H-06 memoria del LLM:** decidir si la validación del Judge pasa a ser solo forward.

### PREGUNTAS abiertas (no se asume nada)

- **H-06:** ¿la validación del Judge pasa a ser solo forward (eventos posteriores a la fecha de corte del modelo)?
- **H-07:** ¿qué informes muestra la app: in-sample o el último OOS congelado?
- **H-08:** ¿qué medida de liquidez y qué umbral?
- **H-10:** ¿el circuit-breaker es un kill-switch permanente o una pausa? ¿Con qué regla de reseteo?
- **H-21:** ¿la intención de los umbrales de EV es la del código o la del docstring?
- **H-32:** ¿qué slippage por defecto en el backtest principal?
- **Sección 3:** ¿se puede cambiar el modelo del Judge (Sonnet 5.5 / Haiku 4.5)?
