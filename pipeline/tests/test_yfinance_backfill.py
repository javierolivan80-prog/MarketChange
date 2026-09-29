"""test_yfinance_backfill.py — forma de lo que devuelve yfinance.

Estos tests no prueban la descarga (no hay salida hacia Yahoo desde el entorno
de desarrollo), sino el ADAPTADOR entre lo que devuelve la librería y lo que
espera el resto del pipeline. Es justo la costura donde falló en producción:
yfinance entrega las columnas en dos niveles (campo, ticker) incluso pidiendo
un solo ticker, así que row['Close'] era una Series de un elemento y float()
reventaba a mitad del backfill con un TypeError que no decía nada del problema
real.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import pytest

from pipeline.ingest import yfinance_backfill
from pipeline.ingest.yfinance_backfill import (
    COLUMNAS_REQUERIDAS,
    _download_one_with_retry,
    _validar_columnas,
    aplanar_columnas,
    extraer_ticker_del_lote,
    nivel_de_tickers,
)

_FECHAS = pd.to_datetime(["2026-09-09", "2026-09-10"])
_VALORES = {
    "Open": [10.0, 11.0],
    "High": [10.5, 11.5],
    "Low": [9.5, 10.5],
    "Close": [10.2, 11.2],
    "Adj Close": [10.1, 11.1],
    "Volume": [1000, 2000],
}


def _df_plano() -> pd.DataFrame:
    return pd.DataFrame(_VALORES, index=_FECHAS)


def _df_multiindex(ticker: str = "AAPL", ticker_al_final: bool = True) -> pd.DataFrame:
    """Lo que devuelve yfinance de verdad: columnas (campo, ticker)."""
    df = _df_plano()
    niveles = [(c, ticker) for c in df.columns] if ticker_al_final else [(ticker, c) for c in df.columns]
    df.columns = pd.MultiIndex.from_tuples(niveles)
    return df


def test_una_columna_multiindex_da_una_series_no_un_numero():
    """EL fallo de producción, reproducido: sin aplanar, float(row['Close'])
    recibe una Series. Este test fija POR QUÉ existe aplanar_columnas."""
    df = _df_multiindex()
    fila = next(df.iterrows())[1]
    assert isinstance(fila["Close"], pd.Series)
    with pytest.raises(TypeError):
        float(fila["Close"])


def test_aplanar_deja_valores_convertibles_a_float():
    df = aplanar_columnas(_df_multiindex(), "AAPL")
    for _, fila in df.iterrows():
        for columna in COLUMNAS_REQUERIDAS:
            assert isinstance(float(fila[columna]), float)


def test_aplanar_encuentra_el_ticker_aunque_no_esté_en_el_último_nivel():
    """No se asume la posición del nivel del ticker: se busca cuál es. Si
    yfinance invierte el orden, el aplanado tiene que seguir funcionando."""
    df = aplanar_columnas(_df_multiindex(ticker_al_final=False), "AAPL")
    assert list(df.columns) == list(_VALORES)


def test_aplanar_no_toca_un_dataframe_que_ya_viene_plano():
    plano = _df_plano()
    assert aplanar_columnas(plano, "AAPL").equals(plano)


def test_aplanar_es_idempotente():
    """_store_with_gap_detection lo llama otra vez por si acaso; llamarlo dos
    veces no puede estropear nada."""
    una_vez = aplanar_columnas(_df_multiindex(), "AAPL")
    dos_veces = aplanar_columnas(una_vez, "AAPL")
    assert una_vez.equals(dos_veces)


def test_aplanar_conserva_los_valores_exactos():
    df = aplanar_columnas(_df_multiindex(), "AAPL")
    assert list(df["Close"]) == _VALORES["Close"]
    assert list(df.index) == list(_FECHAS)


def test_validar_columnas_acepta_el_dataframe_bueno():
    _validar_columnas(_df_plano(), "AAPL")  # no debe lanzar


def test_validar_columnas_dice_cuál_falta_y_qué_llegó():
    """El error tiene que nombrar lo que falta Y volcar lo recibido: adivinar
    el formato en vez de mirarlo es lo que costó tres intentos en el parser
    de EDGAR."""
    df = _df_plano().drop(columns=["Adj Close"])
    with pytest.raises(ValueError, match="Adj Close") as exc:
        _validar_columnas(df, "AAPL")
    assert "Columnas recibidas" in str(exc.value)


def test_validar_columnas_caza_una_columna_duplicada():
    """Una columna repetida devuelve otra vez una Series en row['Close'] — el
    mismo TypeError críptico por otra puerta. Mejor que falle aquí, diciendo
    qué pasa."""
    df = _df_plano()
    df = pd.concat([df, df[["Close"]]], axis=1)
    with pytest.raises(ValueError, match="duplicadas"):
        _validar_columnas(df, "AAPL")


# --- Descarga por lotes -----------------------------------------------------
#
# Medido en producción (run 34943861450): de uno en uno, 150 tickers tardaron
# ~60 minutos. yf.download acepta una lista — de hecho ESA es la razón de que
# las columnas vengan en dos niveles. La librería siempre estuvo preparada para
# el modo por lotes; se estaba usando de una en una.


def _df_lote(tickers: list[str], vacios: tuple[str, ...] = ()) -> pd.DataFrame:
    """Lo que devuelve yf.download con una lista: columnas (campo, ticker).
    Los tickers en `vacios` vienen enteros a NaN, como los deslistados."""
    columnas, datos = [], {}
    for campo, valores in _VALORES.items():
        for t in tickers:
            columnas.append((campo, t))
            datos[(campo, t)] = [float("nan")] * len(valores) if t in vacios else valores
    df = pd.DataFrame(datos, index=_FECHAS)
    df.columns = pd.MultiIndex.from_tuples(columnas)
    return df


def test_saca_cada_ticker_del_lote_con_sus_propios_valores():
    lote = _df_lote(["AAPL", "MSFT"])
    for t in ("AAPL", "MSFT"):
        propio = extraer_ticker_del_lote(lote, t)
        assert list(propio.columns) == list(_VALORES)
        assert list(propio["Close"]) == _VALORES["Close"]


def test_el_sub_dataframe_del_lote_ya_sirve_para_float():
    """Lo que importa: lo que sale del lote tiene que poder guardarse sin más
    conversiones — es el mismo punto donde reventó float(row['Close'])."""
    propio = extraer_ticker_del_lote(_df_lote(["AAPL", "MSFT"]), "AAPL")
    for _, fila in propio.iterrows():
        for columna in COLUMNAS_REQUERIDAS:
            assert isinstance(float(fila[columna]), float)


def test_un_ticker_que_no_viene_en_el_lote_devuelve_none():
    """None es la señal de 'pídelo de uno en uno', no de 'está deslistado'."""
    assert extraer_ticker_del_lote(_df_lote(["AAPL"]), "MSFT") is None


def test_un_ticker_entero_a_nan_devuelve_none():
    """Un deslistado viene en las columnas pero sin un solo dato. No puede
    pasar como serie válida ni generar filas de precio."""
    assert extraer_ticker_del_lote(_df_lote(["AAPL", "NWSLL"], vacios=("NWSLL",)), "NWSLL") is None
    assert extraer_ticker_del_lote(_df_lote(["AAPL", "NWSLL"], vacios=("NWSLL",)), "AAPL") is not None


def test_lote_vacio_o_ausente_devuelve_none():
    """Si la descarga del lote falla entera, todos los tickers caen al camino
    de uno en uno en vez de darse por perdidos."""
    assert extraer_ticker_del_lote(None, "AAPL") is None
    assert extraer_ticker_del_lote(pd.DataFrame(), "AAPL") is None


def test_lote_que_vuelve_plano_se_acepta_igual():
    """Un lote de un solo ticker puede volver sin MultiIndex."""
    propio = extraer_ticker_del_lote(_df_plano(), "AAPL")
    assert list(propio["Close"]) == _VALORES["Close"]


def test_el_nivel_del_ticker_se_detecta_por_contenido_no_por_posicion():
    """El nivel de campos es el que trae 'Open'/'Close'; el otro es el de
    tickers. Detectarlo por contenido hace que invertir el orden de los
    niveles no rompa nada — el tipo de suposición sobre un formato ajeno que ya
    ha costado varios fallos en este proyecto."""
    normal = _df_lote(["AAPL", "MSFT"])           # (campo, ticker)
    assert nivel_de_tickers(normal.columns) == 1

    invertido = normal.copy()
    invertido.columns = pd.MultiIndex.from_tuples([(t, c) for c, t in normal.columns])
    assert nivel_de_tickers(invertido.columns) == 0
    assert list(extraer_ticker_del_lote(invertido, "AAPL")["Close"]) == _VALORES["Close"]


def test_no_mezcla_valores_entre_tickers_del_mismo_lote():
    """El fallo más caro que podría tener el modo por lotes: guardar los
    precios de una empresa bajo el ticker de otra. Pasaría desapercibido —los
    números son plausibles— y contaminaría todos los cálculos posteriores."""
    columnas, datos = [], {}
    for i, t in enumerate(["AAA", "BBB", "CCC"]):
        for campo, valores in _VALORES.items():
            columnas.append((campo, t))
            datos[(campo, t)] = [v + i * 100 for v in valores]
    lote = pd.DataFrame(datos, index=_FECHAS)
    lote.columns = pd.MultiIndex.from_tuples(columnas)

    for i, t in enumerate(["AAA", "BBB", "CCC"]):
        propio = extraer_ticker_del_lote(lote, t)
        assert list(propio["Close"]) == [v + i * 100 for v in _VALORES["Close"]]


# --- No volver a bajar lo que ya está ---------------------------------------
#
# Medido: 1h 24m para redescargar exactamente los mismos ~500 días de 150
# tickers que la ejecución anterior ya había guardado. El paso se reejecuta en
# cada pasada y pedía siempre el rango completo.

from datetime import date as _date

from pipeline.ingest.yfinance_backfill import pendientes_de_descarga

_INICIO, _FIN = _date(2025, 4, 28), _date(2026, 9, 15)


def test_un_ticker_ya_al_dia_no_se_vuelve_a_pedir():
    a_pedir, al_dia = pendientes_de_descarga({"AAPL": _FIN}, ["AAPL"], _INICIO, _FIN)
    assert a_pedir == []
    assert al_dia == ["AAPL"]


def test_un_ticker_nunca_descargado_se_pide_entero():
    a_pedir, al_dia = pendientes_de_descarga({}, ["NUEVA"], _INICIO, _FIN)
    assert a_pedir == [("NUEVA", _INICIO)]
    assert al_dia == []


def test_solo_se_pide_la_cola_que_falta():
    """Lo que hace que una reejecución dure segundos: si hay precios hasta el
    día 10, se piden desde el 11, no desde hace 500 días."""
    a_pedir, _ = pendientes_de_descarga({"AAPL": _date(2026, 9, 10)}, ["AAPL"], _INICIO, _FIN)
    assert a_pedir == [("AAPL", _date(2026, 9, 11))]


def test_no_se_pide_de_nuevo_el_ultimo_dia_guardado():
    """Se empieza en el día SIGUIENTE al último guardado. Volver a pedirlo no
    daría datos malos —el upsert es idempotente— pero sí un día de más en cada
    ejecución, todos los días."""
    a_pedir, _ = pendientes_de_descarga({"AAPL": _date(2026, 9, 10)}, ["AAPL"], _INICIO, _FIN)
    assert a_pedir[0][1] > _date(2026, 9, 10)


def test_nunca_se_pide_antes_del_inicio_solicitado():
    """Un ticker con histórico más antiguo que el rango pedido no debe
    ensanchar la descarga hacia atrás."""
    a_pedir, al_dia = pendientes_de_descarga({"AAPL": _date(2020, 1, 1)}, ["AAPL"], _INICIO, _FIN)
    assert a_pedir == [("AAPL", _INICIO)]
    assert al_dia == []


def test_el_caso_real_de_produccion_no_pide_nada():
    """150 tickers guardados hasta la fecha final: una reejecución no debe
    hacer ni una petición. Ese es el escenario que costó hora y media."""
    tickers = [f"T{i}" for i in range(150)]
    a_pedir, al_dia = pendientes_de_descarga({t: _FIN for t in tickers}, tickers, _INICIO, _FIN)
    assert a_pedir == []
    assert len(al_dia) == 150


def test_mezcla_de_tickers_al_dia_nuevos_y_a_medias():
    ultimos = {"ALDIA": _FIN, "AMEDIAS": _date(2026, 9, 1)}
    a_pedir, al_dia = pendientes_de_descarga(
        ultimos, ["ALDIA", "AMEDIAS", "NUEVA"], _INICIO, _FIN
    )
    assert al_dia == ["ALDIA"]
    assert dict(a_pedir) == {"AMEDIAS": _date(2026, 9, 2), "NUEVA": _INICIO}


# ---------------------------------------------------------------------------
# _flag_full_gap / _store_with_gap_detection (IMPROVEMENT_PLAN.md M10, parte 2/2)
#
# Instrucción explícita del usuario: "flagea tickers deslistados... no
# intentes llenar gaps". Sin test hasta ahora de que el flag en sí se escriba
# donde toca (y solo donde toca) contra la tabla prices real.
# ---------------------------------------------------------------------------


def _df_precios(fechas: list[str], close=100.0) -> pd.DataFrame:
    idx = pd.to_datetime(fechas)
    n = len(fechas)
    return pd.DataFrame(
        {
            "Open": [close] * n,
            "High": [close + 1] * n,
            "Low": [close - 1] * n,
            "Close": [close] * n,
            "Adj Close": [close] * n,
            "Volume": [1000] * n,
        },
        index=idx,
    )


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
class TestGapDetectionAgainstRealPostgres:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from pipeline.db.connection import get_connection, init_schema

        self.conn = get_connection()
        init_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE prices")
        self.conn.commit()
        yield
        self.conn.close()

    def _filas(self, ticker: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, close_raw, survivorship_warning FROM prices "
                "WHERE ticker = %s ORDER BY trade_date",
                (ticker,),
            )
            return cur.fetchall()

    def test_flag_full_gap_inserta_una_fila_centinela_en_start_sin_precio(self):
        from pipeline.ingest.yfinance_backfill import _flag_full_gap

        _flag_full_gap(self.conn, "DESLISTADO", date(2026, 1, 5), date(2026, 1, 10))

        filas = self._filas("DESLISTADO")
        assert len(filas) == 1
        assert filas[0]["trade_date"] == date(2026, 1, 5)
        assert filas[0]["close_raw"] is None  # no se inventa precio, solo se marca
        assert filas[0]["survivorship_warning"] is True

    def test_flag_full_gap_no_pisa_un_precio_real_ya_guardado(self):
        """Si ya hay una fila de precio real en esa fecha (de una descarga
        anterior) y luego se marca full_gap para un rango que la incluye, el
        UPDATE de ON CONFLICT solo debe tocar survivorship_warning, nunca
        borrar el precio que ya estaba."""
        from pipeline.ingest.yfinance_backfill import _flag_full_gap, _store_with_gap_detection

        _store_with_gap_detection(self.conn, "ACME", _df_precios(["2026-01-05"], close=42.0), set())
        _flag_full_gap(self.conn, "ACME", date(2026, 1, 5), date(2026, 1, 10))

        filas = self._filas("ACME")
        assert len(filas) == 1
        assert float(filas[0]["close_raw"]) == pytest.approx(42.0)
        assert filas[0]["survivorship_warning"] is True

    def test_store_with_gap_detection_inserta_los_precios_del_dataframe(self):
        from pipeline.ingest.yfinance_backfill import _store_with_gap_detection

        df = _df_precios(["2026-01-05", "2026-01-06"], close=50.0)
        _store_with_gap_detection(self.conn, "ACME", df, {date(2026, 1, 5), date(2026, 1, 6)})

        filas = self._filas("ACME")
        assert len(filas) == 2
        assert all(float(f["close_raw"]) == pytest.approx(50.0) for f in filas)
        assert all(f["survivorship_warning"] is False for f in filas)

    def test_store_with_gap_detection_marca_los_dias_faltantes_dentro_del_rango(self):
        """El propio ticker cotizó el 5 y el 7 de enero, pero no el 6 (que sí
        era un día de mercado esperado) — debe quedar marcado como hueco sin
        inventar un precio para ese día."""
        from pipeline.ingest.yfinance_backfill import _store_with_gap_detection

        df = _df_precios(["2026-01-05", "2026-01-07"])
        expected_days = {date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)}
        _store_with_gap_detection(self.conn, "ACME", df, expected_days)

        filas = {f["trade_date"]: f for f in self._filas("ACME")}
        assert len(filas) == 3
        assert filas[date(2026, 1, 6)]["close_raw"] is None
        assert filas[date(2026, 1, 6)]["survivorship_warning"] is True
        assert filas[date(2026, 1, 5)]["survivorship_warning"] is False
        assert filas[date(2026, 1, 7)]["survivorship_warning"] is False

    def test_store_with_gap_detection_no_marca_dias_fuera_del_rango_propio_del_ticker(self):
        """expected_days puede cubrir un rango más amplio que compartido entre
        varios tickers de un mismo lote (ver _descargar_grupo). Un día de
        mercado ANTES de que este ticker empezara a cotizar en el rango
        pedido no es un hueco suyo — no debe marcarse."""
        from pipeline.ingest.yfinance_backfill import _store_with_gap_detection

        df = _df_precios(["2026-01-07"])  # el ticker solo trae datos desde el 7
        expected_days = {date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)}
        _store_with_gap_detection(self.conn, "ACME", df, expected_days)

        filas = self._filas("ACME")
        assert len(filas) == 1  # ni el 5 ni el 6 se marcaron como huecos de ACME
        assert filas[0]["trade_date"] == date(2026, 1, 7)

    def test_store_with_gap_detection_es_upsert_actualiza_el_precio(self):
        from pipeline.ingest.yfinance_backfill import _store_with_gap_detection

        _store_with_gap_detection(self.conn, "ACME", _df_precios(["2026-01-05"], close=10.0), set())
        _store_with_gap_detection(self.conn, "ACME", _df_precios(["2026-01-05"], close=20.0), set())

        filas = self._filas("ACME")
        assert len(filas) == 1  # no se duplicó
        assert float(filas[0]["close_raw"]) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# _download_one_with_retry / _descargar_lote_con_reintentos (IMPROVEMENT_PLAN.md M10)
#
# Nunca se han probado directamente: yfinance "no tiene una jerarquía de
# excepciones propia estable" (comentario del propio código), así que lo que
# importa comprobar es que CUALQUIER excepción activa el backoff y que tras
# MAX_RETRIES se rinde devolviendo None en vez de propagar — el propio código
# lo llama "la etapa más frágil del pipeline".
#
# _retry_with_backoff — IMPROVEMENT_PLAN.md Q5: antes, _download_one_with_retry
# y _descargar_lote_con_reintentos duplicaban el MISMO bucle de reintentos
# palabra por palabra; ninguno de los dos tenía test hasta esta sesión.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _sin_esperas_reales(monkeypatch):
    monkeypatch.setattr(yfinance_backfill.time, "sleep", lambda _: None)


def test_download_one_with_retry_reintenta_ante_fallo_transitorio(monkeypatch):
    intentos = {"n": 0}

    def _download(*a, **kw):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise ConnectionError("429 de Yahoo")
        return _df_plano()

    monkeypatch.setattr("yfinance.download", _download)

    resultado = _download_one_with_retry("AAPL", _date(2026, 9, 9), _date(2026, 9, 10))

    assert intentos["n"] == 2
    assert resultado is not None
    assert list(resultado["Close"]) == [10.2, 11.2]


def test_download_one_with_retry_aplana_el_multiindex_que_devuelve_yfinance(monkeypatch):
    monkeypatch.setattr("yfinance.download", lambda *a, **kw: _df_multiindex("AAPL"))

    resultado = _download_one_with_retry("AAPL", _date(2026, 9, 9), _date(2026, 9, 10))

    assert not isinstance(resultado.columns, pd.MultiIndex)


def test_download_one_with_retry_se_rinde_tras_max_retries(monkeypatch):
    intentos = {"n": 0}

    def _download(*a, **kw):
        intentos["n"] += 1
        raise ConnectionError("caída persistente")

    monkeypatch.setattr("yfinance.download", _download)

    resultado = _download_one_with_retry("AAPL", _date(2026, 9, 9), _date(2026, 9, 10))

    assert resultado is None
    assert intentos["n"] == yfinance_backfill.MAX_RETRIES


def test_download_one_with_retry_none_no_revienta_al_aplanar(monkeypatch):
    """yf.download puede devolver None (p. ej. ticker sin ningún dato en el
    rango) — no debe intentar aplanar un DataFrame inexistente."""
    monkeypatch.setattr("yfinance.download", lambda *a, **kw: None)

    assert _download_one_with_retry("AAPL", _date(2026, 9, 9), _date(2026, 9, 10)) is None


def test_descargar_lote_con_reintentos_reintenta_y_devuelve_el_lote(monkeypatch):
    intentos = {"n": 0}

    def _download(*a, **kw):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise ConnectionError("429 de Yahoo")
        return _df_multiindex("AAPL")

    monkeypatch.setattr("yfinance.download", _download)

    resultado = yfinance_backfill._descargar_lote_con_reintentos(["AAPL"], _date(2026, 9, 9), _date(2026, 9, 10))

    assert intentos["n"] == 2
    assert resultado is not None


def test_descargar_lote_con_reintentos_se_rinde_tras_max_retries(monkeypatch):
    intentos = {"n": 0}

    def _download(*a, **kw):
        intentos["n"] += 1
        raise ConnectionError("caída persistente")

    monkeypatch.setattr("yfinance.download", _download)

    resultado = yfinance_backfill._descargar_lote_con_reintentos(["AAPL", "MSFT"], _date(2026, 9, 9), _date(2026, 9, 10))

    assert resultado is None
    assert intentos["n"] == yfinance_backfill.MAX_RETRIES


def test_retry_with_backoff_devuelve_el_resultado_si_no_hay_fallo():
    from pipeline.ingest.yfinance_backfill import _retry_with_backoff

    assert _retry_with_backoff(lambda: 42, "algo") == 42


def test_retry_with_backoff_reintenta_y_acaba_bien():
    from pipeline.ingest.yfinance_backfill import _retry_with_backoff

    intentos = {"n": 0}

    def _func():
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("fallo transitorio")
        return "ok"

    assert _retry_with_backoff(_func, "algo") == "ok"
    assert intentos["n"] == 3


def test_retry_with_backoff_devuelve_none_tras_agotar_intentos():
    from pipeline.ingest.yfinance_backfill import MAX_RETRIES, _retry_with_backoff

    intentos = {"n": 0}

    def _func():
        intentos["n"] += 1
        raise RuntimeError("siempre falla")

    assert _retry_with_backoff(_func, "algo") is None
    assert intentos["n"] == MAX_RETRIES


def test_download_one_with_retry_usa_retry_with_backoff_compartido(monkeypatch):
    """Regresión de cableado (IMPROVEMENT_PLAN.md Q5): _download_one_with_retry
    debe pasar por el bucle de reintentos compartido, no por uno propio — se
    verifica de punta a punta (yf.download falla dos veces y luego responde)
    en vez de mockear _retry_with_backoff, para probar la integración real."""
    import yfinance as yf

    import pipeline.ingest.yfinance_backfill as yfb

    llamadas = {"n": 0}

    def _fake_download(*a, **kw):
        llamadas["n"] += 1
        if llamadas["n"] < 2:
            raise RuntimeError("fallo transitorio")
        return pd.DataFrame({"Close": [10.0]})

    monkeypatch.setattr(yf, "download", _fake_download)
    monkeypatch.setattr(yfb, "aplanar_columnas", lambda df, ticker: df)

    resultado = yfb._download_one_with_retry("AAPL", _INICIO, _FIN)

    assert llamadas["n"] == 2
    assert resultado is not None
