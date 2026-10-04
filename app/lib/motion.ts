// motion.ts — los tokens de motion de globals.css, en ms, para lo que no se
// puede animar con CSS (Recharts recibe duraciones como número). La fuente
// de verdad es globals.css; motion.test.ts falla si se separan.
export const motion = {
  duration: { instant: 0, micro: 120, fast: 180, base: 240, data: 560, max: 800 },
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
 * cursor. */
export const chartTooltipMotion = {
  animationDuration: motion.duration.micro,
  animationEasing: "ease-out",
} as const;
