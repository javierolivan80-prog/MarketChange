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

  // Indicador de la pestaña activa (Tanda 12c, guía de Apple): una píldora
  // que se desliza con un muelle de una pestaña a otra, como un control
  // segmentado, en vez de un subrayado que salta. Posición medida con
  // offsetLeft/offsetTop respecto a la tira (que es `relative`), así que se
  // desplaza con ella al hacer scroll. La primera medida no se anima.
  const [indicador, setIndicador] = useState<{ x: number; y: number; w: number; h: number; animar: boolean } | null>(null);
  function medirIndicador() {
    const activa = tira.current?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!activa) return setIndicador(null);
    setIndicador((prev) => ({ x: activa.offsetLeft, y: activa.offsetTop, w: activa.offsetWidth, h: activa.offsetHeight, animar: prev !== null }));
  }
  useEffect(medirIndicador, [current]);
  useEffect(() => {
    const el = tira.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observador = new ResizeObserver(() => medirIndicador());
    observador.observe(el);
    return () => observador.disconnect();
  }, []);

  // Al desplazarse: (1) borde difuminado bajo la barra solo cuando el
  // contenido pasa por debajo (guía de Apple: efecto de borde, no línea fija);
  // (2) el título de la página se recoge en la barra cuando el grande queda
  // tapado. La barra está pegada cuando su top real coincide con el `top`
  // negativo del sticky (ver layout.tsx).
  const [tituloRecogido, setTituloRecogido] = useState<string | null>(null);
  const cabecera = useRef<HTMLElement>(null);
  useEffect(() => {
    const banda = cabecera.current?.closest<HTMLElement>(".barra-material");
    if (!banda) return;
    let pendiente = 0;
    function actualizar() {
      pendiente = 0;
      if (!banda) return;
      const topFijo = parseFloat(getComputedStyle(banda).top) || 0;
      const caja = banda.getBoundingClientRect();
      const pegada = window.scrollY > 0 && caja.top <= topFijo + 0.5;
      if (pegada) banda.dataset.pegada = "";
      else delete banda.dataset.pegada;
      const h1 = document.querySelector("main h1");
      setTituloRecogido(h1 && h1.getBoundingClientRect().bottom < caja.bottom ? (h1.textContent ?? "").trim() : null);
    }
    function alDesplazar() {
      if (!pendiente) pendiente = requestAnimationFrame(actualizar);
    }
    actualizar();
    window.addEventListener("scroll", alDesplazar, { passive: true });
    window.addEventListener("resize", alDesplazar);
    return () => {
      window.removeEventListener("scroll", alDesplazar);
      window.removeEventListener("resize", alDesplazar);
      cancelAnimationFrame(pendiente);
    };
  }, [current]);

  const mascara =
    mas.izquierda && mas.derecha
      ? "[mask-image:linear-gradient(to_right,transparent,black_2rem,black_calc(100%-2rem),transparent)]"
      : mas.derecha
        ? "[mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)]"
        : mas.izquierda
          ? "[mask-image:linear-gradient(to_right,transparent,black_2rem)]"
          : "";

  return (
    <header ref={cabecera} className="mb-5">
      {/* Por debajo de 360px (móviles de 320) el logo y los huecos se
          reducen: la fila no cabía (311px en 288) y daba scroll horizontal.
          Logo en la fuente del sistema (Tanda 12c), no en monoespaciada y
          mayúsculas. */}
      <div className="flex items-center justify-between gap-4 max-[359px]:gap-2">
        <Link href="/" className="text-[1.0625rem] font-semibold tracking-[-0.01em] text-foreground max-[359px]:text-[0.875rem]">
          MarketChange
        </Link>
        <div className="flex items-center gap-2 max-[359px]:gap-1">
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </div>
      <nav aria-label={t("Secciones", "Sections")} className="mt-2 flex items-center gap-4 pb-2">
        <div ref={tira} onScroll={medir} className={`relative flex min-w-0 flex-1 gap-1 overflow-x-auto [scrollbar-width:none] ${mascara}`}>
          {indicador && (
            <span
              aria-hidden="true"
              className={`absolute left-0 top-0 rounded-full bg-fill-strong motion-reduce:transition-none ${
                indicador.animar ? "transition-[translate,width,height] duration-(--motion-duration-spring) ease-(--motion-ease-spring)" : ""
              }`}
              style={{ translate: `${indicador.x}px ${indicador.y}px`, width: indicador.w, height: indicador.h }}
            />
          )}
          {TABS.map((tab) => {
            const isActive = tab.href === "/" ? current === "/" : current.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={isActive ? "page" : undefined}
                className={`relative whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm transition-colors duration-(--motion-duration-micro) pointer-coarse:py-3 ${
                  isActive ? "font-medium text-foreground" : "text-text-secondary hover:text-foreground active:bg-fill"
                }`}
              >
                {t(tab.es, tab.en)}
              </Link>
            );
          })}
        </div>
        {/* Título recogido: duplica el h1 (por eso aria-hidden) y solo se ve
            desde md; en el móvil la pestaña activa ya dice dónde se está. */}
        <span
          aria-hidden="true"
          className={`hidden max-w-[16rem] shrink-0 truncate text-sm font-semibold text-foreground transition-[opacity,translate] duration-(--motion-duration-spring-fast) ease-(--motion-ease-spring) motion-reduce:transition-none md:block ${
            tituloRecogido ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0"
          }`}
        >
          {tituloRecogido}
        </span>
      </nav>
    </header>
  );
}
