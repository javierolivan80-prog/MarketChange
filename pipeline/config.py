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
