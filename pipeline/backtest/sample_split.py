"""sample_split.py — partición In-Sample / Out-of-Sample (ARCHITECTURE_LEAN.md
§8, T6 — "fuera de muestra, una sola vez", marcado como bloqueante).

Por qué existe: `config.py` ya declaraba `IN_SAMPLE_END` y `OOS_START`, con un
comentario explícito ("ajustar SOLO en IN_SAMPLE, evaluar UNA VEZ en OOS. No
se debe ejecutar el backtest sobre OOS más de una vez") — pero ningún módulo
las leía. `portfolio_simulator.py`, `portfolio_report.py` y
`validation/report.py` corrían siempre sobre el rango completo de eventos, así
que cualquier umbral ajustado a mano (EV thresholds, confidence floors,
bandas de sizing...) se afinaba con visión de dataset completo — el holdout
que el propio diseño del proyecto marca como innegociable no se aplicaba en
ningún sitio. Este módulo es el único punto que traduce esas dos fechas de
config.py a un filtro de consulta, para que in_sample/oos signifiquen
exactamente lo mismo en todos los módulos que los usan.

DISEÑO DELIBERADO — el filtro NO es el default de las funciones de librería:
`sample=None` (el default en fetch_events_for_version, simulate_portfolio,
run_full_backtest, run_event_study, persist_validation_report,
generate_full_validation_report) significa "sin filtro, todo el rango
configurado" — el comportamiento EXACTO de antes de este cambio, para no
romper ningún test ni ninguna llamada existente que no pida explícitamente
una muestra. Es cada **CLI** (`if __name__ == "__main__":` de portfolio_report.py
y validation/report.py) quien por defecto elige SAMPLE_IN_SAMPLE y solo pasa
a SAMPLE_OOS con el flag explícito `--oos` — "todo comando corre sobre
IN_SAMPLE salvo que se pida OOS a propósito" es una decisión de los puntos de
entrada, no de las funciones que reutilizan los tests.
"""
from __future__ import annotations

import os
import subprocess
from datetime import date

from pipeline import config

SAMPLE_IN_SAMPLE = "in_sample"
SAMPLE_OOS = "oos"
SAMPLES = (SAMPLE_IN_SAMPLE, SAMPLE_OOS)

IN_SAMPLE_END: date = date.fromisoformat(config.IN_SAMPLE_END)
OOS_START: date = date.fromisoformat(config.OOS_START)

# Cabecera visible en cualquier reporte/output generado con sample=SAMPLE_OOS
# — el punto 3 del plan: que no se pueda confundir con un reporte in-sample
# ni "colarse" a mirarlo sin darse cuenta de qué se está mirando.
OOS_WARNING = "OUT-OF-SAMPLE — NO USAR PARA AJUSTAR PARÁMETROS"

# Para las consultas del «último informe»: un OOS lanzado a mano no es el
# informe de cada noche y no debe salir en la app ni en las comparaciones
# (BUGS_REPORT.md H-07). Se aplica sobre una columna report_json.
NO_ES_OOS_SQL = "report_json->>'sample' IS DISTINCT FROM 'oos'"


def validate_sample(sample: str | None) -> None:
    if sample is not None and sample not in SAMPLES:
        raise ValueError(f"sample desconocido: {sample!r} (usar uno de {SAMPLES}, o None para sin filtro)")


def date_bounds(sample: str | None) -> tuple[date | None, date | None]:
    """(start_inclusive, end_inclusive) del filtro sobre d0_close_date.
    None en un extremo = sin límite por ese lado. sample=None = (None, None),
    es decir, sin filtro en absoluto (ver nota de diseño del docstring del
    módulo)."""
    validate_sample(sample)
    if sample is None:
        return None, None
    if sample == SAMPLE_IN_SAMPLE:
        return None, IN_SAMPLE_END
    return OOS_START, None


def tag_suffix(sample: str | None) -> str:
    """Sufijo para run_batch_tag/nombres de fichero — visible incluso si
    alguien solo mira el nombre del run sin abrir el contenido."""
    return "-OOS" if sample == SAMPLE_OOS else ""


def git_sha_corto() -> str:
    """Identificador corto del commit para el run_batch_tag del día
    (IMPROVEMENT_PLAN.md Q8) — portfolio_report.py y validation/report.py lo
    llaman cada uno por separado, en procesos de Python distintos dentro del
    MISMO job de nightly_pipeline.yml, y el tag tiene que coincidir EXACTO
    entre ambos para que validation/report.py encuentre el portfolio_report
    de esta misma corrida en vez de recalcular uno nuevo (ver el comentario
    en cada caller).

    Se prefiere GITHUB_SHA (variable de entorno que GitHub Actions define
    SIEMPRE para cualquier job del workflow) sobre invocar `git rev-parse`:
    no depende de que el binario git esté instalado ni de que el directorio
    de trabajo sea un repositorio real, y — más importante para la garantía
    de arriba — es la MISMA variable, ya fijada por la plataforma antes de
    que arranque el job, para las dos corridas de Python de ese job. Antes,
    cada caller invocaba `git rev-parse` por su cuenta con un `except
    Exception` amplio que caía a "unknown" sin loguear por qué — solo
    coincidían por casualidad (mismo repo, mismo commit, mismo fallo en
    ambos) en vez de por diseño.

    Solo se cae a `git rev-parse` fuera de GitHub Actions (una corrida manual
    en un checkout local), y a "unknown" si ninguna de las dos funciona —
    ese "unknown" sigue coincidiendo entre backtest y validación porque
    AMBAS corridas caen al mismo valor por el mismo motivo, no por azar."""
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha:
        return github_sha[:7]
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def registrar_oos(conn, run_batch_tag: str, paso: str, motivo: str) -> int:
    """Apunta en oos_runs que se ha mirado el OOS (H-07) y devuelve cuántas
    veces se había mirado antes. `paso`: 'backtest' o 'validacion'."""
    if not motivo or not motivo.strip():
        raise ValueError("mirar el OOS exige un motivo")
    with conn.cursor() as cur:
        cur.execute("SELECT count(DISTINCT run_batch_tag) AS n FROM oos_runs WHERE run_batch_tag <> %s", (run_batch_tag,))
        antes = cur.fetchone()["n"]
        cur.execute(
            "INSERT INTO oos_runs (run_batch_tag, paso, git_sha, lanzado_por, motivo) VALUES (%s, %s, %s, %s, %s)",
            (run_batch_tag, paso, git_sha_corto(), os.environ.get("GITHUB_ACTOR"), motivo.strip()),
        )
    conn.commit()
    return antes
