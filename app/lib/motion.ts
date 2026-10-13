// motion.ts — los tokens de motion de globals.css, en ms, para lo que no se
// puede animar con CSS (Recharts recibe duraciones como número). La fuente
// de verdad es globals.css; motion.test.ts falla si se separan.
export const motion = {
  duration: { instant: 0, micro: 120, fast: 180, base: 240, data: 560, spring: 588, max: 800 },
  stagger: 80,
} as const;

/** Props comunes de las series de Recharts. Sin esto Recharts usa sus
 * valores por defecto: 1500 ms en Line y Area (por encima del tope de
 * 800 ms) y curva `ease`. `isAnimationActive` se deja en su "auto", que ya
 * desactiva la animación con prefers-reduced-motion. */
export const chartSeriesMotion = {
  animationDuration: motion.duration.data,
  animationEasing: "ease-out",
} as const;

/** Tooltip de Recharts: por defecto se desliza 400 ms y va por detrás del
 * cursor. Y es blanco con el texto heredado: en modo oscuro el título salía
 * blanco sobre blanco. Se pinta con los colores del tema (Tanda 12). */
export const chartTooltipMotion = {
  animationDuration: motion.duration.micro,
  animationEasing: "ease-out",
  contentStyle: {
    background: "var(--surface-raised)",
    border: "1px solid var(--border-subtle)",
    // Esquinas y sombra como el resto de superficies (Tanda 12c).
    borderRadius: "0.625rem",
    boxShadow: "var(--shadow-card)",
    color: "var(--foreground)",
    fontSize: "0.75rem",
  },
  labelStyle: { color: "var(--foreground)", fontWeight: 600 },
  itemStyle: { color: "var(--text-secondary)" },
  cursor: { fill: "var(--surface-raised)" },
} as const;

/** Marcas de los ejes de Recharts: 12 px (con 10 px no se leían en el móvil)
 * y el gris terciario del tema (el #666 por defecto apenas contrastaba en
 * modo oscuro). No es movimiento, pero vive aquí con el resto del estilo
 * común de los gráficos. */
export const chartAxisTick = { fontSize: 12, fill: "var(--text-tertiary)" } as const;
