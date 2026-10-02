# MarketChange — panel

Panel de solo lectura sobre lo que el pipeline de Python (`../pipeline`, en
GitHub Actions) deja en Postgres. No escribe nunca en la base de datos ni
recalcula fórmulas financieras: renderiza lo que el pipeline ya calculó.

| Ruta | Qué responde |
|---|---|
| `/` | ¿Está vivo el sistema? ¿Qué señales hay? ¿Qué se descartó y por qué? ¿Me puedo fiar? |
| `/senales` | Cada evento analizado; el razonamiento se carga al desplegar la fila |
| `/senales/[id]` | Una señal con URL propia (a donde enlaza Telegram) |
| `/historial` | Señales emitidas y su resultado posterior en papel (no el backtest) |
| `/cartera` | Backtest histórico y simulación en papel de esta semana |
| `/funciona` | Event study, calibración, sensibilidad y comparación de versiones |
| `/largo-plazo` | Ranking de calidad fundamental (XBRL de la SEC); enlazado desde el pie |
| `/como-funciona` | Explicación del motor, sin datos |

## Desarrollo

```bash
cp .env.local.example .env.local   # y rellenar DATABASE_URL
npm ci
npm run dev
```

Comprobaciones (las mismas que `.github/workflows/app_ci.yml`):

```bash
npm run lint
npm run typecheck
TEST_DATABASE_URL=postgresql://…/base_de_pruebas npm test   # ¡vacía esa base!
npm run build
```

Sin `TEST_DATABASE_URL`, `npm test` solo corre las pruebas puras.

## Variables de entorno

| Variable | Obligatoria | Uso |
|---|---|---|
| `DATABASE_URL` | Sí | Misma base que el pipeline, con el rol de solo lectura `dashboard_ro` (`../pipeline/db/dashboard_readonly_role.sql`) |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | No | Si ambas están definidas, el panel pide usuario y contraseña (HTTP Basic, `middleware.ts`). Sin ellas es público para cualquiera con la URL |

## Convenciones

- Vocabulario visible (versiones, tipos de evento, motivos de salida): solo en `lib/labels.ts`.
- Formato de cifras: solo en `lib/format.ts`. Un dato ausente se muestra como `—`, nunca como `0`.
- Verde y rojo se reservan para dirección (LONG/SHORT) y P&L.
- Estados vacíos y de error: `components/ui/PageState.tsx`, `app/error.tsx`, `app/loading.tsx`.
- Las páginas leen de `lib/data.ts` (consultas con caché de 1–60 min); `lib/queries.ts` tiene el SQL y es lo que prueban los tests.
- Al usuario se le muestra una versión: la que recomienda el último informe de validación (`getRecommendedVersion`). Las otras dos siguen visibles en el detalle.
