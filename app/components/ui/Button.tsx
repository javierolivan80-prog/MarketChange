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
import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost";

const BASE =
  "inline-flex min-h-9 items-center justify-center gap-1.5 px-3 text-sm font-medium transition-colors duration-(--motion-duration-micro) ease-(--motion-ease-out) disabled:cursor-not-allowed disabled:opacity-40";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent-600 text-white hover:bg-accent-700 dark:bg-accent-500 dark:hover:bg-accent-400 dark:text-black",
  secondary: "border border-border-strong text-foreground hover:border-accent-500 hover:text-accent-700 dark:hover:text-accent-300",
  ghost: "text-text-secondary hover:bg-surface-raised hover:text-foreground",
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
