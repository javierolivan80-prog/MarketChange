"""Configuración de los workflows (BUGS_REPORT.md H-07): el Out-of-Sample
solo se mira a mano. Si alguien vuelve a poner el OOS en la pasada de cada
noche, estos tests fallan."""
import re
import subprocess
import sys
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _sin_comentarios(texto: str) -> str:
    return "\n".join(linea.split("#", 1)[0] for linea in texto.splitlines())


def test_ningun_workflow_programado_mira_el_oos():
    for wf in WORKFLOWS.glob("*.yml"):
        texto = _sin_comentarios(wf.read_text(encoding="utf-8"))
        if "schedule:" not in texto:
            continue
        assert "--oos" not in texto, wf.name
        assert "--full-range" not in texto, wf.name


def test_el_nightly_corre_backtest_y_validacion_solo_in_sample():
    texto = _sin_comentarios((WORKFLOWS / "nightly_pipeline.yml").read_text(encoding="utf-8"))
    assert re.search(r"run: python -m pipeline\.backtest\.portfolio_report\s*$", texto, re.MULTILINE)
    assert re.search(r"run: python -m pipeline\.validation\.report --persist-only\s*$", texto, re.MULTILINE)


def test_el_oos_solo_se_lanza_a_mano_y_con_motivo():
    texto = _sin_comentarios((WORKFLOWS / "oos_manual.yml").read_text(encoding="utf-8"))
    assert "workflow_dispatch:" in texto
    assert "schedule:" not in texto and "push:" not in texto and "pull_request:" not in texto
    assert re.search(r"motivo:\s*\n\s*description:.*\n\s*required: true", texto)
    assert texto.count("--oos --motivo") == 2
    # Grupo propio: compartir el de la nocturna cancelaría una de las dos.
    assert "group: oos-manual" in texto
    assert "init_schema" in texto


def test_ningun_workflow_llama_al_oos_desde_otro():
    for wf in WORKFLOWS.glob("*.yml"):
        if wf.name == "oos_manual.yml":
            continue
        texto = _sin_comentarios(wf.read_text(encoding="utf-8"))
        assert "oos_manual" not in texto, wf.name


def _cli(modulo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", modulo, *args], capture_output=True, text=True, timeout=60)


def test_los_comandos_ya_no_aceptan_full_range():
    for modulo in ("pipeline.backtest.portfolio_report", "pipeline.validation.report"):
        r = _cli(modulo, "--full-range")
        assert r.returncode == 2 and "--full-range" in r.stderr, modulo


def test_oos_sin_motivo_se_rechaza_antes_de_tocar_la_base():
    for modulo in ("pipeline.backtest.portfolio_report", "pipeline.validation.report"):
        r = _cli(modulo, "--oos")
        assert r.returncode == 2 and "--motivo" in r.stderr, modulo
