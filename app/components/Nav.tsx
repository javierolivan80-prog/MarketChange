"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { LanguageToggle } from "@/components/ui/LanguageToggle";
import { useT } from "@/components/i18n/LocaleProvider";

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
  { href: "/", es: "Resumen", en: "Overview" },
  { href: "/senales", es: "Señales", en: "Signals" },
  { href: "/historial", es: "Historial", en: "Track record" },
  { href: "/cartera", es: "Cartera", en: "Portfolio" },
  { href: "/funciona", es: "Fiabilidad", en: "Reliability" },
  { href: "/como-funciona", es: "Cómo funciona", en: "How it works" },
] as const;

export function Nav({ active }: { active?: string }) {
  const pathname = usePathname();
  const current = pathname ?? active ?? "";
  const t = useT();

  return (
    <header className="mb-5 border-b border-border-subtle">
      <div className="flex items-center justify-between gap-4">
        <Link href="/" className="font-mono text-sm font-semibold uppercase tracking-wider text-foreground">
          MarketChange
        </Link>
        <div className="flex items-center gap-2">
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </div>
      <nav aria-label={t("Secciones", "Sections")} className="-mb-px mt-2 overflow-x-auto">
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
                {t(tab.es, tab.en)}
              </Link>
            );
          })}
        </div>
      </nav>
    </header>
  );
}
