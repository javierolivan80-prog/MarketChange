from datetime import date

from pipeline.ops_history_prices import plan_gaps

TODAY = date(2026, 10, 2)


def test_ticker_nuevo_baja_su_ventana_redondeada_al_mes():
    gaps = plan_gaps({"NEW": (date(2020, 3, 17), date(2021, 2, 10))}, {}, TODAY)
    assert gaps == [("NEW", date(2020, 3, 1), date(2021, 2, 28))]


def test_rellena_hacia_atras_lo_anterior_al_primer_dia_guardado():
    gaps = plan_gaps(
        {"OLD": (date(2019, 11, 20), date(2026, 9, 30))},
        {"OLD": (date(2021, 1, 4), date(2026, 9, 30))},
        TODAY,
    )
    assert gaps == [("OLD", date(2019, 11, 1), date(2021, 1, 3))]


def test_ticker_al_dia_no_pide_nada():
    assert plan_gaps(
        {"OK": (date(2021, 3, 5), date(2021, 6, 1))},
        {"OK": (date(2021, 2, 26), date(2026, 10, 1))},
        TODAY,
    ) == []


def test_fin_nunca_pasa_de_hoy():
    gaps = plan_gaps({"X": (date(2026, 8, 10), date(2026, 10, 2))}, {}, TODAY)
    assert gaps == [("X", date(2026, 8, 1), TODAY)]
