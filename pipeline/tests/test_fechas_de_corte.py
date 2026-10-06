"""Fechas de corte de los modelos (BUGS_REPORT.md H-06)."""
from datetime import date

from pipeline import config


def test_cortes_de_entrenamiento_de_la_documentacion_oficial():
    # «Training data cutoff» de platform.claude.com/docs/en/models (2026-10-06).
    assert config.fecha_corte_modelo("claude-haiku-4-5") == date(2025, 7, 31)
    assert config.fecha_corte_modelo("claude-sonnet-4-6") == date(2026, 1, 31)


def test_el_nombre_con_fecha_de_la_api_se_reconoce():
    assert config.fecha_corte_modelo("claude-haiku-4-5-20251001") == date(2025, 7, 31)


def test_un_modelo_sin_corte_conocido_no_valida_nada():
    assert config.fecha_corte_modelo("claude-desconocido") is None
    assert config.fecha_corte_modelo(None) is None
    assert config.primer_d0_validable_ia("claude-haiku-4-5", "claude-desconocido") is None
    assert config.primer_d0_validable_ia() is None


def test_manda_el_corte_mas_tardio_del_par():
    assert config.primer_d0_validable_ia("claude-haiku-4-5", "claude-sonnet-4-6") == date(2026, 2, 1)


def test_todos_los_modelos_configurados_tienen_corte():
    for modelo in (config.CLASSIFIER_MODEL, config.ANALYZER_MODEL, config.JUDGE_MODEL):
        assert config.fecha_corte_modelo(modelo) is not None, modelo
    assert config.AI_VALIDATION_START == date(2026, 2, 1)
