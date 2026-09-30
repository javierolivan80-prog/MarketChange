import Link from "next/link";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

// Nav.tsx — barra de navegación. 5 pestañas en lenguaje llano (ver mapeo
// histórico en el git log) más el nombre del producto y el selector de tema.
const TABS = [
  { href: "/", label: "Inicio" },
  { href: "/senales", label: "Señales" },
  { href: "/largo-plazo", label: "Largo plazo" },
  { href: "/cartera", label: "Cartera" },
  { href: "/funciona", label: "¿Funciona?" },
  { href: "/como-funciona", label: "Cómo funciona" },
] as const;

export function Nav({ active }: { active: string }) {
  return (
    <header className="-mx-6 mb-4 border-b border-border-subtle px-6">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 pt-3">
        <Link href="/" className="font-mono text-sm font-semibold uppercase tracking-wider text-foreground">
          MarketChange
        </Link>
        <ThemeToggle />
      </div>
      <nav aria-label="Secciones" className="mx-auto max-w-7xl overflow-x-auto">
        <div className="flex gap-1">
          {TABS.map((tab) => (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={active === tab.href ? "page" : undefined}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-1.5 font-mono text-xs uppercase tracking-wide ${
                active === tab.href
                  ? "border-accent-600 font-medium text-foreground dark:border-accent-400"
                  : "border-transparent text-text-secondary hover:text-foreground"
              }`}
            >
              {tab.label}
            </Link>
          ))}
        </div>
      </nav>
    </header>
  );
}
