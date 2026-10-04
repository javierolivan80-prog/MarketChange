"""test_edgar_http.py — descarga parcial de cabeceras de EDGAR.

Lo que se prueba aquí es el CORTE: que throttled_get_header deje de leer en
cuanto termina la cabecera SGML, y que lo que devuelve siga sirviendo para
parsear. El motivo es de coste, no de estética: el .txt de un submission trae
todos los anexos (megabytes), y la ingesta solo lee dos líneas de la cabecera.
Bajarlo entero hacía que un día de backfill tardara más de media hora.
"""
from __future__ import annotations

import pytest
import requests

from pipeline.ingest import edgar_http


class _FakeStreamResponse:
    """Imita lo justo de requests.Response en modo stream: trocea el cuerpo y
    lleva la cuenta de cuántos bytes se llegaron a pedir de verdad."""

    def __init__(self, body: str, chunk_size_out: int = 8192, status_code: int = 200, headers: dict | None = None):
        self._body = body.encode()
        self._chunk_size_out = chunk_size_out
        self.status_code = status_code
        self.headers = headers or {}
        self.bytes_served = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        # requests lanza HTTPError, que ES una RequestException y por tanto
        # entra en el bucle de reintentos. Imitarlo con otra excepción haría
        # que el test probara un comportamiento que no existe.
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def iter_content(self, chunk_size=8192, decode_unicode=False):
        step = self._chunk_size_out
        for i in range(0, len(self._body), step):
            chunk = self._body[i : i + step]
            self.bytes_served += len(chunk)
            yield chunk.decode() if decode_unicode else chunk


def _fake_submission(header_items: str, cuerpo_kb: int) -> str:
    """Cabecera SGML realista seguida de un cuerpo grande (los anexos)."""
    cabecera = (
        "<SEC-DOCUMENT>0001234567-26-000123.txt : 20260910\n"
        "<SEC-HEADER>0001234567-26-000123.hdr.sgml : 20260910\n"
        "ACCESSION NUMBER:\t\t0001234567-26-000123\n"
        "CONFORMED SUBMISSION TYPE:\t8-K\n"
        f"{header_items}"
        "FILED AS OF DATE:\t\t20260910\n"
        "</SEC-HEADER>\n"
    )
    return cabecera + ("X" * 1024 + "\n") * cuerpo_kb


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    """El rate limit real (0.1s) y el backoff no aportan nada a estos tests."""
    monkeypatch.setattr(edgar_http.time, "sleep", lambda _: None)


def test_corta_la_descarga_al_acabar_la_cabecera(monkeypatch):
    """EL test que justifica la función: con 5 MB de anexos detrás, no se
    deben transferir 5 MB para leer 4 líneas."""
    resp = _FakeStreamResponse(_fake_submission("ITEM INFORMATION:\t\t2.02\n", cuerpo_kb=5000))
    monkeypatch.setattr(edgar_http, "_get", lambda *a, **kw: resp)

    texto = edgar_http.throttled_get_header("https://sec.gov/x.txt")

    assert "ITEM INFORMATION:" in texto
    assert "</SEC-HEADER>" in texto
    # Se para en el primer trozo que contiene el marcador, muy lejos de los 5 MB.
    assert resp.bytes_served < 64 * 1024


def test_devuelve_la_cabecera_entera_aunque_quepa_en_varios_trozos(monkeypatch):
    """Con trozos pequeños la cabecera se reparte en varias iteraciones; no se
    puede perder ninguna línea por el camino."""
    cuerpo = _fake_submission(
        "ITEM INFORMATION:\t\t2.02\nITEM INFORMATION:\t\t9.01\n", cuerpo_kb=100
    )
    resp = _FakeStreamResponse(cuerpo, chunk_size_out=16)
    monkeypatch.setattr(edgar_http, "_get", lambda *a, **kw: resp)

    texto = edgar_http.throttled_get_header("https://sec.gov/x.txt")

    assert texto.count("ITEM INFORMATION:") == 2
    assert "ACCESSION NUMBER:\t\t0001234567-26-000123" in texto


def test_detecta_el_marcador_partido_entre_dos_trozos(monkeypatch):
    """REGRESIÓN: buscando '</SEC-HEADER>' solo en el último trozo, un marcador
    partido justo en la frontera pasa desapercibido y la descarga sigue hasta
    el tope de bytes. Con trozos de 5 bytes el marcador se parte seguro."""
    resp = _FakeStreamResponse(_fake_submission("ITEM INFORMATION:\t\t2.02\n", cuerpo_kb=300), chunk_size_out=5)
    monkeypatch.setattr(edgar_http, "_get", lambda *a, **kw: resp)

    edgar_http.throttled_get_header("https://sec.gov/x.txt")

    assert resp.bytes_served < 4096  # se cortó en la cabecera, no en el tope de 256 KB


def test_sin_marcador_corta_por_tope_de_bytes_en_vez_de_fallar(monkeypatch):
    """Si EDGAR cambiara el formato y no hubiera '</SEC-HEADER>', la función no
    debe reventar ni tragarse un fichero de 50 MB: corta en el tope duro y
    devuelve lo leído para que el parser haga lo que pueda (y, si no extrae
    Items, scrape_day vuelca la cabecera al log)."""
    sin_marcador = "ACCESSION NUMBER:\t\t0001234567-26-000123\n" + ("Y" * 1024 + "\n") * 2000
    resp = _FakeStreamResponse(sin_marcador)
    monkeypatch.setattr(edgar_http, "_get", lambda *a, **kw: resp)

    texto = edgar_http.throttled_get_header("https://sec.gov/x.txt")

    assert "ACCESSION NUMBER:" in texto
    assert len(texto) >= edgar_http._HEADER_BYTE_CAP
    assert resp.bytes_served < 2000 * 1025  # no llegó al final del fichero


def test_reintenta_ante_fallo_de_red_y_acaba_devolviendo_la_cabecera(monkeypatch):
    intentos = {"n": 0}

    def _get(*a, **kw):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise edgar_http.requests.RequestException("conexión cortada")
        return _FakeStreamResponse(_fake_submission("ITEM INFORMATION:\t\t2.02\n", cuerpo_kb=10))

    monkeypatch.setattr(edgar_http, "_get", _get)

    texto = edgar_http.throttled_get_header("https://sec.gov/x.txt")

    assert intentos["n"] == 2
    assert "ITEM INFORMATION:" in texto


def test_falla_ruidosamente_si_no_hay_manera(monkeypatch):
    def _get(*a, **kw):
        raise edgar_http.requests.RequestException("caída")

    monkeypatch.setattr(edgar_http, "_get", _get)

    with pytest.raises(RuntimeError, match="No se pudo descargar la cabecera"):
        edgar_http.throttled_get_header("https://sec.gov/x.txt")


# --- No reintentar errores permanentes -------------------------------------
#
# Reintentar un 404 no lo arregla: lo DISFRAZA. Con la URL de los filings mal
# construida, cada documento gastaba 30 segundos de esperas (2+4+8+16) antes de
# rendirse. Con cientos de filings al día, un fallo que debería cantar al
# instante convirtió la ingesta en un proceso de 40 minutos que por fuera
# parecía estar trabajando. El coste del reintento no era el tiempo: era que
# escondía el síntoma.


@pytest.mark.parametrize("status", [404, 403, 400])
def test_no_reintenta_un_error_permanente(monkeypatch, status):
    intentos = {"n": 0}

    def _get(*a, **kw):
        intentos["n"] += 1
        return _FakeStreamResponse("", status_code=status)

    monkeypatch.setattr(edgar_http, "_get", _get)

    with pytest.raises(edgar_http.PermanentHTTPError, match=str(status)):
        edgar_http.throttled_get("https://sec.gov/edgar/data/1/x.txt")

    assert intentos["n"] == 1  # una y no más


@pytest.mark.parametrize("status", [429, 408])
def test_si_reintenta_los_4xx_que_son_transitorios(monkeypatch, status):
    """429 (rate limit) y 408 (timeout) son 4xx pero SÍ hay que reintentarlos —
    son justo los casos para los que existe el backoff."""
    intentos = {"n": 0}

    def _get(*a, **kw):
        intentos["n"] += 1
        if intentos["n"] <= 2:
            return _FakeStreamResponse("", status_code=status)
        return _FakeStreamResponse(_fake_submission("ITEM INFORMATION:\t\t2.02\n", cuerpo_kb=1))

    monkeypatch.setattr(edgar_http, "_get", _get)

    texto = edgar_http.throttled_get_header("https://sec.gov/x.txt")

    assert intentos["n"] == 3
    assert "ITEM INFORMATION:" in texto


def test_el_error_permanente_lo_sigue_cazando_el_caller(monkeypatch):
    """scrape_day captura RuntimeError para saltarse un filing suelto; el nuevo
    error tiene que seguir cayendo ahí y no reventar la ingesta entera."""
    monkeypatch.setattr(
        edgar_http, "_get", lambda *a, **kw: _FakeStreamResponse("", status_code=404)
    )

    with pytest.raises(RuntimeError):
        edgar_http.throttled_get_header("https://sec.gov/x.txt")


# --- Retry-After en 429s (IMPROVEMENT_PLAN.md M4) ---------------------------


@pytest.mark.parametrize("raw,esperado", [("30", 30), ("5", 5), ("0", None), ("-1", None)])
def test_retry_after_seconds_lee_el_header(raw, esperado):
    resp = _FakeStreamResponse("", headers={"Retry-After": raw})
    assert edgar_http._retry_after_seconds(resp) == esperado


def test_retry_after_seconds_sin_header_devuelve_none():
    resp = _FakeStreamResponse("", headers={})
    assert edgar_http._retry_after_seconds(resp) is None


def test_retry_after_seconds_no_entero_devuelve_none():
    # Retry-After también admite una fecha HTTP completa — no soportado aquí,
    # se cae al backoff fijo en vez de fallar.
    resp = _FakeStreamResponse("", headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert edgar_http._retry_after_seconds(resp) is None


def test_retry_after_seconds_topa_a_max():
    resp = _FakeStreamResponse("", headers={"Retry-After": "99999"})
    assert edgar_http._retry_after_seconds(resp) == edgar_http._MAX_RETRY_AFTER_S


def test_throttled_get_respeta_retry_after_de_un_429(monkeypatch):
    """El 429 pide explícitamente 3s de espera — el backoff fijo (2s) no debe
    usarse cuando la SEC ha sido explícita sobre cuánto esperar."""
    llamadas = {"n": 0}
    esperas: list[float] = []
    monkeypatch.setattr(edgar_http.time, "sleep", lambda s: esperas.append(s))

    def _get(*a, **kw):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return _FakeStreamResponse("ok", headers={"Retry-After": "3"}, status_code=429)
        return _FakeStreamResponse("ok", status_code=200)

    monkeypatch.setattr(edgar_http, "_get", _get)

    edgar_http.throttled_get("https://sec.gov/edgar/data/1/x.txt")

    assert llamadas["n"] == 2
    assert 3 in esperas
    assert 2 not in esperas  # el backoff fijo quedó sobrescrito, no acumulado


def test_throttled_get_sin_retry_after_usa_el_backoff_fijo(monkeypatch):
    llamadas = {"n": 0}
    esperas: list[float] = []
    monkeypatch.setattr(edgar_http.time, "sleep", lambda s: esperas.append(s))

    def _get(*a, **kw):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return _FakeStreamResponse("", status_code=429)
        return _FakeStreamResponse("ok", status_code=200)

    monkeypatch.setattr(edgar_http, "_get", _get)

    edgar_http.throttled_get("https://sec.gov/edgar/data/1/x.txt")

    assert llamadas["n"] == 2
    assert 2 in esperas

# --- Sesión y throttle por intervalo (BUGS_REPORT.md H-29) -------------------


def test_la_sesion_lleva_el_user_agent_de_la_sec():
    assert edgar_http._SESION.headers["User-Agent"] == edgar_http.HEADERS["User-Agent"]


def test_solo_se_espera_lo_que_falta_del_intervalo(monkeypatch):
    """La petición anterior empezó hace 0,05 s: con un intervalo de 0,125 s
    solo hay que esperar 0,075 s, no el intervalo entero encima de la latencia."""
    esperas: list[float] = []
    monkeypatch.setattr(edgar_http.time, "sleep", lambda s: esperas.append(s))
    monkeypatch.setattr(edgar_http, "_RATE_LIMIT_DELAY", 0.125)
    monkeypatch.setattr(edgar_http, "_reloj", lambda: 10.05)
    monkeypatch.setattr(edgar_http, "_ultimo_inicio", 10.0)

    edgar_http._esperar_turno()

    assert esperas == [pytest.approx(0.075)]


def test_si_ya_paso_el_intervalo_no_se_espera(monkeypatch):
    esperas: list[float] = []
    monkeypatch.setattr(edgar_http.time, "sleep", lambda s: esperas.append(s))
    monkeypatch.setattr(edgar_http, "_RATE_LIMIT_DELAY", 0.125)
    monkeypatch.setattr(edgar_http, "_reloj", lambda: 11.0)
    monkeypatch.setattr(edgar_http, "_ultimo_inicio", 10.0)

    edgar_http._esperar_turno()

    assert esperas == []
    assert edgar_http._ultimo_inicio == 11.0
