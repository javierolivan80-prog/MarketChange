"""test_ops_watchdog.py — relanzar la pasada que GitHub se salta."""
from datetime import datetime, timezone
from pathlib import Path

from pipeline.ops_watchdog import cron_matches, last_due, needs_relaunch, read_crons

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
CRONS = read_crons((WORKFLOWS / "nightly_pipeline.yml").read_text(encoding="utf-8"))


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _run(created_at, title="Nightly Pipeline"):
    return {"created_at": created_at, "display_title": title}


def test_lee_los_tres_crons_del_workflow():
    assert CRONS == ["30 14 * * 1-5", "30 21 * * 1-5", "0 6 * * 2-6"]


def test_cron_dia_de_la_semana_como_cron():
    # 2026-10-05 es lunes; 2026-10-04 domingo.
    assert cron_matches("30 14 * * 1-5", _utc(2026, 10, 5, 14, 30))
    assert not cron_matches("30 14 * * 1-5", _utc(2026, 10, 4, 14, 30))
    assert not cron_matches("0 6 * * 2-6", _utc(2026, 10, 5, 6, 0))  # lunes: no hay pasada de noche
    assert cron_matches("0 6 * * 2-6", _utc(2026, 10, 10, 6, 0))  # sábado sí


def test_relanza_la_pasada_que_no_llego():
    """El caso real: la de las 14:30 del lunes 5/10 no se lanzó."""
    ahora = _utc(2026, 10, 5, 16, 15)
    runs = [_run("2026-10-05T06:03:00Z")]  # la de la noche, anterior
    assert needs_relaunch(CRONS, runs, ahora) == "30 14 * * 1-5"


def test_no_relanza_si_la_pasada_llego_tarde_o_ya_se_relanzo():
    ahora = _utc(2026, 10, 5, 16, 15)
    assert needs_relaunch(CRONS, [_run("2026-10-05T15:40:00Z")], ahora) is None
    assert needs_relaunch(CRONS, [_run("2026-10-05T16:20:00Z", "Nightly Pipeline (relanzada 30 14 * * 1-5)")], ahora) is None


def test_el_diagnostico_y_el_historico_no_cuentan_como_pasada():
    ahora = _utc(2026, 10, 5, 16, 15)
    runs = [_run("2026-10-05T15:00:00Z", "Diagnóstico"), _run("2026-10-05T15:10:00Z", "Histórico")]
    assert needs_relaunch(CRONS, runs, ahora) == "30 14 * * 1-5"


def test_no_relanza_antes_del_margen_ni_lo_demasiado_viejo():
    # A las 15:30 aún no han pasado 90 minutos desde las 14:30, y la de las
    # 6:00 ya es demasiado vieja: no hay nada que revisar.
    assert last_due(CRONS, _utc(2026, 10, 6, 15, 30)) is None
    assert last_due(CRONS, _utc(2026, 10, 6, 7, 45)) == (_utc(2026, 10, 6, 6, 0), "0 6 * * 2-6")
    # Domingo por la tarde: la última fue el sábado a las 6:00, demasiado vieja.
    assert needs_relaunch(CRONS, [], _utc(2026, 10, 11, 18, 0)) is None


def test_el_watchdog_revisa_cada_pasada_dentro_de_su_ventana():
    """Cada hora del watchdog tiene que caer después del margen de su pasada
    y antes de que caduque."""
    crons_watchdog = read_crons((WORKFLOWS / "watchdog.yml").read_text(encoding="utf-8"))
    assert len(crons_watchdog) == 3
    revisadas = set()
    for cron in crons_watchdog:
        minuto, hora, _, _, dias = cron.split()
        # Un día de cada tipo: martes (todas las pasadas) y sábado (solo la noche).
        for dia in (6, 10):
            ahora = _utc(2026, 10, dia, int(hora), int(minuto))
            if cron_matches(cron, ahora):
                due = last_due(CRONS, ahora)
                assert due is not None
                revisadas.add(due[1])
    assert revisadas == set(CRONS)


def test_la_relanzada_hace_los_pasos_de_la_programada():
    """Los pasos que distinguen pasada ligera de nocturna miran también
    relaunch_of, y la relanzada no dispara el backfill manual de precios."""
    texto = (WORKFLOWS / "nightly_pipeline.yml").read_text(encoding="utf-8")
    assert "github.event.schedule !=" not in texto
    assert "\n      relaunch_of:\n" in texto
    job = texto.split("\n  price_backfill_manual:\n", 1)[1]
    condicion = next(linea for linea in job.splitlines() if linea.strip().startswith("if:"))
    assert "relaunch_of == ''" in condicion
