-- schema.sql — POC Semana 1
--
-- Diseño gobernado por AUDIT_LEAN.md y ARCHITECTURE_LEAN.md:
--   - EDGAR es el eje (earnings 8-K Item 2.02). FDA es satélite, columna `is_satellite`.
--   - D0_close_date es explícita: es la fecha en que el evento se considera público.
--     La entrada del backtest NUNCA puede ser <= D0_close_date. Esto se aplica con
--     un CHECK constraint, no solo con disciplina de código (ver backtest/backtester.py).
--   - prices separa close_raw de adj_factor porque yfinance recalcula los ajustados
--     retroactivamente (AUDIT_LEAN.md §2.2.4 / ARCHITECTURE_LEAN.md §4). Sin esto el
--     backtest de hoy no es reproducible mañana.
--   - is_delisted_flag en universe: no se rellenan huecos, se marcan (spec del usuario).
--
-- 3 tablas "main" pedidas por el spec de Fase 1: events, analyses, backtest_runs.
-- prices y universe son soporte imprescindible (no opcional: sin ellas no hay
-- CAR, y sin universe no se puede medir sesgo de supervivencia — AUDIT_LEAN.md §2.4).
--
-- FASE 2 (Análisis de eventos) añade event_enrichment (Etapa 1) y
-- event_analyses (Etapas 2-8: novelty, Bull/Bear/Judge, impact, EV,
-- abstention), que sustituye a la tabla `analyses` de la Fase 1 — ver la
-- cabecera de event_analyses más abajo para el porqué del reemplazo.

CREATE TABLE IF NOT EXISTS universe (
    cik                 TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL,
    sic_code            TEXT,
    company_name        TEXT NOT NULL,
    first_seen_date     DATE NOT NULL,
    last_seen_date      DATE NOT NULL,
    is_delisted_flag    BOOLEAN NOT NULL DEFAULT FALSE,
    delisted_date       DATE,               -- de formulario 25-NSE en EDGAR, si se detecta
    market_cap_last_usd NUMERIC,
    adv_usd_60d         NUMERIC,             -- volumen medio diario en $, 60 días
    in_investable_universe BOOLEAN NOT NULL DEFAULT FALSE,  -- price>$5, cap>$300M, ADV>$1M
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_universe_ticker ON universe (ticker);

-- ============================================================================
-- events: el eje del sistema. Un evento = un 8-K (o item FDA) normalizado.
-- ============================================================================
CREATE TABLE IF NOT EXISTS events (
    event_id            BIGSERIAL PRIMARY KEY,
    cik                 TEXT NOT NULL REFERENCES universe(cik),
    ticker              TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('EDGAR', 'FDA_RSS', 'FDA_OPENFDA')),
    is_satellite        BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE para FDA (AUDIT_LEAN.md §2.2.4):
                                                          -- n insuficiente para probar nada por
                                                          -- debajo de ~4% edge/evento. Nunca se
                                                          -- mezcla con EDGAR en el mismo test.
    event_class         TEXT NOT NULL,        -- '8K_2.02_EARNINGS','8K_1.01_MATERIAL_AGMT',
                                               -- '8K_4.02_RESTATEMENT','8K_5.02_MGMT_CHANGE',
                                               -- '8K_1.03_BANKRUPTCY','8K_8.01_OTHER',
                                               -- 'FDA_APPROVAL','FDA_CRL','FDA_ADCOM'
    item_codes          TEXT[],               -- códigos de Item del 8-K tal cual, ej {'2.02','9.01'}
    accession_number     TEXT,                -- identificador único de EDGAR, NULL si es FDA
    source_url           TEXT NOT NULL,
    filed_at             TIMESTAMPTZ NOT NULL,  -- timestamp real de presentación/publicación
    -- D0_close_date es la piedra angular anti-look-ahead (ARCHITECTURE_LEAN.md §4):
    -- el evento se considera público al CIERRE del día de negociación en que se filed,
    -- ajustado a que si filed_at es después del cierre de mercado (16:00 ET) o es
    -- fin de semana/festivo, D0 pasa al siguiente día hábil.
    d0_close_date        DATE NOT NULL,
    classification_method TEXT NOT NULL CHECK (classification_method IN ('RULE', 'LLM_HAIKU_BATCH')),
    classification_confidence NUMERIC CHECK (classification_confidence BETWEEN 0 AND 1),
    raw_text_hash        TEXT NOT NULL,        -- hash del contenido: evita reclasificar en reruns
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, accession_number, event_class)
);

-- idx_events_class_date (event_class, d0_close_date) e idx_events_ticker
-- (ticker) eran prefijos exactos de idx_car_results_class_lookup y de
-- idx_events_ticker_class_for_cache (más abajo): el planificador usa esos
-- para las mismas consultas. Con ~220k eventos cada índice ocupa varios MB
-- de un plan gratuito de ~500 MB, así que se borran.
DROP INDEX IF EXISTS idx_events_class_date;
DROP INDEX IF EXISTS idx_events_ticker;
CREATE INDEX IF NOT EXISTS idx_events_hash ON events (raw_text_hash);

-- Columnas de Fase 3 (ingest/filing_text.py), vía ALTER — CREATE TABLE
-- IF NOT EXISTS no las habría añadido a una tabla `events` ya existente
-- (misma lección aprendida en la Fase 2 con prices.high_raw/low_raw: ver ahí
-- para el porqué). NULL hasta que el backfill de texto corra sobre el evento;
-- eventos FDA (source != 'EDGAR') se quedan en NULL permanentemente hasta que
-- exista un scraper de FDA — ver filing_text.py:populate_missing_filing_text.
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_length_chars INT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_includes_exhibit BOOLEAN;
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_extracted_at TIMESTAMPTZ;
-- Intentos fallidos de extracción (descarga caída, 404, texto vacío). Sin
-- esto, los que fallaban siempre se quedaban los primeros de la cola (ORDER BY
-- event_id LIMIT n) y, en cuanto se juntaban n, ningún evento nuevo recibía
-- texto nunca más. Tras MAX_ATTEMPTS se dejan de reintentar.
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_attempts INT NOT NULL DEFAULT 0;

-- ============================================================================
-- prices: panel diario. close_raw + adj_factor separados a propósito (ver cabecera).
-- ============================================================================
CREATE TABLE IF NOT EXISTS prices (
    ticker              TEXT NOT NULL,
    trade_date          DATE NOT NULL,
    close_raw           NUMERIC,
    adj_factor          NUMERIC,              -- close_adjusted = close_raw * adj_factor
    volume              BIGINT,
    -- flag de posible gap de supervivencia: NO se rellena, se marca (instrucción explícita).
    -- TRUE cuando yfinance no devuelve fila para una fecha de calendario bursátil
    -- esperada dentro del rango solicitado para ese ticker.
    survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,
    captured_at          TIMESTAMPTZ NOT NULL DEFAULT now(),  -- fecha de la descarga, no del precio
    PRIMARY KEY (ticker, trade_date)
);

-- Duplicaba la PRIMARY KEY (ticker, trade_date), que ya es un índice
-- idéntico: solo ocupaba sitio (~1/3 del tamaño de la tabla).
DROP INDEX IF EXISTS idx_prices_ticker_date;

-- Columnas añadidas en Fase 2, vía ALTER en vez de en el CREATE TABLE de
-- arriba: `CREATE TABLE IF NOT EXISTS` es un no-op silencioso sobre una tabla
-- que ya existe, así que reaplicar schema.sql en un despliegue que ya corrió
-- la Fase 1 NUNCA habría añadido estas columnas (se encontró al reaplicar el
-- schema sobre el Postgres local de esta sesión, que ya tenía `prices` de la
-- Fase 1 — no en una tabla nueva, donde el problema pasa desapercibido).
-- `ADD COLUMN IF NOT EXISTS` sí es idempotente en Postgres 9.6+.
ALTER TABLE prices ADD COLUMN IF NOT EXISTS high_raw NUMERIC;  -- proxy de spread/liquidez:
ALTER TABLE prices ADD COLUMN IF NOT EXISTS low_raw NUMERIC;   -- (high-low)/close — ver
                                                                -- abstention_engine.py. No es
                                                                -- bid-ask real: no hay datos de
                                                                -- microestructura gratis
                                                                -- (AUDIT_LEAN.md §2.1). Proxy
                                                                -- documentado, no ocultado.

-- Añadida para el backtest de cartera (backtest/portfolio_simulator.py):
-- la entrada real es "apertura D+1", y hasta ahora `prices` solo guardaba
-- close/high/low — ningún backfill había pedido nunca el precio de apertura
-- porque compute_car() y el backtest simple de la Fase 1 solo usan cierres.
ALTER TABLE prices ADD COLUMN IF NOT EXISTS open_raw NUMERIC;

-- ============================================================================
-- fama_french_factors: panel diario de factores (Ken French Data Library).
-- ============================================================================
CREATE TABLE IF NOT EXISTS fama_french_factors (
    trade_date  DATE PRIMARY KEY,
    mkt_rf      NUMERIC NOT NULL,
    smb         NUMERIC NOT NULL,
    hml         NUMERIC NOT NULL,
    rf          NUMERIC NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- car_results: retorno anormal acumulado (CAR) por evento y ventana,
-- calculado por backtest/backtester.py:compute_car(). FALTABA en la Fase 1:
-- compute_car() devolvía el resultado en memoria pero nada lo persistía —
-- sin esta tabla, "historical analogues ya calculados" (Etapa 6 de la Fase 2)
-- no tenía nada que leer. Se añade aquí porque analyze/historical_analogues.py
-- la necesita, no como limpieza de la Fase 1 en sí, pero de paso cierra ese
-- hueco (documentado en RUNBOOK.md).
-- ============================================================================
CREATE TABLE IF NOT EXISTS car_results (
    event_id             BIGINT NOT NULL REFERENCES events(event_id),
    window_days           INT NOT NULL,        -- 5 o 20, coincide con backtest/backtester.py
    car                   NUMERIC NOT NULL,     -- fracción, no % (0.05 = 5%)
    abnormal_volume_ratio  NUMERIC,
    n_estimation_days      INT NOT NULL,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, window_days)
);
-- Para el test BMP (H-19): desviación de los residuos del modelo en la
-- ventana de estimación y nº de sesiones de la ventana de evento.
ALTER TABLE car_results ADD COLUMN IF NOT EXISTS resid_std NUMERIC;
ALTER TABLE car_results ADD COLUMN IF NOT EXISTS n_event_days INT;
-- Marca de "ya se intentó completar resid_std": los que no se pueden (sin
-- precios o con un CAR que ya no cuadra) no se reintentan cada noche.
ALTER TABLE car_results ADD COLUMN IF NOT EXISTS resid_std_checked_at TIMESTAMPTZ;

-- Soporta la query de analogues: por clase, ordenado por fecha, excluyendo
-- eventos futuros respecto al evento que se está evaluando (ver
-- analyze/historical_analogues.py — es la misma disciplina anti-look-ahead
-- del resto del proyecto, aplicada a la ventana de análogos históricos).
CREATE INDEX IF NOT EXISTS idx_car_results_class_lookup ON events (event_class, d0_close_date, event_id);

-- ============================================================================
-- event_enrichment: salida de la Etapa 1 (Fase 2) — features de mercado por
-- evento, calculadas UNA VEZ y reutilizadas por novelty/impact/EV, en vez de
-- recalcularlas en cada etapa. Todo lo que entra aquí usa SOLO datos
-- disponibles en o antes de events.d0_close_date (mismo principio
-- anti-look-ahead que backtest_runs — ver ARCHITECTURE_LEAN.md §4).
-- ============================================================================
CREATE TABLE IF NOT EXISTS event_enrichment (
    event_id             BIGINT PRIMARY KEY REFERENCES events(event_id),
    price_d0             NUMERIC,             -- cierre ajustado del día del evento
    price_d_minus_5      NUMERIC,
    price_d_minus_20     NUMERIC,
    volume_d0            BIGINT,
    volume_avg_20d       NUMERIC,
    volume_ratio         NUMERIC,             -- volume_d0 / volume_avg_20d
    beta_vs_spy          NUMERIC,             -- de la regresión de factores (coef. de mkt_rf)
    ff_size_exposure     NUMERIC,             -- coef. de SMB
    ff_value_exposure    NUMERIC,             -- coef. de HML
    vix_d0               NUMERIC,
    sector_etf_ticker    TEXT,                -- ETF usado como proxy de sector (ver enrichment.py)
    sector_mood          NUMERIC,             -- retorno_sector_d0 - retorno_spy_d0
    pre_event_drift_pct  NUMERIC,             -- retorno D-5 -> D-1, insumo del novelty engine
    high_low_range_pct   NUMERIC,             -- (high-low)/close en D0 — proxy de liquidez/spread
    n_estimation_days    INT,                 -- días usados en la regresión — baja confianza si es poco
    had_survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,  -- copiado de prices, propaga a abstention
    enriched_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- event_analyses: pipeline completo de la Fase 2 (Etapas 2-8), un registro
-- por evento con TRAZA COMPLETA de cada etapa (pedido explícito: "esto
-- permite debugear decisiones malas después"). Sustituye a la tabla `analyses`
-- de la Fase 1 (Bull/Bear/Judge con esquema simple) — nada dependía de esa
-- tabla en producción, así que se reemplaza en vez de mantener dos esquemas
-- paralelos.
--
-- Columnas JSONB para el output completo de cada etapa (auditoría) MÁS
-- columnas planas para los campos que se filtran/agregan a menudo (evita
-- tener que hacer ->> en cada query de stats del día 3).
-- ============================================================================
CREATE TABLE IF NOT EXISTS event_analyses (
    event_id              BIGINT PRIMARY KEY REFERENCES events(event_id),

    -- Etapa 2: Novelty Engine
    novelty_score          NUMERIC NOT NULL CHECK (novelty_score BETWEEN 0 AND 100),
    novelty_reasoning       JSONB NOT NULL,     -- {pre_event_drift, has_guidance, rumor_flag, ...}

    -- Etapas 3-5: Bull / Bear / Judge (JSONB = el JSON exacto que pide el spec)
    bull_analyst_output      JSONB NOT NULL,
    bear_analyst_output      JSONB NOT NULL,
    judge_output             JSONB NOT NULL,
    net_conviction           NUMERIC NOT NULL CHECK (net_conviction BETWEEN -1 AND 1),
    confidence_in_conviction NUMERIC NOT NULL CHECK (confidence_in_conviction BETWEEN 0 AND 100),

    -- Etapa 6: Impact Estimation
    impact_estimation        JSONB NOT NULL,
    n_historical_analogues    INT NOT NULL,     -- tamaño de muestra detrás de impact_estimation —
                                                 -- clave para no confundir confianza con ruido
                                                 -- (AUDIT_LEAN.md §2.2.3, MDE por clase de evento)

    -- Etapa 7: Expected Value Engine
    ev_calculation           JSONB NOT NULL,
    ev_conservative           NUMERIC NOT NULL,
    ev_aggressive             NUMERIC NOT NULL,
    ev_balanced               NUMERIC NOT NULL,

    -- Etapa 8: Abstention Engine (una decisión POR VERSIÓN de estrategia, no una sola global —
    -- el mismo evento puede ser TRADE para Aggressive y NO_TRADE para Conservative)
    abstention_decision       JSONB NOT NULL,    -- {CONSERVATIVE: {...}, AGGRESSIVE: {...}, BALANCED: {...}}
    trade_decision_conservative TEXT NOT NULL CHECK (trade_decision_conservative IN ('LONG','SHORT','NO_TRADE')),
    trade_decision_aggressive   TEXT NOT NULL CHECK (trade_decision_aggressive IN ('LONG','SHORT','NO_TRADE')),
    trade_decision_balanced     TEXT NOT NULL CHECK (trade_decision_balanced IN ('LONG','SHORT','NO_TRADE')),

    -- Metadatos / auditoría (pedido explícito: audit trail completo)
    model_version_bull_bear    TEXT NOT NULL,   -- 'claude-haiku-4-5'
    model_version_judge        TEXT NOT NULL,   -- 'claude-sonnet-4-6'
    batch_id_bull_bear          TEXT,
    batch_id_judge               TEXT,
    from_cache                   BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE si reusó un análisis
                                                                   -- de (ticker,event_class) <24h
    analyzed_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_event_analyses_trade_balanced ON event_analyses (trade_decision_balanced);
CREATE INDEX IF NOT EXISTS idx_event_analyses_analyzed_at ON event_analyses (analyzed_at);

-- Soporta la caché de 24h por (ticker, event_class): busca el análisis más
-- reciente de esa combinación sin tener que escanear toda la tabla.
CREATE INDEX IF NOT EXISTS idx_events_ticker_class_for_cache ON events (ticker, event_class, d0_close_date);

-- ============================================================================
-- backtest_runs: un run = una versión de estrategia evaluada sobre un conjunto
-- de eventos. Guarda trade-by-trade, no solo el agregado, porque T8/T9 de
-- ARCHITECTURE_LEAN.md exigen intervalos de confianza y reproducibilidad, y eso
-- requiere los resultados por trade, no solo el resumen.
-- ============================================================================
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id               BIGSERIAL PRIMARY KEY,
    event_id             BIGINT NOT NULL REFERENCES events(event_id),
    strategy_version     TEXT NOT NULL CHECK (strategy_version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    -- fechas de entrada/salida: NUNCA <= d0_close_date. Enforced por CHECK abajo
    -- Y por un assert explícito en backtest/backtester.py (defensa en profundidad).
    entry_date           DATE NOT NULL,
    exit_date_5d          DATE,
    exit_date_20d         DATE,
    entry_price           NUMERIC,
    exit_price_5d          NUMERIC,
    exit_price_20d         NUMERIC,
    predicted_direction    TEXT NOT NULL CHECK (predicted_direction IN ('LONG', 'SHORT', 'NO_TRADE')),
    predicted_ev_pct       NUMERIC NOT NULL,     -- de event_analyses.ev_balanced (u otra versión) en el momento de decidir
    realized_return_5d_pct  NUMERIC,             -- retorno real D+1(apertura)->D+5(cierre)
    realized_return_20d_pct NUMERIC,
    slippage_bps_applied    NUMERIC NOT NULL DEFAULT 25,  -- barrido de sensibilidad T7
    hit_5d                BOOLEAN,               -- TRUE si signo(realized) == signo(predicted)
    hit_20d               BOOLEAN,
    had_survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,  -- copiado de prices para ese trade
    run_batch_tag          TEXT NOT NULL,        -- identifica la corrida (fecha+git sha) para T9
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_no_lookahead_5d  CHECK (exit_date_5d  IS NULL OR exit_date_5d  > entry_date),
    CONSTRAINT chk_no_lookahead_20d CHECK (exit_date_20d IS NULL OR exit_date_20d > entry_date),
    UNIQUE (event_id, strategy_version, run_batch_tag)
);

CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_runs (strategy_version, run_batch_tag);
CREATE INDEX IF NOT EXISTS idx_backtest_event ON backtest_runs (event_id);

-- ============================================================================
-- placebo_runs: T1 de ARCHITECTURE_LEAN.md — mismo pipeline, fechas aleatorias.
-- Tabla separada a propósito: nunca se debe poder confundir un resultado
-- placebo con uno real en una query descuidada.
-- ============================================================================
CREATE TABLE IF NOT EXISTS placebo_runs (
    placebo_id            BIGSERIAL PRIMARY KEY,
    ticker                TEXT NOT NULL,
    fake_event_date        DATE NOT NULL,     -- fecha aleatoria, emparejada por calendario
    matched_real_event_id  BIGINT REFERENCES events(event_id),  -- el evento real que se emparejó
    car_5d                 NUMERIC,
    car_20d                NUMERIC,
    run_batch_tag           TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- portfolio_trades / portfolio_equity_curve: backtest de cartera con gestión
-- de riesgo real (backtest/portfolio_simulator.py) — distinto de
-- backtest_runs (Fase 1), que sigue existiendo y sigue siendo necesario: son
-- dos preguntas distintas (ver AUDIT_LEAN.md §2.2.3 y ARCHITECTURE_LEAN.md §8):
--   - backtest_runs / compute_car(): ¿existe un efecto estadístico? Ventana
--     fija D+1→D+5/D+20, sin gestión de posición — es lo que sostiene T1/T2
--     (placebo y réplica). NO SE TOCA.
--   - portfolio_trades: si de verdad se operara esto con sizing, stops y
--     límites de concurrencia, ¿qué equity curve y qué métricas de riesgo
--     resultarían? Bucle diario con TP/SL/trailing/max-holding real.
--
-- Una posición con trailing stop que cierra en varios tramos (30% aquí, 30%
-- allá) se consolida en UNA fila por posición, no una fila por tramo: el
-- spec pide un registro por trade con un solo entry/exit, así que exit_price
-- y pnl_pct son el promedio ponderado de los tramos, y exit_date es la
-- fecha del último tramo (posición totalmente cerrada). Ver portfolio_simulator.py.
-- ============================================================================
CREATE TABLE IF NOT EXISTS portfolio_trades (
    trade_id               BIGSERIAL PRIMARY KEY,
    event_id               BIGINT NOT NULL REFERENCES events(event_id),
    version                TEXT NOT NULL CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    -- Para BALANCED, cada trade se ejecuta con las reglas de Conservative O
    -- Aggressive (ver portfolio_strategies.py:classify_balanced_execution_style) —
    -- execution_style registra cuál, para poder auditar la mezcla real.
    execution_style         TEXT NOT NULL CHECK (execution_style IN ('CONSERVATIVE', 'AGGRESSIVE')),
    direction               TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_date              DATE NOT NULL,
    entry_price             NUMERIC NOT NULL,
    exit_date               DATE NOT NULL,
    exit_price              NUMERIC NOT NULL,
    exit_reason             TEXT NOT NULL CHECK (exit_reason IN ('TAKE_PROFIT', 'STOP_LOSS', 'MAX_HOLDING', 'TRAILING_STOP')),
    pnl_pct                 NUMERIC NOT NULL,      -- retorno neto de comisiones, con signo
    pnl_abs                 NUMERIC NOT NULL,      -- en $ sobre el tamaño de posición asignado
    position_size_pct       NUMERIC NOT NULL,      -- % de la cartera en el momento de la entrada
    position_size_dollars   NUMERIC NOT NULL,
    confidence              NUMERIC NOT NULL,       -- confidence_in_conviction del Judge, en la decisión
    ev                      NUMERIC NOT NULL,        -- ev_{version} usado para el umbral de entrada
    prediction              NUMERIC NOT NULL,        -- net_conviction del Judge (dirección + fuerza)
    actual_move_pct         NUMERIC NOT NULL,        -- retorno real del SUBYACENTE entry->exit (puede
                                                      -- diferir de pnl_pct: pnl_pct ya lleva comisiones
                                                      -- y el efecto de cierres parciales por trailing stop)
    had_survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,
    run_batch_tag            TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Checksums anti-look-ahead (spec Fase Backtesting): la entrada nunca es
    -- D0, y la salida nunca es anterior o igual a la entrada.
    CONSTRAINT chk_portfolio_no_lookahead CHECK (exit_date >= entry_date),
    -- Sin esto, reejecutar simulate_portfolio() con el MISMO run_batch_tag
    -- (ej. un re-disparo manual del workflow el mismo día) duplicaría cada
    -- trade — mismo patrón que backtest_runs (Fase 1), que sí lo tenía desde
    -- el principio; se encontró la falta al revisar la idempotencia antes
    -- de cablear esto al cron nocturno.
    UNIQUE (event_id, version, run_batch_tag)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_trades_version_tag ON portfolio_trades (version, run_batch_tag);
CREATE INDEX IF NOT EXISTS idx_portfolio_trades_event ON portfolio_trades (event_id);

-- La UNIQUE de arriba (dentro del CREATE TABLE) solo llega a una instalación
-- NUEVA — CREATE TABLE IF NOT EXISTS es un no-op sobre una tabla que ya
-- existe, y a diferencia de una columna, Postgres no soporta
-- "ADD CONSTRAINT IF NOT EXISTS" de forma nativa. Se encontró exactamente
-- este caso al añadir la constraint sobre el Postgres de esta sesión, que ya
-- tenía portfolio_trades de antes: un bloque DO condicional es la forma
-- correcta de hacerlo idempotente (misma lección que high_raw/low_raw y
-- filing_text — ver más arriba en este fichero).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'portfolio_trades_event_id_version_run_batch_tag_key'
    ) THEN
        ALTER TABLE portfolio_trades ADD CONSTRAINT portfolio_trades_event_id_version_run_batch_tag_key
            UNIQUE (event_id, version, run_batch_tag);
    END IF;
END $$;

-- Curva de equity DIARIA (no solo en días de trade): balance = cash +
-- valor a mercado de las posiciones abiertas ese día, marcado con el cierre
-- del día. Es lo que pide el spec ("Plotea balance(date) para 5 años").
CREATE TABLE IF NOT EXISTS portfolio_equity_curve (
    version         TEXT NOT NULL CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    trade_date      DATE NOT NULL,
    balance         NUMERIC NOT NULL,
    n_open_positions INT NOT NULL DEFAULT 0,
    run_batch_tag    TEXT NOT NULL,
    PRIMARY KEY (version, trade_date, run_batch_tag)
);

-- portfolio_reports: el reporte JSON completo que arma
-- backtest/portfolio_report.py:run_full_backtest() (métricas, submétricas
-- por tipo de evento, calibración, regresión, top 10, sesgos, y la
-- recomendación final), una fila por run_batch_tag. Existe para que el
-- dashboard de Next.js (app/, de solo lectura) pueda RENDERIZAR el reporte
-- sin reimplementar en TypeScript/SQL las fórmulas de Sharpe/Sortino/
-- Calmar/calibración que ya viven en pipeline/backtest/portfolio_metrics.py
-- — el mismo principio que ya señalaba app/lib/queries.ts sobre no
-- duplicar el bootstrap de backtester.py del lado del dashboard.
CREATE TABLE IF NOT EXISTS portfolio_reports (
    run_batch_tag   TEXT PRIMARY KEY,
    report_json     JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Fase 4 (spec del usuario) — Paper trading simulado sobre la última semana
-- completa de datos disponibles. Ver pipeline/paper_trading/simulator.py.
--
-- DIFERENCIAS DELIBERADAS frente a portfolio_trades (documentadas, no un
-- descuido): sin dólares (el spec de paper trading no pide sizing ni P&L en
-- $, solo pnl_pct — igual que el dashboard de la Fase 1 ya usaba
-- cumulative_return_pct, no balance), sin trailing-stop de Aggressive (el
-- log del spec no tiene campo para cierres parciales, solo un status
-- OPEN/CLOSED_TP/CLOSED_SL/CLOSED_TIMEOUT de una sola pieza), y con estado
-- OPEN real y persistente: a diferencia del backtest histórico (que fuerza
-- el cierre de todo lo que quede abierto al final de los datos), aquí una
-- posición sin TP/SL/timeout resuelto dentro de los datos de precio
-- disponibles hasta hoy se queda OPEN — es correcto, no un bug: significa
-- "todavía no lo sabemos". Volver a correr el simulador con más días de
-- precio ya cargados resuelve la posición de forma natural (ON CONFLICT
-- DO UPDATE), sin necesitar un mecanismo de estado incremental aparte.
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id        SERIAL PRIMARY KEY,
    run_batch_tag   TEXT NOT NULL,
    week_start      DATE NOT NULL,
    week_end        DATE NOT NULL,
    event_id        INT NOT NULL REFERENCES events(event_id),
    version         TEXT NOT NULL CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    direction       TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_date      DATE NOT NULL,
    entry_price     NUMERIC NOT NULL,
    exit_date       DATE,
    exit_price      NUMERIC,
    exit_reason     TEXT CHECK (exit_reason IN ('TAKE_PROFIT', 'STOP_LOSS', 'TIMEOUT')),
    status          TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED_TP', 'CLOSED_SL', 'CLOSED_TIMEOUT')),
    pnl_pct         NUMERIC,
    confidence      NUMERIC NOT NULL,
    ev              NUMERIC NOT NULL,
    prediction      NUMERIC NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_paper_no_lookahead CHECK (exit_date IS NULL OR exit_date >= entry_date),
    UNIQUE (event_id, version, run_batch_tag)
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_version_tag ON paper_trades (version, run_batch_tag);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status);

-- paper_trading_reports: mismo patrón que portfolio_reports — el reporte
-- semanal completo (log, comparación predicción-vs-real, calibración de la
-- semana, alerts) como JSON, para que el dashboard no reimplemente nada.
CREATE TABLE IF NOT EXISTS paper_trading_reports (
    run_batch_tag   TEXT PRIMARY KEY,
    week_start      DATE NOT NULL,
    week_end        DATE NOT NULL,
    report_json     JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- fundamentals: cuentas anuales reales desde XBRL de la SEC
-- (pipeline/ingest/xbrl_fundamentals.py). Insumo del análisis de largo plazo
-- (calidad del negocio + valoración), que hasta ahora no existía: el proyecto
-- solo tenía eventos y precios.
--
-- filed_at es la piedra angular anti-look-ahead de esta tabla, igual que
-- d0_close_date lo es para events: el ejercicio cerrado el 31-12 no se
-- conoce hasta que se presenta el 10-K semanas después. TODA lectura desde
-- el camino de decisión debe acotarse por filed_at, NUNCA por
-- fiscal_period_end (ver la cabecera de xbrl_fundamentals.py).
--
-- Columnas NULL cuando la empresa no reporta esa magnitud bajo ninguna
-- etiqueta us-gaap conocida — nunca imputadas.
-- ============================================================================
CREATE TABLE IF NOT EXISTS fundamentals (
    cik                  TEXT NOT NULL REFERENCES universe(cik),
    fiscal_period_end    DATE NOT NULL,   -- período que cubre el dato
    filed_at             DATE NOT NULL,   -- cuándo se hizo PÚBLICO (anti-look-ahead)
    form                 TEXT NOT NULL,   -- '10-K'
    revenue              NUMERIC,
    net_income           NUMERIC,
    stockholders_equity  NUMERIC,
    total_assets         NUMERIC,
    total_liabilities    NUMERIC,
    long_term_debt       NUMERIC,
    operating_cash_flow  NUMERIC,
    capex                NUMERIC,
    shares_outstanding   NUMERIC,
    fetched_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cik, fiscal_period_end, form),
    -- Un ejercicio no puede presentarse antes de cerrar. Si esto salta, el
    -- parseo está confundiendo las dos fechas — que es justo el bug que esta
    -- tabla está diseñada para hacer imposible.
    CONSTRAINT chk_fundamentals_filed_after_period CHECK (filed_at >= fiscal_period_end)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_cik_filed ON fundamentals (cik, filed_at);

-- quality_scores: salida de analyze/quality_score.py — una nota por empresa
-- y fecha de cálculo, con el desglose por criterio en JSONB para que el
-- dashboard lo renderice sin reimplementar ninguna fórmula en TypeScript
-- (mismo principio que portfolio_reports / validation_reports).
--
-- as_of_date es la fecha con la que se acotó filed_at al calcular: deja
-- explícito en la propia fila QUÉ se sabía cuando se calculó esa nota, en vez
-- de depender de cuándo se corrió el proceso.
CREATE TABLE IF NOT EXISTS quality_scores (
    cik           TEXT NOT NULL REFERENCES universe(cik),
    as_of_date    DATE NOT NULL,
    total_score   NUMERIC,           -- NULL si no había ni un criterio calculable
    verdict       TEXT NOT NULL,
    components    JSONB NOT NULL,    -- desglose por criterio, con explicación en texto
    n_years       INT NOT NULL,
    price_used    NUMERIC,           -- precio con el que se valoró (NULL = sin componente de precio)
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cik, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_quality_scores_as_of ON quality_scores (as_of_date, total_score DESC);

-- ============================================================================
-- validation_reports: Fase 6 (pipeline/validation/report.py) — event study,
-- sensibilidad y veredicto GREENLIGHT/YELLOWLIGHT/REDLIGHT, persistidos en
-- Postgres en vez de solo como docs/VALIDATION_REPORT.md.
--
-- Por qué esta tabla, y por qué no existía antes: el runner de GitHub
-- Actions es efímero (ARCHITECTURE_LEAN.md §1.5) y este proyecto nunca ha
-- hecho commits automáticos — un fichero Markdown escrito en el runner se
-- perdía al terminar el job, así que report.py quedó documentado como "paso
-- manual/local" y jamás corrió en el cron nocturno. Guardar el mismo
-- payload como JSONB (mismo patrón que portfolio_reports/
-- paper_trading_reports, que sí sobreviven porque viven en Neon, no en el
-- runner) resuelve eso sin tocar nada del cálculo: se sigue escribiendo el
-- .md localmente cuando se corre a mano, y ADEMÁS se persiste aquí para que
-- el dashboard lo lea cada noche.
-- ============================================================================
CREATE TABLE IF NOT EXISTS validation_reports (
    run_batch_tag   TEXT PRIMARY KEY,
    report_json     JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- oos_runs: registro de cada vez que alguien mira el Out-of-Sample
-- (BUGS_REPORT.md H-07). El OOS solo se lanza a mano (workflow
-- oos_manual.yml); cada lanzamiento queda aquí con fecha, tag, commit, quién
-- y por qué, para que se sepa cuántas veces se ha mirado.
-- ============================================================================
CREATE TABLE IF NOT EXISTS oos_runs (
    oos_run_id      SERIAL PRIMARY KEY,
    run_batch_tag   TEXT NOT NULL,
    paso            TEXT NOT NULL,
    git_sha         TEXT NOT NULL,
    lanzado_por     TEXT,
    motivo          TEXT NOT NULL,
    lanzado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- notified_at (event_analyses): marca de "ya se avisó por Telegram de esta
-- señal" (pipeline/notify/signals_notifier.py). Vía ALTER, no en el CREATE
-- TABLE de arriba — mismo motivo que high_low_range_pct/filing_text más
-- arriba: CREATE TABLE IF NOT EXISTS es un no-op sobre una tabla que ya
-- existe en cualquier base que no sea una instalación nueva. NULL = todavía
-- no notificado; se pone a now() justo después de un envío correcto, nunca
-- antes, para que un fallo de red a mitad del envío no se pierda: si el
-- paso falla a mitad, la próxima pasada reintenta las filas que se
-- quedaron en NULL.
-- ============================================================================
ALTER TABLE event_analyses ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;

-- Grupo de control (auditoría, Tanda 1): la decisión que se habría tomado
-- SIN la IA, con la misma Etapa 6, el mismo EV y la misma abstención, pero
-- con la dirección y la confianza de los análogos en lugar de las del Judge.
-- Comparar ambas sobre los mismos eventos es la única forma de saber si la IA
-- añade algo. JSONB con los ingredientes en bruto (dirección, magnitud,
-- confianza) para poder recalcular el control con otra regla sin re-analizar.
ALTER TABLE event_analyses ADD COLUMN IF NOT EXISTS decision_sin_ia JSONB;

-- ============================================================================
-- notifications_sent: deduplicación de avisos que, a diferencia de una señal
-- de trading (una fila de event_analyses, notificada una única vez), pueden
-- recalcularse idénticos noche tras noche — las alerts de paper trading
-- (pipeline/paper_trading/analysis.py:compute_alerts) se recomputan sobre la
-- MISMA semana en cada pasada nocturna (run_batch_tag es por semana, no por
-- día — ver paper_trading/report.py), así que sin esta tabla el mismo
-- "2 pérdidas consecutivas en BALANCED" se reenviaría cada noche mientras la
-- semana siga abierta.
--
-- notification_id es una clave estable construida por el notificador
-- (típicamente run_batch_tag + version + type + un hash corto del mensaje) —
-- el INSERT ... ON CONFLICT DO NOTHING RETURNING sirve de "compare-and-set"
-- atómico: si la fila ya existía, no se devuelve nada y el notificador sabe
-- que ese aviso concreto ya se mandó y no lo repite.
-- ============================================================================
CREATE TABLE IF NOT EXISTS notifications_sent (
    notification_id   TEXT PRIMARY KEY,
    channel            TEXT NOT NULL DEFAULT 'telegram',
    sent_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- DYNAMIC (4ª versión de portfolio_simulator.py — ver nota 5 del docstring
-- de pipeline/backtest/portfolio_strategies.py): amplía los CHECK de
-- `version` en portfolio_trades/portfolio_equity_curve para admitirla. Vía
-- ALTER (DROP + ADD), no editando el CHECK del CREATE TABLE de arriba —
-- mismo motivo que el resto de ALTERs de este fichero: CREATE TABLE IF NOT
-- EXISTS es un no-op sobre una tabla que ya existe, así que el CHECK
-- original se quedaría desactualizado en cualquier base que no sea una
-- instalación nueva. DYNAMIC reutiliza trade_decision_balanced (no hay columna
-- trade_decision_dynamic ni falta añadirla), así que backtest_runs.strategy_version
-- (Fase 1, backtester.py — un sistema distinto y anterior a
-- portfolio_simulator.py) y paper_trades.version (Fase 4 paper trading, que
-- no simula sizing en dólares — ver la nota 2 de paper_trading/simulator.py,
-- así que DYNAMIC no le aporta nada nuevo ahí) se quedan tal cual, sin
-- DYNAMIC.
-- ============================================================================
ALTER TABLE portfolio_trades DROP CONSTRAINT IF EXISTS portfolio_trades_version_check;
ALTER TABLE portfolio_trades ADD CONSTRAINT portfolio_trades_version_check
    CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED', 'DYNAMIC'));

ALTER TABLE portfolio_equity_curve DROP CONSTRAINT IF EXISTS portfolio_equity_curve_version_check;
ALTER TABLE portfolio_equity_curve ADD CONSTRAINT portfolio_equity_curve_version_check
    CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED', 'DYNAMIC'));

-- ============================================================================
-- DATA_GAP (exit_reason nuevo — bug de auditoría corregido en
-- portfolio_simulator.py:_resolve_forced_close): el cierre forzado al final
-- del panel de precios asumía que la última fila de `prices` de un ticker
-- SIEMPRE tiene close_raw válido — falso cuando el ticker se deslista o
-- entra en halt a mitad de una posición abierta (la última fila es un
-- centinela de survivorship_warning con close_raw=NULL, ver
-- yfinance_backfill.py:_flag_full_gap). `float(None)` ahí no sesgaba el
-- resultado: reventaba la corrida entera. DATA_GAP marca ese cierre como
-- distinto de un MAX_HOLDING normal — un trade cerrado por falta de datos
-- posteriores (deslistado/halt sin resolver), no porque venciera el holding
-- period con precios normales — para que no se confundan al leer los
-- reportes ni al auditar sesgos.
--
-- El DROP+ADD CONSTRAINT que originalmente iba aquí se ELIMINÓ (no se
-- duplica): con DOS bloques DROP+ADD sucesivos para el mismo nombre de
-- constraint, cada re-ejecución de schema.sql (init_schema, en cada test o
-- despliegue) revalida TODAS las filas existentes contra el PRIMER bloque
-- antes de llegar al segundo — en cuanto una sola fila usara un exit_reason
-- añadido más tarde (ver memoria de tesis más abajo), ese primer bloque,
-- más estrecho, fallaba con CheckViolation y rompía init_schema() para
-- siempre en cualquier base de datos que ya tuviera una fila así (bug real,
-- encontrado exactamente así al escribir los tests de integración de la
-- memoria de tesis). La única definición vigente de este CHECK es la de más
-- abajo (bloque "portfolio_trades: enlace a la tesis..."), que ya incluye
-- TAKE_PROFIT/STOP_LOSS/MAX_HOLDING/TRAILING_STOP/DATA_GAP de este hallazgo
-- más los 4 nuevos de la memoria de tesis.
-- ============================================================================

-- ============================================================================
-- circuit_breaker_active (portfolio_equity_curve): hallazgo de la auditoría,
-- prioridad máxima — protección de capital real. Marca, día a día, si el
-- circuit-breaker de drawdown (portfolio_simulator.DRAWDOWN_CIRCUIT_BREAKER_PCT,
-- 15% pico-a-valle sobre la equity de ESA versión) estaba activo y por tanto
-- bloqueando entradas nuevas (las posiciones ya abiertas se siguen
-- gestionando con sus reglas normales — ver el docstring de la constante en
-- portfolio_simulator.py para el porqué de "solo bloquear entradas" en vez
-- de forzar liquidación). Se persiste día a día, no solo un resumen
-- agregado, para poder mostrar en el dashboard EXACTAMENTE cuándo estuvo
-- activo, no solo si estuvo activo alguna vez.
-- ============================================================================
ALTER TABLE portfolio_equity_curve ADD COLUMN IF NOT EXISTS circuit_breaker_active BOOLEAN NOT NULL DEFAULT FALSE;

-- ============================================================================
-- had_adv_cap_applied (portfolio_trades): hallazgo de la auditoría — tope de
-- posición por %ADV (portfolio_simulator.MAX_POSITION_PCT_OF_ADV, 5% del
-- volumen medio diario en $ de los 60 días de negociación ANTERIORES a la
-- entrada — calculado desde el propio panel de precios del backtest, NO
-- desde universe.adv_usd_60d, que se recalcula sobre los 60 días más
-- recientes respecto a HOY y aplicarlo a un trade histórico sería un
-- look-ahead sutil; ver compute_trailing_adv_usd). TRUE cuando el tamaño que
-- el sizing por confianza/EV pedía se tuvo que REDUCIR por falta de
-- liquidez real del ticker (nunca se descarta el trade, solo se dimensiona
-- con más cautela — decisión explícita del usuario). Se persiste por trade
-- para poder auditar cuántas señales se operaron con menos capital del
-- pedido y en qué tickers/clases de evento concentra el problema.
-- ============================================================================
ALTER TABLE portfolio_trades ADD COLUMN IF NOT EXISTS had_adv_cap_applied BOOLEAN NOT NULL DEFAULT FALSE;

-- ============================================================================
-- adv_usd_60d (event_enrichment): hallazgo de la auditoría — segundo
-- componente del proxy de liquidez de abstention_engine.py (junto a
-- high_low_range_pct, ver su docstring). ADV en $ de los 60 días de
-- negociación ANTERIORES al evento (enrichment.py:compute_enrichment) —
-- mismo cálculo point-in-time que portfolio_simulator.compute_trailing_adv_usd,
-- deliberadamente NO universe.adv_usd_60d por el mismo motivo documentado
-- ahí (esa columna usa los 60 días más recientes respecto a HOY, no
-- respecto a la fecha del evento).
-- ============================================================================
ALTER TABLE event_enrichment ADD COLUMN IF NOT EXISTS adv_usd_60d NUMERIC;

-- ============================================================================
-- theses / thesis_updates: memoria de tesis (hallazgo de auditoría — el
-- sistema no recordaba por qué emitió una alerta ayer). Ver
-- pipeline/backtest/thesis_engine.py para la lógica completa de las
-- condiciones objetivas y pipeline/backtest/portfolio_simulator.py para la
-- integración con el bucle diario (gateada por config.THESIS_MEMORY_ENABLED,
-- desactivada por defecto — cero cambio de comportamiento hasta que se
-- decida activarla).
--
-- Una tesis = una posición REALMENTE abierta en el backtest (entry_price ya
-- conocido) — no una alerta de event_analyses en abstracto, que no tiene ni
-- precio de entrada ni versión de estrategia. Point-in-time: created_at_date
-- es la fecha de entrada real (D+1, la misma que portfolio_trades.entry_date),
-- nunca el reloj real de cuándo corrió el pipeline.
--
-- invalidation_conditions es JSON estructurado y fijo desde la creación
-- (nunca se reescribe): {"price_below": float|null, "price_above": float|null,
-- "opposite_event_classes": [str,...], "max_holding_days": int}. Ver
-- thesis_engine.py sobre por qué price_below/price_above se dejan en null en
-- esta versión (con STOP_LOSS comprobado primero sobre low/high de cada día,
-- cualquier condición de precio en la misma dirección que el STOP_LOSS queda
-- matemáticamente subsumida por él — no es una limitación de datos, es una
-- consecuencia de cómo ya funciona el simulador, documentada explícitamente
-- en vez de fingir una condición que nunca dispararía primero).
-- ============================================================================
CREATE TABLE IF NOT EXISTS theses (
    thesis_id               BIGSERIAL PRIMARY KEY,
    ticker                  TEXT NOT NULL,
    event_id                BIGINT NOT NULL REFERENCES events(event_id),  -- evento origen de la alerta
    version                 TEXT NOT NULL CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED', 'DYNAMIC')),
    execution_style         TEXT NOT NULL CHECK (execution_style IN ('CONSERVATIVE', 'AGGRESSIVE')),
    direction               TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    created_at_date         DATE NOT NULL,        -- point-in-time: entry_date real (D+1), no el reloj real
    entry_price             NUMERIC NOT NULL,
    rationale               TEXT NOT NULL,        -- resumen corto y citable del evento origen (ver thesis_engine.py)
    expected_move_pct       NUMERIC NOT NULL,     -- magnitud (positiva), de analyze.historical_analogues — nunca inventada por el LLM
    expected_horizon_days   INT NOT NULL,
    invalidation_conditions JSONB NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('open', 'fulfilled', 'invalidated', 'saturated', 'expired', 'closed_by_stop')),
    closed_at_date          DATE,
    close_reason_code       TEXT,
    close_rationale         TEXT,
    run_batch_tag           TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Igual que portfolio_trades: sin esto, reejecutar simulate_portfolio()
    -- con el MISMO run_batch_tag duplicaría cada tesis en vez de actualizar
    -- la existente (el backtest recalcula la cartera completa desde cero en
    -- cada corrida — ver el docstring de DRAWDOWN_CIRCUIT_BREAKER_PCT).
    UNIQUE (event_id, version, run_batch_tag)
);

CREATE INDEX IF NOT EXISTS idx_theses_ticker_status ON theses (ticker, status);
CREATE INDEX IF NOT EXISTS idx_theses_run_batch_tag ON theses (run_batch_tag);

-- thesis_updates: log append-only de cada reconciliación (spec: "esto
-- permite debugear decisiones malas después", mismo principio que
-- event_analyses). blind_judgment_event_id es el evento NUEVO cuyo juicio
-- ciego (Judge, ya calculado por event_analysis_pipeline.py SIN ver la
-- tesis) disparó esta actualización — NULL en una comprobación puramente
-- por fecha (EXPIRED, ver thesis_engine.py) que no viene de un evento nuevo.
CREATE TABLE IF NOT EXISTS thesis_updates (
    update_id                BIGSERIAL PRIMARY KEY,
    thesis_id                BIGINT NOT NULL REFERENCES theses(thesis_id),
    as_of_date               DATE NOT NULL,
    trigger                  TEXT NOT NULL CHECK (trigger IN ('new_event', 'daily_check', 'price_check')),
    blind_judgment_event_id  BIGINT REFERENCES events(event_id),
    action                   TEXT NOT NULL CHECK (action IN ('HOLD', 'ADD', 'REDUCE', 'SELL')),
    reason_code               TEXT NOT NULL,
    rationale                 TEXT NOT NULL,
    metrics_snapshot          JSONB NOT NULL,
    run_batch_tag              TEXT NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Mismo motivo que la UNIQUE de `theses`: idempotencia entre corridas con
    -- el mismo run_batch_tag. blind_judgment_event_id puede ser NULL (ver
    -- arriba), así que no puede formar parte de una UNIQUE por sí solo —
    -- se combina con as_of_date, que sí es siempre determinista para una
    -- comprobación dada en una corrida dada.
    UNIQUE (thesis_id, as_of_date, blind_judgment_event_id, run_batch_tag)
);

CREATE INDEX IF NOT EXISTS idx_thesis_updates_thesis ON thesis_updates (thesis_id, as_of_date);

-- ============================================================================
-- portfolio_trades: enlace a la tesis que gobernó la posición (NULL si
-- THESIS_MEMORY_ENABLED estaba desactivado en esa corrida, el caso por
-- defecto) y 4 exit_reason nuevos para las salidas decididas por
-- thesis_engine.py — deliberadamente NO un único "THESIS_CLOSE" genérico:
-- el motivo exacto (cumplida/invalidada/saturada) es justo la información
-- que esta funcionalidad existe para capturar, y colapsarla en un solo
-- valor obligaría a volver a mirar close_reason_code para saber cuál de
-- las 3 fue, duplicando en la práctica lo que el propio exit_reason ya
-- debería decir sin ambigüedad.
-- ============================================================================
ALTER TABLE portfolio_trades ADD COLUMN IF NOT EXISTS thesis_id BIGINT REFERENCES theses(thesis_id);

ALTER TABLE portfolio_trades DROP CONSTRAINT IF EXISTS portfolio_trades_exit_reason_check;
ALTER TABLE portfolio_trades ADD CONSTRAINT portfolio_trades_exit_reason_check
    CHECK (exit_reason IN ('TAKE_PROFIT', 'STOP_LOSS', 'MAX_HOLDING', 'TRAILING_STOP', 'DATA_GAP', 'FULFILLED', 'INVALIDATED', 'SATURATED', 'EXPIRED'));

-- ============================================================================
-- notified_at (paper_trades): marca de "ya se avisó por Telegram de que toca
-- cerrar esta posición" (pipeline/notify/exits_notifier.py). Mismo patrón y
-- mismo motivo que event_analyses.notified_at más arriba (NULL = pendiente,
-- se pone a now() solo tras un envío correcto) — existe porque quien opera a
-- mano en su propio bróker (no hay ejecución automática en este proyecto)
-- solo sabía CUÁNDO ENTRAR (signals_notifier.py ya avisaba de eso) pero no
-- CUÁNDO SALIR: sin esto, la única forma de enterarse de que el sistema
-- detectó un take-profit/stop-loss/límite de tiempo era entrar al dashboard
-- cada día. paper_trades.status ya pasa a CLOSED_TP/CLOSED_SL/CLOSED_TIMEOUT
-- solo (paper_trading/simulator.py, cron nocturno) — este aviso no repite
-- ningún cálculo, solo notifica la primera vez que ve esa transición.
-- ============================================================================
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;

-- ============================================================================
-- technical_analyses: confirmación técnica y plan de operación por señal
-- (pipeline/analyze/technical_analysis.py). Una fila por evento con alguna
-- versión operando. Calculado SOLO con precios de trade_date <= d0_close_date.
-- details guarda indicadores, niveles, comprobaciones, puntuación desglosada,
-- reglas de salida y limitaciones declaradas del cálculo.
-- ============================================================================
CREATE TABLE IF NOT EXISTS technical_analyses (
    event_id            BIGINT PRIMARY KEY REFERENCES events(event_id),
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    direction           TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price         NUMERIC,
    stop_price          NUMERIC,
    target_price        NUMERIC,      -- objetivo parcial (primera resistencia/soporte)
    target2_price       NUMERIC,      -- objetivo final
    risk_reward         NUMERIC,      -- recompensa/riesgo hasta target_price
    confidence          INT NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    passes_filters      BOOLEAN NOT NULL,
    position_size_pct   NUMERIC,      -- % máximo del capital, ya ajustado por volatilidad
    timeframe_days      INT,
    details             JSONB NOT NULL
);

-- Libro de batches enviados a la Batch API (BUGS_REPORT.md H-24). El tope de
-- gasto diario contaba solo los eventos GUARDADOS: un Bull/Bear pagado cuyo
-- Judge fallaba (o cuyo guardado fallaba) no contaba y se volvía a pagar al
-- día siguiente. Se escribe nada más crear el batch, antes de esperar.
CREATE TABLE IF NOT EXISTS ai_batches (
    batch_id      TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,          -- 'bull_bear' | 'judge'
    n_requests    INT NOT NULL,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Gasto REAL de cada batch, de los tokens que devuelve la API (se rellena al
-- terminar; vacío = en curso o sin precio, y se cuenta con la estimación).
ALTER TABLE ai_batches ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE ai_batches ADD COLUMN IF NOT EXISTS input_tokens BIGINT;
ALTER TABLE ai_batches ADD COLUMN IF NOT EXISTS output_tokens BIGINT;
ALTER TABLE ai_batches ADD COLUMN IF NOT EXISTS cost_usd NUMERIC;
ALTER TABLE ai_batches ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

-- BUGS_REPORT.md H-31: la sesión de entrada también se evalúa. Se entra a la
-- apertura, así que un stop u objetivo tocado ese mismo día es una salida
-- legítima (exit_date = entry_date); salir ANTES de entrar sigue prohibido.
-- Las bases ya creadas tenían "exit_date > entry_date": se reemplaza.
-- Solo si aún tiene la forma antigua: así no se bloquea ni se recorre la
-- tabla en cada pasada que aplica el schema.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_portfolio_no_lookahead'
               AND pg_get_constraintdef(oid) LIKE '%exit_date > entry_date%') THEN
        ALTER TABLE portfolio_trades DROP CONSTRAINT chk_portfolio_no_lookahead;
        ALTER TABLE portfolio_trades ADD CONSTRAINT chk_portfolio_no_lookahead CHECK (exit_date >= entry_date);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_paper_no_lookahead'
               AND pg_get_constraintdef(oid) LIKE '%exit_date > entry_date%') THEN
        ALTER TABLE paper_trades DROP CONSTRAINT chk_paper_no_lookahead;
        ALTER TABLE paper_trades ADD CONSTRAINT chk_paper_no_lookahead CHECK (exit_date IS NULL OR exit_date >= entry_date);
    END IF;
END $$;

-- ============================================================================
-- Calidad de precios (Tanda 4; decisión del usuario, 2026-10-06): las velas
-- imposibles y los picos de más del 50 % que se deshacen al día siguiente se
-- marcan con su motivo (NULL = sin problema). Los CAR cuya ventana toca una
-- vela marcada se excluyen (car_results.calidad_excluido), igual que las
-- operaciones del backtest. No se borra nada: el marcado se recalcula.
-- Ver pipeline/ingest/calidad_precios.py.
-- ============================================================================
ALTER TABLE prices ADD COLUMN IF NOT EXISTS calidad_motivo TEXT;
ALTER TABLE car_results ADD COLUMN IF NOT EXISTS calidad_excluido TEXT;
-- FALSE hasta que el control lo ha mirado una vez (los CAR nuevos).
ALTER TABLE car_results ADD COLUMN IF NOT EXISTS calidad_revisada BOOLEAN NOT NULL DEFAULT FALSE;
-- Las velas marcadas son pocas: un índice parcial hace baratas las consultas
-- de exclusión (CAR y backtest) sin pesar en la tabla de precios.
CREATE INDEX IF NOT EXISTS idx_prices_calidad_marcada ON prices (ticker, trade_date) WHERE calidad_motivo IS NOT NULL;
CREATE TABLE IF NOT EXISTS calidad_precios_revision (
    ticker        TEXT PRIMARY KEY,
    revisado_en   TIMESTAMPTZ NOT NULL
);

-- ============================================================================
-- splits: historial de splits por ticker (BUGS_REPORT.md H-04). Yahoo da el
-- Close ajustado por los splits posteriores a la fecha de descarga; con el
-- historial se recupera el precio negociado de verdad:
--   negociado(D) = close_raw(D) × Π ratio de los splits en (D, captured_at].
-- ratio = acciones nuevas por acción vieja (2 en un 2:1; 0,02 en un 1:50).
-- splits_revision: cuándo se pidió el historial de cada ticker.
-- ============================================================================
CREATE TABLE IF NOT EXISTS splits (
    ticker      TEXT NOT NULL,
    split_date  DATE NOT NULL,
    ratio       NUMERIC NOT NULL CHECK (ratio > 0),
    PRIMARY KEY (ticker, split_date)
);
CREATE TABLE IF NOT EXISTS splits_revision (
    ticker       TEXT PRIMARY KEY,
    revisado_en  TIMESTAMPTZ NOT NULL
);

-- ============================================================================
-- xbrl_descargas: cuándo se descargaron por última vez las cuentas XBRL de
-- cada empresa (caché, Tanda 4). La pasada nocturna solo vuelve a pedir una
-- empresa si nunca se pidió, si hace más de 90 días, o si ya le toca un 10-K
-- nuevo y no ha llegado (entonces, como mucho una vez por semana). Ver
-- xbrl_fundamentals.ciks_por_descargar.
-- ============================================================================
CREATE TABLE IF NOT EXISTS xbrl_descargas (
    cik            TEXT PRIMARY KEY,
    descargado_en  TIMESTAMPTZ NOT NULL
);
