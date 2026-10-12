"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { LanguageToggle } from "@/components/ui/LanguageToggle";
import { useT } from "@/components/i18n/LocaleProvider";

// Nav.tsx — barra de navegación.
//
// La pestaña activa se deduce de la URL (usePathname): así error.tsx y
// not-found.tsx, que no saben en qué página están, también marcan bien la
// sección.
//
// Vive en el layout raíz (Tanda 12), con el mismo ancho en todas las
// páginas: antes cada página la pintaba dentro de su propio <main> (3xl, 5xl
// o 7xl) y en pantallas anchas la cabecera saltaba de sitio al cambiar de
// pestaña o al llegar la página tras la pantalla de carga.
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

export function Nav() {
  const pathname = usePathname();
  const current = pathname ?? "";
  const t = useT();
  // En móvil las pestañas no caben (Tanda 12): la activa se desplaza a la
  // vista y un degradado en el borde avisa de que hay más mientras quede
  // contenido por ese lado.
  const tira = useRef<HTMLDivElement>(null);
  const [mas, setMas] = useState({ izquierda: false, derecha: false });

  function medir() {
    const el = tira.current;
    if (!el) return;
    setMas({ izquierda: el.scrollLeft > 4, derecha: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  }

  // Posiciones medidas en pantalla y relativas a la tira (offsetLeft lo es
  // al documento); el margen es el ancho del degradado, para que la activa
  // no quede debajo. Sin scrollIntoView: también movería la página en
  // vertical si la cabecera no está a la vista.
  useEffect(() => {
    const el = tira.current;
    const activa = el?.querySelector<HTMLElement>('[aria-current="page"]');
    if (el && activa) {
      const margen = 32;
      const caja = el.getBoundingClientRect();
      const a = activa.getBoundingClientRect();
      if (a.left < caja.left + margen) el.scrollLeft -= caja.left + margen - a.left;
      else if (a.right > caja.right - margen) el.scrollLeft += a.right - (caja.right - margen);
    }
    medir();
  }, [current]);

  // Al girar el móvil o cambiar el ancho, cambia si hay pestañas ocultas.
  useEffect(() => {
    const el = tira.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observador = new ResizeObserver(medir);
    observador.observe(el);
    return () => observador.disconnect();
  }, []);

  const mascara =
    mas.izquierda && mas.derecha
      ? "[mask-image:linear-gradient(to_right,transparent,black_2rem,black_calc(100%-2rem),transparent)]"
      : mas.derecha
        ? "[mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)]"
        : mas.izquierda
          ? "[mask-image:linear-gradient(to_right,transparent,black_2rem)]"
          : "";

  return (
    <header className="mb-5 border-b border-border-subtle">
      {/* Por debajo de 360px (móviles de 320) el logo y los huecos se
          reducen: la fila no cabía (311px en 288) y daba scroll horizontal. */}
      <div className="flex items-center justify-between gap-4 max-[359px]:gap-2">
        <Link href="/" className="font-mono text-sm font-semibold uppercase tracking-wider text-foreground max-[359px]:text-xs max-[359px]:tracking-wide">
          MarketChange
        </Link>
        <div className="flex items-center gap-2 max-[359px]:gap-1">
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </div>
      <nav aria-label={t("Secciones", "Sections")} className="-mb-px mt-2">
        <div ref={tira} onScroll={medir} className={`flex gap-1 overflow-x-auto [scrollbar-width:none] ${mascara}`}>
          {TABS.map((tab) => {
            const isActive = tab.href === "/" ? current === "/" : current.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={isActive ? "page" : undefined}
                className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm pointer-coarse:py-3 active:bg-surface-pressed ${
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
