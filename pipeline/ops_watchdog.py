"""ops_watchdog.py — relanza la pasada programada que GitHub se haya saltado.

GitHub Actions no garantiza los crons: con carga alta los retrasa o, a
veces, no los lanza (pasó con la de las 14:30 UTC del 5/10). Una pasada
perdida son filings del día que la IA no ve hasta la siguiente.

Este script lo lanza .github/workflows/watchdog.yml un rato después de cada
pasada. Busca la última hora programada de nightly_pipeline.yml que ya
debería haber arrancado (hace más de GRACE_MINUTES) y, si desde entonces no
hay NINGUNA corrida de una pasada normal (programada o relanzada; el
diagnóstico y el histórico no cuentan), la relanza con workflow_dispatch y
relaunch_of=<su cron>, para que haga los mismos pasos que la programada.

No relanza nada de hace más de MAX_AGE_HOURS: para entonces ya viene la
siguiente pasada, que recoge lo pendiente igual.

Solo usa la biblioteca estándar (urllib): el job no instala dependencias.

    python -m pipeline.ops_watchdog [--dry-run]
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

WORKFLOW_FILE = "nightly_pipeline.yml"
GRACE_MINUTES = 90
MAX_AGE_HOURS = 6
# Margen por si GitHub crea la corrida programada unos minutos ANTES de la
# hora exacta (no suele, pero no cuesta nada).
EARLY_MINUTES = 5
# Corridas que NO son una pasada normal (ver run-name en nightly_pipeline.yml).
NOT_A_PASS_PREFIXES = ("Diagnóstico", "Histórico")


def read_crons(workflow_text: str) -> list[str]:
    """Los crons del bloque schedule del workflow, leídos del propio fichero
    para que cambiar una hora allí no exija acordarse de cambiarla aquí."""
    return re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', workflow_text, flags=re.MULTILINE)


def _field_matches(field: str, value: int) -> bool:
    for part in field.split(","):
        if part == "*":
            return True
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-"))
            if lo <= value <= hi:
                return True
        elif int(part) == value:
            return True
    return False


def cron_matches(cron: str, t: datetime) -> bool:
    """Cron de 5 campos (minuto hora día-del-mes mes día-de-la-semana), con
    *, listas y rangos: lo que usan nuestros workflows. Día de la semana como
    en cron: 0 = domingo."""
    minute, hour, dom, month, dow = cron.split()
    cron_dow = (t.weekday() + 1) % 7
    return (
        _field_matches(minute, t.minute)
        and _field_matches(hour, t.hour)
        and _field_matches(dom, t.day)
        and _field_matches(month, t.month)
        and _field_matches(dow, cron_dow)
    )


def last_due(crons: list[str], now: datetime) -> tuple[datetime, str] | None:
    """La hora programada más reciente que ya debería haber arrancado: entre
    hace MAX_AGE_HOURS y hace GRACE_MINUTES. None si no hay ninguna."""
    t = (now - timedelta(minutes=GRACE_MINUTES)).replace(second=0, microsecond=0)
    oldest = now - timedelta(hours=MAX_AGE_HOURS)
    while t >= oldest:
        for cron in crons:
            if cron_matches(cron, t):
                return t, cron
        t -= timedelta(minutes=1)
    return None


def is_a_pass(run: dict) -> bool:
    titulo = run.get("display_title") or run.get("name") or ""
    return not titulo.startswith(NOT_A_PASS_PREFIXES)


def needs_relaunch(crons: list[str], runs: list[dict], now: datetime) -> str | None:
    """Devuelve el cron a relanzar, o None. `runs`: corridas del workflow
    (formato de la API de GitHub: created_at, display_title)."""
    due = last_due(crons, now)
    if due is None:
        return None
    due_at, cron = due
    desde = due_at - timedelta(minutes=EARLY_MINUTES)
    for run in runs:
        creada = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if creada >= desde and is_a_pass(run):
            return None
    return cron


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict | None:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def main(dry_run: bool = False) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    ref = os.environ["WATCHDOG_REF"]
    now = datetime.now(timezone.utc)
    with open(f".github/workflows/{WORKFLOW_FILE}", encoding="utf-8") as f:
        crons = read_crons(f.read())
    desde = (now - timedelta(hours=MAX_AGE_HOURS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = _api("GET", f"/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=50&created=>={desde}", token)
    cron = needs_relaunch(crons, runs["workflow_runs"], now)
    if cron is None:
        logger.info("Sin pasadas perdidas: nada que relanzar")
        return
    logger.warning("GitHub no lanzó la pasada '%s': se relanza", cron)
    if dry_run:
        return
    _api(
        "POST",
        f"/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        token,
        {"ref": ref, "inputs": {"relaunch_of": cron}},
    )


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="solo dice si relanzaría")
    main(dry_run=parser.parse_args().dry_run)
