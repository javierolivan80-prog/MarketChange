"""yfinance_backfill.py — panel de precios diario, con flags de supervivencia.

INSTRUCCIÓN EXPLÍCITA DEL USUARIO: "flagea tickers deslistados como
'WARNING — posible data gap'. No intentes llenar gaps (es trabajo de Tiingo,
fase 2)." Este archivo hace exactamente eso y nada más: detecta, marca,
nunca interpola ni rellena.

DISEÑO (ver AUDIT_LEAN.md §2.4 y ARCHITECTURE_LEAN.md §4, §7):
  - close_raw + adj_factor se guardan por separado. yfinance recalcula los
    cierres ajustados retroactivamente por splits/dividendos; guardar solo
    el ajustado rompe la reproducibilidad del backtest de un día para otro.
  - captured_at registra CUÁNDO se descargó, no la fecha del precio — permite
    detectar si una fila fue recalculada en una descarga posterior.
  - survivorship_warning = TRUE cuando, para un ticker con eventos en el rango
    solicitado, faltan filas en fechas de calendario bursátil esperadas
    DENTRO de ese rango. Esto es una señal, no una corrección.
  - Descarga por lotes con pausa entre lotes + backoff exponencial (yfinance
    no es oficial, sufre 429 alrededor de los ~950 tickers seguidos — ver
    ARCHITECTURE_LEAN.md §7). Resumible: se puede parar y reanudar por ticker.
  - La idempotencia NO viene de una caché HTTP (no hay ninguna en este
    fichero — el docstring lo afirmaba hasta IMPROVEMENT_PLAN.md M11, sin que
    el código la tuviera nunca) sino de pendientes_de_descarga(): compara
    contra ultimo_dia_guardado() en Postgres y solo pide la cola que falta,
    así que reejecutar el mismo (ticker, rango) no vuelve a pedir nada ya
    guardado sin necesidad de cachear la respuesta HTTP en sí.

ADVERTENCIA DE VALIDACIÓN — sin ejecutar en vivo (egress bloqueado a
query1/query2.finance.yahoo.com, AUDIT_LEAN.md §1.5). La forma de uso de
`yfinance.download()` sigue la API pública documentada de la librería, pero
no se ha podido correr contra el servidor real desde aquí. Lanzar contra
5-10 tickers conocidos y revisar a mano antes del backfill de 2.500+.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta

import pandas as pd

from pipeline import config
from pipeline.ingest.ticker_map import is_tradable_symbol  # noqa: F401 — también se usa desde fuera

logger = logging.getLogger(__name__)

BATCH_SIZE = 50           # tickers por lote — margen de sobra bajo el umbral de ~950 reportado
PAUSE_BETWEEN_BATCHES_S = 5
MAX_RETRIES = 4
BACKOFF_BASE_S = 2         # 2, 4, 8, 16 — misma política que el resto del proyecto
DOWNLOAD_THREADS = 8       # descargas en paralelo dentro de un lote (ver _descargar_lote_con_reintentos)
# Tope de tamaño de la base de datos (MB) por encima del cual se deja de
# descargar: mejor precios incompletos que una base de datos llena (el plan
# gratuito de Neon/Supabase ronda los 500 MB). Mismo valor y variable que la
# carga de histórico (ops_history_prices.py).
MAX_DB_MB = float(os.environ.get("HISTORY_MAX_DB_MB", "400"))

# Splits (BUGS_REPORT.md H-03). Con auto_adjust=False, la columna Close de
# yfinance NO es el precio negociado: ya viene ajustada por splits, en la base
# del día de la descarga. Como las descargas son incrementales, un split entre
# dos descargas deja las filas viejas en la base anterior y las nuevas en la
# posterior: un salto falso de −50 % (o +900 % en un contrasplit) que rompe
# CAR, beta, stops del backtest y paper trading, en silencio y para siempre.
#
# Por eso cada descarga incremental vuelve a pedir el último día ya guardado:
# si su Close ya no coincide, la base cambió y se rebaja la serie entera del
# ticker. Los dividendos no tocan Close (solo Adj Close), así que una
# diferencia aquí es un split o una corrección de Yahoo; ambas se arreglan
# igual. 3 %: muy por encima de las revisiones de redondeo y muy por debajo
# del split más pequeño habitual (5:4, un 20 %).
TOLERANCIA_CAMBIO_DE_BASE = 0.03
# Reparación de lo que ya se guardó mal antes de este arreglo: un salto entre
# dos días consecutivos que vinieron de descargas DISTINTAS (captured_at
# diferente) es la firma de un cambio de base. Un salto real también puede
# cumplirlo (una biotech tras la FDA): el coste es rebajar ese ticker una vez,
# porque después toda la serie comparte captured_at y deja de ser candidata.
# 30 %: capta los splits 3:2 y mayores.
UMBRAL_SALTO_DE_BASE = 0.30
# Rebajas completas por corrida: la primera noche puede haber muchas
# candidatas acumuladas; el resto se hace en las siguientes.
MAX_REPARACIONES_POR_CORRIDA = int(os.environ.get("PRICE_REPAIR_MAX", "200"))

def database_mb(conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database()) AS b")
        row = cur.fetchone()
    return (row["b"] if isinstance(row, dict) else row[0]) / 1e6


def _retry_with_backoff(func, description: str):
    """Reintenta func() hasta MAX_RETRIES veces con backoff BACKOFF_BASE_S *
    2**intento (2, 4, 8, 16s — misma secuencia que edgar_http.throttled_get,
    aunque calculada con una fórmula en vez de una lista literal: ambos
    archivos hablan con APIs distintas y no comparten dependencias, así que
    no había manera limpia de que compartieran una sola constante sin
    acoplar dos módulos que no tienen nada más en común).

    Hallazgo de auditoría (IMPROVEMENT_PLAN.md Q5): _download_one_with_retry
    y _descargar_lote_con_reintentos tenían el MISMO bucle de reintentos
    duplicado palabra por palabra, solo cambiando qué se llama y qué texto
    se loguea — exactamente el tipo de cosa que diverge en silencio con el
    tiempo (un cambio en una copia y no en la otra).

    func no debe tener efectos secundarios que no sean seguros de repetir
    (aquí, siempre una llamada de red de solo lectura a yfinance). Devuelve
    el resultado de func(), o None si se agotan los intentos."""
    for attempt in range(MAX_RETRIES):
        try:
            return func()
        except Exception as exc:  # yfinance no tiene una jerarquía de excepciones propia estable
            delay = BACKOFF_BASE_S * (2**attempt)
            logger.warning("Fallo %s (intento %d): %s — esperando %ds", description, attempt, exc, delay)
            time.sleep(delay)
    logger.error("%s falló tras %d intentos", description, MAX_RETRIES)
    return None


def _trading_days_expected(start: date, end: date) -> set[date]:
    """Días en los que la bolsa abre de verdad, festivos incluidos.

    ANTES era lunes-viernes a secas, con un comentario que daba los falsos
    positivos por "aceptables porque el flag es una SEÑAL DE ALERTA". No lo
    eran: en el primer run con precios reales marcó 14 huecos en CASI LOS 150
    tickers, 3M y Adobe entre ellos, y los 14 eran exactamente los festivos del
    rango. Una alerta que salta en todos los casos a la vez no avisa de nada —
    tapaba justo lo que tenía que detectar.
    """
    from pipeline.ingest.market_calendar import dias_de_negociacion

    return dias_de_negociacion(start, end)


COLUMNAS_REQUERIDAS = ("Open", "High", "Low", "Close", "Adj Close", "Volume")


def aplanar_columnas(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Deja las columnas en un solo nivel ('Close', 'Open'...).

    BUG REAL (2026-09-15): yfinance devuelve las columnas como MultiIndex
    (campo, ticker) TAMBIÉN cuando se pide un único ticker. Con eso,
    row['Close'] no da un número sino una Series de un elemento, y
    float(...) revienta con:

        TypeError: float() argument must be a string or a real number,
                   not 'Series'

    Se quita el nivel que contiene el ticker, esté donde esté, en vez de
    asumir que es el último: si yfinance cambia el orden de los niveles, el
    aplanado sigue siendo correcto.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    for nivel in range(df.columns.nlevels):
        if set(df.columns.get_level_values(nivel)) <= {ticker}:
            return df.droplevel(nivel, axis=1)
    return df.droplevel(nivel_de_tickers(df.columns), axis=1)


def nivel_de_tickers(columnas: pd.MultiIndex) -> int:
    """Cuál de los dos niveles lleva los tickers y cuál los campos.

    Se identifica por el CONTENIDO, no por la posición: el nivel de campos es
    el que trae 'Open', 'Close' y compañía; el otro es el de tickers. Así el
    código aguanta que yfinance invierta el orden de los niveles, que es
    justamente el tipo de suposición sobre un formato ajeno que ya ha costado
    varios fallos en producción en este proyecto.
    """
    for nivel in range(columnas.nlevels):
        if not set(columnas.get_level_values(nivel)) & set(COLUMNAS_REQUERIDAS):
            return nivel
    return columnas.nlevels - 1  # por defecto de yfinance, el ticker va el último


def _validar_columnas(df: pd.DataFrame, ticker: str) -> None:
    """Falla en voz alta si falta una columna o si alguna está duplicada.

    Una columna duplicada hace que row['Close'] vuelva a ser una Series, que
    es exactamente el fallo que se acaba de arreglar: sin esta comprobación
    reaparecería como el mismo TypeError críptico a mitad de la descarga, en
    vez de decir qué columnas trae el DataFrame de verdad.
    """
    faltan = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    duplicadas = [c for c in COLUMNAS_REQUERIDAS if list(df.columns).count(c) > 1]
    if faltan or duplicadas:
        raise ValueError(
            f"Columnas inesperadas en los precios de {ticker} "
            f"(faltan: {faltan or 'ninguna'}; duplicadas: {duplicadas or 'ninguna'}). "
            f"Columnas recibidas: {list(df.columns)}. ¿Cambió el formato de yfinance?"
        )


def _download_one_with_retry(ticker: str, start: date, end: date) -> pd.DataFrame | None:
    import yfinance as yf

    def _intentar():
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,  # crítico: queremos Close crudo Y Adj Close por separado
            progress=False,
            threads=False,
        )
        return aplanar_columnas(df, ticker) if df is not None else None

    return _retry_with_backoff(_intentar, f"descargando {ticker}")


def _descargar_lote_con_reintentos(tickers: list[str], start: date, end: date) -> pd.DataFrame | None:
    """Un lote entero en UNA sola petición.

    POR QUÉ (medido en producción, run 34943861450): descargando de uno en uno,
    150 tickers tardaron ~60 minutos, a razón de 2,5 por minuto. No es un
    problema de hoy sino de mañana: el universo crece con cada día ingestado, y
    a este ritmo la pasada nocturna deja de caber en la noche.

    yf.download acepta una lista y devuelve las columnas como (campo, ticker) —
    que es, precisamente, POR QUÉ venían en dos niveles y reventaba float():
    la librería llevaba todo el tiempo preparada para el modo por lotes y se
    estaba usando de una en una.
    """
    import yfinance as yf

    def _intentar():
        return yf.download(
            tickers,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            # threads=False hacía que yfinance recorriera el lote EN SERIE
            # por dentro: agrupar no quitaba ni una petición, solo movía el
            # bucle dentro de la librería. Por eso el primer run con lotes
            # tardó lo mismo (1h 24m). Un número modesto y explícito, no
            # True: el límite de Yahoo no está documentado y con 50 hilos a
            # la vez el 429 es seguro.
            threads=DOWNLOAD_THREADS,
        )

    return _retry_with_backoff(_intentar, f"descargando el lote de {len(tickers)} tickers")


def extraer_ticker_del_lote(df: pd.DataFrame | None, ticker: str) -> pd.DataFrame | None:
    """Saca de la descarga por lotes el sub-DataFrame de un ticker.

    Devuelve None cuando el lote no trae nada utilizable de ese ticker (no
    aparece en las columnas, o viene entero a NaN porque está deslistado). El
    caller lo reintenta entonces de uno en uno: si alguna suposición sobre la
    forma del lote es errónea, el peor caso es volver al comportamiento
    anterior —lento pero correcto— en vez de perder precios en silencio.
    """
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        nivel = nivel_de_tickers(df.columns)
        if ticker not in set(df.columns.get_level_values(nivel)):
            return None
        propio = df.xs(ticker, axis=1, level=nivel)
    else:
        # Un lote de un solo ticker puede volver ya plano.
        propio = df

    propio = propio.dropna(how="all")
    return propio if not propio.empty else None


def ultimo_dia_guardado(conn, tickers: list[str]) -> dict[str, date]:
    """Último día con precio ya almacenado, por ticker."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, MAX(trade_date) AS ultimo FROM prices "
            "WHERE ticker = ANY(%s) AND close_raw IS NOT NULL GROUP BY ticker",
            (list(tickers),),
        )
        return {r["ticker"]: r["ultimo"] for r in cur.fetchall() if r["ultimo"] is not None}


def pendientes_de_descarga(
    ultimo_por_ticker: dict[str, date], tickers: list[str], start: date, end: date
) -> tuple[list[tuple[str, date]], list[str]]:
    """Reparte los tickers en (los que hay que pedir, con desde qué fecha) y
    (los que ya están al día).

    POR QUÉ (medido: 1h 24m en el run 34948... para volver a bajar exactamente
    lo que ya estaba en la base de datos): el paso se reejecuta en cada pasada
    y volvía a pedir el rango COMPLETO de los ~500 días de cada ticker, aunque
    la ejecución anterior ya los hubiera guardado. Descargar solo lo que falta
    convierte una reejecución de hora y media en segundos.

    Ojo: eso solo vale para dividendos. Close ya viene ajustado por splits,
    así que tras un split la cola nueva llega en otra base que lo guardado
    (BUGS_REPORT.md H-03): backfill_tickers lo detecta volviendo a pedir el
    último día guardado y rebaja entonces la serie entera. Para forzar una
    redescarga completa de todo está --forzar.
    """
    a_pedir: list[tuple[str, date]] = []
    al_dia: list[str] = []
    for ticker in tickers:
        ultimo = ultimo_por_ticker.get(ticker)
        if ultimo is None:
            a_pedir.append((ticker, start))  # nunca descargado: rango entero
            continue
        desde = max(start, ultimo + timedelta(days=1))
        if desde > end:
            al_dia.append(ticker)
        else:
            a_pedir.append((ticker, desde))
    return a_pedir, al_dia


def cierres_de_referencia(conn, tickers: list[str]) -> dict[str, tuple[date, float]]:
    """Último cierre guardado de cada ticker: el que se vuelve a pedir para
    saber si la base de precios cambió (ver TOLERANCIA_CAMBIO_DE_BASE)."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (ticker) ticker, trade_date, close_raw FROM prices "
            "WHERE ticker = ANY(%s) AND close_raw IS NOT NULL "
            "ORDER BY ticker, trade_date DESC",
            (list(tickers),),
        )
        return {r["ticker"]: (r["trade_date"], float(r["close_raw"])) for r in cur.fetchall()}


def cambio_de_base(cierre_guardado: float | None, cierre_nuevo: float | None, tolerancia: float = TOLERANCIA_CAMBIO_DE_BASE) -> bool:
    """True si el mismo día trae ahora otro cierre: la serie guardada está en
    otra base (un split entre las dos descargas)."""
    if not cierre_guardado or not cierre_nuevo or pd.isna(cierre_guardado) or pd.isna(cierre_nuevo):
        return False
    return abs(cierre_nuevo / cierre_guardado - 1) > tolerancia


def base_cambiada(df: pd.DataFrame, ticker: str, referencia: tuple[date, float] | None) -> bool:
    """Compara el día de referencia de la descarga nueva con lo guardado. Si
    la descarga no trae ese día no se puede saber: False, y la reparación
    (tickers_con_salto_de_base) lo cazará si hay salto."""
    if referencia is None or df is None or df.empty:
        return False
    dia, cierre_guardado = referencia
    df = aplanar_columnas(df, ticker)
    filas = df[[d.date() == dia for d in df.index]]
    if filas.empty or "Close" not in filas.columns:
        return False
    return cambio_de_base(cierre_guardado, float(filas["Close"].iloc[0]))


def backfill_tickers(tickers: list[str], start: date, end: date, forzar: bool = False) -> None:
    from pipeline.db.connection import get_connection

    conn = get_connection()
    # Siempre se cierra: una conexión abandonada queda "idle in transaction"
    # con un lock sobre prices tras la última lectura, y cualquier TRUNCATE o
    # VACUUM posterior (ops_prune, los tests) se queda esperando sin fin.
    try:
        _backfill_con_conexion(conn, tickers, start, end, forzar)
    finally:
        conn.close()


def _backfill_con_conexion(conn, tickers: list[str], start: date, end: date, forzar: bool) -> None:
    expected_days = _trading_days_expected(start, end)
    descartados = [t for t in tickers if not t.startswith("^") and not is_tradable_symbol(t)]
    if descartados:
        logger.info("%d símbolos que no son acciones ordinarias operables, sin descargar (p. ej. %s)", len(descartados), ", ".join(descartados[:8]))
        tickers = [t for t in tickers if t.startswith("^") or is_tradable_symbol(t)]

    referencias: dict[str, tuple[date, float]] = {}
    if forzar:
        a_pedir, al_dia = [(t, start) for t in tickers], []
    else:
        a_pedir, al_dia = pendientes_de_descarga(
            ultimo_dia_guardado(conn, tickers), tickers, start, end
        )
        # Las descargas incrementales piden también el último día guardado,
        # para comprobar que la base de precios no cambió (H-03).
        referencias = {
            t: ref for t, ref in cierres_de_referencia(conn, [t for t, _ in a_pedir]).items() if ref[0] >= start
        }
        a_pedir = [(t, referencias[t][0] if t in referencias else desde) for t, desde in a_pedir]
    if al_dia:
        logger.info("%d de %d tickers ya al día, no se vuelven a pedir", len(al_dia), len(tickers))
    if not a_pedir:
        logger.info("Nada que descargar: los %d tickers ya cubren hasta %s", len(tickers), end)
    else:
        # Se agrupan por fecha de inicio para que cada lote sea una sola
        # petición con un rango común. En la práctica casi todos comparten fecha.
        por_fecha: dict[date, list[str]] = {}
        for ticker, desde in a_pedir:
            por_fecha.setdefault(desde, []).append(ticker)

        for desde, tickers_desde in sorted(por_fecha.items()):
            logger.info("%d tickers a descargar desde %s", len(tickers_desde), desde)
            if not _descargar_grupo(conn, tickers_desde, desde, end, expected_days, max_db_mb=MAX_DB_MB, referencias=referencias):
                return
        logger.info("Backfill de precios completo para %d tickers", len(tickers))

    reparar_saltos_de_base(conn, tickers, end)


def confirmar_forzar(n_tickers: int, start: date, end: date, *, leer_respuesta=input) -> bool:
    """True si el usuario confirma un --forzar interactivamente (IMPROVEMENT_PLAN.md A7).

    --forzar re-descarga el rango ENTERO para TODOS los tickers, ignorando
    lo que ya está guardado — con el universo completo eso son miles de
    peticiones a Yahoo Finance (ver el incidente de 1h24m documentado en
    pendientes_de_descarga, que --forzar deshace a propósito). Un --forzar
    tecleado sin querer, o copiado de un ejemplo sin pensarlo, no debería
    arrancar en silencio.

    leer_respuesta inyectable (por defecto input()) para poder probar esto
    sin depender de stdin real."""
    print(
        f"--forzar va a re-descargar TODO el rango {start.isoformat()} -> {end.isoformat()} "
        f"para los {n_tickers} tickers, aunque ya estén al día."
    )
    respuesta = leer_respuesta("Escribe 'si' para confirmar (cualquier otra cosa cancela): ")
    return respuesta.strip().lower() == "si"


def _descargar_grupo(
    conn,
    tickers: list[str],
    start: date,
    end: date,
    expected_days: set[date],
    max_db_mb: float | None = None,
    referencias: dict[str, tuple[date, float]] | None = None,
) -> bool:
    """Descarga por lotes. Devuelve False si se paró por el tope de tamaño de
    la base de datos (max_db_mb), True si terminó.

    referencias: último cierre guardado por ticker (cierres_de_referencia).
    Si la descarga lo trae con otro valor, la base cambió y en vez de añadir
    la cola se rebaja la serie entera del ticker."""
    referencias = referencias or {}

    def _guardar(ticker: str, df: pd.DataFrame) -> None:
        if base_cambiada(df, ticker, referencias.get(ticker)):
            logger.warning(
                "%s: el cierre del %s ya no coincide con el guardado (split o corrección): "
                "se rebaja la serie completa para no mezclar bases",
                ticker, referencias[ticker][0],
            )
            redescargar_serie_completa(conn, ticker, end)
        else:
            _store_with_gap_detection(conn, ticker, df, expected_days)

    for i in range(0, len(tickers), BATCH_SIZE):
        if max_db_mb is not None:
            size = database_mb(conn)
            if size > max_db_mb:
                logger.warning(
                    "Base de datos en %.0f MB (> %.0f): paro la descarga de precios para no llenarla. "
                    "Sube HISTORY_MAX_DB_MB si el plan lo permite.", size, max_db_mb,
                )
                return False
        batch = tickers[i : i + BATCH_SIZE]
        logger.info("Lote %d-%d de %d tickers", i, i + len(batch), len(tickers))

        lote = _descargar_lote_con_reintentos(batch, start, end)
        pendientes = []
        for ticker in batch:
            df = extraer_ticker_del_lote(lote, ticker)
            if df is None:
                pendientes.append(ticker)
                continue
            _guardar(ticker, df)

        # Red de seguridad: lo que el lote no trajo se reintenta de uno en uno
        # ANTES de darlo por deslistado. Un ticker ausente del lote puede serlo
        # por estar deslistado de verdad, pero también porque alguna suposición
        # sobre la forma del resultado sea errónea — y no se ha podido validar
        # contra el servidor real desde el entorno de desarrollo. Con esto, el
        # peor caso de equivocarse es tardar lo que se tardaba antes, no
        # marcar como deslistadas 150 empresas que cotizan perfectamente.
        if pendientes:
            logger.info(
                "%d de %d tickers del lote no vinieron en la descarga conjunta, "
                "se piden de uno en uno", len(pendientes), len(batch),
            )
        for ticker in pendientes:
            df = _download_one_with_retry(ticker, start, end)
            if df is None or df.empty:
                logger.warning("Sin datos para %s en absoluto — probable deslistado total", ticker)
                _flag_full_gap(conn, ticker, start, end)
                continue
            _guardar(ticker, df)

        time.sleep(PAUSE_BETWEEN_BATCHES_S)
    return True


def redescargar_serie_completa(conn, ticker: str, end: date) -> bool:
    """Vuelve a bajar TODO el rango ya guardado de un ticker, en la base de
    precios de hoy, y sustituye la serie. Los días con precio que la descarga
    nueva ya no trae se borran (si no, quedarían en la base vieja); las filas
    centinela de hueco (sin precio) se conservan. Si la descarga falla no se
    toca nada: mejor la serie de antes que ninguna."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(trade_date) AS desde FROM prices WHERE ticker = %s AND close_raw IS NOT NULL", (ticker,))
        fila = cur.fetchone()
    desde = fila["desde"] if fila else None
    if desde is None or desde > end:
        return False
    df = _download_one_with_retry(ticker, desde, end)
    if df is None or df.empty:
        logger.warning("%s: no se pudo rebajar la serie completa; se deja la guardada", ticker)
        return False
    df = aplanar_columnas(df, ticker).dropna(how="all")
    if df.empty:
        logger.warning("%s: la serie rebajada viene vacía; se deja la guardada", ticker)
        return False
    dias_nuevos = [d.date() for d in df.index]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM prices WHERE ticker = %s AND trade_date BETWEEN %s AND %s "
            "AND close_raw IS NOT NULL AND NOT (trade_date = ANY(%s))",
            (ticker, desde, end, dias_nuevos),
        )
    _store_with_gap_detection(conn, ticker, df, _trading_days_expected(desde, end))
    logger.info("%s: serie rebajada entera (%s -> %s, %d días)", ticker, desde, end, len(dias_nuevos))
    return True


def tickers_con_salto_de_base(conn, tickers: list[str], umbral: float = UMBRAL_SALTO_DE_BASE) -> list[str]:
    """Tickers con un salto > umbral entre dos días consecutivos guardados en
    descargas distintas (captured_at diferente): la firma de una serie que
    mezcla dos bases de precios (ver UMBRAL_SALTO_DE_BASE)."""
    if not tickers:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ticker FROM (
                SELECT ticker, close_raw, captured_at,
                       lag(close_raw) OVER w AS cierre_previo,
                       lag(captured_at) OVER w AS captura_previa
                FROM prices
                WHERE ticker = ANY(%s) AND close_raw IS NOT NULL
                WINDOW w AS (PARTITION BY ticker ORDER BY trade_date)
            ) s
            WHERE cierre_previo > 0
              AND abs(close_raw / cierre_previo - 1) > %s
              AND captured_at <> captura_previa
            ORDER BY ticker
            """,
            (list(tickers), umbral),
        )
        return [r["ticker"] for r in cur.fetchall()]


def reparar_saltos_de_base(conn, tickers: list[str], end: date, maximo: int = MAX_REPARACIONES_POR_CORRIDA) -> int:
    """Rebaja entera la serie de los tickers que mezclan bases de precios
    (los guardados antes de detectar los splits al descargar). Devuelve
    cuántos se repararon."""
    candidatos = tickers_con_salto_de_base(conn, [t for t in tickers if not t.startswith("^")])
    if not candidatos:
        return 0
    logger.warning(
        "%d tickers con un salto de precio entre descargas distintas (posible split mal guardado); "
        "se rebajan enteros hasta %d en esta corrida", len(candidatos), maximo,
    )
    reparados = 0
    for ticker in candidatos[:maximo]:
        if redescargar_serie_completa(conn, ticker, end):
            reparados += 1
    return reparados


def _flag_full_gap(conn, ticker: str, start: date, end: date) -> None:
    """Ticker sin ninguna fila devuelta: se registra una única fila centinela
    con survivorship_warning=TRUE en la fecha de inicio, para que quede
    constancia del hueco sin inventar precios."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning)
            VALUES (%s, %s, NULL, NULL, NULL, TRUE)
            ON CONFLICT (ticker, trade_date) DO UPDATE SET survivorship_warning = TRUE
            """,
            (ticker, start),
        )
    conn.commit()


def _store_with_gap_detection(conn, ticker: str, df: pd.DataFrame, expected_days: set[date]) -> None:
    """Guarda las filas de un ticker de una vez (executemany) en vez de un
    INSERT por fila: contra una base de datos remota cada viaje cuesta
    decenas de milisegundos, y fila a fila un lote de 50 tickers x 350 días
    tardaba ~15 minutos (run 37119212595 se quedó sin tiempo con 3.831
    tickers). Los huecos (días esperados que no vinieron) se marcan igual que
    antes, sin inventar precios."""
    df = aplanar_columnas(df, ticker)  # idempotente: no-op si ya viene plano
    _validar_columnas(df, ticker)
    present_days = {d.date() for d in df.index}
    rows = []
    for idx, row in df.iterrows():
        close_raw = float(row["Close"])
        adj_close = float(row["Adj Close"])
        adj_factor = adj_close / close_raw if close_raw else None
        # high/low: proxy de spread (abstention_engine.py); open: entrada
        # "apertura D+1" del backtest de cartera (portfolio_simulator.py).
        rows.append(
            (ticker, idx.date(), float(row["Open"]), close_raw, float(row["High"]), float(row["Low"]), adj_factor, int(row["Volume"]))
        )
    # Días esperados dentro del rango de ESTE ticker que no vinieron en absoluto:
    # se marcan como huecos sin inventar una fila de precio.
    ticker_range_expected = {d for d in expected_days if min(present_days, default=d) <= d <= max(present_days, default=d)}
    missing = sorted(ticker_range_expected - present_days)
    with conn.cursor() as cur:
        if rows:
            cur.executemany(
                """
                INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    open_raw = EXCLUDED.open_raw,
                    close_raw = EXCLUDED.close_raw,
                    high_raw = EXCLUDED.high_raw,
                    low_raw = EXCLUDED.low_raw,
                    adj_factor = EXCLUDED.adj_factor,
                    volume = EXCLUDED.volume,
                    captured_at = now()
                """,
                rows,
            )
        if missing:
            cur.executemany(
                """
                INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning)
                VALUES (%s, %s, NULL, NULL, NULL, TRUE)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET survivorship_warning = TRUE
                """,
                [(ticker, d) for d in missing],
            )
            logger.warning("WARNING — posible data gap: %s tiene %d días faltantes dentro de su rango", ticker, len(missing))
    conn.commit()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Backfill de precios yfinance con flags de supervivencia")
    parser.add_argument("--tickers", type=str, required=True, help="Fichero con un ticker por línea, o lista separada por comas")
    parser.add_argument("--start", type=str, default=config.BACKTEST_START)
    parser.add_argument("--end", type=str, default=config.BACKTEST_END)
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Rebaja el rango entero aunque ya esté guardado (por defecto solo se pide lo que falta)",
    )
    parser.add_argument(
        "--si",
        action="store_true",
        help="Confirma --forzar sin preguntar (para uso no interactivo, p.ej. desde un script)",
    )
    args = parser.parse_args()

    from datetime import datetime as _dt
    from pathlib import Path

    if Path(args.tickers).exists():
        ticker_list = [line.strip() for line in Path(args.tickers).read_text().splitlines() if line.strip()]
    else:
        ticker_list = [t.strip() for t in args.tickers.split(",")]

    start_date = _dt.strptime(args.start, "%Y-%m-%d").date()
    end_date = _dt.strptime(args.end, "%Y-%m-%d").date()

    if args.forzar and not args.si and not confirmar_forzar(len(ticker_list), start_date, end_date):
        print("Cancelado.")
        raise SystemExit(0)

    backfill_tickers(
        ticker_list,
        start_date,
        end_date,
        forzar=args.forzar,
    )
