"""hora_publicacion.py — cuándo se sabe que la hora de un filing es real.

BUGS_REPORT.md H-17. La hora de publicación sale de <ACCEPTANCE-DATETIME> de
la cabecera del filing (edgar_scraper.parse_acceptance_datetime). Cuando no
aparece, scrape_day guarda las 00:00 (Nueva York) del día del índice. EDGAR
solo acepta filings de 6:00 a 22:00 (Nueva York), así que la medianoche
exacta nunca es una hora real: es ese respaldo. No hace falta una columna
nueva para saberlo, y vale también para las filas que ya hay.

Con la hora desconocida no se sabe si el filing salió antes o después del
cierre, así que:
- D0 pasa a la sesión siguiente (compute_d0_close_date): lo conservador. Si
  salió antes del cierre, el CAR pierde parte de la reacción y la operación
  entra un día tarde; al revés, el CAR contaría el salto del anuncio como si
  fuera posterior a D0 y la IA leería como «reacción de D0» un día sin noticia.
- No entran en las estadísticas (análogos, prior, event study, backtest): su
  ventana empieza en un día dudoso.
- Los guardados antes de la Tanda 13 tienen D0 = el día del índice (el
  supuesto optimista): no van a la cola de la IA (D0_DUDOSO_SQL).

Filas anteriores a la hora de aceptación (revisión adversarial de la Tanda
13): hasta el 25-9-2026 (commit 2c8e969, fusionado a las 08:53 UTC)
scrape_day no la leía y guardaba la medianoche SIN zona. En una base en UTC
(la de producción y la de la CI) eso queda a las 00:00 UTC, que en Nueva
York son las 19:00/20:00 del día anterior: «medianoche de Nueva York» no las
reconoce. Se reconocen por la medianoche UTC en filas creadas antes del
26-9-2026; las posteriores a esa hora son reales (y así no se confunden con
los fixtures de los tests, que guardan filed_at como fecha). Su D0 es el día
del índice, como el de las otras sin hora.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

_NUEVA_YORK = ZoneInfo("America/New_York")

_MEDIANOCHE_NUEVA_YORK = "(e.filed_at AT TIME ZONE 'America/New_York')::time = TIME '00:00'"
_MEDIANOCHE_SIN_ZONA = (
    "(e.created_at < TIMESTAMPTZ '2026-09-26 00:00+00' AND (e.filed_at AT TIME ZONE 'UTC')::time = TIME '00:00')"
)
HORA_DESCONOCIDA_SQL = f"(({_MEDIANOCHE_NUEVA_YORK}) OR {_MEDIANOCHE_SIN_ZONA})"
HORA_CONOCIDA_SQL = f"NOT {HORA_DESCONOCIDA_SQL}"
# D0 = el día del índice: el supuesto optimista (antes de la Tanda 13).
D0_DUDOSO_SQL = (
    f"((({_MEDIANOCHE_NUEVA_YORK}) AND e.d0_close_date = (e.filed_at AT TIME ZONE 'America/New_York')::date)"
    f" OR ({_MEDIANOCHE_SIN_ZONA} AND e.d0_close_date = (e.filed_at AT TIME ZONE 'UTC')::date))"
)


def hora_desconocida(filed_at: datetime | None) -> bool:
    """True si filed_at es el respaldo sin hora (00:00:00 en Nueva York).
    Un datetime sin zona se toma como hora de Nueva York (así lo construye
    edgar_scraper)."""
    if filed_at is None:
        return False
    local = filed_at.astimezone(_NUEVA_YORK) if filed_at.tzinfo else filed_at
    return local.time() == time(0, 0)
