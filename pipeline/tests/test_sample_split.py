"""test_sample_split.py — pipeline/backtest/sample_split.py. Puro, sin I/O:
solo aritmética de fechas y validación de argumentos."""
from datetime import date, timedelta

import pytest

from pipeline.backtest.sample_split import (
    IN_SAMPLE_END,
    OOS_START,
    SAMPLE_IN_SAMPLE,
    SAMPLE_OOS,
    date_bounds,
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


def test_tag_suffix_only_for_oos():
    assert tag_suffix(SAMPLE_OOS) == "-OOS"
    assert tag_suffix(SAMPLE_IN_SAMPLE) == ""
    assert tag_suffix(None) == ""
