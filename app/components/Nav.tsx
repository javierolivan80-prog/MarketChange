"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

// Nav.tsx — barra de navegación.
//
// La pestaña activa se deduce de la URL (usePathname) en vez de depender
// solo de la prop: así error.tsx y not-found.tsx, que no saben en qué página
// están, también marcan bien la sección. La prop `active` se mantiene por
// compatibilidad y se usa si no hay ruta.
//
// Antes la cabecera usaba márgenes negativos (-mx-6) para "salirse" del
// padding de la página — con páginas de padding 4 eso la hacía 8px más ancha
// que la pantalla y provocaba scroll horizontal en móvil.
// Largo plazo (ranking fundamental) es otro producto, con otro horizonte: va
// en el pie de página, no compitiendo con el flujo principal de señales.
const TABS = [
  { href: "/", label: "Resumen" },
  { href: "/senales", label: "Señales" },
  { href: "/historial", label: "Historial" },
  { href: "/cartera", label: "Cartera" },
  { href: "/funciona", label: "Fiabilidad" },
  { href: "/como-funciona", label: "Cómo funciona" },
] as const;

export function Nav({ active }: { active?: string }) {
  const pathname = usePathname();
  const current = pathname ?? active ?? "";

  return (
    <header className="mb-5 border-b border-border-subtle">
      <div className="flex items-center justify-between gap-4">
        <Link href="/" className="font-mono text-sm font-semibold uppercase tracking-wider text-foreground">
          MarketChange
        </Link>
        <ThemeToggle />
      </div>
      <nav aria-label="Secciones" className="-mb-px mt-2 overflow-x-auto">
        <div className="flex gap-1">
          {TABS.map((tab) => {
            const isActive = tab.href === "/" ? current === "/" : current.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={isActive ? "page" : undefined}
                className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${
 isActive
                    ? "border-accent-600 font-medium text-foreground dark:border-accent-400"
                    : "border-transparent text-text-secondary hover:text-foreground"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </header>
  );
}
