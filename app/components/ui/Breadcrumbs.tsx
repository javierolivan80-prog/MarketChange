// Breadcrumbs.tsx — ruta de navegación en las páginas de detalle. La barra de
// pestañas dice en qué sección estás; esto dice en qué punto DENTRO de ella
// (Señales › STARK) y da un camino de vuelta a cada nivel.
import Link from "next/link";

export function Breadcrumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav aria-label="Ruta" className="mb-3 text-sm">
      <ol className="flex flex-wrap items-center gap-1.5 text-text-secondary">
        {items.map((item, i) => (
          <li key={`${item.label}-${i}`} className="flex items-center gap-1.5">
            {i > 0 && <span aria-hidden="true" className="text-text-tertiary">›</span>}
            {item.href ? (
              <Link href={item.href} className="hover:text-foreground hover:underline">
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="font-medium text-foreground">
                {item.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
