# PRODUCT AUDIT — MarketChange

Fecha: 2026-10-02. Alcance: todo el repositorio (`app/`, `pipeline/`, workflow de
GitHub Actions, docs). Método: lectura del código, no de la documentación;
`next build` + `tsc`; los 762 tests de Python contra un Postgres 16 real; el
panel arrancado contra una base con datos sintéticos y revisado con Playwright
a 375 px y 1280 px. No hay acceso de red a EDGAR/FDA/yfinance desde aquí, así
que **nada de lo que sigue valida que el sistema gane dinero** — esa es,
precisamente, la conclusión principal.

Leyenda de estado: **OK** implementado y verificado · **INCOMPLETO** ·
**SIMULADO** (datos sintéticos) · **ROTO** · **SIN VALIDAR** (código existe,
nunca ejecutado contra el mundo real) · **NO EXISTE**.

---

## 1. RESUMEN EJECUTIVO

**Qué es hoy.** Un sistema personal de investigación cuantitativa: cada noche
lee los 8-K de la SEC y decisiones de la FDA, decide si el evento es una
sorpresa, hace que un LLM debata a favor y en contra (Bull/Bear/Judge), estima
un valor esperado con análogos históricos y decide operar o abstenerse con tres
perfiles de riesgo. Lo acompaña un backtest de cartera, una simulación en papel,
avisos por Telegram y un panel Next.js de solo lectura. Un solo usuario: su
autor.

**Qué funciona.** La ingeniería del motor es seria — mucho más que la media de
proyectos de este tamaño: disciplina anti look-ahead aplicada en código
(D+1, `validate_no_lookahead`, split IN_SAMPLE/OOS), 762 tests que pasan,
control de gasto de la API, pre-filtro gratuito antes del LLM, idempotencia en
todas las escrituras, avisos de Telegram deduplicados. El panel es sobrio, no
tiene la estética de plantilla SaaS y hace algo que casi ningún producto de
señales hace: enseña por qué **no** opera.

**Qué está mal.** Lo más importante no es técnico: **no existe ninguna prueba
de que haya ventaja (edge)**. El único informe de validación es sintético y su
event study da p = 0,46–0,68 en todas las clases. Un producto cuyo valor es
"estas señales ganan dinero" vale cero hasta que eso se demuestre, y el
proyecto lleva ~57 PRs puliendo todo lo demás. Después: el panel era público
sin ninguna capa de acceso, tenía un bug que duplicaba filas en el feed de
señales, pintaba métricas ausentes como "0%" y caídas como "+12%", y el aviso de
Telegram — el único punto de contacto que de verdad usa alguien — era un
volcado técnico ("AGGRESSIVE: LONG · EV=+2.10% · net_conviction=-0.31") sin el
porqué.

**Mayor oportunidad.** La transparencia. El mercado de señales está lleno de
"compra X" sin historial auditable. Un servicio que publica cada señal con su
razonamiento, su condición de invalidación, su historial verificable con fecha
y, sobre todo, cuándo y por qué se abstiene, tiene un posicionamiento propio
("la señal honesta") — **si y solo si** el historial real resulta positivo.

---

## 2. PRODUCT SCORECARD

| Área | Estado | Problemas concretos |
|---|---|---|
| **Producto** | Débil | Propuesta de valor sin validar (edge no demostrado). Tres productos mezclados en una navegación: señales event-driven, backtest/validación (herramienta interna del autor) y ranking fundamental de largo plazo (otro producto, otro horizonte, otro usuario). Expone 3 versiones de estrategia al usuario sin decirle cuál usar: parálisis de elección. |
| **UX** | Aceptable | El flujo real es Telegram → filing. El panel no estaba conectado al aviso (sin enlace) y la portada no decía qué había pasado hoy ni si el sistema estaba vivo. `/cartera` y `/funciona` son volcados de métricas (14 indicadores × 3 versiones) sin jerarquía: es un informe para el autor, no para un usuario. |
| **UI** | Buena base | Sistema de tokens coherente, un solo acento, verde/rojo reservados a dirección/P&L, numerales tabulares, foco visible. Problemas: mayúsculas monoespaciadas en toda la navegación ("cosplay de terminal"), esquinas cuadradas forzadas con un *override* global de `.rounded`, el toggle de tema muestra el estado actual en vez de la acción, vocabulario inconsistente (corregido). |
| **Tecnología** | Buena (pipeline) / Correcta (panel) | Pipeline: sólido, testado. Panel: cero tests, sin ESLint, todas las páginas `force-dynamic` sin caché, se descarga el `report_json` completo (todas las operaciones de 3 versiones) para pintar 4 números en la portada, el feed manda hasta 500 filas con todo el JSON del LLM al cliente. Código muerto (2 componentes, `backtester.py`). Comentarios-ensayo en casi cada función. |
| **Seguridad** | Insuficiente → corregida en parte | Sin autenticación (ahora opcional). El panel usa la misma credencial de escritura que el pipeline. Texto del LLM interpolado en HTML de Telegram sin escapar (corregido). Inyección de prompt vía texto de filings: riesgo real no tratado. SQL siempre parametrizado (bien). |
| **Performance** | Correcta a escala de 1 usuario | `/cartera` cargaba 372 kB de JS por jsPDF (ahora 236 kB). Sin caché: cada visita = 3–6 consultas a Postgres con JSONB grandes. Pool de 3 conexiones por instancia serverless en un plan gratuito: con decenas de usuarios concurrentes se agota. |
| **Retención** | Inexistente como sistema | El único bucle es el aviso de Telegram. No hay historial personal, no hay seguimiento de "las señales que yo tomé", no hay resumen semanal, no hay forma de configurar qué avisos recibir. |
| **Monetización** | No existe | Ni usuarios, ni cuentas, ni pagos. Y un obstáculo regulatorio no considerado (ver §13). |
| **Diferenciación** | Potencial alto, no explotado | Debate adversarial + abstención explícita + calibración + IN/OOS es único. Hoy está enterrado en una fila desplegable. |

---

## 3. TOP 10 PROBLEMAS

1. **No hay evidencia de edge.** `docs/VALIDATION_REPORT.md` es sintético; el
   event study no es significativo en ninguna clase. Todo lo demás es
   secundario. *Estado: SIN VALIDAR.*
2. **Validación nunca ejecutada con datos reales.** Scraper de EDGAR, backfill
   de yfinance, Fama-French y extractor de texto de filings: *SIN VALIDAR*
   contra los servidores reales (bloqueo de red del sandbox).
3. **Panel público sin control de acceso.** Cualquiera con la URL de Vercel veía
   señales, cartera y razonamiento. *Corregido: Basic Auth opcional.*
4. **Feed de señales con filas duplicadas.** `LEFT JOIN portfolio_trades` sin
   filtrar `run_batch_tag`: cada evento operado aparecía una vez por cada
   corrida nocturna histórica (verificado: 28 filas para 12 eventos con 3
   corridas), también en el CSV, y el `LIMIT 500` se consumía en duplicados.
   *ROTO → corregido.*
5. **Datos inventados en pantalla.** `/funciona` pintaba `(x ?? 0)`: una
   métrica no calculada se mostraba como "+0.0%", indistinguible de un cero
   real; y si faltaba una versión, la página entera caía. *Corregido.*
6. **Signo de la caída invertido.** `max_drawdown` es positivo en el pipeline y
   se mostraba "Peor caída: +12.0%". *Corregido.*
7. **El aviso de Telegram no explicaba nada** y podía perderse para siempre:
   texto sin escapar en `parse_mode=HTML` → Telegram rechaza el mensaje → el
   evento no se marca y se reintenta en cada pasada sin llegar nunca.
   *Corregido: escapado, tesis ganadora, qué lo invalidaría, enlace al panel.*
8. **Tres productos en una navegación.** El usuario no sabe si esto es un
   servicio de señales, un laboratorio de backtesting o un screener
   fundamental.
9. **Credencial de escritura en el panel.** El panel "de solo lectura" usa la
   misma `DATABASE_URL` que el pipeline, con permisos de escritura. Un fallo en
   el panel compromete los datos. *Pendiente (infra): rol de solo lectura.*
10. **Superficie de inyección de prompt.** El texto de un 8-K, redactado por el
    emisor, entra directamente en el prompt de Bull/Bear/Judge. Un emisor
    podría redactar el filing para empujar la decisión. No hay mitigación.

## 4. TOP 10 OPORTUNIDADES

1. **Primera corrida real de punta a punta** (backfill 2–3 años → event study
   real → decisión). Convierte el proyecto de "promesa" en "dato".
2. **Historial público y verificable**: cada señal con fecha/hora de emisión
   inmutable y resultado posterior. Es el activo de confianza del producto.
3. **Telegram como producto principal**, el panel como "ver más". *Iniciado.*
4. **Una sola recomendación por señal**: la versión que el motor de validación
   recomienda, no tres.
5. **"Por qué no opero"** como funcionalidad de portada: el resumen diario de
   eventos descartados y su motivo es contenido único y barato (sin LLM).
6. **Resumen semanal** (Telegram/email): señales emitidas, cerradas, acierto
   real vs. calibrado. Bucle de retención natural.
7. **Mis operaciones**: el usuario marca qué señales tomó; el panel le enseña
   su P&L real frente al del sistema.
8. **Página de señal con URL propia** (`/senales/[id]`): enlazable desde
   Telegram, compartible, indexable si se hace pública.
9. **Watchlist**: avisos solo para tickers o tipos de evento elegidos.
10. **API de eventos clasificados** (8-K → clase, novedad, análogos): producto
    B2B con mucho menos riesgo regulatorio que vender señales.

## 5. FUNCIONALIDADES QUE FALTAN (priorizadas)

| Funcionalidad | Problema que resuelve | Usuario | Valor | Complejidad | Impacto | Prioridad |
|---|---|---|---|---|---|---|
| Corrida real + informe de validación real | No se sabe si funciona | Fundador | Decide si existe producto | Media (infra, no código) | Máximo | 🔴 |
| Rol Postgres de solo lectura para el panel | Panel con permisos de escritura | Operador | Contención de fallos | Baja | Alto | 🔴 |
| Página de detalle `/senales/[id]` | El enlace de Telegram filtra por ticker, no abre la señal | Usuario de avisos | Un clic al porqué | Baja | Alto | 🟠 |
| Historial verificable de señales | Nadie se fía de un backtest | Cualquier usuario | Confianza | Media | Alto | 🟠 |
| Resumen semanal | No hay motivo para volver | Usuario activo | Retención | Baja | Alto | 🟠 |
| Watchlist / filtros de aviso | Ruido de avisos | Usuario activo | Relevancia | Media | Medio | 🟡 |
| "Mis operaciones" | El P&L simulado no es el del usuario | Usuario que opera | Personalización | Media | Medio | 🟡 |
| Cuentas multiusuario + pagos | No hay negocio | Futuro cliente | Ingresos | Alta | — | 🟢 (solo tras validar) |
| API de eventos | Diversificar más allá de señales | B2B | Ingresos sin riesgo de asesoramiento | Alta | Medio | 🟢 |

## 6. COSAS QUE ELIMINARÍA

- **Las tres versiones de estrategia de cara al usuario.** Mantenerlas como
  herramienta interna; al usuario se le da una recomendación. Elegir entre
  "conservador/equilibrado/agresivo" sin saber cuál funciona es trasladarle
  una decisión que el sistema ya sabe tomar (`best_version`).
- **DYNAMIC** (4ª versión): ya está marcada como pendiente de decisión en el
  plan; sin datos reales que la justifiquen, es complejidad pura.
- **Los componentes muertos.** `VersionOverviewCard` y `CombinedEquityChart`
  (no los importa nadie) y los SVG de la plantilla de create-next-app:
  *eliminados*. (Corrección: la primera versión de este informe daba
  `backtester.py` como código muerto pendiente; no lo es — el motor paralelo
  ya se había borrado en un PR anterior y lo que queda, `compute_car`, es
  código vivo.)
- **"Largo plazo" de la navegación principal.** Es otro producto (screener
  fundamental, horizonte de años). O se separa en su propia sección/subdominio,
  o se aparca hasta que el producto principal esté validado.
- **"Cómo funciona" como pestaña.** Su contenido es bueno, pero es material de
  onboarding: debería aparecer la primera vez y estar enlazado desde cada
  señal, no ocupar una pestaña permanente.
- **El exportador PDF.** Útil para el autor, irrelevante para un usuario; si se
  queda, que sea en la página de validación, no en la cabecera de Cartera.
- **La mitad de los comentarios.** Muchos comentarios son ensayos que narran
  la historia del cambio ("Antes… ahora…", "pedido explícitamente…"). Esa
  historia pertenece al git log y a los PR. En el código, solo el *porqué* no
  obvio. (Los comentarios añadidos en esta auditoría siguen el estilo del repo
  para no romper la convención; la limpieza debería ser una pasada propia.)
- **RUNBOOK.md e IMPROVEMENT_PLAN.md de 40 KB cada uno.** Un plan que nadie
  puede leer de una sentada no guía. Partir en: runbook operativo corto,
  decisiones (ADR) y un tablero de tareas abiertas.

## 7. AI-SLOP AUDIT

El panel **no** tiene los síntomas clásicos: sin gradientes, sin
glassmorphism, sin morado-azul de IA, sin iconos de Lucide decorativos, sin hero
genérico, sin "Empower your…". Es un mérito real. Lo que sí delata un producto
generado o sin criterio editorial:

| Síntoma | Dónde | Por qué perjudica | Alternativa |
|---|---|---|---|
| "Terminal cosplay": mayúsculas + monoespaciada en navegación, botones y títulos | `Nav.tsx`, botones, `h1` | Imita la estética de Bloomberg sin su densidad ni su función; reduce legibilidad en móvil | Monoespaciada solo para tickers y cifras; texto de interfaz en la sans del sistema, en caja normal |
| `.rounded { border-radius: 0 }` global | `globals.css` | Un *override* que contradice la clase que se usa: quien lea `rounded` en un componente no sabrá que no redondea | Quitar `rounded` de los componentes, o definir el radio con un token |
| Volcado de métricas sin jerarquía | `PortfolioVersionCard.tsx` (14 métricas × 3) | Todo pesa igual: el usuario no sabe qué mirar | 3 cifras protagonistas + "ver detalle" |
| Copy narrativo/coloquial | "en cristiano", "¿Ya descontado?", "Esto es normal al principio — no es un fallo" | Suena a explicación de chatbot, no a producto | Frases cortas e informativas (*corregidas las más visibles*) |
| Jerga inglesa mezclada | "Win rate", "Profit factor", "Expectancy", cabeceras "Conservative/Aggressive/Balanced", PDF "Money POC — Backtest Report" | Producto en español con restos de un spec en inglés: descuido | *Traducido y unificado en `lib/labels.ts`* |
| Mismo concepto, dos nombres | "Equilibrado" (Cartera) vs "Balanceado" (Señales) | Erosiona confianza | *Corregido* |
| Códigos internos en pantalla | `8K_2.02_EARNINGS`, `FDA_OPENFDA`, `TAKE_PROFIT` | El usuario ve el modelo de datos | *Etiquetas legibles en tabla, filtros, PDF, Telegram* |
| Emoji ⚠ en la interfaz | `cartera/page.tsx` | Contradice la propia regla del sistema de diseño ("cero emojis") | *Sustituido por `Callout`* |
| Toggle que muestra el estado y no la acción | `ThemeToggle.tsx` ("OSCURO") | Ambiguo: ¿es el modo actual o al que voy? | Icono sol/luna con `aria-label`, o "Modo claro" |

## 8. UX AUDIT

Recorrido de un usuario nuevo, tal como estaba:

1. **Llega a la URL.** Lo primero que veía, encima incluso del nombre del
   producto, era un aviso legal de 4 líneas (en móvil). *Movido al pie.*
2. **Portada.** Un semáforo y 4 cifras del backtest. No decía qué ha pasado
   hoy, ni si el sistema está vivo (un pipeline caído hace una semana se veía
   igual). *Rediseñada: última actualización con aviso si está desfasada,
   últimas señales operables enlazadas, y las cifras de la versión que el motor
   recomienda (antes, siempre BALANCED aunque el semáforo hablara de otra).*
3. **Señales.** Buena tabla; el detalle desplegable es lo mejor del producto.
   Pero: 500 filas con todo el JSON del LLM en el HTML; sin URL por señal; la
   columna "Señal" se deriva del signo de la convicción aunque solo opere una
   versión.
4. **Cartera / ¿Funciona?** Pensadas para el autor. Un usuario cierra aquí.
5. **Retorno.** Nada le trae de vuelta salvo Telegram, que no enlazaba al
   panel. *Enlazado (con `DASHBOARD_URL`).*

**Qué haría que alguien cerrase la app:** no entender en 10 segundos qué hacer
con lo que ve; tres versiones sin recomendación; métricas de quant sin
traducción a decisión.

**Qué haría que volviera mañana:** una señal nueva en Telegram con un porqué
que se lea en 15 segundos, y ver al día siguiente cómo va.

Otros: no existía `error.tsx` (un fallo de Postgres mostraba el error genérico
de Next en inglés), ni `loading.tsx` (al cambiar de pestaña no había ninguna
señal de carga), ni 404 propio. *Añadidos.* La navegación usaba márgenes
negativos que desbordaban 8 px en páginas con padding 4 → scroll horizontal en
móvil. *Corregido; verificado scrollWidth = viewport a 375 px.*

## 9. UI AUDIT

- **Bien:** tokens de color en un solo sitio, modo oscuro real, contraste alto,
  foco visible, numerales tabulares, verde/rojo con significado único,
  estados vacíos explícitos.
- **Mal:** padding inconsistente entre páginas (p-4/p-6/p-8 → *unificado*),
  cinco variantes de estado vacío (→ *`PageState.tsx`*), tarjetas de métricas
  sin jerarquía, gráficos de Recharts sin estilo propio coherente con el resto,
  pestañas que en móvil quedan cortadas sin indicación de scroll.
- **Accesibilidad:** buena base (`aria-sort`, `aria-expanded`, `caption`,
  labels asociados, `:focus-visible`). Faltaba `scope` en cabeceras de la
  tabla de comparación (*añadido*). Las barras de "Sorpresa" no tienen texto
  alternativo más allá del número adyacente (aceptable). Botones de
  paginación de 28 px de alto: por debajo de 44 px de objetivo táctil en móvil.
  El esqueleto de carga respeta `prefers-reduced-motion`.

## 10. TECHNICAL AUDIT

| Hallazgo | Impacto | Solución | Estado |
|---|---|---|---|
| Duplicados en `getSignalsFeed` | Datos incorrectos | Filtrar por la última corrida | ✅ |
| `?? 0` en comparativa | Datos inventados | `—` para ausentes, `Partial` en tipos | ✅ |
| Tabla de sensibilidad sin BALANCED (el pipeline ya la calcula, R13) | Información perdida | Columnas por `VERSION_ORDER` | ✅ |
| jsPDF en el bundle inicial | 136 kB de JS innecesario | `import()` al pulsar | ✅ (372 → 236 kB) |
| `ComparisonTable` cliente con Tanstack para 6 filas | JS sin interacción | Tabla de servidor | ✅ |
| Sin tests en `app/`, sin ESLint | Regresiones como las de arriba pasan desapercibidas | ESLint + tests de `lib/` (format, labels, queries contra Postgres) + workflow `app_ci.yml` | ✅ |
| `force-dynamic` en todo + `report_json` completo para 4 cifras | Coste por visita, latencia | `lib/data.ts`: caché de datos de 1–60 min; Inicio lee solo las rutas JSON que pinta | ✅ |
| Feed: 500 filas con JSONB completo al cliente | HTML de varios MB a escala | Filas ligeras; detalle bajo demanda (`/api/senales/[id]`) | ✅ |
| Pool `max: 3` por instancia serverless | Agota conexiones del plan gratuito con tráfico | Pooler (Neon/Supabase pgbouncer) | Pendiente |
| `queries.ts` de 700 líneas con tipos a mano | Deriva silenciosa respecto a `schema.sql` | Generar tipos o validar con un esquema en runtime | Pendiente |
| Código muerto | Confusión | Borrar | ✅ |
| Comentarios-ensayo | Ruido, coste de lectura | Pasada de limpieza | Pendiente |
| `npm audit`: dompurify; postcss dentro de `next` | Solo build | `next` 15.5.27 + `overrides` de postcss ≥ 8.5.28 | ✅ 0 vulnerabilidades |

**Escalabilidad.** 10 usuarios: sin problema. 100: el pool y las consultas sin
caché empiezan a notarse en el plan gratuito. 1.000: hace falta caché y
pooler; el JSON por visita domina el coste. 10.000+: el modelo "un Postgres
compartido entre pipeline y panel" no aguanta — separar una réplica de lectura o
publicar los informes como estáticos tras cada corrida. 100.000: el panel debería
ser casi enteramente estático (los datos cambian 3 veces al día) servido desde
CDN; lo dinámico serían solo cuentas y preferencias.

## 11. SECURITY AUDIT

| Riesgo | Impacto | Solución | Prioridad |
|---|---|---|---|
| Panel sin autenticación | Cualquiera con la URL ve todo | `middleware.ts`: Basic Auth con `DASHBOARD_USER`/`DASHBOARD_PASSWORD` (comparación en tiempo constante) — *implementado, opcional* | 🔴 activar ya en Vercel |
| Panel con credencial de escritura | Un fallo o RCE en el panel puede modificar/borrar datos | `pipeline/db/dashboard_readonly_role.sql` (idempotente; además fuerza transacciones de solo lectura y `statement_timeout`) — *script hecho y probado*; falta ejecutarlo y cambiar la URL en Vercel | 🔴 operador |
| HTML de Telegram sin escapar | Avisos perdidos para siempre; inyección de enlaces | `html.escape` en todo texto interpolado — *implementado + tests* | ✅ |
| Inyección de prompt vía texto del filing | Un emisor puede sesgar la decisión del LLM | Extracto delimitado como dato no confiable, etiquetas de cierre falsas neutralizadas, regla explícita en los 3 system prompts — *implementado + tests*. Pendiente: evaluación adversarial con el modelo real | 🟡 |
| Clickjacking / cabeceras | Bajo | `X-Frame-Options`, `frame-ancestors`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, sin `X-Powered-By` — *implementado* | ✅ |
| Indexación | Un panel privado en buscadores | `robots: noindex` — *implementado* | ✅ |
| SQL injection | — | Todas las consultas parametrizadas; el único SQL dinámico concatena fragmentos fijos | OK |
| XSS | — | Único `dangerouslySetInnerHTML` es el script estático de tema; enlaces externos con `rel="noopener noreferrer"` | OK |
| Secretos | — | Solo en GitHub Secrets / Vercel; `.trim()` defensivo; nada en el cliente | OK |
| Rate limiting | Bajo hoy (solo lectura) | Vercel Firewall si se hace público | 🟢 |
| Regulatorio (no es seguridad, pero es el riesgo nº 1 de negocio) | Vender señales personalizadas puede requerir autorización (CNMV en España/UE; SEC/FINRA en EE. UU.) | Consultar antes de cobrar; ver §13 | 🔴 antes de monetizar |

## 12. RETENTION STRATEGY

El usuario de este producto no "entra a la app": recibe un aviso y decide. La
retención se diseña alrededor del aviso, no del panel.

- **Mañana:** porque llega una señal nueva con un porqué legible y un enlace a
  su análisis (*hecho*), y porque recibe el aviso de cierre de las que siguió
  (ya existe `exits_notifier`).
- **En una semana:** resumen semanal — señales emitidas, cerradas, acierto real
  frente a la confianza declarada, eventos descartados más interesantes. Se
  construye con datos que ya existen (`paper_trading_reports`).
- **En seis meses:** un historial verificable que el usuario puede auditar y
  comparar con lo que él hizo ("Mis operaciones"). La confianza acumulada es el
  único foso real de un servicio de señales.
- **Evitar:** rachas, medallas, puntos. En un producto de inversión, gamificar
  la frecuencia empuja a operar más, que es justo lo contrario de lo que el
  motor de abstención intenta conseguir.

## 13. MONETIZATION STRATEGY

Ninguna opción es viable hasta tener un historial real positivo. Dicho eso:

| Modelo | Encaje | Riesgo |
|---|---|---|
| **B2C suscripción de señales** (15–40 €/mes) | Natural para el producto actual | Alto: puede considerarse asesoramiento/recomendación de inversión regulada. Requiere asesoría legal y, probablemente, presentarse como contenido editorial impersonal |
| **B2C herramienta de investigación** ("inteligencia de eventos": clasificación, novedad, debate, análogos — sin decir compra/vende) | Bueno: aprovecha lo diferencial (el análisis) sin el punto conflictivo (la orden) | Medio |
| **B2B API/feed de eventos clasificados** | Bueno para fondos pequeños/quants: 8-K estructurado con clase, novedad y análogos | Bajo regulatoriamente; ciclo de venta largo |
| **Freemium** | Free: señales con 24 h de retraso + historial público. Pro: en tiempo real, watchlist, resumen semanal, exportación | El retraso de 24 h es el gancho natural: el valor de una señal event-driven decae con las horas |

**Recomendación:** validar primero; después lanzar como herramienta de
investigación freemium (gratis con retraso, Pro en tiempo real) y explorar la
API como segunda línea. No lanzar "señales de compra/venta" de pago sin
asesoría legal.

## 14. PRODUCT ROADMAP

### FASE 1 — FOUNDATION (antes de cualquier otra cosa)
- Corrida real completa (`.github/workflows/nightly_pipeline.yml` con
  `backfill_start`), regenerar `docs/VALIDATION_REPORT.md` con datos reales.
- Activar `DASHBOARD_USER`/`DASHBOARD_PASSWORD` en Vercel; rol de solo
  lectura para el panel (SQL en §11).
- Definir la variable de repo `DASHBOARD_URL` para que Telegram enlace al panel.
- ESLint + tests de `app/lib/*` (incluido un test de `getSignalsFeed` contra
  Postgres con varias corridas, para que el bug de duplicados no vuelva).
- Decidir con datos: ¿hay edge en alguna clase? Si no, parar y replantear.

### FASE 2 — UX
- `/senales/[id]` (página propia por señal) y enlace de Telegram a ella
  (`app/app/senales/[id]/page.tsx`, `pipeline/notify/signals_notifier.py`).
- Una sola versión recomendada de cara al usuario
  (`SignalDetail.tsx`, `SignalsTable.tsx`, portada).
- `PortfolioVersionCard.tsx`: 3 cifras protagonistas + detalle plegado.
- Tipografía: monoespaciada solo para cifras/tickers (`Nav.tsx`, botones,
  `globals.css`), quitar el *override* de `.rounded`.

### FASE 3 — PRODUCT
- Historial público verificable (tabla inmutable de señales emitidas con
  hash/fecha; página `/historial`).
- "Por qué no opero hoy": resumen de descartes en la portada.
- Mitigación de inyección de prompt (`adversarial_analyzer.py`).

### FASE 4 — RETENTION
- Resumen semanal por Telegram (`pipeline/notify/weekly_digest.py`, nuevo).
- Watchlist y filtros de avisos.
- "Mis operaciones".

### FASE 5 — MONETIZATION
- Asesoría legal → posicionamiento (herramienta de investigación vs. señales).
- Cuentas (Auth.js/Clerk), Stripe, retraso de 24 h en el plan gratuito.

### FASE 6 — SCALE
- Caché/ISR de páginas de informes; informes publicados como estáticos tras
  cada corrida.
- Pooler de conexiones; réplica de lectura.
- API pública de eventos.

## 15. FINAL VISION

**Dentro de 12 meses, si el edge existe:** MarketChange es el servicio de
inteligencia de eventos corporativos más transparente del mercado
hispanohablante. El usuario — inversor particular avanzado, con cuenta en un
bróker y sin tiempo para leer 8-K — recibe dos o tres avisos a la semana. Cada
uno se lee en quince segundos: qué ha pasado, por qué importa, qué lo
invalidaría, con qué confianza y cuántas veces ha acertado el sistema en casos
parecidos. Un toque abre la página de la señal: el debate completo, el filing
original y el historial de esa clase de evento. Cada domingo, un resumen dice
cómo han ido las señales y, con la misma importancia, de cuántos eventos se
abstuvo el sistema y por qué. El historial completo es público y verificable;
nadie tiene que fiarse de un backtest.

- **MVP actual:** motor sólido + panel interno + avisos técnicos.
- **V1:** validación real, panel privado, avisos legibles con enlace, página por
  señal, una sola recomendación, historial verificable.
- **V2:** resumen semanal, watchlist, "mis operaciones", freemium con retraso.
- **V3:** API de eventos clasificados para terceros; cobertura más allá de
  EE. UU. (CNMV, ESMA).

**Si el edge no existe:** el activo reutilizable es la infraestructura de
clasificación y análisis de eventos, no las señales. La V3 (API/herramienta de
investigación) pasa a ser la V1.

---

## Anexo — cambios implementados en esta auditoría

| Cambio | Ficheros |
|---|---|
| Feed de señales sin duplicados por corrida | `app/lib/queries.ts` |
| Métricas ausentes como "—" en vez de "0"; página robusta a versiones ausentes; columna Equilibrado en sensibilidad | `app/app/funciona/page.tsx`, `app/lib/queries.ts` |
| Caída máxima con signo negativo | `app/lib/format.ts` (+ portada, comparativa, tarjetas) |
| Vocabulario único: versiones, tipos de evento, motivos de salida, fuentes | `app/lib/labels.ts` y sus usos |
| Portada: frescura del pipeline, últimas señales, cifras de la versión recomendada | `app/app/page.tsx`, `app/lib/queries.ts` |
| Estados vacíos unificados; `error.tsx`, `loading.tsx`, `not-found.tsx` | `app/components/ui/PageState.tsx`, `app/app/*.tsx` |
| Responsive: navegación sin desbordamiento, padding unificado, aviso legal al pie | `app/components/Nav.tsx`, `app/app/layout.tsx`, `Disclaimer.tsx` |
| Basic Auth opcional, cabeceras de seguridad, `noindex` | `app/middleware.ts`, `app/next.config.ts`, `app/app/layout.tsx` |
| jsPDF bajo demanda; PDF con marca y vocabulario correctos | `app/components/ExportPdfButton.tsx` |
| Tabla comparativa en servidor | `app/components/ComparisonTable.tsx` |
| Código muerto eliminado | `VersionOverviewCard.tsx`, `CombinedEquityChart.tsx`, `public/*.svg` |
| Aviso de Telegram: escapado HTML, porqué, qué lo invalidaría, enlace al panel | `pipeline/notify/signals_notifier.py`, `pipeline/config.py`, workflow, `pipeline/tests/test_notify.py` |
| README real del panel | `app/README.md` |

Verificación: `tsc --noEmit` y `next build` sin errores; 762 tests de Python
en verde (759 previos + 3 nuevos); panel probado contra Postgres con datos
sintéticos a 375 px y 1280 px (sin scroll horizontal, 12 filas para 12
eventos); Basic Auth comprobada (401/401/200).


## Anexo 2 — segunda tanda de implementación

Aplicado todo lo que se podía hacer desde el código; lo que queda depende de
infraestructura, datos reales o decisiones de negocio.

| Punto del informe | Estado | Dónde |
|---|---|---|
| Página propia por señal, enlazada desde Telegram | ✅ | `app/app/senales/[id]/page.tsx`, `signals_notifier.py` |
| Feed ligero + detalle bajo demanda | ✅ | `lib/queries.ts` (`getSignalsFeed`, `getSignalDetail`), `app/api/senales/[id]/route.ts`, `SignalsTable.tsx` |
| Una sola versión de cara al usuario (la recomendada por el motor) | ✅ | `getRecommendedVersion`; columna Señal, Inicio, Historial y detalle la usan; las otras dos siguen en el detalle |
| Historial hacia delante (resultado en papel tras cada señal) | ✅ | `app/app/historial/page.tsx`, `getSignalHistory` |
| "Por qué no se ha operado" en Inicio | ✅ | `getAbstentionSummary`, `app/app/page.tsx` |
| Resumen semanal por Telegram | ✅ | `pipeline/notify/weekly_digest.py` (sábado UTC, una vez por semana ISO, reintenta si falla) |
| Onboarding de primera visita | ✅ | `components/WelcomeNote.tsx` |
| Tarjetas de cartera: 3 cifras + resto plegado | ✅ | `PortfolioVersionCard.tsx` |
| Tipografía: monoespaciada solo en cifras/tickers/marca; sin override de `.rounded` | ✅ | `globals.css`, componentes |
| Toggle de tema muestra la acción | ✅ | `ThemeToggle.tsx` |
| Largo plazo fuera de la navegación principal; Historial dentro | ✅ | `Nav.tsx`, `Disclaimer.tsx` |
| Caché de datos | ✅ | `lib/data.ts` |
| ESLint, tests del panel (18, incluidas consultas contra Postgres), CI | ✅ | `eslint.config.mjs`, `lib/*.test.ts`, `.github/workflows/app_ci.yml` |
| Vulnerabilidades npm | ✅ 0 | `package.json` (`overrides`) |
| Rol de solo lectura | ✅ script / 🔴 ejecutarlo | `pipeline/db/dashboard_readonly_role.sql` |
| Mitigación de inyección de prompt | ✅ | `adversarial_analyzer.py` + 3 tests |
| Gráficos: eje Y recortado, jerga en inglés | ✅ | `components/*Chart*.tsx`, `CalibrationCurve.tsx` |
| Sesgos y aciertos con signo "+" | ✅ | `formatShare` en `lib/format.ts` |

**No hecho, y por qué:**

- **Corrida real y validación con datos reales**: necesita las credenciales
  y la red de GitHub Actions; no se puede hacer desde el código.
- **Activar Basic Auth, crear el rol de solo lectura, definir
  `DASHBOARD_URL`**: configuración en Vercel/GitHub/Postgres (pasos en
  `RUNBOOK.md` §4).
- **Watchlist y "Mis operaciones"**: el panel es de solo lectura por diseño y
  no tiene cuentas; ambas necesitan almacenar preferencias por usuario. Tiene
  sentido construirlas junto con las cuentas (Fase 5), no antes.
- **Cuentas, pagos, API**: dependen de validar el edge y de la consulta
  legal.
- **Limpieza de comentarios-ensayo y partir RUNBOOK/IMPROVEMENT_PLAN**: es
  una pasada editorial sobre miles de líneas sin cambio funcional; hacerla
  junto a cambios de comportamiento haría ilegible el diff. Mejor en un PR
  propio.
- **Retirar DYNAMIC**: el propio plan (Q3) deja la decisión pendiente de
  datos reales; retirarla sin ellos sería decidir a ciegas.
- **404 con estado HTTP 200 en `/senales/[id]` inexistente**: el
  `loading.tsx` raíz hace que la respuesta empiece a enviarse antes de saber
  que no existe; la página muestra correctamente "no encontrada". Impacto
  nulo con `noindex`; no compensa perder el estado de carga.
