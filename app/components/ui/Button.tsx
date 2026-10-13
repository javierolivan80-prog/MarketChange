// Button.tsx — una sola forma de botón en todo el panel.
//
// Antes había cinco: relleno azul ("Filtrar"), con borde ("Exportar",
// paginación, tema), texto suelto ("Limpiar", "Entendido") y el de error,
// cada uno con su propio padding y tamaño de letra. El usuario no podía saber
// por la forma qué acción era la principal. Ahora:
//   primary   — la acción principal de la pantalla (una por bloque)
//   secondary — acciones de apoyo (exportar, paginar, reintentar)
//   ghost     — acciones menores o de descarte (limpiar, cerrar)
// buttonClass() sirve también para enlaces que deben verse como botón.
//
// Respuesta al pulsar (Tanda 12, guía de diseño de Apple): el botón reacciona
// en el mismo momento de pulsar (active:), no al soltar, también en pantallas
// táctiles, donde hover: no existe; se encoge apenas (sin si el usuario pide
// menos movimiento) y al soltar vuelve con un muelle (Tanda 12c). Con el
// dedo, al menos 44px de alto.
import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost";

const BASE =
  "inline-flex min-h-9 pointer-coarse:min-h-11 items-center justify-center gap-1.5 rounded-control border border-transparent px-3.5 text-sm font-medium transition-[color,background-color,border-color,scale] duration-(--motion-duration-spring-fast) ease-(--motion-ease-spring) [-webkit-tap-highlight-color:transparent] active:scale-[0.97] active:duration-(--motion-duration-micro) contrast-more:border-border-strong motion-reduce:active:scale-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100";

// Estilos de botón de Apple (Tanda 12c): relleno para la acción principal,
// gris para las de apoyo y solo texto en el color de acento para las menores.
// El borde es transparente: no se ve, salvo con más contraste (borde marcado)
// y con colores forzados (el sistema lo pinta; sin él, los grises y los de
// texto no tenían contorno). En oscuro el principal va en accent-600 con
// texto blanco: sobre accent-500/400 no llegaba a 4,5:1.
const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent-600 text-white hover:bg-accent-700 active:bg-accent-700 dark:bg-accent-600 dark:hover:bg-accent-700 dark:active:bg-accent-700",
  secondary: "bg-fill text-foreground hover:bg-fill-strong active:bg-fill-pressed",
  ghost: "text-accent-700 hover:bg-fill active:bg-fill-pressed dark:text-accent-400",
};

export function buttonClass(variant: ButtonVariant = "secondary", extra = ""): string {
  return `${BASE} ${VARIANTS[variant]} ${extra}`.trim();
}

export function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button type={type} className={buttonClass(variant, className)} {...props} />;
}
