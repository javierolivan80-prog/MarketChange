"""ops_secretos.py — comprueba que los secretos del repositorio existen
(Tanda 5), sin enseñar nunca su valor.

GitHub no deja leer los secretos desde fuera; desde dentro de un job solo se
ve si llegan como variable de entorno. Este script lo comprueba en cada
pasada y deja un aviso ::warning:: en el resumen del run por cada uno que
falte, explicando qué deja de funcionar. Nunca falla el job.

    python -m pipeline.ops_secretos
"""
from __future__ import annotations

import os

# nombre -> qué deja de funcionar sin él
SECRETOS = {
    "DATABASE_URL": "todo el pipeline (no hay base de datos)",
    "ANTHROPIC_API_KEY": "el análisis con IA (las señales nuevas)",
    "TELEGRAM_BOT_TOKEN": "los avisos de Telegram (señales, salidas, fallos, gasto al 80 %)",
    "TELEGRAM_CHAT_ID": "los avisos de Telegram (señales, salidas, fallos, gasto al 80 %)",
    "BACKUP_PASSPHRASE": "el backup semanal cifrado (backup.yml)",
}


def secretos_que_faltan(entorno: dict | None = None) -> dict[str, str]:
    entorno = os.environ if entorno is None else entorno
    return {nombre: efecto for nombre, efecto in SECRETOS.items() if not (entorno.get(nombre) or "").strip()}


def main() -> None:
    faltan = secretos_que_faltan()
    for nombre, efecto in faltan.items():
        print(
            f"::warning title=Falta el secreto {nombre}::Sin {nombre} no funciona {efecto}. "
            "Se añade en Settings > Secrets and variables > Actions."
        )
    presentes = [n for n in SECRETOS if n not in faltan]
    print(f"Secretos presentes: {', '.join(presentes) or 'ninguno'}; faltan: {', '.join(faltan) or 'ninguno'}")


if __name__ == "__main__":
    main()
