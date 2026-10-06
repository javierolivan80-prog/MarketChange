"""config.py — configuración centralizada del pipeline.

Todo lo que necesita un secreto o vía de red vive aquí, léido de entorno.
Nada se hardcodea. Ver RUNBOOK.md para qué variables hay que definir y dónde.
"""
import os

# --- Base de datos ---
# Postgres alojado (Neon/Supabase free tier recomendado — ver RUNBOOK.md).
# Este pipeline se ejecuta en GitHub Actions (con salida a internet completa),
# NO en el sandbox de desarrollo, que tiene el egress bloqueado a EDGAR/FDA/
# Yahoo Finance/Ken French por política de la organización (AUDIT_LEAN.md §1.5).
_db_url = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/money_poc")
DATABASE_URL = _db_url.strip() if _db_url else ""

# --- Anthropic ---
_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
# .strip(): mismo motivo que DATABASE_URL más arriba. Un secreto de GitHub
# pegado con un salto de línea al final llega aquí intacto, y al mandarlo como
# cabecera HTTP (Authorization / x-api-key) la librería lo rechaza:
#   httpx2.LocalProtocolError: Illegal header value b'***\n'
# Un salto de línea en una cabecera es el vector clásico de inyección de
# cabeceras HTTP, así que el rechazo es correcto — lo que hay que arreglar es
# no mandarlo sucio. Sin este strip, el mensaje de "falta ANTHROPIC_API_KEY"
# de más abajo no salta (la variable SÍ existe), y el fallo real queda
# enterrado dentro de las tripas del SDK.
ANTHROPIC_API_KEY = _anthropic_key.strip() if _anthropic_key else None
CLASSIFIER_MODEL = "claude-haiku-4-5"
ANALYZER_MODEL = "claude-haiku-4-5"  # Bull/Bear (Etapas 3-4) — "rápido", pedido por el spec
# Judge (Etapa 5): "mejor reasoning" — el spec de Fase 2 nombra Sonnet 4.6
# explícitamente, así que se usa ese ID en vez de la generación más reciente
# disponible (claude-sonnet-5). Ver adversarial_analyzer.py.
JUDGE_MODEL = "claude-sonnet-4-6"

# --- Telegram (pipeline/notify/telegram.py) ---
# Bot creado con @BotFather (gratis, sin verificación) — ver RUNBOOK.md para
# el paso a paso de cómo sacar el token y el chat_id. Ausentes por defecto:
# sin ellas, telegram.send_message no falla el pipeline, solo se omite (ver
# su docstring) — así este mismo sandbox de desarrollo, que no las tiene
# definidas, puede seguir corriendo el resto del código sin tropezar aquí.
_telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_TOKEN = _telegram_token.strip() if _telegram_token else None
_telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_CHAT_ID = _telegram_chat_id.strip() if _telegram_chat_id else None
# URL pública del panel (app/, Vercel), sin barra final. Opcional: si está
# definida, cada aviso de Telegram enlaza al análisis completo de la señal en
# vez de dejar al usuario solo con el filing en bruto.
_dashboard_url = os.environ.get("DASHBOARD_URL")
DASHBOARD_URL = _dashboard_url.strip().rstrip("/") if _dashboard_url and _dashboard_url.strip() else None

# --- EDGAR ---
# La SEC exige un User-Agent identificable con contacto real. No es opcional:
# sin esto, EDGAR devuelve 403. https://www.sec.gov/os/webmaster-faq#developers
EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "Money-POC-Research contact@example.com"
)
EDGAR_RATE_LIMIT_PER_SEC = 8  # la SEC pide <=10 req/s; 8 deja margen
EDGAR_BASE = "https://www.sec.gov"

# --- Universo invertible (ARCHITECTURE_LEAN.md §10) ---
MIN_PRICE_USD = 5.0
# Bajado de 300 M$ a 50 M$ (pedido explícito: cubrir small caps, no solo
# medianas/grandes). Los filtros de PRECIO y VOLUMEN de abajo son los que de
# verdad protegen de operar algo peligroso (centavos, iliquidez real); este
# umbral es sobre todo una cuestión de tamaño/cobertura de información
# (cuantas menos empresas comparables, menos fiable el histórico de
# análogos), no de seguridad de ejecución — es el que tiene sentido relajar.
# La ingesta de EDGAR (edgar_scraper.py) ya cubre el mercado entero sin
# filtrar por tamaño: este número decide cuánto de ese mercado se ANALIZA y
# se OPERA, no cuánto se ve. El control de gasto diario acumulado
# (DAILY_SPEND_CAP_USD, más abajo) es lo que impide que más empresas
# elegibles dispare el coste — racionará solo, sin tocar nada aquí.
MIN_MARKET_CAP_USD = 50_000_000
MIN_ADV_USD = 1_000_000
# Un precio más viejo que esto no cuenta para decidir si la empresa es
# invertible HOY (deslistadas, tickers que yfinance dejó de servir...).
MAX_PRICE_STALENESS_DAYS = 10


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int | None) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else default


# --- Filtros de los avisos de señal por Telegram (notify/signals_notifier.py) ---
# Una "watchlist" de un solo usuario sin cuentas: variables del workflow, no
# código. Vacías = sin filtro (salvo la antigüedad, que tiene valor por defecto).
#
# ALERT_MAX_AGE_DAYS: solo se avisa de eventos cuyo D0 es de los últimos N
# días. Sin este tope, un backfill histórico (que analiza eventos de hace
# años) mandaba un aviso de "entra" por cada evento antiguo que pasara los
# filtros — señales inservibles, porque la entrada es en D+1. 0 = sin tope.
ALERT_MAX_AGE_DAYS = _env_int("ALERT_MAX_AGE_DAYS", 7) or None
ALERT_MIN_CONFIDENCE = _env_float("ALERT_MIN_CONFIDENCE", 0.0)


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name) or ""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


# Listas separadas por comas, p. ej. ALERT_TICKERS="AAPL,MSFT",
# ALERT_EVENT_CLASSES="8K_2.02_EARNINGS,FDA_CRL".
ALERT_TICKERS = tuple(t.upper() for t in _env_list("ALERT_TICKERS"))
# Avisar solo de señales cuyo plan técnico pasa las comprobaciones previas
# (catalizador confirmado, >= 2 indicadores alineados, riesgo/beneficio >= 1:2,
# stop sobre soporte real — analyze/technical_analysis.py). Desactivado por
# defecto: esa capa todavía no se ha validado con datos reales en el backtest,
# así que de entrada informa en cada aviso pero no decide cuáles se envían.
ALERT_REQUIRE_TECHNICAL = (os.environ.get("ALERT_REQUIRE_TECHNICAL") or "").strip().lower() in ("1", "true", "yes", "si", "sí")
ALERT_EVENT_CLASSES = _env_list("ALERT_EVENT_CLASSES")

# --- Cola del análisis con IA (Bull/Bear/Judge) ---
# Qué empresas pasan por la IA. Por defecto, el universo invertible entero
# (>= MIN_MARKET_CAP_USD). Para centrarse en empresas grandes, subirlo desde
# un secreto/variable del workflow, sin tocar código — p. ej. 10000000000
# (10.000 M$, "large caps").
ANALYSIS_MIN_MARKET_CAP_USD = _env_float("ANALYSIS_MIN_MARKET_CAP_USD", MIN_MARKET_CAP_USD)
# Tope de eventos analizados por corrida: es un tope de GASTO. Con los precios
# de la Batch API (-50%), un evento cuesta ~0,011 $ (2 llamadas Haiku 4.5 +
# 1 Sonnet 4.6 sobre ~8.000 caracteres de filing): 500 eventos ~ 5,5 $/corrida.
# Vacío o 0 = sin tope.
ANALYSIS_MAX_EVENTS_PER_RUN = _env_int("ANALYSIS_MAX_EVENTS_PER_RUN", 500) or None
ANALYSIS_EST_COST_PER_EVENT_USD = 0.011
# Desglose por request, para contar el gasto por batch ENVIADO (H-24): un
# Bull/Bear (Haiku 4.5) ~0,0024 $ y un Judge (Sonnet 4.6) ~0,0062 $, que
# suman los 0,011 $ por evento de arriba (2 x 0,0024 + 0,0062).
EST_COST_BULL_BEAR_REQUEST_USD = 0.0024
EST_COST_JUDGE_REQUEST_USD = 0.0062

# Precios de la API en $ por millón de tokens (entrada, salida), para contar
# el gasto REAL de cada batch con el `usage` que devuelve la API (ver
# event_analysis_pipeline.cerrar_batch). La Batch API cobra la mitad. Si se
# cambia de modelo, añadir aquí su precio: un modelo sin precio se cuenta con
# la estimación de arriba, nunca como gratis.
MODEL_PRICES_USD_PER_MTOK = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5-5": (2.0, 10.0),
}
BATCH_PRICE_FACTOR = 0.5
# Multiplicadores de la caché de prompts sobre el precio de entrada (hoy no
# se usa — H-26 —, pero si vuelve a usarse el coste debe seguir cuadrando).
CACHE_WRITE_PRICE_FACTOR = 1.25
CACHE_READ_PRICE_FACTOR = 0.1


def model_price_usd_per_mtok(model: str | None) -> tuple[float, float] | None:
    """Precio de `model`. La API devuelve a veces el nombre con fecha
    (claude-haiku-4-5-20251001): se busca por prefijo."""
    if not model:
        return None
    for nombre, precio in MODEL_PRICES_USD_PER_MTOK.items():
        if model == nombre or model.startswith(nombre + "-"):
            return precio
    return None


def batch_cost_usd(uso: dict) -> float | None:
    """Coste en $ de un batch a partir de sus tokens (ver
    adversarial_analyzer.run_batch_and_collect). None si el modelo no tiene
    precio."""
    precio = model_price_usd_per_mtok(uso.get("model"))
    if precio is None:
        return None
    entrada, salida = precio
    tokens_entrada = (
        uso.get("input_tokens", 0)
        + CACHE_WRITE_PRICE_FACTOR * uso.get("cache_creation_input_tokens", 0)
        + CACHE_READ_PRICE_FACTOR * uso.get("cache_read_input_tokens", 0)
    )
    coste = (tokens_entrada * entrada + uso.get("output_tokens", 0) * salida) / 1_000_000
    return round(coste * BATCH_PRICE_FACTOR, 6)

# Cota máxima de espera al polling de la Batch API (IMPROVEMENT_PLAN.md R6 +
# M1) — sin esto, adversarial_analyzer.run_batch_and_collect hacía
# `while True: ...; time.sleep(30)` sin límite: si la Batch API se queda
# atascada en "in_progress" (un incidente del lado de Anthropic, no del
# pipeline), el paso de GitHub Actions se queda colgado hasta el
# timeout-minutes del job (sin definir hasta esta sesión -> 360 min por
# defecto de GitHub), quemando horas de CI sin ningún aviso de que algo va
# mal. Los batches reales de este proyecto tardan minutos-decenas de
# minutos (ver el docstring de process_chunk sobre el run 34964242549, ~20
# min para dos batches) — 2 horas da margen de sobra sin acercarse al
# timeout del job.
BATCH_MAX_WAIT_SECONDS = 2 * 60 * 60

# Tope de GASTO DIARIO ACUMULADO (IMPROVEMENT_PLAN.md A2) — distinto de
# ANALYSIS_MAX_EVENTS_PER_RUN de arriba: ese limita el gasto de UNA corrida
# (500 eventos ~ 5,5 $), pero nightly_pipeline.yml programa 3 corridas/día —
# si las 3 agotaran su tope, el gasto real podría llegar a ~16,5 $/día, muy
# por encima del presupuesto real, porque ninguna corrida mira lo que las
# OTRAS corridas del mismo día ya gastaron. Presupuesto decidido: 50 €/mes,
# repartido a partes iguales entre los 30 días del mes (más simple de
# aplicar día a día que un tope mensual que haya que vigilar a mano, y evita
# que un solo día agote el mes entero).
DAILY_SPEND_CAP_EUR = 50.0 / 30
# Sin llamada a una API de forex (mismo principio de parsimonia que el resto
# del proyecto — AUDIT_LEAN.md): un tipo de cambio fijo, deliberadamente
# CONSERVADOR (más bajo que el EUR/USD habitual, ~1.05-1.10 en 2024-2026),
# para que el tope en dólares salga siempre MENOR que el presupuesto real en
# euros, nunca mayor — el error de no consultar el tipo de cambio real se
# paga gastando de menos, no de más.
EUR_USD_RATE_CONSERVATIVE = 1.03
DAILY_SPEND_CAP_USD = DAILY_SPEND_CAP_EUR * EUR_USD_RATE_CONSERVATIVE

# --- Ventanas de evento (ARCHITECTURE_LEAN.md §3, §5) ---
ESTIMATION_WINDOW_DAYS = (-250, -30)
EVENT_WINDOWS_DAYS = [5, 20]

# --- Periodo del POC ---
BACKTEST_START = "2021-01-01"
BACKTEST_END = "2025-12-31"
# Split OOS (ARCHITECTURE_LEAN.md §9, T6): ajustar SOLO en IN_SAMPLE, evaluar
# UNA VEZ en OOS. No se debe ejecutar el backtest sobre OOS más de una vez.
IN_SAMPLE_END = "2023-12-31"
OOS_START = "2024-01-01"

# --- Costes de transacción (T7: barrido de sensibilidad) ---
SLIPPAGE_BPS_SWEEP = [0, 10, 25, 50]
# Deslizamiento base por lado (BUGS_REPORT.md H-32; decisión del usuario,
# auditoría 2026-10-05): se resta del P&L de cada operación en el backtest y
# en el paper trading, además de la comisión. La capitalización es la de D0
# (acciones del último 10-K publicado antes de D0 × cierre de D0), no la de
# hoy; sin ese dato se aplica el de empresa pequeña, el conservador.
SLIPPAGE_LARGE_CAP_USD = 10_000_000_000
SLIPPAGE_BPS_PER_SIDE_LARGE = 10.0
SLIPPAGE_BPS_PER_SIDE_SMALL = 25.0


def slippage_bps_por_lado(market_cap_usd: float | None) -> float:
    if market_cap_usd is not None and market_cap_usd >= SLIPPAGE_LARGE_CAP_USD:
        return SLIPPAGE_BPS_PER_SIDE_LARGE
    return SLIPPAGE_BPS_PER_SIDE_SMALL

# --- Memoria de tesis (hallazgo de auditoría: el sistema no recordaba por qué
# emitió una alerta ayer). Ver pipeline/backtest/thesis_engine.py para la
# lógica completa. Desactivada por defecto — se activa explícitamente para
# comparar con/sin memoria en el mismo backtest; el cron nocturno sigue
# llamando a simulate_portfolio() sin pasar este flag, así que el
# comportamiento en producción no cambia hasta que se decida lo contrario
# con datos reales de esa comparación. ---
THESIS_MEMORY_ENABLED = False

# Umbral de saturación: "movimiento típico" de la clase de evento = la
# MEDIANA (percentil 50, no la media — más robusta a colas largas) del |CAR|
# histórico de esa clase, calculada SOLO con analogos anteriores a la fecha
# de la tesis (reutiliza analyze/historical_analogues.get_historical_analogues,
# ya point-in-time). Saturada = movimiento realizado (a favor de la tesis)
# >= este múltiplo de esa mediana.
THESIS_SATURATION_TYPICAL_MOVE_PERCENTILE = 50
THESIS_SATURATION_MULTIPLE = 2.0
# Igual que historical_analogues.MIN_ANALOGUES_FOR_ANY_CONFIDENCE: por debajo
# de esto, el percentil histórico es ruido, no se calcula (saturación por
# precio deshabilitada para esa clase, la señal de volumen sigue en pie).
THESIS_SATURATION_MIN_ANALOGUES = 5
# Segundo componente de saturación (independiente del precio, ver
# thesis_engine.py): volumen del día del evento nuevo frente a su propia
# media de los ADV_TRAILING_WINDOW_DAYS/ADV_MIN_TRADING_DAYS anteriores
# (portfolio_simulator.py) — MISMA ventana que el tope de posición por ADV,
# reutilizada aquí en vez de inventar una tercera.
THESIS_ABNORMAL_VOLUME_RATIO = 3.0

# Fracción de la posición que se cierra en una reducción por contradicción
# débil (ver arriba). No es 100% (eso sería INVALIDATED) ni 0% (eso sería
# HOLD) — un recorte a la mitad es la respuesta intermedia más simple.
THESIS_REDUCE_FRACTION = 0.5

# Clases de evento que invalidan una tesis por su sola aparición (regla
# objetiva "evento contrario de clase X" del spec), indexadas por la
# DIRECCIÓN de la tesis, no por la clase de origen: un hecho casi
# universalmente negativo (bancarrota, restatement contable) invalidaría
# cualquier tesis LONG sea cual sea el evento que la originó, y viceversa con
# una aprobación de la FDA para una tesis SHORT. Deliberadamente conservador
# y no exhaustivo — es más seguro no marcar como "invalidante" una clase
# ambigua que inventar una tabla de equivalencias caso por caso sin base de
# datos que la respalde. Punto de extensión: ampliar esta tabla es un cambio
# de una línea, no de lógica.
THESIS_INVALIDATING_EVENT_CLASSES = {
    "LONG": ("8K_1.03_BANKRUPTCY", "8K_4.02_RESTATEMENT", "FDA_CRL"),
    "SHORT": ("FDA_APPROVAL",),
}

# Presupuesto de contexto acotado (spec): al reconciliar, como mucho se citan
# las N actualizaciones más recientes de la tesis en el snapshot/rationale,
# no el historial completo.
THESIS_UPDATES_CONTEXT_WINDOW = 5
