"""test_sample_split.py — pipeline/backtest/sample_split.py. Puro, sin I/O:
solo aritmética de fechas y validación de argumentos (salvo el registro del
OOS, que va a Postgres)."""
import os
from datetime import date, timedelta

import pytest

from pipeline.backtest.sample_split import (
    IN_SAMPLE_END,
    OOS_START,
    SAMPLE_IN_SAMPLE,
    SAMPLE_OOS,
    date_bounds,
    git_sha_corto,
    tag_suffix,
    validate_sample,
)


def test_in_sample_end_and_oos_start_match_config_literal_values():
    # El corte que pide el plan: 2023-12-31 / 2024-01-01. Si config.py
    # cambia estas fechas, este test falla y avisa de que el corte se movió.
    assert IN_SAMPLE_END == date(2023, 12, 31)
    assert OOS_START == date(2024, 1, 1)


def test_in_sample_and_oos_are_contiguous_no_gap_no_overlap():
    """Mutuamente excluyentes Y sin hueco: OOS_START tiene que ser
    EXACTAMENTE el día siguiente a IN_SAMPLE_END, ni antes (solaparía un día
    en ambas particiones) ni después (ese día intermedio no pertenecería a
    ninguna, y quedaría fuera de cualquier reporte sin que nadie lo note)."""
    assert OOS_START - IN_SAMPLE_END == timedelta(days=1)


def test_date_bounds_in_sample_caps_at_in_sample_end_no_floor():
    start, end = date_bounds(SAMPLE_IN_SAMPLE)
    assert start is None
    assert end == IN_SAMPLE_END


def test_date_bounds_oos_floors_at_oos_start_no_cap():
    start, end = date_bounds(SAMPLE_OOS)
    assert start == OOS_START
    assert end is None


def test_date_bounds_none_means_no_filter_at_all():
    """Comportamiento por defecto de toda función de librería (ver docstring
    del módulo) — None no filtra nada, ni por arriba ni por abajo."""
    assert date_bounds(None) == (None, None)


def test_date_bounds_rejects_unknown_sample():
    with pytest.raises(ValueError):
        date_bounds("some_other_thing")


def test_validate_sample_accepts_none_and_known_values():
    validate_sample(None)
    validate_sample(SAMPLE_IN_SAMPLE)
    validate_sample(SAMPLE_OOS)  # no debe lanzar


def test_a_single_date_is_never_in_both_partitions():
    """Recorre un rango alrededor del corte y comprueba, fecha a fecha, que
    ninguna cae dentro de los dos bounds a la vez — la propiedad real que
    "mutuamente excluyentes" pide, no solo la resta de un día."""
    _, in_sample_end = date_bounds(SAMPLE_IN_SAMPLE)
    oos_start, _ = date_bounds(SAMPLE_OOS)
    for offset in range(-5, 6):
        d = IN_SAMPLE_END + timedelta(days=offset)
        in_in_sample = d <= in_sample_end
        in_oos = d >= oos_start
        assert not (in_in_sample and in_oos), f"{d} cae en ambas particiones"
        assert in_in_sample or in_oos, f"{d} no cae en ninguna partición"


def test_tag_suffix_only_for_oos(monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert tag_suffix(SAMPLE_OOS) == "-OOS"
    assert tag_suffix(SAMPLE_IN_SAMPLE) == ""
    assert tag_suffix(None) == ""


def test_tag_suffix_oos_distingue_cada_lanzamiento_en_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    assert tag_suffix(SAMPLE_OOS) == "-OOS-123456"
    assert tag_suffix(SAMPLE_IN_SAMPLE) == ""


# ---------------------------------------------------------------------------
# git_sha_corto — IMPROVEMENT_PLAN.md Q8: antes, portfolio_report.py y
# validation/report.py invocaban `git rev-parse` cada uno por su cuenta, con
# un except Exception amplio que caía a "unknown" sin loguear el motivo. La
# garantía real que hace falta es que las DOS corridas (mismo job de
# nightly_pipeline.yml) obtengan el MISMO valor, para que el tag coincida.
# ---------------------------------------------------------------------------


def test_git_sha_corto_prefiere_github_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abc123def456")
    assert git_sha_corto() == "abc123d"  # primeros 7 caracteres, como git rev-parse --short


def test_git_sha_corto_cae_a_git_rev_parse_sin_github_sha(monkeypatch):
    """Fuera de GitHub Actions (corrida manual local): sin GITHUB_SHA, se
    invoca git rev-parse de verdad — se comprueba con un fake en vez de
    depender de que este sandbox tenga un repositorio git real en el cwd."""
    import pipeline.backtest.sample_split as ss

    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr(ss.subprocess, "check_output", lambda *a, **kw: "deadbee\n")

    assert git_sha_corto() == "deadbee"


def test_git_sha_corto_cae_a_unknown_si_todo_falla(monkeypatch):
    """Determinista entre las dos corridas por el mismo motivo (ambas sin
    GITHUB_SHA y sin git disponible), no por casualidad — ver docstring de
    git_sha_corto."""
    import pipeline.backtest.sample_split as ss

    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _falla(*a, **kw):
        raise FileNotFoundError("git no instalado")

    monkeypatch.setattr(ss.subprocess, "check_output", _falla)

    assert git_sha_corto() == "unknown"


# ---------------------------------------------------------------------------
# Registro de cada vez que se mira el OOS (BUGS_REPORT.md H-07)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
def test_registrar_oos_apunta_cada_vez_y_cuenta_las_anteriores(monkeypatch):
    from pipeline.backtest.sample_split import registrar_oos
    from pipeline.db.connection import get_connection, init_schema

    monkeypatch.setenv("GITHUB_SHA", "abcdef0123")
    monkeypatch.setenv("GITHUB_ACTOR", "alguien")
    conn = get_connection()
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE oos_runs RESTART IDENTITY")
    conn.commit()
    try:
        assert registrar_oos(conn, "2026-10-06-abcdef0-OOS", "backtest", "primera vez") == 0
        # La validación del mismo lanzamiento no cuenta como otra mirada.
        assert registrar_oos(conn, "2026-10-06-abcdef0-OOS", "validacion", "primera vez") == 0
        assert registrar_oos(conn, "2026-11-01-abcdef0-OOS", "backtest", "segunda") == 1
        with pytest.raises(ValueError):
            registrar_oos(conn, "x", "backtest", "  ")
        with conn.cursor() as cur:
            cur.execute("SELECT run_batch_tag, paso, git_sha, lanzado_por, motivo FROM oos_runs ORDER BY oos_run_id")
            filas = cur.fetchall()
        assert len(filas) == 3
        assert filas[0]["git_sha"] == "abcdef0" and filas[0]["lanzado_por"] == "alguien"
        assert filas[0]["motivo"] == "primera vez"
    finally:
        conn.close()
