# MOTION_PLAN — animación de datos en MarketChange

Estado: **implementado y verificado** (PR 1-4, #68 y siguiente). Las preguntas de producto del §5 están decididas; el PR 5 queda descartado.

Referencias aplicadas: skills `animate` (Emil Kowalski) y `motion-design`
(arquetipo **Corporate**: 200-400 ms, `cubic-bezier(0.2,0,0,1)`, 0 % de
rebote). Donde chocan con este brief (los springs del `layoutId` de `animate`,
el *shimmer* con gradiente, los pulsos de atención de `motion-design`),
manda el brief institucional.

---

## 0. Hallazgos que condicionan el plan

1. **No hay datos en vivo.** Todas las páginas son componentes de servidor
   `force-dynamic` con caché de 1-60 min (`lib/data.ts`). El pipeline escribe
   varias veces al día y **ningún cliente hace polling ni usa websockets**.
   En una sesión, los números no cambian delante del usuario. Por eso el
   *flash* de precio, el *throttle*, la interpolación del punto nuevo y la
   entrada y salida de filas en directo **no tienen hoy ningún evento que los
   dispare**. Quedan especificados por si un día se añaden (§2.B), pero no
   entran en ningún PR. → **PREGUNTA 2**
2. **Varios parámetros del brief no salen en la interfaz.** FULFILLED,
   INVALIDATED, SATURATED y EXPIRED (thesis_engine), el circuit-breaker
   (portfolio_simulator) y el %ADV existen en el pipeline, pero no en la app.
   STOP_LOSS solo aparece como *motivo de salida* en un texto. No se puede
   animar algo que no se pinta. → **PREGUNTA 3**
3. **Stack:** Next 15 (App Router, casi todo de servidor), React 19,
   Tailwind v4, Recharts 3 y TanStack Table. **No hay Framer Motion, y no
   hace falta añadirlo:** todo lo que se propone sale con CSS (transiciones
   y `@keyframes`) más las props de animación de Recharts. Como casi todo
   es de servidor, las animaciones en CSS funcionan sin hidratar y sin
   enviar JS al navegador.
4. **El count-up choca con una regla del propio brief.** Por un lado pide
   count-up en EV, P&L y confianza; por otro, que «el usuario pueda leer el
   valor final de inmediato». Durante 400-700 ms un count-up enseña cifras
   que no son ciertas (un P&L que pasa por 0 % o por +3,1 % antes de llegar a
   +7,4 %). Además, los valores llegan pintados desde el servidor, así que
   habría que hidratarlos y volver a pintarlos a 0, con el parpadeo que eso
   supone. → **PREGUNTA 1** (recomiendo no hacer count-up).

---

## 1. Tokens de motion

Una sola fuente de verdad en `app/app/globals.css` y un espejo en TS
(`app/lib/motion.ts`) solo para Recharts, que pide números en ms. Un test
(`lib/motion.test.ts`) comprueba que los dos coinciden.

```css
:root {
  /* Duraciones */
  --motion-duration-instant: 0ms;     /* hover de filas en tablas densas */
  --motion-duration-micro:   120ms;   /* hover, tooltip, chevron */
  --motion-duration-fast:    180ms;   /* crossfade de estado, salidas */
  --motion-duration-base:    240ms;   /* expansión de panel, entradas */
  --motion-duration-data:    560ms;   /* barras que se rellenan, gráficos */
  --motion-duration-max:     800ms;   /* tope duro: nada lo supera */
  --motion-stagger:          80ms;    /* Bull → Bear → Judge */

  /* Curvas (sin rebote ni elástico) */
  --motion-ease-out:    cubic-bezier(0.2, 0, 0, 1);    /* entradas: firma de la app */
  --motion-ease-in:     cubic-bezier(0.4, 0, 1, 1);    /* salidas */
  --motion-ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);  /* movimiento dentro de pantalla */
  --motion-ease-linear: linear;                        /* solo opacidad en bucle (skeleton) */

  /* Distancia */
  --motion-shift: 4px;                                 /* desplazamiento de entrada */
}
```

```ts
// app/lib/motion.ts: espejo para Recharts (ms y nombres de easing de Recharts)
export const motion = {
  duration: { micro: 120, fast: 180, base: 240, data: 560, max: 800 },
  stagger: 80,
  rechartsEase: "ease-out" as const,
};
```

Clases de utilidad reutilizables (en `globals.css`, nada de valores mágicos
en los componentes):

| Clase | Qué hace |
|---|---|
| `.m-enter` | `opacity 0→1` + `translateY(var(--motion-shift))→0`, `base`, `ease-out`, `both` |
| `.m-stagger > *:nth-child(n)` | `animation-delay: calc(var(--motion-stagger) * (n-1))`, con tope en 3 hijos |
| `.m-fill-x` | `clip-path: inset(0 100% 0 0)→inset(0)`, `data`, `ease-out` (rellena barras sin animar `width`) |
| `.m-draw` | `stroke-dashoffset 1→0` con `pathLength="1"`, `data`, `ease-out` |
| `.m-fade` | `opacity`, `fast`, `ease-out` |

**Movimiento reducido (obligatorio).** Hay un bloque global
`@media (prefers-reduced-motion: reduce)` que deja todas las clases `.m-*`
en su estado final, sin animación ni transición, y Recharts recibe
`isAnimationActive={false}`. Así, «reducido» significa **estado final
instantáneo**, no una versión más lenta. Solo se mantienen los cambios de
color de `hover` y foco (no son movimiento).

---

## 2. Tabla de parámetros

### 2.A Lo que hoy se pinta en la app

Columna «Cambia»: *nunca en sesión* significa que solo cambia al recargar o
navegar; *interacción* significa que lo provoca el usuario.

| Parámetro | Componente / archivo | Hoy | Cambia | Animación propuesta | Duración | Easing | Trigger | Justificación | Reduced-motion |
|---|---|---|---|---|---|---|---|---|---|
| **Precios: entrada, stop, objetivo** | `app/(paneles)/page.tsx` (tabla «Señales operables») | Texto `.num` estático | Nunca en sesión | **Ninguna** | — | — | — | Precio de referencia de un plan, no una cotización. Animarlo sugiere un «en vivo» que no existe y eso le resta credibilidad | — |
| **Plan de niveles** (stop–entrada–objetivo) | `components/viz/TradeLevels.tsx` en su versión completa (ficha de la señal) | Barra estática | Nunca en sesión | Los tramos crecen **desde la marca de entrada hacia fuera**: el de riesgo hacia el stop y el de beneficio hacia el objetivo (`.m-fill-x` con `transform-origin` en la entrada) | `data` 560 ms | `ease-out` | Carga | Cuenta la causalidad del plan: todo sale de la entrada, y el riesgo y el beneficio se comparan por longitud. Las cifras de la leyenda **no** se animan | Estado final directo |
| Plan de niveles, versión compacta | `TradeLevels compact` en la tabla de Inicio | Estático | Nunca en sesión | **Ninguna** | — | — | — | En una tabla, 10 barras moviéndose a la vez son ruido; la tabla se escanea, no se descubre | — |
| **% de cambio / P&L con signo** | `SignedPct` en `ui/DirectionBadge.tsx` (historial, señales, funciona, cartera) | Signo `+`/`−` y color, tabular | Nunca en sesión | **Ninguna** | — | — | — | Ya cumple «signo + color». No hay transición de estado que comunicar | — |
| Barra de P&L | `PnlBar` en `viz/Bars.tsx` (tabla de historial) | Estática | Nunca en sesión | **Ninguna** | — | — | — | Columna de tabla; la misma razón que la versión compacta de TradeLevels | — |
| **KPIs del resumen** (resultado, acierto, peor caída, nº de operaciones) | `Stat` en `app/(paneles)/page.tsx`; cabecera de `PortfolioVersionCard.tsx` | `text-2xl` / `text-xl` estáticos | Nunca en sesión | Recomendado: **fade de entrada del bloque** (`.m-enter`), sin count-up. Alternativa si se aprueba la PREGUNTA 1: count-up del número | `base` 240 ms (count-up: 560 ms) | `ease-out` | Primera carga | Señala que el bloque es el centro de la página sin enseñar cifras intermedias falsas | Sin animación |
| **Confianza del análisis** (0-100) | Columna de `SignalsTable.tsx`; ficha | Texto `NN%` | Nunca en sesión | **Ninguna** | — | — | — | Número en tabla. Va en la misma línea que la confianza técnica | — |
| **Confianza técnica** (barra) | `viz/Meter.tsx` en las tablas de Inicio y Señales | Barra estática con el color fijado por `strong` | Nunca en sesión | **Ninguna** en tablas | — | — | — | Hasta 25 barras rellenándose a la vez anulan el efecto; el color ya llega fijo | — |
| Puntuación técnica por partes (40/30/20/10) | `ScoreSegments` en `TechnicalPlanCard.tsx` | Segmentos estáticos | Nunca en sesión | Los segmentos cumplidos aparecen **en orden** (stagger de 80 ms, `.m-fade`); el color de cada uno llega fijo | `fast` 180 ms ×4 | `ease-out` | Carga de la ficha | Es una suma por criterios y el orden refuerza que la nota se construye por partes | Estático |
| **EV** (valor esperado) | Columna de `SignalsTable`; sección «Valor esperado» de `SignalAnalysis.tsx` | Texto | Nunca en sesión | **Ninguna** propia (entra con su sección, ver la fila del pipeline) | — | — | — | — | — |
| Tamaño de posición | `TechnicalPlanCard.tsx` (`position_size_pct`) | Texto | Nunca en sesión | **Ninguna** | — | — | — | Dato de lectura directa | — |
| Probabilidad de movimiento ±5/10/20 % | `HBars` en `SignalAnalysis.tsx` | Barras estáticas | Nunca en sesión | Relleno `.m-fill-x` de las 3 barras, sin stagger (se comparan entre sí) | `data` 560 ms | `ease-out` | Carga o despliegue | Tres barras comparables: el relleno simultáneo deja ver las proporciones | Estático |
| **Decisión recomendada** (LONG/SHORT/NO_TRADE) | Recuadro superior de `SignalAnalysis.tsx` y `DirectionBadge` | Badge estático | Nunca en sesión | **Ninguna**: es la respuesta y tiene que estar ya ahí | — | — | — | Si el veredicto aparece animado, se retrasa la respuesta | — |
| **Pipeline Bull → Bear → Judge** | Rejilla `#analisis` de `SignalAnalysis.tsx` | 6 secciones a la vez | Al cargar la ficha o desplegar una fila | `.m-stagger` + `.m-enter`: Bull 0 ms → Bear 80 ms → Judge 160 ms; el resto de secciones entra con Judge | `base` 240 ms + 160 ms máx. = **400 ms** en total | `ease-out` | Carga o despliegue | Hace visible el orden del razonamiento: tesis → antítesis → veredicto | Todo a la vez, sin animación |
| Las tres estrategias | Bloque `#versiones` de `SignalAnalysis.tsx` | Estático | Nunca en sesión | **Ninguna** | — | — | — | Se comparan lado a lado; escalonarlas daría a entender una jerarquía que no hay | — |
| Novedad (0-100) | Barra de la columna «Sorpresa» en `SignalsTable` | Barra estática | Nunca en sesión | **Ninguna** | — | — | — | Columna de tabla | — |
| Fiabilidad A/B/C | `viz/ReliabilityScale.tsx` en Inicio | Paso activo coloreado | Nunca en sesión | **Ninguna** | — | — | — | Un veredicto institucional no se «revela» | — |
| Aviso crítico (violaciones look-ahead) | `Callout kind="critical"` en `PortfolioVersionCard.tsx` | Recuadro rosa con la etiqueta «CRÍTICO» | Nunca en sesión | **Ninguna** (el estado crítico más parecido a un circuit-breaker que hay hoy) | — | — | — | La etiqueta escrita y el color ya bastan; un pulso en cada visita a una página que no ha cambiado sería alarma falsa | — |
| Alertas de paper trading | `Callout` en `cartera/page.tsx` | Estático | Nunca en sesión | **Ninguna** | — | — | — | Lo mismo | — |
| Frescura del pipeline (retraso en ámbar) | Cabecera de Inicio | Texto ámbar | Nunca en sesión | **Ninguna** | — | — | — | Lo mismo | — |
| **Curva de equity** (SVG hecho a mano) | `PortfolioEquityCurve.tsx` | Estática | Nunca en sesión | La línea se dibuja (`.m-draw`). El punto final y la etiqueta `+x %` aparecen **después** con `.m-fade` y retardo = `data` | Línea 600 ms + etiqueta 180 ms | `ease-out` | Primera carga | La curva cuenta un recorrido y la cifra final es su conclusión | Estado final directo |
| Sparkline del resultado | `viz/Sparkline.tsx` en Inicio | Estática | Nunca en sesión | **Ninguna** | — | — | — | Acompaña a la cifra, no es protagonista | — |
| Drawdown (área) | `DrawdownChart.tsx` (Recharts) | Animación por defecto de Recharts (ver §3) | Al abrir su `<details>` | Barrido de entrada de Recharts | `data` 560 ms | `ease-out` | Al abrir | Lectura de tramo temporal | `isAnimationActive={false}` |
| Histograma de retornos | `ReturnHistogram.tsx` | Animación por defecto | Al abrir su `<details>` | Barras que crecen desde 0 | `data` 560 ms | `ease-out` | Al abrir | El crecimiento desde el eje refuerza que se lee una frecuencia | Igual |
| P&L diario | `DailyPnLChart.tsx` | Animación por defecto | Carga | Barras que crecen desde el eje 0, hacia arriba o hacia abajo | `data` 560 ms | `ease-out` | Carga | Igual | Igual |
| Acierto por tramo de confianza | `ConfidenceBucketBars.tsx` | Animación por defecto | Al abrir | Igual | `data` 560 ms | `ease-out` | Al abrir | Igual | Igual |
| Calibración / predicho frente a real | `CalibrationCurve.tsx`, `ScatterPredictedActual.tsx` | Animación por defecto, **también en la diagonal** | Al abrir | Los puntos entran con un fade; la **diagonal de referencia no se anima** | `base` 240 ms | `ease-out` | Al abrir | La diagonal es el marco de referencia: si se anima, parece un dato más | Igual |
| Tooltips de gráficos | Recharts `<Tooltip>` | 400 ms de deslizamiento (por defecto) | Interacción | Seguir el cursor con `micro` 120 ms, o sin animación | `micro` 120 ms | `ease-out` | Hover | Con 400 ms el tooltip va detrás del cursor y parece descuidado | Sin animación |
| **Tabla de señales: ordenar** | `SignalsTable.tsx` | Reordenación instantánea | Interacción | **Ninguna** (sin animación de layout) | — | — | — | Con 25 filas cruzándose, el ojo pierde la fila que estaba siguiendo. Los terminales profesionales ordenan al instante | — |
| Tabla de señales: paginar y filtrar | `SignalsTable.tsx`, `SignalsFilterForm.tsx` | Instantáneo (filtrar es navegar) | Interacción | **Ninguna** | — | — | — | Igual | — |
| **Despliegue de una fila** (razonamiento) | `SignalRowDetail` en `SignalsTable.tsx` | El `<tr>` aparece de golpe; el chevron cambia de glifo ▸/▾ | Interacción | Un solo chevron que rota 90° (`micro` 120 ms). El contenido entra con `.m-enter` y después el stagger del pipeline. **Sin animar la altura** (no se puede animar un `<tr>` sin romper la tabla) | 120 ms + 240 ms | `ease-out` | Clic | Une causa (clic) y efecto (contenido) | Sin rotación ni fade |
| Plegar una fila | Igual | Desaparece de golpe | Interacción | Salida instantánea | 0 | — | Clic | Animar la salida retrasa la siguiente acción y obliga a mantener el nodo montado | — |
| Carga del razonamiento de una fila | `SignalRowDetail` (texto «Cargando análisis…») | Texto | Interacción | **Skeleton** con la forma real (recuadro de decisión + 2×2 bloques) y un pulso de opacidad sutil; se sustituye con `.m-fade` | Pulso de 1,6 s en bucle (solo mientras carga) | `linear` | Fetch | Avisa de que algo llega y deja ver ya la forma | Skeleton estático |
| Paneles `<details>` (todas las métricas, gráficos plegados, reglas) | `PortfolioVersionCard.tsx`, `TechnicalPlanCard.tsx`, `cartera/page.tsx` | Apertura instantánea, sin animación | Interacción | El contenido entra con `.m-enter`. La altura solo se anima con `::details-content` + `interpolate-size` donde el navegador lo admite (mejora progresiva) | `base` 240 ms | `ease-out` | Clic | Deja claro qué apareció. Animar la altura es lo único que el brief permite como excepción, y aquí no es imprescindible | Instantáneo |
| Tarjeta de calidad (largo plazo) | `QualityCard.tsx` | El panel aparece de golpe | Interacción | `grid-template-rows: 0fr→1fr` + opacidad (`base`); las 5 barras de criterio se rellenan con `.m-fill-x` y un stagger de 40 ms | 240 ms + 560 ms | `ease-out` | Clic | Deja ver qué criterios sostienen la nota | Instantáneo |
| Estado de carga de la página | `app/(paneles)/loading.tsx` | `animate-pulse` de Tailwind (2 s, opacidad 1↔0,5) | Navegación | Pulso más suave (opacidad 0,55↔0,85, 1,6 s, `linear`) y bloques con la forma real (franja de KPIs + tabla) | 1,6 s en bucle | `linear` | Navegación | Lo pide el brief. **Sin el shimmer con gradiente que sugieren las skills:** el brief prohíbe gradientes | Estático (ya tiene `motion-reduce`) |
| Estados vacío y error | `ui/PageState.tsx`, `app/error.tsx`, vacíos de los gráficos | Estáticos | Nunca en sesión | **Ninguna** | — | — | — | Un estado vacío tiene que leerse al momento | — |
| Nota de bienvenida | `WelcomeNote.tsx` | Aparece tras hidratar (salto de layout) y desaparece de golpe | Primera visita | Entrada `.m-fade` (180 ms); al cerrar, salida `ease-in` 150 ms antes de desmontar | 180 / 150 ms | `ease-out` / `ease-in` | Carga / clic | El cierre confirma la acción. *Al margen del motion: el salto de layout se arregla reservando el hueco, y va en el mismo PR* | Instantáneo |
| Botones y chips de filtro | `ui/Button.tsx`, `SignalsFilterForm.tsx` | `transition-colors` 150 ms (valores sueltos) | Interacción | Igual, pero con token `micro` | 120 ms | `ease-out` | Hover y foco | Respuesta táctil sin rebote | Se mantiene (es color, no movimiento) |
| Hover de filas | Tablas de Inicio, Señales e Historial | `hover:bg-surface-raised` instantáneo | Interacción | **Instantáneo** (`--motion-duration-instant`) | 0 | — | Hover | En tablas densas, un fundido entre filas deja un rastro al mover el ratón | — |
| Exportar CSV / PDF | `ExportCsvButton.tsx`, `ExportPdfButton.tsx` | Cambio de texto («Generando…», «Descargado») | Interacción | Crossfade del texto | `fast` 180 ms | `ease-out` | Clic | Cambio de estado del botón | Instantáneo |
| Tema y idioma | `ThemeToggle.tsx`, `LanguageToggle.tsx` | Instantáneo | Interacción | **Ninguna**; además, que los gráficos **no** se vuelvan a animar tras `router.refresh()` (ver §3) | — | — | — | Si los datos se redibujan al cambiar de idioma, parece que han cambiado | — |
| Saltos de ancla en la ficha | `globals.css` (`scroll-behavior: smooth`) | Ya protegido por `prefers-reduced-motion` | Interacción | Se mantiene | Nativo | Nativo | Clic | Ya está bien | Ya instantáneo |

### 2.B Especificado pero sin disparador hoy (solo si se añaden datos en vivo; PREGUNTA 2)

| Parámetro | Animación | Duración | Easing | Reduced-motion |
|---|---|---|---|---|
| Precio que cambia | Flash del fondo de la celda con `--gain`/`--loss` al 12 % de opacidad, que se desvanece. Sin contador rodante. El número se escribe ya con su valor final | 480 ms | `ease-out` | Sin flash; solo cambia el valor |
| Ritmo | Como mucho 1 flash por celda cada 1 s; si llegan más de 10 cambios por segundo en la vista, se suspenden los flashes | — | — | — |
| Cambio de estado de la señal (badge) | Crossfade del badge viejo al nuevo | `fast` 180 ms | `ease-out` | Instantáneo |
| STOP_LOSS / circuit-breaker al activarse en directo | **Un único** pulso de contorno de 2 px (sin bucle) y aviso en `aria-live="assertive"` | 600 ms, 1 vez | `ease-in-out` | Sin pulso; el aviso de `aria-live` se mantiene |
| Punto nuevo en una serie | Recharts interpola el punto nuevo, sin redibujar la serie (claves de datos estables) | `base` 240 ms | `ease-out` | Sin animación |
| Fila nueva / eliminada | Entrada: fade + 4 px (`.m-enter`). Salida: fade `ease-in` 150 ms. Las demás filas **no** se recolocan con animación | 240 / 150 ms | `ease-out` / `ease-in` | Instantáneo |

---

## 3. Animaciones que ya hay y que conviene eliminar o suavizar

| # | Dónde | Problema | Acción |
|---|---|---|---|
| 1 | **Todos los gráficos Recharts** (`DrawdownChart`, `DailyPnLChart`, `ReturnHistogram`, `ConfidenceBucketBars`, `CalibrationCurve`, `ScatterPredictedActual`) | No fijan `isAnimationActive` ni `animationDuration`, así que usan los valores por defecto de Recharts: **1500 ms en Line y Area** (pasan el tope de 800 ms) y 400 ms en Bar y Scatter, con curva `ease` | Fijar de forma explícita `animationDuration={motion.duration.data}` y `animationEasing="ease-out"` |
| 2 | `CalibrationCurve`, `ScatterPredictedActual`: **la diagonal y=x** | Se anima como si fuera un dato, y es la referencia | `isAnimationActive={false}` en la `<Line>` de la diagonal |
| 3 | Recharts y movimiento reducido | En el repo no hay ninguna gestión de `prefers-reduced-motion` para los gráficos. Al implementar hay que comprobar si la versión instalada lo respeta por defecto | `isAnimationActive` atado a un hook `usePrefersReducedMotion()` (o el valor `"auto"` si la versión lo admite) |
| 4 | Recharts tras `router.refresh()` (cambio de idioma) | Los gráficos de cliente pueden volver a montarse y redibujarse, y eso da a entender que los datos han cambiado | Comprobarlo en el navegador; si pasa, animar solo en el primer montaje (una ref `hasAnimated`) |
| 5 | Tooltips de Recharts | 400 ms de deslizamiento por defecto: el tooltip va detrás del cursor | `animationDuration={motion.duration.micro}` |
| 6 | `loading.tsx`: `animate-pulse` | Pulso de 2 s con opacidad que baja a 0,5: demasiada amplitud para un skeleton institucional | Un `@keyframes` propio más suave (§2.A); se mantiene `motion-reduce:animate-none` |
| 7 | `Button.tsx` (`duration-150`), chip de `SignalsFilterForm` (`transition-colors` sin duración) | Valores sueltos, sin token | Pasar a `duration-(--motion-duration-micro)` y `ease-(--motion-ease-out)` |
| 8 | Colores fijos en los gráficos (`#16a34a`, `#dc2626`, `#2563eb`, `#9ca3af`) | No seguían el tema ni el par validado para daltonismo | **Hecho:** `--gain`/`--loss` en series, acento y `--border-strong` del tema; la cifra de la curva de equity usa el par de texto de `SignedPct` (rosa/esmeralda 700 en claro y 400 en oscuro), porque en claro `--loss` no llega al contraste de texto |

Se mantiene sin cambios: `scroll-behavior: smooth` con su protección de
movimiento reducido.

---

## 4. Orden de implementación (PRs pequeños)

Cada PR pasa `npm run typecheck`, `npm run lint`, `npm test` y
`npm run build`. Además se revisa en Chrome con DevTools → Rendering →
«Emulate prefers-reduced-motion: reduce» y se hace una grabación de
Performance para comprobar que no hay tareas largas ni *layout thrashing*.

1. **PR 1: tokens y red de seguridad.** Tokens en `globals.css`,
   `lib/motion.ts` con su test de sincronía, bloque global de movimiento
   reducido y clases `.m-*` (sin usarlas todavía). Puntos 1, 2, 3, 5 y 7 del
   §3 (Recharts a 560 ms con `ease-out`, diagonales sin animación, tooltips
   a 120 ms, movimiento reducido en los gráficos). *Es la parte de más valor
   y menos riesgo: quita lo que hoy incumple el brief.*
2. **PR 2: carga.** Skeleton de `loading.tsx` con la forma real y pulso más
   suave; skeleton de `SignalRowDetail`; crossfade de los botones de
   exportar.
3. **PR 3: despliegues.** Chevron que rota y entrada del contenido en las
   filas de `SignalsTable`; `<details>` con `.m-enter`; `QualityCard`
   (0fr→1fr y relleno de barras); `WelcomeNote` con entrada y salida y su
   hueco reservado.
4. **PR 4: entrada de datos.** Stagger Bull → Bear → Judge; segmentos de
   puntuación; `HBars` en la ficha; `TradeLevels` crece desde la entrada;
   la curva de equity se dibuja y su etiqueta aparece después; punto 4 del §3.
5. **PR 5 (según las respuestas): count-up de KPIs** (PREGUNTA 1) y/o
   **animaciones de datos en vivo del §2.B** (PREGUNTA 2), con un hook
   `useLiveFlash` con *throttle*.

---

## 5. Decisiones tomadas

1. **Count-up: no.** Durante medio segundo enseñaría cifras de P&L que no son ciertas y obligaría a hidratar componentes que hoy se pintan en el servidor. Los KPIs se quedan estáticos: es lo que se lee primero y tiene que estar ya ahí.
2. **Datos en vivo: no se añaden.** El §2.B se queda como especificación por si algún día se añade polling o streaming; no hay código para ello.
3. **Estados de la tesis y circuit-breaker: fuera de este trabajo.** Mostrarlos es una funcionalidad, no motion. Si se muestran en el futuro, al cargar la página serán un estado estático bien etiquetado; el pulso único del §2.B solo aplica si saltan en directo.

## 6. Cambios respecto a la propuesta, tras implementar

- **Recharts ya respeta el movimiento reducido:** la v3 instalada usa `isAnimationActive: "auto"`, que comprueba `prefers-reduced-motion`. Se deja ese valor y solo se fijan duración y curva (`lib/motion.ts`), así que el hook propio no hizo falta.
- **QualityCard sin `grid-template-rows 0fr→1fr`:** eso anima el layout. El panel entra con `.m-enter` (opacidad + 4 px) como el resto de desplegables.
- **El count-up de KPIs se quita del inventario** (decisión 1).
- **Nota de bienvenida:** se pinta en el servidor y el script anterior al primer pintado (`layout.tsx`) la oculta si ya se cerró, así que ya no desplaza la página al hidratar.
- **Reanimación al cambiar de idioma (punto 4 del §3): confirmada y corregida.** Recharts relanza la animación cada vez que los datos llegan con otra referencia, aunque los valores sean iguales, y eso pasa en cada `router.refresh()`: el área de drawdown se aplanaba unos 600 ms y volvía a crecer. Ahora `components/viz/useChartMotion.ts` anima cada serie solo la primera vez y la desactiva en `onAnimationEnd`. No se hace con un temporizador desde el montaje porque cortaba la entrada a medias: el gráfico no se dibuja hasta que `ResponsiveContainer` mide su caja.

## Verificación

- `tsc`, `eslint`, `npm test` (26/26, incluidos 3 tests nuevos de tokens: sincronía CSS↔TS, tope de 800 ms y curvas sin rebote) y `next build` en verde.
- Revisado en Chrome con una página temporal con datos de ejemplo, ya borrada, inspeccionando `document.getAnimations()`: duraciones, retardos (stagger 0/80/160 ms) y curva `cubic-bezier(0.2,0,0,1)` correctos, nada por encima de 740 ms en total.
- Movimiento reducido: todas las reglas `.m-*` y `details[open]` están dentro de `@media (prefers-reduced-motion: no-preference)` (0 fuera), y los chevrons llevan `motion-reduce:transition-none`. Con movimiento reducido, todo se pinta directamente en su estado final.
