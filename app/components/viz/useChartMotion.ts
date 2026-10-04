"use client";

// useChartMotion.ts — props de animación para las series de Recharts: se
// animan una sola vez, la primera vez que se dibujan. Recharts relanza la
// animación cada vez que los datos llegan con otra referencia aunque los
// valores sean iguales, y eso pasa en cada router.refresh() (cambiar de
// idioma): el área de drawdown se aplanaba ~600 ms y volvía a crecer, como si
// el dato hubiera cambiado. Aquí no hay datos en vivo (ver MOTION_PLAN.md),
// así que tras la primera animación la serie se pinta siempre directamente en
// su estado final.
//
// Se corta en onAnimationEnd y no con un temporizador desde el montaje: el
// gráfico no se dibuja hasta que ResponsiveContainer mide su caja (cientos de
// ms después de montar), y un temporizador cortaba la entrada a medias.
import { useCallback, useState } from "react";
import { chartSeriesMotion } from "@/lib/motion";

export function useChartMotion(duration: number = chartSeriesMotion.animationDuration) {
  const [done, setDone] = useState(false);
  const onAnimationEnd = useCallback(() => setDone(true), []);
  return {
    ...chartSeriesMotion,
    animationDuration: duration,
    // "auto": Recharts ya la desactiva con prefers-reduced-motion.
    isAnimationActive: done ? false : ("auto" as const),
    onAnimationEnd,
  };
}
