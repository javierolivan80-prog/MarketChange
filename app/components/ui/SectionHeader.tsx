// SectionHeader.tsx — cabecera común de cada bloque de una página: título,
// una línea que dice qué responde el bloque y, si la hay, la acción que lleva
// a su detalle. Antes cada sección improvisaba la suya (h2 suelto, enlace a
// veces arriba a la derecha, descripción a veces arriba y a veces abajo), y
// los bloques de una misma página se leían como piezas sueltas.
import Link from "next/link";

export function SectionHeader({
  id,
  title,
  description,
  action,
}: {
  id?: string;
  title: string;
  description?: React.ReactNode;
  action?: { href: string; label: string };
}) {
  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={id} className="text-base font-semibold text-foreground">
          {title}
        </h2>
        {action && (
          <Link href={action.href} className="text-sm text-accent-700 hover:underline dark:text-accent-400">
            {action.label} →
          </Link>
        )}
      </div>
      {description && <p className="mt-0.5 max-w-3xl text-sm text-text-secondary">{description}</p>}
    </div>
  );
}
