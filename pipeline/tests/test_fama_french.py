"""test_fama_french.py — valida el parser de Ken French contra un fixture offline.

No se pudo verificar contra el fichero real (mba.tuck.dartmouth.edu bloqueado
en este sandbox). Fixture con la misma forma documentada del CSV real:
cabecera de texto libre, tabla de datos, footer de copyright.
"""
import io
import os
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from pipeline.ingest import fama_french
from pipeline.ingest.fama_french import _default_min_date, parse_ff3_csv

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_ff3_csv_skips_header_and_footer_text():
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw)
    assert len(df) == 3  # solo las 3 filas de datos, no cabecera ni footer


def test_parse_ff3_csv_converts_percent_points_to_fractional_returns():
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw)
    first = df.iloc[0]
    assert first["trade_date"] == date(2021, 1, 4)
    assert first["mkt_rf"] == pytest.approx(-0.0036)  # -0.36 puntos porcentuales -> -0.0036 fraccional
    assert first["smb"] == pytest.approx(0.012)
    assert first["hml"] == pytest.approx(0.036)
    assert first["rf"] == pytest.approx(0.0)


def test_parse_ff3_csv_without_min_date_returns_all_rows():
    """Default (min_date=None) no filtra — mismo comportamiento de antes de
    añadir el filtro, para no romper ningún caller existente."""
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw)
    assert len(df) == 3


def test_parse_ff3_csv_min_date_filters_earlier_rows():
    """Regresión: sin filtrar por fecha, fetch_ff3_daily() descargaba el
    histórico completo de Ken French desde 1926 (~25.000 filas) y
    store_factors() las insertaba una a una contra Neon — el paso del
    pipeline nocturno real se quedó colgado más de 2 horas sin fallar. El
    filtro reduce esto a solo lo que el proyecto necesita."""
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw, min_date=date(2021, 1, 5))
    assert len(df) == 2  # descarta la fila del 2021-01-04, quedan 2 de las 3
    assert all(d >= date(2021, 1, 5) for d in df["trade_date"])


def test_parse_ff3_csv_min_date_after_all_rows_returns_empty():
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw, min_date=date(2099, 1, 1))
    assert len(df) == 0


def test_default_min_date_is_before_backtest_start():
    """_default_min_date() debe quedar ANTES de BACKTEST_START (necesita
    colchón para la ventana de estimación de -250 días de mercado), nunca
    después — si no, event_enrichment.py no tendría suficiente historial de
    factores para ajustar el modelo en los primeros eventos del backtest."""
    from pipeline import config

    backtest_start = date.fromisoformat(config.BACKTEST_START)
    assert _default_min_date() < backtest_start
    assert (backtest_start - _default_min_date()).days >= 250


# ---------------------------------------------------------------------------
# fetch_ff3_daily / store_factors (IMPROVEMENT_PLAN.md M9)
#
# Sin test hasta ahora, justo la capa donde ya ocurrió un incidente real: el
# bucle fila a fila de store_factors() dejó el pipeline nocturno colgado más
# de 2 horas insertando ~25.000 filas una a una contra Neon (ver su docstring).
#
# fetch_ff3_daily pasa por _get_with_retry (IMPROVEMENT_PLAN.md R7, ver más
# abajo), así que las respuestas falsas necesitan status_code para que
# _es_permanente() no reviente con un AttributeError.
# ---------------------------------------------------------------------------


def _zip_con_csv(raw_csv: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("F-F_Research_Data_Factors_daily.CSV", raw_csv)
    return buf.getvalue()


def test_fetch_ff3_daily_descomprime_el_zip_y_aplica_el_filtro_de_fecha(monkeypatch):
    from pipeline.ingest import fama_french

    raw_csv = (FIXTURES / "sample_ff3_daily.csv").read_text()

    class _FakeResp:
        status_code = 200
        content = _zip_con_csv(raw_csv)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(fama_french.requests, "get", lambda url, timeout=60: _FakeResp())

    df = fama_french.fetch_ff3_daily(min_date=date(2021, 1, 5))

    assert len(df) == 2  # descarta la fila del 2021-01-04 (ver fixture)
    assert all(d >= date(2021, 1, 5) for d in df["trade_date"])


def test_fetch_ff3_daily_propaga_un_error_permanente(monkeypatch):
    """Un 404 real ya no propaga requests.HTTPError sin más: _get_with_retry
    (R7) lo reconoce como permanente y lo envuelve en PermanentHTTPError, sin
    gastar reintentos en él — ver test_get_with_retry_no_reintenta_un_error_permanente
    para la cobertura de _get_with_retry en sí."""
    from pipeline.ingest import fama_french

    class _FakeResp:
        status_code = 404

        def raise_for_status(self):
            raise fama_french.requests.HTTPError("404")

    monkeypatch.setattr(fama_french.requests, "get", lambda url, timeout=60: _FakeResp())

    with pytest.raises(fama_french.PermanentHTTPError):
        fama_french.fetch_ff3_daily()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
class TestStoreFactorsAgainstRealPostgres:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from pipeline.db.connection import get_connection, init_schema

        self.conn = get_connection()
        init_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE fama_french_factors")
        self.conn.commit()
        yield
        self.conn.close()

    def test_store_factors_inserta_las_filas_del_dataframe(self):
        from pipeline.ingest.fama_french import parse_ff3_csv, store_factors

        raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
        df = parse_ff3_csv(raw)
        store_factors(self.conn, df)

        with self.conn.cursor() as cur:
            cur.execute("SELECT trade_date, mkt_rf FROM fama_french_factors ORDER BY trade_date")
            rows = cur.fetchall()
        assert len(rows) == 3
        assert rows[0]["trade_date"] == date(2021, 1, 4)
        assert float(rows[0]["mkt_rf"]) == pytest.approx(-0.0036)

    def test_store_factors_con_dataframe_vacio_no_revienta(self):
        from pipeline.ingest.fama_french import parse_ff3_csv, store_factors

        vacio = parse_ff3_csv((FIXTURES / "sample_ff3_daily.csv").read_text(), min_date=date(2099, 1, 1))
        store_factors(self.conn, vacio)  # no debe lanzar ni intentar un executemany sin filas

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM fama_french_factors")
            assert cur.fetchone()["n"] == 0

    def test_store_factors_es_upsert_no_duplica_ni_pierde_actualizaciones(self):
        """ON CONFLICT ... DO UPDATE: recargar el mismo trade_date con un valor
        distinto debe actualizar la fila, no duplicarla ni ignorarla."""
        from pipeline.ingest.fama_french import parse_ff3_csv, store_factors

        raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
        df = parse_ff3_csv(raw)
        store_factors(self.conn, df)

        df_actualizado = df.copy()
        df_actualizado.loc[0, "mkt_rf"] = 0.5
        store_factors(self.conn, df_actualizado)

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM fama_french_factors")
            assert cur.fetchone()["n"] == 3  # no se duplicó
            cur.execute(
                "SELECT mkt_rf FROM fama_french_factors WHERE trade_date = %s",
                (df.iloc[0]["trade_date"],),
            )
            assert float(cur.fetchone()["mkt_rf"]) == pytest.approx(0.5)  # se actualizó


# ---------------------------------------------------------------------------
# _get_with_retry — IMPROVEMENT_PLAN.md R7: sin esto, fetch_ff3_daily hacía un
# requests.get desnudo, sin ningún reintento ante un fallo de red transitorio.
# ---------------------------------------------------------------------------


class _RespuestaFalsa:
    def __init__(self, status_code: int = 200, content: bytes = b""):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}", response=self)


@pytest.fixture(autouse=True)
def _sin_esperas_reales(monkeypatch):
    monkeypatch.setattr(fama_french.time, "sleep", lambda _: None)


def test_get_with_retry_reintenta_ante_fallo_de_red_y_acaba_bien(monkeypatch):
    intentos = {"n": 0}

    def _get(*a, **kw):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise requests.RequestException("conexión cortada")
        return _RespuestaFalsa(200, b"contenido")

    monkeypatch.setattr(fama_french.requests, "get", _get)

    resp = fama_french._get_with_retry("https://mba.tuck.dartmouth.edu/x.zip")

    assert intentos["n"] == 2
    assert resp.content == b"contenido"


def test_get_with_retry_falla_ruidosamente_si_no_hay_manera(monkeypatch):
    monkeypatch.setattr(
        fama_french.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.RequestException("caída"))
    )

    with pytest.raises(RuntimeError, match="No se pudo descargar"):
        fama_french._get_with_retry("https://mba.tuck.dartmouth.edu/x.zip")


@pytest.mark.parametrize("status", [404, 403, 400])
def test_get_with_retry_no_reintenta_un_error_permanente(monkeypatch, status):
    """Mismo motivo que edgar_http.py: reintentar un 404 no lo arregla, lo
    esconde detrás de 30s de esperas inútiles."""
    intentos = {"n": 0}

    def _get(*a, **kw):
        intentos["n"] += 1
        return _RespuestaFalsa(status_code=status)

    monkeypatch.setattr(fama_french.requests, "get", _get)

    with pytest.raises(fama_french.PermanentHTTPError, match=str(status)):
        fama_french._get_with_retry("https://mba.tuck.dartmouth.edu/x.zip")

    assert intentos["n"] == 1  # una y no más


@pytest.mark.parametrize("status", [429, 408])
def test_get_with_retry_si_reintenta_los_4xx_transitorios(monkeypatch, status):
    intentos = {"n": 0}

    def _get(*a, **kw):
        intentos["n"] += 1
        if intentos["n"] <= 2:
            return _RespuestaFalsa(status_code=status)
        return _RespuestaFalsa(200, b"ok")

    monkeypatch.setattr(fama_french.requests, "get", _get)

    resp = fama_french._get_with_retry("https://mba.tuck.dartmouth.edu/x.zip")

    assert intentos["n"] == 3
    assert resp.content == b"ok"


def test_fetch_ff3_daily_usa_get_with_retry(monkeypatch):
    """Regresión de cableado: fetch_ff3_daily debe pasar por _get_with_retry,
    no por un requests.get directo — si alguien reintroduce la llamada
    desnuda, este test debe fallar."""
    llamado = {}

    def _fake_get_with_retry(url, **kwargs):
        llamado["url"] = url
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("data.csv", (FIXTURES / "sample_ff3_daily.csv").read_text())
        return SimpleNamespace(content=buf.getvalue())

    monkeypatch.setattr(fama_french, "_get_with_retry", _fake_get_with_retry)

    df = fama_french.fetch_ff3_daily(min_date=date(2020, 1, 1))

    assert llamado["url"] == fama_french.FF3_DAILY_URL
    assert len(df) == 3
