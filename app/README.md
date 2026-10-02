# MarketChange — panel

Panel de solo lectura sobre lo que el pipeline de Python (`../pipeline`, en
GitHub Actions) deja en Postgres. No escribe nunca en la base de datos ni
recalcula fórmulas financieras: renderiza lo que el pipeline ya calculó.

| Ruta | Qué responde |
|---|---|
| `/` | ¿Está vivo el sistema? ¿Qué señales hay? ¿Me puedo fiar? |
| `/senales` | Cada evento analizado, con el razonamiento completo |
| `/cartera` | Backtest histórico y simulación en papel de esta semana |
| `/funciona` | Event study, calibración, sensibilidad y comparación de versiones |
| `/largo-plazo` | Ranking de calidad fundamental (XBRL de la SEC) |
| `/como-funciona` | Explicación del motor, sin datos |

## Desarrollo

```bash
cp .env.local.example .env.local   # y rellenar DATABASE_URL
npm ci
npm run dev
```

## Variables de entorno

| Variable | Obligatoria | Uso |
|---|---|---|
| `DATABASE_URL` | Sí | Misma base que el pipeline (ver `../RUNBOOK.md`) |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | No | Si ambas están definidas, el panel pide usuario y contraseña (HTTP Basic, `middleware.ts`). Sin ellas es público para cualquiera con la URL |

## Convenciones

- Vocabulario visible (versiones, tipos de evento, motivos de salida): solo en `lib/labels.ts`.
- Formato de cifras: solo en `lib/format.ts`. Un dato ausente se muestra como `—`, nunca como `0`.
- Verde y rojo se reservan para dirección (LONG/SHORT) y P&L.
- Estados vacíos y de error: `components/ui/PageState.tsx`, `app/error.tsx`, `app/loading.tsx`.
