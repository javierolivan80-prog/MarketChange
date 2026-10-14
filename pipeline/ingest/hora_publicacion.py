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
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

_NUEVA_YORK = ZoneInfo("America/New_York")

HORA_DESCONOCIDA_SQL = "((e.filed_at AT TIME ZONE 'America/New_York')::time = TIME '00:00')"
HORA_CONOCIDA_SQL = f"NOT {HORA_DESCONOCIDA_SQL}"
D0_DUDOSO_SQL = (
    f"({HORA_DESCONOCIDA_SQL} AND e.d0_close_date = (e.filed_at AT TIME ZONE 'America/New_York')::date)"
)


def hora_desconocida(filed_at: datetime | None) -> bool:
    """True si filed_at es el respaldo sin hora (00:00:00 en Nueva York).
    Un datetime sin zona se toma como hora de Nueva York (así lo construye
    edgar_scraper)."""
    if filed_at is None:
        return False
    local = filed_at.astimezone(_NUEVA_YORK) if filed_at.tzinfo else filed_at
    return local.time() == time(0, 0)
